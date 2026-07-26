"""Embeddings and the pgvector pre-filter.

The cheap deterministic gate in front of the expensive one: embed every JD, rank
by cosine distance against the candidate's own resume text, and only send the top
K to the LLM. This is what keeps scoring cost proportional to relevance rather
than to how many boards were pulled.
"""

import logging
from dataclasses import dataclass

from jobpilot_shared.canonical_facts import CanonicalFacts
from jobpilot_shared.db.models import Job, JobEmbedding
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


def job_text(job: Job) -> str:
    return f"{job.title}\n{job.location or ''}\n\n{job.description}"[:20000]


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

    model = get_settings().embedding_model
    embedded = 0
    for start in range(0, len(pending), batch_size):
        batch = pending[start : start + batch_size]
        try:
            vectors = client.embed([job_text(j) for j in batch], input_type="document")
        except Exception as exc:  # one batch failing must not lose the rest
            log.warning("Embedding batch failed: %s", exc)
            continue
        for job, vector in zip(batch, vectors, strict=True):
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
        .order_by(distance)
        .limit(top_k + len(exclude_job_ids or ()))
    )

    results: list[Candidate] = []
    for job, dist in session.execute(statement):
        if exclude_job_ids and job.id in exclude_job_ids:
            continue
        results.append(Candidate(job=job, similarity=1.0 - float(dist)))
        if len(results) >= top_k:
            break
    return results
