# Running JobPilot on your machine

Everything below assumes macOS with Homebrew. Run from the repo root.

---

## One-time setup

### 1. Postgres + pgvector

```bash
brew install postgresql@17 pgvector
brew services start postgresql@17
echo 'export PATH="/opt/homebrew/opt/postgresql@17/bin:$PATH"' >> ~/.zshrc
source ~/.zshrc
createdb jobpilot
```

### 2. Python dependencies

```bash
uv sync
```

If `uv` is missing: `brew install uv`.

### 3. Node dependencies (for the dashboard)

```bash
cd apps/web && npm install && cd ../..
```

### 4. Credentials

`.env` already exists with your keys and is gitignored. Confirm it looks right:

```bash
uv run jobpilot version
```

You should see `fixture mode: False` and the NVIDIA model names. If anything is
blank, edit `.env`.

> **Rotate the NVIDIA key.** It was pasted into a chat transcript. It is the only
> credential doing LLM *and* embedding work, so it is worth replacing.

### 5. Database schema

```bash
uv run alembic upgrade head
```

### 6. Load the company registry and your resume facts

```bash
uv run jobpilot seed-companies infra/seed_companies.yaml
uv run jobpilot confirm-facts
```

`profile/canonical_facts.json` is already filled in from your resume. Read it once
before confirming — it is the whitelist every tailored resume is checked against,
and anything wrong in it becomes permission to state that wrong thing.

---

## The daily loop

```bash
uv run jobpilot run-pipeline
```

This discovers, dedupes, embeds, scores, tailors, and renders PDFs. First run on a
fresh database takes ~10 minutes (mostly embedding); later runs are much faster
because only new postings are embedded.

Then start the two services, each in its own terminal:

```bash
uv run uvicorn jobpilot_api.main:app --reload
```

```bash
cd apps/web && npm run dev
```

Open **<http://localhost:3000/queue>**.

---

## Using the dashboard

Tabs across the top, with live counts:

| Tab | What is in it |
|---|---|
| **To review** | Tailored and fact-checked, waiting on you |
| **Approved** | You said yes; go apply |
| **Applied** | You applied — this is what the response-rate baseline is built from |
| **Shortlist** | Scored and kept, but below the tailoring cut. Open one and press **Tailor this** to generate a resume for it on demand |
| **Needs attention** | Tailoring could not pass the fact-check after 3 tries |
| **Rejected** | You said no |
| **All** | Everything in India + remote |
| **Overseas** | Roles outside India and open-remote, across every status. Kept and scored, but they do not spend the daily tailoring budget — open one and press **Tailor this** to pursue it |

Every tab except **Overseas** is filtered to India and remote roles. On the right
of the tab bar, **Skills to learn →** opens the study list (below).

**Nothing is ever deleted.** Every job that gets scored produces a card — the top
ones are tailored automatically, the rest land in **Shortlist**. Reject moves a
card to the Rejected tab; open it and press **Restore to queue** to bring it back.
Cards in *Needs attention* restore the same way, and keep the exact fact-check
failures listed for your inspection.

Every transition is written to the `events` table, so the history of what you
rejected and restored is auditable.

On a card you get the word-level diff (original bullet → rewritten), the score
rationale, any flagged technologies, the tailored PDF, and a link to the real
application form. Phase 0 apply is manual: click **Open application**, apply,
then press **I applied**.

---

## The dials

Set these in `.env`. Defaults in brackets.

| Setting | [default] | What it does |
|---|---|---|
| `JOBPILOT_MAX_POSTING_AGE_DAYS` | `[30]` | Only postings this fresh reach the queue. `0` disables. |
| `JOBPILOT_MAX_YEARS_REQUIRED` | `[8]` | Above this many years the role is dropped. Staff/principal/director titles are dropped regardless. |
| `JOBPILOT_MATCH_SCORE_THRESHOLD` | `[70]` | Advisory only now — selection no longer drops anything on score. |
| `JOBPILOT_MAX_TAILORED_PER_DAY` | `[12]` | Daily cap — the volume dial. |
| `JOBPILOT_EMBED_TOP_K` | `[40]` | How many jobs the LLM scores per run. |
| `JOBPILOT_TAILOR_OVERSEAS` | `[false]` | Let overseas roles spend the daily tailoring budget too. |
| `JOBPILOT_AGGREGATOR_QUERIES` | *(9 terms)* | Comma-separated Adzuna searches. Includes forward deployed engineer, AI engineer, LLM engineer, applied AI. Each runs against India and against remote. |

Want more results? Raise `MAX_POSTING_AGE_DAYS` to 60 and `EMBED_TOP_K` to 60.

### What actually drops a job

Only two things, both of which you asked for:

1. **Seniority** — more than `MAX_YEARS_REQUIRED` years stated, or a
   staff / principal / distinguished / architect / director / VP / manager title.
2. **Location** — overseas roles do not spend the daily budget. They are not
   dropped; they sit in the **Overseas** tab.

**A skills gap never drops a job.** That was the change you asked for. A role
asking for something you have not used is ranked lower, not deleted — tailoring
re-emphasises what you *do* have, and the gap itself is recorded in the study
list. Everything scored but not tailored today is in **Shortlist**.

### Skills to learn

**<http://localhost:3000/skills>** — every term a job description wanted that
your resume does not cover, ranked by how many jobs asked for it, with the
companies that asked. Filter by 1+/2+/3+/5+ jobs.

This never touches your resume. The fact-check still rejects any skill that is
not genuinely yours; this page is the reading list, so you can decide what is
worth a weekend.

### On experience years — important

`JOBPILOT_MAX_YEARS_REQUIRED=8` controls **which jobs you are shown**. It does
*not* change what your resume claims.

`experience_years` in `profile/canonical_facts.json` is `2.0`, and that is the
honesty ceiling: the whitelist gate rejects any tailored resume claiming more.
Raising it to 8 would let the tailoring engine write "8 years of experience" on
your resume, which would be a lie. These are deliberately two separate settings —
you now see roles up to 8 years, and you apply to them truthfully as a 2-year
engineer. The same principle governs skills: you now *see* jobs asking for things
you have not used, and the resume still only ever claims what you have done.

---

## If a run fails

Nothing in a run is all-or-nothing any more. Stages commit as they finish, and a
single bad row — a provider timeout, a malformed listing — is recorded and
skipped. The summary line reports it:

```
boards=97 (failed 0) jobs+18 deduped=0 unstorable=0 embedded=18 scored=40 ...
```

`unstorable` counts listings a provider sent that could not be stored;
`tailor_failed` counts jobs whose LLM call did not come back. Both are logged
with the reason. A non-zero number is not a failed run — it is a handful of rows
out of thousands.

If you saw `value too long for type character varying(128)` before, that was
`jobs.external_id` — fixed by a migration, so run `uv run alembic upgrade head`.

---

## Adding more companies

```yaml
# infra/seed_companies.yaml
  - name: Some Company
    board_token: somecompany
    ats_provider: greenhouse   # or lever, ashby, workable, smartrecruiters
```

Find the token in the careers-page URL, e.g.
`boards.greenhouse.io/**stripe**` → `stripe`. Then:

```bash
uv run jobpilot seed-companies infra/seed_companies.yaml
```

Idempotent — safe to re-run. Verify a token before adding it:

```bash
curl -s "https://boards-api.greenhouse.io/v1/boards/TOKEN/jobs" | head -c 200
```

---

## Individual stages

```bash
uv run jobpilot version           # config + active dials
uv run jobpilot discover          # discovery + dedupe only
uv run jobpilot queue             # print the queue in the terminal
uv run jobpilot run-pipeline      # everything
```

---

## Checks

```bash
uv run ruff check . && uv run ruff format --check .
uv run pytest
cd apps/web && npm run typecheck && npm run test && npm run build
```

---

## Troubleshooting

**Dashboard says "Could not reach the API"** — the FastAPI server is not running.
Start it in another terminal.

**`connection refused` on Postgres** — `brew services start postgresql@17`.

**Queue is empty after a run** — check the freshness filter first:

```bash
psql -d jobpilot -tAc "SELECT count(*) FROM jobs WHERE posted_at >= now() - interval '30 days';"
```

If that is 0, either discovery has not run or every posting is older than the
window. Raise `JOBPILOT_MAX_POSTING_AGE_DAYS`.

**WeasyPrint import error** — needs the Homebrew graphics libraries:

```bash
brew install cairo pango gdk-pixbuf libffi
```

**Everything scores low** — check `profile/canonical_facts.json` actually reflects
your resume. Scoring compares the job against that object, not against the PDF.

**Try it without touching any API** — `JOBPILOT_FIXTURE_MODE=1` runs the entire
pipeline against recorded payloads with a deterministic local model. Useful for
checking the plumbing after a change.
