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

1. Copy `.env.example` to `.env` and fill in `ANTHROPIC_API_KEY`, `VOYAGE_API_KEY`,
   and the Adzuna pair. Set `JOBPILOT_FIXTURE_MODE=0`.
2. Replace the demo entry in `infra/seed_companies.yaml` with real Greenhouse
   board tokens, then re-run `seed-companies`.
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

No browser automation, no answer bank, no edit-in-place, no Lever or Ashby, no
response-rate analytics. Submission is manual — you click apply, then press
"I applied" so the outcome is recorded for the baseline.
