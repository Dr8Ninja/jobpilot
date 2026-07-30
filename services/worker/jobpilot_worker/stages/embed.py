"""Embeddings and the pgvector pre-filter.

The cheap deterministic gate in front of the expensive one: embed every JD, rank
by cosine distance against the candidate's own resume text, and only send the top
K to the LLM. This is what keeps scoring cost proportional to relevance rather
than to how many boards were pulled.
"""

import datetime as dt
import logging
from dataclasses import dataclass

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Job, JobEmbedding
from jobpilot_shared.seniority import is_too_senior
from jobpilot_shared.settings import get_settings
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..clients.embeddings import EmbeddingClient

log = logging.getLogger(__name__)


def resume_text(facts: CanonicalFacts) -> str:
    """A flat text projection of canonical_facts, for the similarity comparison."""
    parts = [
        f"{facts.identity.name} — {facts.experience_years} years experience",
        "Skills: " + ", ".join(facts.skills),
    ]
    for role in facts.employment:
        parts.append(f"{role.title} at {role.company} ({role.start}–{role.end})")
        parts.extend(f"- {b}" for b in role.bullets)
    for edu in facts.education:
        parts.append(f"{edu.degree}, {edu.institution} ({edu.year})")
    return "\n".join(parts)


def job_text(job: Job, char_budget: int | None = None) -> str:
    """Text handed to the embedding model.

    Embedding models have a hard input cap — `nv-embedqa-e5-v5` rejects anything
    over 512 tokens outright, and a full JD is routinely 2-4x that. This is only
    the cheap pre-filter, so truncation is fine: the title and the opening of the
    description carry the signal, and the LLM scorer later reads the whole thing.
    """
    budget = char_budget or get_settings().embedding_char_budget
    header = f"{job.title}\n{job.location or ''}\n\n"
    return (header + job.description)[:budget]


def embed_pending_jobs(session: Session, client: EmbeddingClient, *, batch_size: int = 32) -> int:
    """Embed every job that has no embedding yet. Returns how many were embedded."""
    pending = list(
        session.scalars(
            select(Job)
            .outerjoin(JobEmbedding, JobEmbedding.job_id == Job.id)
            .where(JobEmbedding.job_id.is_(None), Job.superseded_by.is_(None))
        )
    )
    if not pending:
        return 0

    settings = get_settings()
    model = settings.embedding_model
    budget = settings.embedding_char_budget
    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = client.embed([job_text(j) for j in batch], input_type="document")
            pairs = list(zip(batch, vectors, strict=True))
        except Exception as exc:
            # A batch is rejected wholesale if any single item is oversized, so
            # fall back to per-item rather than losing 31 good jobs to one bad one.
            log.warning("Embedding batch failed (%s); retrying individually", exc)
            pairs = []
            for job in batch:
                try:
                    vector = client.embed([job_text(job)], input_type="document")[0]
                    pairs.append((job, vector))
                except Exception:
                    # Some descriptions tokenize far denser than the usual
                    # ~4 chars/token, so a budget that is safe on average still
                    # overflows on outliers. Halve once before giving up.
                    try:
                        short = job_text(job, char_budget=budget // 2)
                        vector = client.embed([short], input_type="document")[0]
                        pairs.append((job, vector))
                    except Exception as inner:
                        log.warning("Could not embed job %s: %s", job.id, inner)

        for job, vector in pairs:
            session.add(JobEmbedding(job_id=job.id, embedding=vector, model=model))
            embedded += 1
        session.flush()
    return embedded


@dataclass
class Candidate:
    job: Job
    similarity: float


def prefilter(
    session: Session,
    facts: CanonicalFacts,
    client: EmbeddingClient,
    *,
    top_k: int | None = None,
    exclude_job_ids: set[int] | None = None,
) -> list[Candidate]:
    """Rank jobs by cosine similarity to the resume, keeping the top K."""
    settings = get_settings()
    top_k = top_k or settings.embed_top_k

    query_vector = client.embed([resume_text(facts)], input_type="query")[0]
    distance = JobEmbedding.embedding.cosine_distance(query_vector)

    statement = (
        select(Job, distance.label("distance"))
        .join(JobEmbedding, JobEmbedding.job_id == Job.id)
        .where(Job.superseded_by.is_(None))
    )

    # Freshness. A four-month-old posting is usually filled or stale, and it
    # crowds out live ones. Undated rows are excluded rather than assumed fresh.
    if settings.max_posting_age_days > 0:
        cutoff = dt.datetime.now(dt.UTC) - dt.timedelta(days=settings.max_posting_age_days)
        statement = statement.where(Job.posted_at.is_not(None), Job.posted_at >= cutoff)

    # Location and seniority are the only two things that can rule a job out, and
    # both are knowable without an LLM. Applying them here means the scoring
    # budget is spent entirely on jobs that could actually be tailored — and the
    # shortlist tab stays a list of real options rather than staff roles.
    if not settings.tailor_overseas:
        statement = statement.where(Job.location_kind.in_(("india", "remote")))

    # Over-fetch: the seniority check below rejects some rows, and rejected rows
    # must not eat into the top_k the caller asked for.
    fetch = (top_k * 3) + len(exclude_job_ids or ())
    statement = statement.order_by(distance).limit(fetch)

    results: list[Candidate] = []
    for job, dist in session.execute(statement):
        if exclude_job_ids and job.id in exclude_job_ids:
            continue
        if is_too_senior(job.title, job.description or "", max_years=settings.max_years_required):
            continue
        results.append(Candidate(job=job, similarity=1.0 - float(dist)))
        if len(results) >= top_k:
            break
    return results
