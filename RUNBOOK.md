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
| **Needs attention** | Tailoring could not pass the fact-check after 3 tries |
| **Rejected** | You said no |
| **All** | Everything |

**Nothing is ever deleted.** Reject moves a card to the Rejected tab; open it and
press **Restore to queue** to bring it back. Cards in *Needs attention* can be
restored the same way — they stay for your inspection with the exact fact-check
failures listed.

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
| `JOBPILOT_MAX_YEARS_REQUIRED` | `[5]` | Roles asking up to this many years still count as real opportunities. |
| `JOBPILOT_MATCH_SCORE_THRESHOLD` | `[70]` | Minimum score to be tailored. |
| `JOBPILOT_MAX_TAILORED_PER_DAY` | `[12]` | Daily cap — the volume dial. |
| `JOBPILOT_EMBED_TOP_K` | `[40]` | How many jobs the LLM scores per run. |

Want more results? Raise `MAX_POSTING_AGE_DAYS` to 60 and `EMBED_TOP_K` to 60.
Want stricter? Raise `MATCH_SCORE_THRESHOLD` to 80.

### On experience years — important

`JOBPILOT_MAX_YEARS_REQUIRED=5` controls **which jobs you are shown**. It does
*not* change what your resume claims.

`experience_years` in `profile/canonical_facts.json` is `2.0`, and that is the
honesty ceiling: the whitelist gate rejects any tailored resume claiming more.
Raising it to 5 would let the tailoring engine write "5 years of experience" on
your resume, which would be a lie. These are deliberately two separate settings —
you now see 3–5 year roles, and you apply to them truthfully as a 2-year engineer.

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
