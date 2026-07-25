# Phase 0 Design — Prove the Loop

**Date:** 2026-07-25
**Status:** Approved
**Scope:** Phase 0 only, per `CLAUDE.md` roadmap
**Source of truth for requirements:** `PRD.md`

---

## 1. Goal

Build the JobPilot pipeline end-to-end except automated form-fill: ingest a base resume into
`canonical_facts`, discover Greenhouse jobs, score them, tailor the resume per role behind a hard
anti-hallucination gate, render an ATS-parseable PDF, and review the results in a dashboard. The
user applies manually using the generated PDF.

Phase 0 exists to answer one question: **is the tailored output good enough to be worth
automating the submission of?** Every design choice below favours being able to judge that.

## 2. Decisions made during brainstorming

### 2.1 Answers to the CLAUDE.md open questions

| # | Question | Decision |
|---|---|---|
| 1 | Response-rate baseline | Log outcomes to `events` from day one; build no analytics in Phase 0. **Requires user input:** the baseline figure ("of my last N manual applications, M got a reply") has not yet been supplied. The `events` mechanism does not depend on it, so implementation is unblocked; the number is recorded in §13 when known. |
| 2 | Answer-bank scope | **Deferred to Phase 1.** No form-fill in Phase 0, so no consumer. |
| 3 | Salary data | Accept sparse. `jobs.salary` nullable, populated only when a payload happens to include it. Renders as `—`. Not an input to scoring. |
| 4 | Extension vs headed Playwright | **Deferred to Phase 1.** No browser automation in Phase 0. |
| 5 | Volume dial | Governs tailoring throughput in Phase 0: `MAX_TAILORED_PER_DAY=12`, `MATCH_SCORE_THRESHOLD=70`. Both config, not constants. |

### 2.2 Additional decisions

| Topic | Decision |
|---|---|
| Company registry source | **Aggregator (Adzuna) pulled forward from Phase 2 into Phase 0.** See §2.3. |
| Aggregator role | Both company discovery **and** a direct job source. |
| Cross-source dedupe | Certainty only: drop the aggregator row **only** when its resolved URL yields a Greenhouse `(board_token, ats_job_id)` matching an existing row. No fuzzy title matching. |
| Thin descriptions | Upgrade via the board API where the resolved URL identifies a Greenhouse job; otherwise keep the snippet, mark `description_quality='thin'`, still score and tailor, and badge it in the queue. |
| `canonical_facts` confirmation | CLI: `ingest-resume` → hand-edit gitignored JSON → `confirm-facts`. No wizard UI. |
| Pipeline trigger | Hybrid: individually invocable stages + one composite `run-pipeline`; Celery beat schedule added at the end of Phase 0. |
| Whitelist gate placement | Pure function in `packages/shared`. No I/O. Built and tested before the tailoring engine exists. |
| Embeddings | Voyage `voyage-3` (1024d). Anthropic has no embeddings endpoint. |
| Tailoring model | Claude Sonnet 5, forced tool-use, temperature 0. Synchronous — not the Batches API, which has no nightly bulk run to optimise in Phase 0. |
| Database | Local Postgres 17 + pgvector via Homebrew. Redis deferred to end of phase. |

### 2.3 Amendment to the CLAUDE.md roadmap

Phase 0 acceptance in `CLAUDE.md` reads "Discovery from **Greenhouse board API only**", and Phase 2
owns "aggregator-driven company discovery". The user has directed that the aggregator move into
Phase 0 as both a company-discovery mechanism and a direct job source.

**`CLAUDE.md` must be updated in the same PR** so the operating contract stays accurate:
- Phase 0 acceptance gains aggregator discovery + cross-source dedupe.
- Phase 2 loses "aggregator-driven company discovery auto-grows the registry".

This is a deliberate, recorded deviation from phase discipline, not an oversight.

## 3. Non-negotiables and how this design enforces them

| Non-negotiable | Enforcement in Phase 0 |
|---|---|
| No scraping / no evasion | Only documented APIs: Greenhouse board API, Adzuna API. Redirect resolution follows standard HTTP redirects; a 403 or bot-check is **recorded and skipped, never retried around**. |
| Never fabricate resume content | §5 gate, enforced at two independent layers (API filter + render assertion), covered by an adversarial test suite. |
| Submission is human-present | No browser automation exists in Phase 0 at all. Apply is manual. |
| Volume is a bounded dial | `MAX_TAILORED_PER_DAY=12` caps tailoring; nothing in the code path maximises throughput. |
| Secrets never hit the repo | All keys in gitignored `.env`; `.env.example` documents names only. `profile/` (PII) is gitignored. Fixtures contain no real personal data. |

## 4. Architecture

Organizing rule: **every pipeline stage is a plain Python function with no framework in its
signature.** Celery tasks and CLI commands are both thin wrappers over the same functions. Tests
never need a broker, a scheduler, or a running API.

```
packages/shared/          # zero I/O. no DB, no HTTP, no LLM client.
  canonical_facts.py      #   pydantic model + validation
  tailoring_io.py         #   tailoring request/response schemas
  whitelist.py            #   check(facts, output) -> Ok | Rejected(reasons)
  lexicon.py              #   technology token lexicon (static, checked in)

services/worker/stages/   # each: plain function, clients injected
  discover_greenhouse.py  #   board_token -> list[RawJob]
  discover_aggregator.py  #   query -> list[RawListing]
  resolve.py              #   RawListing -> ResolvedListing (redirect + GH id parse)
  ingest.py               #   RawJob | ResolvedListing -> upsert + dedupe
  embed.py                #   job -> vector; cosine prefilter
  score.py                #   (facts, job) -> Score
  tailor.py               #   (facts, job, score) -> TailoringOutput, gate, retry
  render.py               #   (facts, TailoringOutput) -> HTML -> PDF

services/worker/tasks.py  # @celery.task wrappers, ~5 lines each
services/api/             # FastAPI: queue read endpoints + approve/reject/mark-applied
services/api/cli.py       # Typer: ingest-resume, confirm-facts, seed-companies, run-pipeline
apps/web/                 # Next.js review queue
infra/                    # docker-compose (Postgres+pgvector, Redis) for portability
```

Three boundaries worth stating explicitly:

- **`packages/shared` imports nothing from `services/`.** It holds the two schemas and the gate.
  It is the only package the gate tests need, which is why the gate lands before the engine it guards.
- **`resolve.py` is separate from both discovery stages.** It owns redirect-following and
  Greenhouse-URL parsing — the mechanism dedupe depends on — so "is this the same job?" has one
  home and one test file, and neither discovery source knows about the other.
- **`render.py` takes `canonical_facts` directly, not a DB row.** The PDF is generated from the
  same object the gate validated, so no unvalidated intermediate can reach the document.

## 5. The whitelist gate (the critical unit)

```python
# packages/shared/whitelist.py
def check(facts: CanonicalFacts, output: TailoringOutput) -> GateResult
# GateResult = Ok() | Rejected(reasons: list[Violation])
# Violation = (rule, severity, detail, evidence)
```

| Rule | Check | Severity |
|---|---|---|
| `unknown_skill` | `set(skills_referenced) ∪ set(skills_ordered_for_this_jd) ⊆ set(facts.skills)`, normalized for case, whitespace, and punctuation (`Node.js` ≡ `NodeJS`) | **reject + re-run** |
| `yoe_inflation` | regex `(\d+(?:\.\d+)?)\+?\s*years?` over summary and bullets; any figure greater than `facts.experience_years` | **reject + re-run** |
| `unknown_employer` | capitalized org-like token in prose absent from `facts.employment[].company` | **reject + re-run** |
| `unlisted_token` | prose tokens matched against the shipped technology lexicon; token in lexicon but not in `facts.skills` | **flag for human** |

The severity split follows `PRD.md` §4.4, which specifies reject-and-rerun for declared skills but
flag-for-human for prose tokens.

`unlisted_token` uses a **static lexicon rather than NER** deliberately: NER requires a model, is
nondeterministic, and cannot be unit-tested against a fixed expectation. A checked-in lexicon is
auditable, and its failure mode is a false negative — already covered by `unknown_skill` for
anything the model explicitly declares.

**Retry lives outside the gate.** `tailor.py` runs at most 3 attempts, each re-prompting with the
prior violations as explicit constraints. After 3 failures the run persists with
`whitelist_passed=false` and the application moves to `needs_human`. It is never silently shown.

**Two independent enforcement layers**, because a single point of failure here breaks the
product's core ethical guarantee:
1. The API filters approvable cards on `whitelist_passed`.
2. `render.py` asserts it before generating any PDF.

## 6. Data model

```sql
users        (id, email, created_at)
profiles     (user_id PK, canonical_facts jsonb, base_resume_path, confirmed_at)

companies    (id, name, normalized_name, ats_provider, board_token,
              discovered_via,              -- 'seed' | 'aggregator'
              UNIQUE(normalized_name))
             -- partial unique: (ats_provider, board_token) WHERE board_token IS NOT NULL

jobs         (id, company_id,
              source,                      -- 'greenhouse' | 'aggregator'
              external_id,                 -- GH job id, or aggregator listing id
              ats_job_id,                  -- GH id only; NULL for unresolved aggregator rows
              title, location, description, apply_url, resolved_url,
              description_quality,         -- 'full' | 'thin'
              salary,                      -- nullable, sparse, not scored on
              content_hash, discovered_at, superseded_by,
              UNIQUE(source, external_id))
             -- partial unique: (company_id, ats_job_id) WHERE ats_job_id IS NOT NULL

job_embeddings (job_id PK, embedding vector(1024), model, embedded_at)
scores         (id, job_id, match_score, verdict jsonb, scored_at)
tailoring_runs (id, job_id, output jsonb, whitelist_passed bool,
                gate_rejections jsonb, attempt, pdf_path, created_at)
applications   (id, job_id, tailoring_run_id, status, approved_at, rejected_at)
                -- status: queued | approved | applied | rejected | needs_human | failed
events         (id, application_id, type, payload jsonb, ts)
```

Rationale for the choices that differ from `PRD.md` §5:

- **`UNIQUE(source, external_id)`** is the always-safe idempotency key: re-running discovery never
  duplicates.
- **Partial unique on `(company_id, ats_job_id)`** enforces the certainty dedupe rule at the
  database level — a Greenhouse row and a resolved aggregator row sharing a real job id cannot
  coexist. Unresolved aggregator rows carry `ats_job_id = NULL`, and Postgres treats NULLs as
  distinct, so they are never falsely collapsed.
- **`superseded_by`** records the drop instead of deleting, so it is measurable how often the
  aggregator was redundant — evidence for whether pulling it forward paid off.
- **`answer_bank` is omitted.** No consumer until Phase 1.
- **`events` exists but is unused by any Phase 0 feature.** It is the substrate for the
  response-rate baseline: cheap to write from day one, expensive to backfill later.

## 7. Scoring, tailoring, rendering

Voyage `voyage-3` (1024d) embeds each JD and the canonical resume text. pgvector cosine takes the
top K (default 40) above a similarity floor. Survivors go to Claude Sonnet 5 with forced tool-use
for the `PRD.md` §4.3 verdict schema at temperature 0. Jobs above `MATCH_SCORE_THRESHOLD=70`, capped
at the top `MAX_TAILORED_PER_DAY=12` by score, proceed to tailoring.

Tailoring is Sonnet 5 with forced tool-use against the `PRD.md` §4.4 output schema. Rendering is
Jinja2 → HTML → WeasyPrint: single column, no tables-for-layout, embedded standard fonts,
selectable text.

The PDF carries a **round-trip test**: generate, extract with `pdfplumber`, assert canonical name,
employers, and dates appear verbatim and that no non-whitelisted skill appears anywhere in the
extracted text. This proves the guarantee survives to the artefact a recruiter actually opens.

## 8. Dashboard

Next.js, three routes:

- **`/queue`** — cards showing company, role, location, `match_score`, salary or `—`, a source
  badge (`greenhouse` / `aggregator`), a `thin` badge where applicable, and any gate warnings.
- **`/queue/[id]`** — diff view: original bullet → rewritten, side by side, changed tokens
  highlighted, plus the rendered PDF.
- Actions: **Approve** / **Reject** only. No edit-in-place — that is Phase 2.

Because Phase 0 apply is manual, an approved card exposes `apply_url` and a **Mark as applied**
action, which writes the `events` rows the response-rate baseline is later computed from.

## 9. Error handling

| Failure | Behaviour |
|---|---|
| One board token fails | Isolated and logged; the run continues. One bad token never aborts discovery. |
| 5xx / 429 | Retry with exponential backoff. |
| 403 or bot-check | Recorded and skipped. **Never retried around** (non-negotiable #1). The row simply stays un-deduped. |
| LLM schema validation fails | Retry twice, then mark the job failed and continue. |
| Gate rejects 3× | Persist run with `whitelist_passed=false`; application → `needs_human`. |
| WeasyPrint fails | Persist run with `pdf_path=NULL`; card shows "render failed". |

Every one of these writes an `events` row.

## 10. Testing

- **`pytest`, no network.** Recorded Greenhouse and Adzuna JSON in `tests/fixtures/`. LLM and
  embedding clients sit behind Protocols with fakes.
- **The gate suite is the priority suite** and is table-driven and adversarial: casing variants,
  unicode lookalikes, synonym injection, YOE inflation, unknown employers, empty skill lists.
- **One PDF round-trip test** exercising real WeasyPrint and real `pdfplumber`.
- **Frontend:** vitest + testing-library for the diff view.

### Fixture mode

`JOBPILOT_FIXTURE_MODE=1` wires the same fakes into the real pipeline, so `run-pipeline` completes
end-to-end and populates the dashboard with demo data on a machine with **zero API keys**. Adding
real keys to `.env` puts the identical code path live. This is what makes the system demonstrable
before the user has supplied credentials or a resume.

## 11. Out of scope for Phase 0

Browser automation of any kind; the answer bank; edit-in-place with PDF re-render; Lever and Ashby;
Workday; response-rate analytics; the Message Batches API; Opus premium routing; Supabase hosting.

## 12. Known environment constraints

- **No Docker on the development machine.** Postgres 17 + pgvector installed via Homebrew.
  `infra/docker-compose.yml` is still checked in for portability but is not the local path.
- **No Redis.** Celery tasks are thin wrappers over directly-callable functions, so no broker is
  needed until the beat schedule lands at the end of the phase.
- **`pnpm` is not installed; `npm` is.** The `CLAUDE.md` commands block specifies pnpm and must be
  updated to match whatever is actually used.
- WeasyPrint's system dependencies (cairo, pango, gdk-pixbuf, glib, libffi) are already present.

Verified during setup: Postgres 17.10 running via `brew services`, database `jobpilot` created,
`vector` extension 0.8.5 enabled, cosine distance operator confirmed working.

## 13. Awaiting user input

These do not block implementation but leave the phase incomplete until supplied:

1. **Response-rate baseline** — the user's current manual reply rate (§2.1 Q1).
2. **Base resume PDF** — required by `ingest-resume` before any real `canonical_facts` exists.
   Fixture mode covers the pipeline until then.
3. **API keys** — `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`, `ADZUNA_APP_ID` / `ADZUNA_APP_KEY`.
   Fixture mode runs without them.

