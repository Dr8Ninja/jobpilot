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


#: Hard cap of `nv-embedqa-e5-v5`. Everything below aims safely under it.
EMBEDDING_TOKEN_LIMIT = 512
#: Measured from the provider's rejection messages — see `estimated_tokens`.
NON_ASCII_TOKENS_PER_CHAR = 2.5


def estimated_tokens(text: str) -> int:
    """Rough token count that is honest about non-Latin scripts.

    A character budget alone was wrong, and it failed on real data: English
    prose runs about 4 characters per token, but Japanese and Korean run closer
    to *one token per character*. A 1600-character budget is ~400 tokens of
    English and ~1600 tokens of Japanese, so every CJK posting was rejected —
    62 of them in one run, including whole boards like Datadog Tokyo.

    The weights are calibrated against the provider's own rejection messages,
    which report the true token count: solving for the per-character cost across
    six rejected Japanese and Korean postings gave 1.88-2.25 tokens per non-ASCII
    character. 2.5 is used here so the estimate errs high — an over-estimate
    costs a few characters of context, an under-estimate costs the whole row.
    """
    ascii_chars = sum(1 for c in text if ord(c) < 128)
    return int(ascii_chars / 4 + (len(text) - ascii_chars) * NON_ASCII_TOKENS_PER_CHAR)


def fit_to_token_budget(text: str, token_budget: int) -> str:
    """Trim text until its estimated token count fits, by binary search."""
    if estimated_tokens(text) <= token_budget:
        return text
    low, high = 0, len(text)
    while low < high:
        mid = (low + high + 1) // 2
        if estimated_tokens(text[:mid]) <= token_budget:
            low = mid
        else:
            high = mid - 1
    return text[:low]


def job_text(job: Job, char_budget: int | None = None, token_budget: int | None = None) -> str:
    """Text handed to the embedding model.

    Two caps apply. The character budget keeps English JDs to a sensible prefix;
    the token budget is the one the provider actually enforces. This is only the
    cheap pre-filter, so truncation is fine — the title and the opening of the
    description carry the signal, and the LLM scorer later reads the whole thing.
    """
    settings = get_settings()
    budget = char_budget or settings.embedding_char_budget
    header = f"{job.title}\n{job.location or ''}\n\n"
    text = (header + job.description)[:budget]
    # Leave headroom: the estimate is approximate and a rejection costs the row.
    return fit_to_token_budget(text, token_budget or settings.embedding_token_budget)


def _embed_one(client: EmbeddingClient, job: Job) -> list[float] | None:
    """Embed one job, shrinking the budget until the provider accepts it.

    `job_text` already trims to an estimated token budget, but the estimate is a
    heuristic — a posting full of emoji, rare scripts or long unbroken tokens can
    still come in over the limit. Halving repeatedly converges for any text,
    where the previous single halving did not: a Japanese JD trimmed from 1600 to
    800 characters was still ~700 tokens and was dropped outright.
    """
    settings = get_settings()
    token_budget = settings.embedding_token_budget
    last: Exception | None = None
    for _ in range(4):
        try:
            return client.embed([job_text(job, token_budget=token_budget)], input_type="document")[
                0
            ]
        except Exception as exc:
            last = exc
            token_budget //= 2
    log.warning(
        "Could not embed job %s after shrinking to %s tokens: %s", job.id, token_budget, last
    )
    return None


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
                vector = _embed_one(client, job)
                if vector is not None:
                    pairs.append((job, vector))

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

    # The query side needs the same cap as the document side. It was missed
    # because a short resume fit comfortably — until the candidate added a
    # project and eight skills, which pushed it to 576 tokens and took the whole
    # run down with a 400. Growing your own resume must never break the pipeline.
    query = fit_to_token_budget(resume_text(facts), settings.embedding_token_budget)
    query_vector = client.embed([query], input_type="query")[0]
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
