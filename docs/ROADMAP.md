# JobPilot — Gap Audit and Phase Roadmap

**Written:** 2026-08-20 · **Audited against:** `PRD.md`, `CLAUDE.md`, `README.md`,
`RUNBOOK.md`, `docs/superpowers/specs/2026-07-25-phase-0-design.md`, and the code as it
stands (17,870 jobs · 1,327 companies · 138 applications · 300 tests).

This document does two things: it lists what the written specs promise but the code does
not yet do, and it sequences the remaining work into phases small enough to finish in one
session each. Every phase ends with a **master prompt** — paste it into a fresh session and
it carries the full context, including the invariants that must not break.

---

## Part 1 — What is specified but not built

### 1.1 Phase 0 leftovers

| Gap | Where promised | Status |
|---|---|---|
| **Celery beat schedule** wrapping `run_pipeline` | `CLAUDE.md` ledger, "the last Phase 0 item" | Not started. Celery and Redis are not even dependencies in `pyproject.toml`. `run-pipeline` is a blocking CLI invocation. |
| **`answer_bank` table** | `PRD.md` §5 data model | Deliberately omitted (spec §183: "no consumer until Phase 1"). Correct call, still outstanding. |
| **README accuracy** | `CLAUDE.md` operating contract | Stale: claims 75 boards (now 94), names `openai/gpt-oss-120b` for tailoring (now `nemotron-3-super`), still references Voyage embeddings which were removed. |

### 1.2 Phase 1 — assisted apply (nothing built)

The entire phase. `packages/extension/` does not exist.

- Browser extension **or** headed Playwright — PRD open question #4 is still unanswered.
- Declarative field-mapping registry per ATS (`PRD.md` §4.6 sketches the JSON shape).
- Answer bank with semantic matching for recurring questions (CTC, notice period, visa, "why us").
- CAPTCHA and unknown-required-field handoff — **non-negotiable #1**, must be tested.
- Status callback writing `submitted` / `needs_manual` / `failed` back to the dashboard.
- Lever and Ashby field mappings. *(Discovery from both already works; only form-fill is missing.)*

### 1.3 Phase 2 — scale and polish

- **Response-rate analytics.** The PRD's *primary outcome metric* and it has never been
  measured. Open question #1 has been outstanding since day one.
- **Edit-in-place** with PDF re-render (spec §210 defers it explicitly).
- **Pacing controls.** Only a daily cap (`max_tailored_per_day`) exists; there is no
  inter-submission spacing, which is what PRD §4.6 actually asks for.
- **Workday support** — optional, high-effort, only if the data justifies it.

### 1.4 PRD items never scheduled into any phase

| Item | PRD reference | Note |
|---|---|---|
| WebSocket API | §2 architecture diagram says "REST / WebSocket API" | Only REST exists. Needed if the dashboard is to watch a pipeline run live. |
| Message Batches API for the nightly bulk pass | §4.4, §6 | Roughly half price for asynchronous bulk work. Provider is now NVIDIA NIM, so this needs re-evaluation rather than direct adoption. |
| Premium model routing for top-tier roles | §4.4 | "Cheap model for bulk, premium only for top roles" — the client already supports per-purpose routing, so this is a small change. |
| Supabase hosting + blob storage | §6 | PDFs are written to a local `storage/resumes/` directory and the path is stored in the database. Nothing is portable off this machine. |
| Auth and multi-user | §5 (`users` table) | The `users` table exists and is **never used**. Zero auth references in the API; `user_id` appears zero times. |
| Salary enrichment from the aggregator | Open question #3 | Still unanswered; salary remains sparse. |

### 1.5 Backend weaknesses — the "proper backend" ask

Verified in the code, not inferred:

1. **No authentication of any kind.** Anyone who can reach the port can approve applications
   and download your resume. Fine on `localhost`; unacceptable the moment it is deployed.
2. **No user scoping.** Every query is global. The `users` and `profiles` tables imply
   multi-user but nothing enforces it.
3. **Long work runs inside request handlers.** `POST /api/queue/{id}/tailor` calls
   `tailor_job` synchronously — up to 3 attempts × 180 s timeout. A browser will give up
   long before the server does, and the work is then invisible.
4. **No pagination.** `GET /api/queue` returns every matching row. 138 applications today;
   this degrades silently as it grows.
5. **N+1 queries.** `list_queue` calls `_latest_run` and `_latest_score` per card inside the
   loop — 2 extra round trips per row.
6. **PDFs served from local disk paths** held in the database. No object storage, no signed
   URLs, no portability.
7. **CORS origins hardcoded** to `localhost:3000`. No settings-driven configuration.
8. **No API versioning, no structured error model, no request logging, no rate limiting.**
9. **No CI.** The quality gates exist and pass, but nothing runs them automatically.
10. **No deployment story.** `infra/docker-compose.yml` is checked in but unused; there is no
    Dockerfile for the API or the worker.

---

## Part 2 — Enhancements worth building

Beyond fixing gaps. Ordered by value against the PRD's own success metrics.

### Closes the measurement loop (highest value)

- **Reply detection from email.** Read-only IMAP or Gmail API against the address used for
  applications, matched to companies in the queue, to detect recruiter replies automatically.
  This is what finally makes "recruiter response rate" a measured number instead of an
  aspiration — and it is the metric the entire PRD is built around.
- **Outcome tracking beyond `applied`.** `replied` / `screen` / `rejected` / `ghosted`, with
  timestamps, so funnel conversion is visible per company, per source, per score band.
- **A/B tailoring variants.** Two tailoring strategies, randomly assigned, compared on reply
  rate. Turns the tailoring prompt from a guess into something with evidence behind it.

### Cuts time per application (the < 2 min target)

- **Keyboard-driven review** — `j`/`k` to move, `a` to approve, `r` to reject, `o` to open.
  The single highest-leverage UI change for the primary efficiency metric.
- **Bulk approve** for a filtered set.
- **Company research brief** per card: what they do, recent funding, stack — generated once
  and cached, so the "why us" answer is not written from scratch each time.
- **Cover letter generation** behind the same whitelist gate as the resume.

### Improves what reaches the queue

- **Per-company throttling** so five roles at one employer do not consume half a day's budget.
- **JD change detection** — re-score when a posting's content hash changes.
- **Multiple profiles.** A backend-flavoured resume and an AI-flavoured one, each with its own
  canonical facts, selected per job. The architecture already supports this; only the profile
  lookup is hardcoded to "the single row".
- **Saved searches** with their own dials.
- **Salary enrichment** from the aggregator where the ATS omits it, plus a floor filter.

### Operational

- **Interview prep pack** — likely questions from the JD plus the skill gaps already tracked.
- **Follow-up reminders** — nudge after N days of silence.
- **Export** to CSV or Notion.
- **PWA / mobile review** so the morning queue can be cleared from a phone.

---

## Part 3 — Phases

Each phase is independently shippable and leaves the system working.

| Phase | Theme | Why this order |
|---|---|---|
| **A** | Backend foundation | Everything else needs async execution and a real API. Also the explicit ask. |
| **B** | Measurement loop | The PRD's primary metric is still unmeasured. Until it exists, no later change can be judged. |
| **C** | Assisted apply | PRD Phase 1. The largest remaining user-time win, and it depends on A's job runner. |
| **D** | Review speed and quality | Directly targets < 2 min per application, measurable once B exists. |
| **E** | Intelligence and scale | Optimisations that only pay off with B's data to steer them. |

---

## Phase A — Backend foundation

**Goal:** a backend that could be deployed without embarrassment, without changing any
existing behaviour.

Scope: async job runner (pipeline runs move off the request thread and off the blocking CLI);
`pipeline_runs` table plus endpoints to trigger and poll; Celery beat for the nightly schedule
— the last open Phase 0 item; pagination and N+1 fixes on the queue endpoints; settings-driven
CORS; structured error responses; request logging; single-user token auth with `user_id`
scoping wired through; `/api/v1` prefix with the old paths still working; Dockerfiles for API
and worker; GitHub Actions running the existing gates.

### Master prompt — Phase A

```
Read CLAUDE.md, PRD.md and docs/ROADMAP.md first. You are implementing Phase A
(Backend foundation) from the roadmap. Work only that phase.

Context: JobPilot is a working single-user job-application pipeline — 17k+ jobs from
97 boards, LLM scoring and tailoring behind a whitelist gate, a Next.js review
dashboard, 300 passing tests. The backend works but is shaped for localhost only.

Build, in this order, each with tests written first:

1. An async job runner. `run_pipeline` currently blocks a CLI invocation, and
   POST /api/queue/{id}/tailor blocks a request handler for up to 3x180s. Add
   Celery + Redis (both are in the PRD stack; Redis installs via Homebrew) and a
   `pipeline_runs` table recording id, kind, status, started_at, finished_at,
   summary jsonb, error. Expose POST /api/v1/runs to trigger and GET
   /api/v1/runs/{id} to poll. Keep the CLI working unchanged — it should enqueue
   or run inline behind a flag, so `uv run jobpilot run-pipeline` still works
   exactly as it does today.
2. A Celery beat schedule for the nightly run. This is the last open Phase 0 item
   in the CLAUDE.md ledger; check it off there when done.
3. Pagination (limit/offset, sensible cap) and the N+1 fix on GET /api/queue —
   `_latest_run` and `_latest_score` are called per card inside the loop; fold
   them into the main query. Response shape must not change.
4. Settings-driven CORS, a structured error model, request logging, and an
   /api/v1 prefix with the current unprefixed paths still routing (the dashboard
   must not break).
5. Single-user token auth: a bearer token from settings, `user_id` threaded
   through queries so the existing `users`/`profiles` tables stop being decorative.
   Auth must be disable-able for local use via settings, defaulting to off so
   nothing breaks today.
6. Dockerfiles for API and worker, and a GitHub Actions workflow running the
   existing gates. Note that the dev machine has no Docker — the files must be
   correct but are not expected to run locally.

Non-negotiables from CLAUDE.md still apply in full — especially #2, the whitelist
gate, and #4, volume as a bounded dial.

These invariants must not regress; assert them with tests:
- The whitelist gate still blocks approval, rendering and PDF download for any
  tailoring run with whitelist_passed=false, in both enforcement layers.
- A tailored resume still renders exactly the source resume's bullet count and
  order per role, on one page.
- Nothing is ever deleted: reject and restore round-trip, and every status stays
  reachable from the dashboard tabs.
- Discovery, scoring and tailoring still absorb per-row failures without aborting
  a run, and stages still commit as they finish.
- Location classification still routes India/remote to the main tabs and
  everything else to Overseas.

Finish with: `uv run ruff check . && uv run ruff format --check . && uv run pytest`
and `cd apps/web && npm run typecheck && npm run test && npm run build` all green,
CLAUDE.md's ledger and command block updated, and README.md corrected — it is
stale: it says 75 boards (94 now), names gpt-oss-120b for tailoring (nemotron-3-super
now), and still mentions Voyage embeddings, which were removed.

Commit with my git identity from `git config --global`. If you cannot, do not
commit — I will do it myself.
```

---

## Phase B — Measurement loop

**Goal:** make recruiter response rate a number, not a hope.

Scope: outcome statuses beyond `applied` (`replied`, `screen`, `rejected`, `ghosted`) with
timestamps and events; a baseline capture prompt so the pre-tool rate is recorded once;
`/api/v1/analytics` returning funnel conversion sliced by source, score band, location kind
and company; a dashboard analytics page; optional read-only email reply detection behind a
flag, matched to queued companies.

### Master prompt — Phase B

```
Read CLAUDE.md, PRD.md and docs/ROADMAP.md first. You are implementing Phase B
(Measurement loop). Work only that phase. Phase A is done.

The PRD names recruiter response rate as the primary outcome metric and it has
never been measured — open question #1 has been outstanding since day one. The
events table has recorded every status transition from the start, so the history
is already there to build on.

Build, tests first:

1. Outcome tracking past `applied`: replied / screen / rejected_by_company /
   ghosted, each with a timestamp and an events row. Extend the status CHECK
   constraint by migration. `applied` must keep meaning exactly what it means
   today so existing rows stay valid.
2. A one-time baseline capture — a CLI command and a dashboard prompt recording
   the user's pre-tool manual reply rate, so improvement can be stated as a
   comparison rather than an absolute.
3. GET /api/v1/analytics: applications over time, and conversion at each funnel
   step, sliced by source, score band, location_kind and company. Derive it from
   the events table so history before this phase is included.
4. A dashboard analytics page. Follow the existing visual language — the queue
   and skills pages set it.
5. Behind a settings flag, default off: read-only email reply detection. IMAP or
   Gmail API against the application address, matching sender domains to
   companies in the queue, proposing an outcome the user confirms. Never
   auto-confirm, never send mail, never read anything outside the matched
   threads. Credentials come from env, never the repo.

Invariants that must not regress; assert them with tests:
- Nothing is ever deleted; every existing status stays reachable and every
  transition stays reversible.
- The whitelist gate, the bullet-count guarantee and the per-row failure
  isolation all still hold.
- Existing queue endpoints keep their response shapes; the dashboard must not
  need changes to keep working.

Finish with all quality gates green and the CLAUDE.md ledger updated. Resolve
open question #1 in CLAUDE.md, or state precisely what is still missing.

Commit with my git identity from `git config --global`. If you cannot, do not
commit — I will do it myself.
```

---

## Phase C — Assisted apply

**Goal:** PRD Phase 1. Pre-fill the form; the human clicks submit.

Scope: resolve extension-vs-Playwright (open question #4) with a recommendation before
building; `packages/extension/`; declarative field-mapping registry for Greenhouse, Lever and
Ashby; `answer_bank` table with semantic matching; CAPTCHA and unknown-required-field handoff;
status callback; inter-submission pacing.

### Master prompt — Phase C

```
Read CLAUDE.md, PRD.md and docs/ROADMAP.md first. You are implementing Phase C
(Assisted apply) — PRD Phase 1. Work only that phase. Phases A and B are done.

Non-negotiable #3 governs this entire phase: submission is human-present. The
layer pre-fills fields and attaches the tailored PDF in my own logged-in browser
session, and I click submit. There is no headless auto-submit, at any volume.
Non-negotiable #1 also binds: a CAPTCHA or bot check means hand off to the human,
never solve, never evade.

Start by resolving PRD open question #4 — browser extension versus headed
Playwright. Give me a recommendation with reasoning and wait for my answer before
building the apply layer.

Then, tests first:

1. `packages/extension/` (or the Playwright equivalent) with a declarative
   field-mapping registry per ATS — the JSON shape in PRD 4.6 is the starting
   point. Greenhouse first, then Lever and Ashby. Discovery from all three
   already works; only form-fill is missing.
2. Attach the tailored PDF, which must come through the API's existing PDF
   endpoint so the whitelist gate stays on the path. A run that failed the gate
   must be impossible to attach.
3. An `answer_bank` table with semantic matching for recurring questions — CTC,
   notice period, visa, "why this company". PRD 4.1 and the spec deferred this
   here deliberately. An unmatched question is flagged and left blank, never
   auto-answered.
4. Handoff triggers, each with a test: unknown required field, and CAPTCHA or
   bot check detected. Both pause and flag in the UI.
5. Status callback writing submitted / needs_manual / failed back through the
   API, and inter-submission pacing — PRD 4.6 asks for spacing, and only a daily
   cap exists today.

Invariants that must not regress; assert them with tests:
- The whitelist gate still blocks approval, rendering and PDF download in both
  layers, and no un-gated PDF can reach a form.
- The bullet-count and one-page guarantees still hold.
- Nothing is ever deleted; reject and restore still round-trip.
- Per-row failure isolation still holds across discovery, scoring and tailoring.
- Volume stays a bounded, paced dial. Do not raise any default.

Finish with all quality gates green, RUNBOOK.md documenting the apply flow and
its handoff cases, and the CLAUDE.md ledger updated with Phase 1 checked off.

Commit with my git identity from `git config --global`. If you cannot, do not
commit — I will do it myself.
```

---

## Phase D — Review speed and quality

**Goal:** drive human time per application toward the PRD's < 2 minute target, now that
Phase B can measure it.

Scope: keyboard-driven review; bulk approve; edit-in-place with PDF re-render (the gate re-runs
on every edit); cover letter generation behind the same gate; cached company research brief.

### Master prompt — Phase D

```
Read CLAUDE.md, PRD.md and docs/ROADMAP.md first. You are implementing Phase D
(Review speed and quality). Work only that phase. Phases A through C are done.

The target is the PRD's primary efficiency metric: under two minutes of human
time per quality application. Phase B's analytics can now measure it, so
instrument before and after and report the actual numbers.

Build, tests first:

1. Keyboard-driven review: j/k to move, a to approve, r to reject, o to open the
   posting, ? for help. This is the highest-leverage change for the metric.
2. Bulk approve across a filtered set, with the whitelist gate enforced per item
   — a bulk action must not become a way around it.
3. Edit-in-place on bullets with PDF re-render. The gate must re-run on every
   edit, and an edit that would fail it is refused with the reason shown. Spec
   section 210 deferred this here.
4. Cover letter generation behind the same whitelist gate as the resume, with
   the same completeness check and the same retry loop.
5. A cached company research brief per card, generated once and stored, so the
   "why this company" answer is not written from scratch each time.

Invariants that must not regress; assert them with tests:
- Editing cannot bypass the gate. This is the sharpest risk in the phase: a
  human-edited bullet is still subject to non-negotiable #2.
- The tailored resume keeps the source resume's bullet count and order per role,
  on one page, after any edit.
- Nothing is ever deleted; every status stays reachable and reversible.

Finish with all quality gates green, the CLAUDE.md ledger updated, and a measured
before/after on human time per application.

Commit with my git identity from `git config --global`. If you cannot, do not
commit — I will do it myself.
```

---

## Phase E — Intelligence and scale

**Goal:** use Phase B's data to steer the pipeline instead of guessing.

Scope: A/B tailoring variants scored on reply rate; per-company throttling; JD change
detection via content hash; multiple profiles with per-job selection; saved searches; salary
enrichment and a floor filter; premium model routing for top-band roles.

### Master prompt — Phase E

```
Read CLAUDE.md, PRD.md and docs/ROADMAP.md first. You are implementing Phase E
(Intelligence and scale). Work only that phase. Phases A through D are done.

Everything here is steered by Phase B's measurements. Where a change cannot be
justified by that data, say so rather than building it on instinct.

Build, tests first:

1. A/B tailoring variants: two strategies, randomly assigned per application,
   compared on reply rate in the analytics from Phase B. Both variants pass the
   whitelist gate — this tests phrasing, never truthfulness.
2. Per-company throttling so several roles at one employer cannot consume the
   day's budget.
3. JD change detection: re-score when a posting's content_hash changes. The
   dedupe rule stays certainty-only — an aggregator row is dropped solely on a
   matching Greenhouse (board_token, ats_job_id), never on a fuzzy title match.
4. Multiple profiles: more than one canonical_facts object, chosen per job — for
   example a backend-flavoured resume and an AI-flavoured one. Each keeps its own
   immutable whitelist, and the gate checks against the profile actually used.
   Today the profile lookup assumes a single row; that is the main change.
5. Saved searches with their own dials, and salary enrichment from the aggregator
   where the ATS omits it, with an optional floor filter. This resolves PRD open
   question #3.
6. Premium model routing for top-band roles — the client already supports
   per-purpose routing, so this is small. Benchmark before choosing a model:
   defaults in this project are set by measurement, never by reputation.

Invariants that must not regress; assert them with tests:
- Each profile's whitelist gate is enforced against that profile, and cross-
  profile contamination is impossible.
- Certainty-only dedupe still holds, enforced by the partial unique index.
- Volume stays a bounded, paced dial.
- Nothing is ever deleted.
- The bullet-count and one-page guarantees still hold for every profile.

Finish with all quality gates green, the CLAUDE.md ledger updated, and every
remaining open question in CLAUDE.md either resolved or explicitly restated.

Commit with my git identity from `git config --global`. If you cannot, do not
commit — I will do it myself.
```

---

## How to use these

Paste one master prompt into a fresh session. Each is self-contained: it names the phase, the
invariants, the gates, and the commit rule. Do not run two phases in one session — the
invariant lists are what keep the existing system working, and they are long enough to matter.

If a phase turns out bigger than a session, the natural split points are numbered inside each
prompt; take items 1–3, then 4–6.
