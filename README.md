# JobPilot — Phase 0

A personal, semi-automated job-application tool. Phase 0 proves the loop end to
end **except** automated form-fill: it discovers roles, scores them, tailors your
resume behind a hard anti-hallucination gate, renders an ATS-parseable PDF, and
gives you a review queue. You apply manually with the generated PDF.

See `PRD.md` for requirements and `CLAUDE.md` for the operating contract.

## Run it now, with no API keys

Fixture mode wires recorded API payloads and a deterministic local LLM into the
*real* pipeline — every stage, the whitelist gate, the PDF renderer, the database
writes, and the dashboard all run exactly as they do live.

```bash
brew install postgresql@17 pgvector && brew services start postgresql@17
createdb jobpilot
export JOBPILOT_FIXTURE_MODE=1

uv sync
uv run alembic upgrade head
uv run jobpilot seed-companies infra/seed_companies.yaml

# Load the sample facts object so the pipeline has a whitelist to check against.
uv run python -c "from jobpilot_worker.fixtures import SAMPLE_FACTS; import pathlib; \
pathlib.Path('profile').mkdir(exist_ok=True); \
pathlib.Path('profile/canonical_facts.json').write_text(SAMPLE_FACTS.model_dump_json(indent=2))"
uv run jobpilot confirm-facts

uv run jobpilot run-pipeline
uv run jobpilot queue
```

Then start the API and dashboard in two terminals:

```bash
uv run uvicorn jobpilot_api.main:app --reload
```

```bash
cd apps/web && npm install && npm run dev
```

Open <http://localhost:3000/queue>.

## Going live

1. Copy `.env.example` to `.env` and fill in `NVIDIA_API_KEY` and the Adzuna pair.
   Set `JOBPILOT_FIXTURE_MODE=0`. Embeddings run on the same NVIDIA key, so no
   second provider is needed.
2. `infra/seed_companies.yaml` already carries 75 verified boards. Add your own
   targets and re-run `seed-companies` (idempotent).
3. Ingest your actual resume:

```bash
uv run jobpilot ingest-resume ~/path/to/resume.pdf
```

That writes `profile/canonical_facts.json` (gitignored — it holds your PII).
**Read it carefully and correct anything wrong**, especially skills, dates, and
`experience_years`: this object is the immutable whitelist every tailored resume
is checked against. Then:

```bash
uv run jobpilot confirm-facts
uv run jobpilot run-pipeline
```

## Where jobs come from

All documented, public JSON APIs. Nothing here is scraped.

| Source | Type | Auth |
|---|---|---|
| Greenhouse, Lever, Ashby, Workable, SmartRecruiters | Per-company ATS boards | none |
| Remotive, Arbeitnow, RemoteOK | Global/remote boards | none |
| Adzuna | Aggregator (India + worldwide) | free API key |

`infra/seed_companies.yaml` ships **75 verified board tokens** (~11k live postings).
Every token was probed against its provider before being written to the file — none
are guessed. Tokens go stale as companies switch ATS vendors; a dead one is logged
and skipped, never fatal.

### Naukri and Cutshort

Not supported, deliberately. Neither publishes a third-party job-search API, so
pulling listings from them would mean scraping their web UI — which
`CLAUDE.md` non-negotiable #1 forbids, and which risks your account.

If you want their coverage, the compliant options are:

1. **Set up job alerts** on Naukri/Cutshort and apply to those manually. The
   tailoring engine still helps: paste the JD and run tailoring against it.
2. **Add the company's own ATS board** to `seed_companies.yaml`. Most Indian
   startups posting on Cutshort also run Greenhouse, Lever, or Ashby — that is
   the same job, from a documented source.

## The whitelist gate

The one thing worth understanding before trusting the output. `check()` in
`packages/shared/jobpilot_shared/whitelist.py` is a pure function — no database,
no network, no model — that validates a tailoring output against your confirmed
facts:

| Rule | What it catches | Severity |
|---|---|---|
| `unknown_skill` | A declared skill that is not in your facts | reject + re-run |
| `invalid_employment_index` | A bullet attached to a role that does not exist | reject + re-run |
| `yoe_inflation` | Any years-of-experience claim above yours | reject + re-run |
| `unknown_employer` | An organisation you never worked for | reject + re-run |
| `unlisted_token` | A known technology in prose that you did not declare | flag for review |

A rejected attempt is re-prompted with the violations as explicit constraints, up
to three times. After that the run is marked `needs_human` and **cannot** be
approved, rendered, or downloaded.

Two independent layers enforce that: the API refuses to approve or serve a PDF
for a failed run, and `render.py` re-runs the gate itself rather than trusting a
flag. Employer, title, and dates always come from your facts at render time, not
from the model.

## Commands

```bash
uv run jobpilot version           # config and active dials
uv run jobpilot seed-companies    # load the Greenhouse board registry
uv run jobpilot ingest-resume     # resume PDF -> profile/canonical_facts.json
uv run jobpilot confirm-facts     # validate and load into Postgres
uv run jobpilot discover          # discovery + dedupe only
uv run jobpilot run-pipeline      # the whole loop
uv run jobpilot queue             # print the review queue
```

Quality gates:

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
cd apps/web && npm run typecheck && npm run test && npm run build
```

Database tests need Postgres; they skip cleanly if it is unreachable.

## What Phase 0 deliberately does not do

No browser automation, no answer bank, no edit-in-place, no response-rate
analytics. Submission is manual — you click apply, then press "I applied" so the
outcome is recorded for the baseline.


## Models

Defaults were chosen by benchmarking the configured key, not by reputation:

| Role | Model | Why |
|---|---|---|
| Scoring | `nvidia/nemotron-3-super-120b-a12b` | strict JSON in ~3s, correctly calibrated |
| Tailoring | `openai/gpt-oss-120b` | strongest rewriting of the fast options |
| Embeddings | `nvidia/nv-embedqa-e5-v5` | 1024-dim, same width as voyage-3 |

`z-ai/glm-5.2` and `deepseek-ai/deepseek-v4-pro` are listed by NVIDIA but never
returned a token on this account (60s and 240s timeouts, while an 8B model on the
same key answered in 0.6s). Set `JOBPILOT_TAILORING_MODEL` / `JOBPILOT_SCORING_MODEL`
in `.env` if that changes — the client is model-agnostic and carries a fallback chain.

**Scores are band-derived.** Models proved unreliable at emitting a raw 0-100
integer — on an identical prompt one returned `[92, 88, 0, 90]` and another
`[9, 8, 88, 8]`, while their categorical judgments were correct every time. The
model picks a `fit_band`, and the number the pipeline thresholds on is derived
from that band in code.
