# CLAUDE.md — JobPilot (Job-Hunt Assist Platform)

> This file is auto-loaded every session. It is the operating contract for building this project.
> **Source of truth for requirements:** `PRD.md`. When this file and the PRD disagree, the PRD wins — flag the conflict.
> **Keep this file accurate.** When structure, commands, or status change, update the relevant section in the same PR.

---

## What we're building

A personal, semi-automated job-application tool for one user (a ~1.5-YOE software engineer targeting SWE roles in India + Remote). It discovers relevant roles, uses an LLM to tailor a base resume per role, lets the user review/approve, and then **assists** the user in submitting — pre-filling forms so the human clicks the final submit.

**Objective we optimize for:** cut human time per *quality* application to **< 2 minutes**, and improve **recruiter response rate**. We do **not** optimize raw submission volume.

---

## 🚫 Non-negotiables (do not violate; if a task seems to require it, STOP and ask)

1. **No scraping or bot-detection evasion.** Job discovery uses only documented, public ATS board APIs (Greenhouse, Lever, Ashby) plus an aggregator API (Adzuna / SerpAPI) for discovery. No residential proxies, no browser-fingerprint spoofing, no CAPTCHA solving, no evading anti-bot controls. If a site presents a CAPTCHA or bot check, the correct behavior is **hand off to the human** — never bypass.
2. **Never fabricate resume content.** The user's `canonical_facts` object is immutable. The tailoring engine may rephrase/reorder/re-emphasize existing content only. Before any tailored resume is shown or used, the whitelist check MUST pass:
   - every skill referenced ⊆ `canonical_facts.skills`
   - `experience_years`, employers, titles, and dates are byte-identical to `canonical_facts`
   - any technology token in rewritten text not in the whitelist → reject + re-run (or flag for human).
   This check is a hard gate, not a warning. Build it early and test it hard.
3. **Submission is human-present.** The apply layer pre-fills fields and attaches the tailored PDF in the user's own logged-in browser session. **The human clicks submit.** No fully-headless auto-submit at scale.
4. **Volume is a bounded, paced dial** — default low (~10–15/day), configurable, with pacing between submissions. Never build toward maximizing throughput.
5. **Secrets never hit the repo.** API keys, tokens, and the user's PII live in env vars / `.env` (gitignored) and Supabase. No secrets in code, logs, tests, or fixtures.

---

## Architecture (see `PRD.md` §2–3 for full detail)

- **Discovery + tailoring are fully automated and headless** — pure HTTP + LLM, no browser.
- **Only final submission touches a browser** — and it runs in the user's session, human-present.
- This split is deliberate: it removes both the bot-detection problem and the "50 concurrent browsers crash the server" problem. Preserve it.

## Tech stack

| Layer | Tool |
|---|---|
| Frontend | Next.js + Tailwind |
| Backend API | FastAPI (Python) |
| DB / Auth / Storage | Supabase (Postgres) + **pgvector** |
| Async workers | Celery + Redis |
| Resume parsing | `pdfplumber` (or `unstructured`) |
| LLM | Claude API — **Sonnet 5** for bulk tailoring via **Message Batches API**, **Opus 4.8** for premium/top roles; enforce structure via tool-use / strict JSON |
| PDF generation | WeasyPrint (HTML/CSS → selectable-text, ATS-parseable PDF) |
| Apply layer | Browser extension (preferred) or headed Playwright |

Verify current Claude model IDs/features at https://docs.claude.com/en/api/overview before hardcoding.

---

## Repo layout (target — create as you go, keep this map current)

```
PRD.md              # requirements, source of truth
/apps/web                 # Next.js dashboard (review queue, diff view, approve/edit/reject)
/services/api             # FastAPI: parsing, matching, tailoring, PDF, REST/WS
/services/worker          # Celery tasks: discovery cron, scoring, tailoring, PDF gen
/packages/extension       # assisted-apply browser extension + field-mapping registry
/packages/shared          # shared schemas/types (canonical_facts, tailoring output)
/infra                    # docker-compose (postgres+pgvector, redis), env templates
/tests                    # see testing discipline below
```

## Commands (target — implement and keep accurate)

```bash
# Backend
uv run uvicorn services.api.main:app --reload    # run API
uv run celery -A services.worker worker -l info  # run worker
uv run celery -A services.worker beat -l info    # run scheduler (discovery cron)

# Frontend
pnpm --filter web dev

# Quality gates (must pass before "done")
uv run ruff check . && uv run ruff format --check .
uv run pytest -q
pnpm --filter web lint && pnpm --filter web typecheck
pnpm --filter web test

# Infra
docker compose -f infra/docker-compose.yml up -d   # postgres+pgvector, redis
```

> When you add or change a command, update this block in the same PR.

---

## How we work (superpowers workflow is installed — use it)

- For any non-trivial unit: `/superpowers:brainstorm` → `/superpowers:write-plan` → `/superpowers:execute-plan`.
- **Plan before code. Tests before implementation.** Do not jump straight to writing code.
- **Work the current phase only** (see roadmap). Do not build ahead. If you spot future-phase work, note it in the ledger and move on.
- Keep PRs small and single-purpose. One capability at a time.
- Resolve the relevant open question (below) before building anything it blocks — propose a default rather than waiting silently.

## Definition of Done (per unit of work)

- [ ] Tests written first and passing (`pytest` / frontend tests as applicable)
- [ ] Lint + typecheck clean
- [ ] Any relevant non-negotiable enforced *and covered by a test* (esp. the whitelist gate and the CAPTCHA/unknown-field handoff)
- [ ] No secrets added; env template updated if new config introduced
- [ ] Commands/repo-map/status in this file updated if they changed
- [ ] Progress ledger updated (below)

---

## Roadmap & acceptance criteria

### Phase 0 — Prove the loop (one ATS, manual apply)
Build the full pipeline end-to-end except automated form-fill; user applies manually using the generated PDF to validate quality first.
**Acceptance:**
- Ingest base resume → produce a confirmed `canonical_facts` object.
- Discovery from **Greenhouse board API only**, upserted + deduped in Postgres.
- Embedding pre-filter (pgvector) + LLM scoring with structured JSON output.
- Tailoring + **whitelist gate passing in tests** + WeasyPrint PDF with selectable text.
- Dashboard: queue with diff view + approve/reject.
- No browser automation yet.

### Phase 1 — Assisted apply
**Acceptance:**
- Browser extension autofills Greenhouse forms from `canonical_facts` and attaches the tailored PDF; **human clicks submit**.
- Answer bank for recurring custom questions; unmatched → flagged, not auto-answered.
- CAPTCHA / unknown-required-field → human handoff (tested).
- Add Lever + Ashby discovery and field mappings.
- Status reporting (`submitted` / `needs_manual` / `failed`) back to dashboard.

### Phase 2 — Scale & polish
**Acceptance:**
- Aggregator-driven company discovery auto-grows the `companies` registry.
- Response-rate analytics (which tailoring/keywords correlate with replies).
- Pacing controls; edit-in-place with PDF re-render.
- (Optional, high-effort) Workday support — only if justified by data.

---

## Open questions (resolve within the phase that needs them; propose defaults)

1. Response-rate baseline (user's current manual reply rate) — needed to prove value.
2. Answer-bank scope: which recurring questions to pre-write (CTC, notice period, visa, "why us").
3. Salary data: most ATS APIs omit it — accept sparse, or enrich from aggregator?
4. Apply layer: browser extension vs. headed Playwright for Phase 1.
5. Default volume dial (start ~10–15/day, tune on response rate).

---

## Progress ledger (living — update at the end of every session)

**Current phase:** Phase 0
**Now working on:** _project scaffolding / infra not yet started_
**Next action:** stand up repo layout + `docker-compose` (postgres+pgvector, redis), then `canonical_facts` schema and ingestion.
**Blockers:** none

### Done
- [ ] _nothing yet_

### In progress
- [ ] _nothing yet_

> Update the three status lines above and check off items as you complete them. Note decisions and blockers here so the next session (and claude-mem) has continuity.
