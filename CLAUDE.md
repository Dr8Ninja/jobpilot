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
| LLM | NVIDIA NIM (OpenAI-compatible): `openai/gpt-oss-120b` tailoring, `nvidia/nemotron-3-super-120b-a12b` scoring. Structure via `response_format` json_schema strict. Anthropic client retained behind the same Protocol. |
| Embeddings | `nvidia/nv-embedqa-e5-v5` (1024-dim, 512-token cap) on the same key |
| PDF generation | WeasyPrint (HTML/CSS → selectable-text, ATS-parseable PDF) |
| Apply layer | Browser extension (preferred) or headed Playwright |

Verify current Claude model IDs/features at https://docs.claude.com/en/api/overview before hardcoding.

---

## Repo layout (target — create as you go, keep this map current)

```
PRD.md                    # requirements, source of truth
README.md                 # setup + how to run it
/apps/web                 # Next.js dashboard (queue, diff view, approve/reject)
/services/api             # FastAPI queue endpoints + Typer CLI
/services/worker          # pipeline stages, clients, prompts, fixtures, templates
/packages/extension       # assisted-apply browser extension — Phase 1, not created yet
/packages/shared          # canonical_facts, tailoring/scoring IO, the whitelist gate, db models
/migrations               # Alembic
/infra                    # seed_companies.yaml; docker-compose is Phase 1+
/tests                    # shared/ worker/ api/ + fixtures
```

## Commands (target — implement and keep accurate)

```bash
# Pipeline (each stage is individually invocable; run-pipeline composes them)
uv run jobpilot version | seed-companies | ingest-resume | confirm-facts
uv run jobpilot discover | run-pipeline | queue

# Services
uv run uvicorn jobpilot_api.main:app --reload     # API on :8000
cd apps/web && npm run dev                        # dashboard on :3000

# Quality gates (must pass before "done")
uv run ruff check . && uv run ruff format --check .
uv run pytest
cd apps/web && npm run typecheck && npm run test && npm run build

# Infra — local Postgres, not Docker (no Docker on the dev machine)
brew services start postgresql@17
uv run alembic upgrade head
```

> `npm`, not `pnpm` — pnpm is not installed on the dev machine.
> Celery + Redis are deferred to the end of Phase 0; stages are plain functions,
> so no broker is needed to run or test the pipeline.

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

### Phase 0 — Prove the loop (manual apply)
Build the full pipeline end-to-end except automated form-fill; user applies manually using the generated PDF to validate quality first.

> **Amended 2026-07-26 (user direction):** aggregator discovery moved *out of
> Phase 2 and into Phase 0*, serving both as company-registry growth and as a
> direct job source. Cross-source dedupe is certainty-only — an aggregator row is
> dropped solely on a matching Greenhouse `(board_token, ats_job_id)`, never on a
> fuzzy title match. See `docs/superpowers/specs/2026-07-25-phase-0-design.md` §2.3.

**Acceptance:**
- Ingest base resume → produce a confirmed `canonical_facts` object.
- Discovery from Greenhouse, Lever, Ashby, Workable, SmartRecruiters, three keyless
  remote boards (Remotive/Arbeitnow/RemoteOK), **and Adzuna**, upserted + deduped.
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
- ~~Aggregator-driven company discovery auto-grows the `companies` registry.~~ *(moved to Phase 0)*
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
**Now working on:** Phase 0 runs live against the real resume and 94 verified boards (13,265 jobs). Selection now drops only on seniority and location; a skills gap never drops a job. Remaining Phase 0 item: Celery beat schedule.
**Next action:** `docs/ROADMAP.md` holds the gap audit and five phased master
prompts (A backend foundation → E intelligence). Start with Phase A.
**Blockers:** response-rate baseline figure still not supplied. GLM-5.2/DeepSeek V4 Pro unavailable on the provided key.

### Done
- [x] Phase 0 design spec + implementation plan (`docs/superpowers/specs/`, `docs/superpowers/plans/`)
- [x] `canonical_facts` schemas — frozen, strict, JSON round-tripping
- [x] **Whitelist gate** — pure function in `packages/shared`, 4 rules, 39 adversarial tests
- [x] Data model + Alembic migration; certainty dedupe enforced by a partial unique index
- [x] Greenhouse board discovery with per-board failure isolation
- [x] Adzuna discovery + redirect resolution + certainty-only cross-source dedupe
- [x] Voyage embeddings + pgvector pre-filter
- [x] LLM scoring (strict JSON via `output_config.format`, no sampling params)
- [x] Tailoring with gate retry loop; `needs_human` after the attempt budget
- [x] WeasyPrint PDF + round-trip test extracting the artefact with pdfplumber
- [x] FastAPI queue endpoints; approval blocked on `whitelist_passed`
- [x] Next.js review dashboard: queue, word-level diff, approve/reject/mark-applied
- [x] Fixture mode — full pipeline with zero API keys
- [x] CLI: seed-companies, ingest-resume, confirm-facts, discover, run-pipeline, queue
- [x] Resume template reproduces the user's own PDF — same sections, same skill
      groups, one page. Only bullet wording and within-group ordering change.
- [x] Seniority rule: reject 8+ years and staff/principal/director titles only
      (`packages/shared/jobpilot_shared/seniority.py`)
- [x] Location classification + Overseas tab; India and remote get the budget
      (`packages/shared/jobpilot_shared/location.py`)
- [x] Skills-to-learn report — `/api/skill-gaps`, dashboard at `/skills`
- [x] Forward-deployed / AI aggregator queries; 19 more verified AI-forward boards

### In progress
- [ ] Celery beat schedule wrapping `run_pipeline` (the last Phase 0 item)

### Decisions worth remembering
- **The document's shape is the candidate's, not the model's.** Every live
  tailoring returned fewer bullets than the resume has — one gave 1 bullet for a
  5-bullet role — and the renderer published the truncation. `bullets_for_render`
  now walks the *canonical* bullets and pulls in a rewrite where one exists,
  falling back to the original wording otherwise, so the count and order can
  never depend on the model. Surplus bullets are dropped too: the resume must not
  grow either.
- **Token budgets are not character budgets.** 62 postings were silently dropped
  every run because `nv-embedqa-e5-v5` counts tokens and the code counted
  characters. English runs ~4 chars/token; Japanese and Korean run ~2.5 *tokens
  per character*, so a 1600-char budget was ~400 tokens of English and ~1600 of
  Japanese, and every CJK posting was rejected with a 400. The weights in
  `estimated_tokens` were calibrated against the provider's own rejection
  messages, which report the true count. Trim by estimated tokens, then shrink on
  failure — a single halving did not converge.
- **"Invalid payload" usually is not the schema.** Nemotron logged repeated
  `ScoreVerdict` validation failures; the real cause was `finish_reason=length`.
  On roughly one input in five it stops mid-object and pads with whitespace to
  whatever ceiling it is given — 6,713 characters of padding in one capture,
  burning all 8,000 tokens and 58s to return an unparseable body. The ceiling is
  what a runaway costs, not what a good answer needs (real ones use 848-2,297),
  so `scoring_max_tokens` is 3,000 and truncation is now a named, retried error.
- **Benchmark the account, not the leaderboard.** Re-measuring all 102 served
  models against the *real* schemas moved tailoring from `openai/gpt-oss-120b`
  (106-180s, frequent timeouts) to `nvidia/nemotron-3-super-120b-a12b` (20-79s,
  8/8 bullets every run) and made scoring reliable at 8-15s. The earlier note
  that nemotron "returns an empty tailoring" was true only of the old prompt;
  once the prompt stated a per-role bullet budget the same model answered fully.
  Re-test a model after changing its prompt before writing it off.
- **A fallback is only a fallback if the next model does the work.** Measured on
  this account: `nvidia/nemotron-3-super-120b-a12b` (2.6s) and
  `openai/gpt-oss-20b` (16s) both answer a *tailoring* request with a
  schema-valid but empty `tailored_bullets` list, while `openai/gpt-oss-120b`
  (54s idle, ~106s live) is the only one that does the job. Falling back to an
  empty answer is worse than a timeout — it passes the gate and burns the
  attempt. Tailoring therefore has its own empty fallback chain and retries the
  primary; scoring keeps the shared chain, where the same models return correct
  categorical verdicts. `llm_timeout_seconds` is 180: 90 cut off the one model
  that works.
- **An empty tailoring is not a passing tailoring.** A fallback model returned
  zero bullets, the gate had nothing to reject, and the result was a
  perfectly-shaped *untailored* PDF that looked like success. Completeness is now
  checked next to the gate and retried through the same loop, and the most
  complete attempt is kept rather than the last — a later attempt can come back
  emptier than an earlier one.
- **A provider can always surprise the schema; draw the boundary at one row.**
  Three separate runs died to a single bad row: an LLM timeout during tailoring,
  a transport error escaping the scoring `except`, and a 133-char Arbeitnow slug
  overflowing `jobs.external_id(128)`. Discovery, scoring and tailoring now each
  absorb per-row failures, stages commit as they finish, and `ingest_one` rolls
  back before continuing — Postgres aborts the whole transaction on a failed
  statement, so without the rollback every later insert fails too.
- **Never truncate an identity column.** `external_id` is now `String(512)` with
  `bound_external_id` collapsing anything longer to a prefix plus a digest of the
  original. Plain truncation would silently merge two distinct postings that
  share a long prefix; the digest keeps them apart and keeps dedupe stable
  across runs.
- **A skills gap never drops a job** (user instruction, 2026-07-28). Selection
  filters on seniority and location only, then *ranks* by score — a weak band
  sinks, it does not disappear. The gaps feed the skills-to-learn report instead.
  What this does **not** mean: adding a missing skill to the resume. The user
  asked for that; it is non-negotiable #2 and was declined. Tailoring re-weights
  what is already true.
- **Location: unknown is overseas, never remote.** Enumerating countries, states
  and cities never converges — "Remote - Austin" and "Düsseldorf und Remote" both
  appeared live. The rule is inverted: a remote posting is *open* only if its
  location field contains nothing but neutral filler words. Mislabelling an open
  role costs one tab; mislabelling a US-only role puts an unreachable job at the
  top of the queue.
- **Provider is NVIDIA NIM, not Anthropic.** `z-ai/glm-5.2` and
  `deepseek-ai/deepseek-v4-pro` are *listed* by NVIDIA but never serve on this
  account (60s and 240s with no first token; an 8B model on the same key answers
  in 0.6s). Defaults were chosen by benchmarking. The client carries a fallback
  chain so an unserved model degrades instead of failing the run.
- **Never gate on a model's free integer.** Identical prompt, four runs:
  `[92, 88, 0, 90]` and `[9, 8, 88, 8]`. Categorical fields were correct every
  time. `ScoreVerdict` carries a `fit_band` enum and the pipeline thresholds on a
  band-derived `effective_score`. The model's `should_apply` is advisory only —
  it was observed inverted ("skip" on an 88).
- **No sampling parameters anywhere.** Rejected with a 400 on Claude Sonnet 5;
  omitted on NIM for consistency. Structure comes from the schema.
- **The gate takes an optional `target_company`.** Naming the company you are
  applying *to* is not an employment claim; without this every honest summary
  burned all three retries. Found by running the pipeline, not by review.
- **Embedding inputs are truncated to ~1600 chars.** `nv-embedqa-e5-v5` rejects
  anything over 512 tokens and rejects the *whole batch*, so a batch failure now
  falls back to per-item. Before this, 136 of 11,144 jobs embedded.
- **Naukri and Cutshort are out of scope permanently.** No third-party API
  exists, so they would require scraping (non-negotiable #1).
- **Tailwind v4**: `@theme` tokens generate utilities (`bg-accent`). The v3
  `bg-[--color-accent]` form silently emits nothing.
- Postgres is local via Homebrew, not Docker — Docker is not installed here.

> Update the three status lines above and check off items as you complete them.
