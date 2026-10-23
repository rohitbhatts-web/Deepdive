# Phase 5 audit (React frontend)

Scope: the frontend (`frontend/`), the read endpoints added for it, and the database-connection change made while building it. Research
behaviour was not touched and no existing live database was modified (the browser runs used copies; the file mtimes of `data/*.db` are
unchanged). No model, search or fetch call was made.

## Findings and fixes

| # | Finding | Evidence | Fix |
|---|---|---|---|
| 1 | The header showed all stored model calls (21) next to a job limit, and the Usage tab showed the latest job's 12 / 15 under a similar name | live copy of run 1: header 21, Usage "12 of 15" | Header: "Total model calls for this run" and "Latest job requests used / limit" (Unavailable when no job budget was recorded). Usage tab: two titled blocks with scope sentences. Progress panel: "Model calls recorded for this run (each job is limited to N)". Cost stays Unavailable (estimated, calculated, provider-reported); tests assert no currency amount is rendered |
| 2 | `db.tx` left the transaction open if `COMMIT` itself failed (busy database); a job connection is long-lived, so its next `BEGIN` would fail with "cannot start a transaction within a transaction" | `FailingCommit` fixture; mutation check: the old code fails the new test | `COMMIT` is inside the `try`; any exception rolls back when `in_transaction` |
| 3 | Two of my new tests wrote to the default `data/deepdive.db` (they called the real `get_db` against the default settings) | file appeared with 4 fixture rows | tests now use a `real_get_db` fixture bound to the test database; the stray file (fixture rows only, created today) was removed; the suite no longer creates it |
| 4 | Report-tab claim buttons and citation buttons had an `aria-label` that did not contain their visible text; `aria-controls` pointed at an element that does not exist until a claim is open | axe: `label-content-name-mismatch` (12 nodes), `aria-valid-attr-value` (6 nodes, critical) | labels and `aria-controls` removed; the visible text is the accessible name |
| 5 | Sidebar run id text had contrast 4.0:1 on the active row | axe `color-contrast` | `--sidebar-muted` lightened; axe clean afterwards |
| 6 | The section links used `role="tab"` without arrow-key support (an incomplete tab widget) | review | now a `<nav aria-label="Run sections">` with `aria-current="page"` and a section heading |
| 7 | Error screen had no `<h1>`; loading screen had no heading | axe `page-has-heading-one` | headings added |
| 8 | Opening a claim did not move keyboard focus; closing did not return it | keyboard run | focus moves to the inspector heading on open and back to the originating claim on close |
| 9 | The recent-runs list stayed "Running" after a run finished until you navigated | simulated run | refresh once when the open run reaches a terminal state, and every 4 s only while a listed run is active (none otherwise); cleaned up on unmount |
| 10 | A finished run with no stored report was labelled "Completed with gaps"; a never-started run said "stopped before a report" | simulated runs, empty-state screenshot | "Completed, no report"; "Research has not started" |
| 11 | A dev-proxy error with no JSON body showed "500 Internal Server Error"; the failed-run header repeated the same reason twice | outage screenshot | "The backend is not responding correctly (HTTP 500)."; duplicate notice suppressed |
| 12 | `package.json` contained a stray dependency `cn` (the shadcn CLI installed the npm package of that name) and dead code (`downloadMarkdown`) | `npm ls`, grep | both removed |
| 13 | `npm audit` reported 3 vulnerabilities (2 critical, in Vitest 3 and its dependency tinypool; dev tooling only) | `npm audit --json` | Vitest 4.1.12 (works on Node 22.11): `npm audit` reports 0, the 25 frontend tests pass |

### Database threading review (item 2)

- API: `get_db` opens one connection per request and closes it in `finally`, whichever worker thread runs that step. `cross_thread=True` only
  turns off Python's thread-ownership check for that per-request connection; it is never shared between requests (tested: two requests get
  distinct connections; an endpoint exception thrown into the generator still closes it; an abandoned `BEGIN` does not block the next writer).
- Jobs: `services.open_db` opens a separate connection per job with the thread check **on**; sharing it across threads fails loudly (tested).
- Transactions: every write goes through `db.tx` (`BEGIN IMMEDIATE`, rollback on any exception including a failed commit). SQLite is in WAL
  mode with Python's default 5 s busy timeout. Tested: 6 writer threads and 2 readers on separate connections produce exactly 60 rows, no error, and
  `PRAGMA integrity_check` is `ok`; an API read is not blocked by a job's open write transaction; an API write waits for it and then succeeds.
- Not covered: sustained write contention beyond the 5 s busy timeout would surface as a 500 `database is locked`; no test drives that.

## Simulated progress validation (item 3) - NOT live agent acceptance

`scripts/ui_sim_backend.py` serves the real API and database code but replaces the research job with a timed script of stage events and status
changes (`"simulated": true` in every payload). Question text picks the script. Browser: Edge headless driven by Playwright, 2 s per step.

| Check | Observed |
|---|---|
| Start from the UI, stage updates | latest event and stage chips advanced about every 2 s from "Run created" through searching, fetching, drafting, coverage, correction to the terminal state; double clicks produced one run |
| Terminal state | header changed to "Completed, no report" and the run total / latest job lines appeared; polling requests to the run in the next 7 s: **0** |
| Sidebar | showed "Running", then "Completed, no report" without navigating |
| Failure script | "Failed", reason shown once under "Collection failed", polling stopped |
| Budget stop script | "Stopped", reason `max_model_requests reached (20)` shown |
| Leaving a running run | requests to that run in the next 6 s: **0**; the sidebar kept refreshing (2 list requests) because a run is still active |
| Backend outage mid-run | "Could not refresh ... Retrying" kept the last stored state; after restart the backend marked the run failed ("interrupted: server restarted"), the notice cleared and polling stopped (0 requests in 5 s) |

Limits: the script's timings and events are invented; it does not test the real pipeline, real durations or real failures.

## Accessibility and rendering (item 4)

axe-core (wcag2a/aa, 2.1 a/aa, best-practice), injected in Edge, on: New research, report, report with inspector, Evidence (with inspector),
Coverage, Run history, Usage, empty (never started) run, failed run, error state (backend 500), loading state, and the 390 px report - **0
violations** after the fixes above. This is an automated check, not a full audit: screen readers were not run.

- Keyboard: tab order is skip link, New research, run list, section links, Download, then claim and citation buttons in document order; every
  stop showed a focus outline (2-3 px). Enter on a claim moved focus to the inspector heading; closing returned focus to that claim.
- Status announcements: `role="status"` regions for the run state ("Run 1: Completed with gaps."), the latest progress event and loading.
- 390 px: no horizontal overflow on Report, Evidence, Coverage, Run history and Usage (`scrollWidth` = `clientWidth` = 390). Screenshots of
  the empty, failed, error, loading and 390 px inspector screens were viewed; the other tabs were checked by text and axe only.
- Colour and meaning: every state chip has a text label and an icon.
- HTML safety (browser, real responses with a hostile FIXTURE payload substituted: `<img onerror>`, `<script>`, `<b>`, `javascript:` URL, Markdown
  with a script): `window.__pwned` stayed 0, no injected `img`/`script`/bold element, the markup shown as literal text, no `javascript:` link
  (non-http(s) URLs render as plain text). The UI never renders Markdown; the download is a `text/markdown` Blob. Component tests cover the same.

## Installation reproducibility (item 5)

Tested path: **Node 22.11.0, bundled npm 10.9.0, `cd frontend && npm ci`**, in a clean copy outside the repo: 245 packages, no engine warning,
`package-lock.json` byte-identical afterwards, `npm run check` (tsc, 25 tests, production build) passes, `npm audit` 0 vulnerabilities.
Every locked package's `engines.node` is satisfied by 22.11.0 (checked from the lockfile); `@testing-library/jest-dom` requires Node >= 22.
The earlier workaround (`npx npm@11 install`) is removed from the docs; it was used only to generate the lockfile. Reason for the Vite 6 pin: Vite 8
and oxlint require Node >= 22.12 for native binaries, and npm 10.9 silently skipped them on 22.11. Not tested: other Node/npm versions, Linux/macOS.

## Checks run

Backend: `uv run pytest` 340 passed; `uv run ruff check .` clean. Frontend: `tsc -b`, `vitest run` (25 tests), `vite build` pass.

## Remaining limitations

- Cost is Unavailable everywhere; the backend stores no price table or provider charge per run.
- "Latest job" figures exist only after a completion job finishes; collection-only runs show Unavailable.
- No test for lock contention longer than the 5 s busy timeout; no screen-reader testing; Chrome/Firefox/Safari untested (Edge only).
- The dev server is the only documented way to serve the UI; no production serving or CORS configuration exists.
- Run history shows the stored event payloads as text and does not diff claim text word by word.
- The UI cannot start research for a run that was created but never started (no control was built, by instruction).

## Phase 6 readiness

The UI shows persisted data without changing it and states what it does not know. Phase 6 inputs available from this audit: the unresolved
compound-claim and joint-obligation cases from `docs/PHASE4_REVIEW.md`, the selection-quality question, and the fact that every UI acceptance
so far used stored data or a simulated backend, never a fresh live run through the browser.

---

# Visual polish (follow-up)

Scope: styling and layout of the existing frontend only. No backend code, API, research semantics, labels or dependencies changed. Reused the installed
shadcn components (Badge for every status chip, Card for every panel, Button) and the existing licence notice (`THIRD_PARTY_NOTICES.md` lists them;
nothing new was added). Real API data only: no scores, no decorative statistics, no new controls.

## Changes

- **Shared building blocks** (`bits.tsx`): `Panel` (a restrained Card with an optional status accent bar), `SectionHeading`, `StatTile`, `CountChips`, skeleton
  loading, an icon empty state, and notices with an accent bar. Every status keeps a text label and an icon. The base border colour moved into a Tailwind layer
  so accent borders are no longer overridden (before, the active-run and status accents never showed).
- **Header and navigation:** run title with a row of stat tiles (the same real fields and labels as before: total model calls for this run, latest job
  requests used / limit, tokens, searches, sources, corrective rounds); underline section links; sidebar with a teal active-run bar.
- **Report:** report left, sticky inspector right on wide screens (the inspector scrolls inside itself), stacked on narrow screens; claim cards with a status accent;
  section eyebrows; snapshot note merged into one panel; requirements as accent cards. Exact claim text, citation buttons, historical-versus-current state and
  the Markdown download are unchanged.
- **Coverage:** count chips (real counts), requirement cards with an amber "Remaining gap" box, obligations as a labelled list.
- **Run history:** each corrective round shows coverage BEFORE and AFTER as count chips, task counts with the full task list folded away, claim versions
  side by side as Before / After, searches as cards, and the event timeline in its own scrolling region.
- **Usage:** tiles and a table for the real per-agent numbers; cost stays Unavailable.
- **States:** skeleton loading, icon empty states, a "Stopped early: results are partial" notice (shows the recorded reason) for a stopped run, "Finished because" for a
  completed one, failure notice, error screen with a heading. The New research page also lists the stages a run goes through (static text, no data).

## Checks (browser: Edge headless, Playwright; stored run 1 plus simulated failed / stopped / running / never-started runs from the simulated test backend)

- Frontend: `tsc -b` clean, 25 Vitest tests pass, production build passes. Backend tests were not run because no backend code changed.
- axe-core (WCAG 2 A/AA, 2.1 A/AA, best practice): 0 violations on New research, report, report with inspector, Evidence, Evidence with inspector, Coverage,
  Run history, Usage, never-started run, failed run, error state, loading state and the 390 px report (this includes colour contrast).
- 390 px: `scrollWidth` = `clientWidth` = 390 on Report, Evidence, Coverage, Run history and Usage.
- Keyboard: all 22 tab stops of the report page show a focus outline; Enter on a claim moves focus to the inspector; closing returns it to the claim.
- Safety: the hostile-markup test (script, img onerror, javascript: URL) again executed nothing and rendered as plain text.
- Simulated progress rerun: polling stops at the terminal state (0 requests in the next 7 s), the sidebar updates without navigation, leaving a running run stops
  its polling, a backend outage shows "Could not refresh" and recovers to the failed state after a restart. (An earlier aborted run left an orphaned test backend that
  interrupted a later run; the table above is from a clean run. That was a test-script problem, not an application one.)
- Screenshots viewed: before and after of New research, report with inspector, coverage, run history, stopped run, running run, and the narrow report.
  Not individually viewed: the other 390 px screens, loading and error states in the after set (covered by axe and the overflow check). Before/after pairs of eight
  screens are in `docs/screenshots/phase5-polish/`.

## Remaining limitations

- Not run: screen readers, Chrome / Firefox / Safari, Windows high-contrast mode, forced-colour mode.
- The inspector is sticky and self-scrolling only from the large breakpoint; on narrow screens it appears below the report and the page scrolls to it.
- The long event timeline scrolls inside its own region; very long payloads are folded.
- Stat tile labels that wrap to two lines ("Latest job requests used / limit") make tile heights uneven at some widths.
- Cost remains Unavailable everywhere (the backend stores none).

---

# Acceptance-script fix and recovery check (follow-up)

## What went wrong in the first live acceptance run

The script's wait loop looked for the on-screen text "Research in progress" once, before the run page had rendered, concluded "terminal" after 0 s, and
its cleanup then stopped whatever listened on port 8000. That interrupted the paid job after its first search (2 model calls, $0.000098 reported by the
provider). Reproduced against the simulated backend (no credits): the old logic left the run at `researching / verifying / completing` with only two events
and the backend gone.

## Fix: `scripts/ui_acceptance/` (lib, driver, tests)

- `lib.mjs` (pure, tested): a run is active iff its STORED job status says so (`waitForTerminal` awaits page rendering first, then polls the API and never
  lets the screen decide); `timeout` and `unreachable` are distinct outcomes; `cleanupDecision` stops a backend only if this script started it AND the job is known
  terminal (or no job was started); `startOwnedBackend` refuses an occupied port; every HTTP status >= 400 is an unexpected error unless a rule names it.
- `driver.mjs`: starts the project's venv python directly (not `uv run`, which exits after launching its child on Windows, and not through a cmd wrapper), detached
  (on Windows libuv otherwise kills the child when the script exits), records its pid, releases the child when it leaves a job running, and probes the API for the
  cleanup decision. Test hooks `ACCEPT_TEST_*` exist only to test these rules.
- Tests: `node --test scripts/ui_acceptance/lib.test.mjs` (12 logic tests, no browser) and
  `PLAYWRIGHT_CORE=<dir> node --test --test-timeout=900000 scripts/ui_acceptance/e2e.test.mjs` (5 scenarios on the simulated backend, needs Vite on :5173 and Edge).

Evidence the bug is fixed (simulated backend, no credits): (1) with the run page delayed by 6 s the driver still waited for the real job (about 20 s), saw several
real stage updates, finished on the stored terminal status, and stopped only its own backend; (2) with a timeout while the job was active, and (3) with a forced
assertion failure while the job was active, the driver left the backend running and the job then finished by itself (`completed`, no startup-recovery event);
(4) with something else already on port 8000 the driver refused to start and left it alone; (5) a look-alike process (command line mentioning the sim backend, on
another port) survived a normal run. Three of the five scenarios failed on the way and exposed real defects that were fixed: a cmd/`uv` wrapper pid that no longer
owned the server, children killed on exit by libuv's job object, and a driver that never exited while a left-running child kept its event loop alive.

## The 404 console errors

The only HTTP error responses the browser saw, across every tab of a stored run with a report, an active run, a never-started run and a failed run, were
`GET /api/runs/{id}/report` returning 404 on the Report tab of runs that have no report (the documented response; the UI handled it and showed its empty state, but the
browser logs each as a console error). Nothing else. The Report tab now asks for the report only when the run summary says one exists (`report_outcome`), so no such
request is made; a missing report for a run that claims one is still shown as an error. After the change the same capture shows 0 HTTP errors and 0 console errors on
all screens and states.

## Startup recovery of the interrupted run

The interrupted acceptance database was preserved (byte-identical before and after; all checks ran on copies). Starting the real app on a copy marked the run
`failed` ("interrupted: server restarted during research") and kept the 5 events, 2 model calls and 1 search. It also marked verification and completion failed with
"interrupted: server restarted during verification / completion" although neither had begun (they are claimed up front but start only after collection). That was inaccurate;
recovery now records them as "not started: the server restarted during research, before this stage began", while a run cut off after collection still says "during
completion" / "during verification". The failed-run header no longer shows a second "finished because" notice. Tests: `tests/test_recovery.py` (3), the report-request
and failed-header frontend tests (4).

## Checks

Frontend `tsc -b`, 29 Vitest tests, production build; 12 script logic tests and 5 simulated e2e scenarios; backend tests (see the summary message). Mutations of the
report-request skip and of the recovery wording each fail a test. No paid call was made.

## Not done

The live acceptance of progress, report, citations, coverage and history on a real run has still not happened; it needs a new approval.


---

# Drafting-timeout diagnosis, deadline handling and explicit retry (follow-up)

No paid call was made for this work. The failed acceptance database (`data/ui-acceptance-2.db`) was read only, through copies; its files are byte-identical.

## Diagnosis of the failed run (run 1, 2026-10-21)

**Confirmed from the stored records and the code**
- The failing request was the first drafting call: model `qwen/qwen3-235b-a22b-instruct-2507`, structured output on, `max_tokens` 1,500, one attempt, no usage returned, no
  latency stored. The passages offered to it were 10 passages / 8,808 characters (limit 9,000) plus a 1,934-character system prompt and example; its prompt-token count is unknown (a
  rough estimate is 3,000-3,500 tokens).
- Job clock: collection ended at 205.1 s of the 300 s budget with 10 of 20 requests used; the drafting call started at that moment (94.9 s left) and ended 60.0 s later with
  `APITimeoutError` (job time 265.1 s, 34.9 s left). 60 s is `MESH_TIMEOUT`, the fixed client timeout; the SDK is built with `max_retries=0` and `llm.call` does not retry a failed
  request, so it was one request. The budget had not run out: requests 11/20, output tokens 1,718/15,000, time 265/300 s.
- The job then stopped as `failed` with the reason shown in the UI; the 9 sources and 155 passages are stored and inspectable (checked through the real API on a copy).
- Code gap found (not the cause here): a model request was only checked against the elapsed limit BEFORE it started, so with, say, 5 s of the job left it could still run for up
  to 60 s past the deadline.
- Provider latency in the same run was far slower than earlier stored runs: the 8 later qwen calls produced 2.9-16 output tokens per second (relevance check 48.1 s for 409 output
  tokens; research calls 6-32 s). Earlier stored drafter calls averaged 24.8 s (max 41.7 s) for about 266 output tokens, roughly 10 tokens per second.

**Hypotheses (not confirmed)**
- The provider or Mesh was slow or queued at that moment. At 3-16 tokens per second, a drafting reply of 600-1,000 tokens (up to 8 claims) needs 40-300 s, so a 60 s timeout would be
  too short for this workload even without an incident. We do not know how long the reply would have been or whether the provider finished it after our client gave up.
- The larger drafting input (8.8k characters here, 3.4k in the earlier run) lengthened the work.
- Whether the timed-out request was billed is unknown. The Mesh balance fell by $0.0013 while the provider-reported charges of the 8 calls that returned usage total $0.000945;
  the difference is unexplained (two early calls and the timed-out call returned no cost).

## Fixes

1. **Per-call deadline** (`budget.call_timeout`, used by `llm.call`): a request's timeout is `min(MESH_TIMEOUT, time left in the job)`; with less than `MIN_CALL_SECONDS`
   (default 10 s) left no request is started, nothing is counted or recorded, and the job stops honestly (`insufficient time remaining for a model request (...)`). A deadline that
   has already passed keeps its established message. The timeout itself is NOT raised and no model was switched.
2. **Timed-out requests**: they were already counted in the request budget before being sent; the stored record keeps usage unknown (NULL) and the error now says which timeout
   applied. For the output-token CAP only, a failed request is counted at the per-call maximum (like a reply without usage), so failures cannot defeat the cap; nothing is invented in
   the record.
3. **Explicit retry from stored evidence** (`backend/retry.py`, `GET` and `POST /runs/{id}/retry`): allowed only for a collected run with stored sources, a failed or stopped
   verification and completion, and no claim yet; never automatic; `POST` needs `{"confirm_paid_retry": true}`. It runs drafting, verification and completion under a NEW job
   budget whose search and fetch limits are 0 (it cannot retrieve anything and no query-writing step is reached), never higher than the configured limits. The lineage is the audit
   event `job_retry_started` (attempt number, the failed job's final status, stop reason and budget snapshot, the new limits); the failed job's own events and budget record are
   untouched. A second retry is a new attempt that again needs explicit confirmation. Real-data check (copy, GET only): the plan for run 1 reports allowed, 9 sources, 155 passages,
   0 claims, new limits 20 requests / 0 searches / 0 fetches / 15,000 tokens / 300 s, previous job 11 of 20 requests.
4. **UI**: the failure notice is derived from the failed job and stage ("Drafting failed after collection finished", coverage, report assembly, verification, or "Collection
   failed"), never "Finished because"; the collected source and passage counts stay visible; a stage label "Planning" replaces the raw "planner" (and every stage the backend
   emits now has a label, as do the 14 event types that had none); a confirmed-retry panel shows the stored evidence, the new limits, the failed job's budget and a checkbox that
   must be ticked before the button works.

## Tests

`tests/test_deadline_retry.py` (12): deadline arithmetic, the timeout reaching the client, no request when time is short, a timed-out request counted with unknown usage, repeated
timeouts using the one budget, drafting timeout through the real graph with evidence preserved, insufficient time stopping before any call, the retry plan, refusal without
confirmation, a retry with no search / no page request / no query-writing step and the lineage event, refusals (not failed, running, no evidence, claims exist), a second attempt.
`frontend/src/test/failure.test.tsx` (7): failure wording per stage, counts visible, labels, retry panel (disabled until confirmed, one request, refusal shown, unavailable reasons).
Backend 433 tests and the frontend 36 tests pass; lint and build clean.

## Limits that remain

- The retry only continues a job that produced no claims; continuing partially verified work is not supported.
- The fix does not make this particular call succeed: with the same 60 s timeout the same drafting request would time out again if the provider is that slow. Whether 60 s is too
  short for drafting at this input size is exactly what the proposed test below measures.
- Provider latency and billing of a timed-out request remain unknown.

## Proposed bounded drafting-only test (NOT run; needs approval)

- **Evidence:** a copy of `data/ui-acceptance-2.db` (the original stays untouched): the same 10 passages (8,808 characters) the failed call was offered, selected by the production selector.
- **Request:** exactly one drafting request, the same model and structured output, `max_tokens` 1,500, production timeout of 60 s, no retry, no verification afterwards.
- **Limits:** 1 model request (a second request only if the first times out AND you approve it as a diagnostic with a 150 s timeout, to measure the real reply time; it would not change the
  product default), 0 searches, 0 fetches, at most 3,000 output tokens, 60 s (150 s for the diagnostic).
- **Measures:** time to the reply or the timeout, returned prompt and completion tokens, finish reason, number of claims, provider-reported charge from the usage API, balance change.
- **Estimated cost (calculated, not a charge):** about $0.0004-0.0005 per request at the catalog price ($0.09 per million input, $0.10 per million output tokens, about 3,000-3,500 input
  tokens, at most 1,500 output); at most about $0.001 for both. Mesh balance is $5.1372.
- **Decision it informs:** whether to give drafting a longer per-call limit (with the job deadline still bounding it), a smaller input or claim limit to shorten the reply, or neither.


---

# Drafting-only diagnostic on the failed run's evidence (2026-10-21)

Run once with `scripts/live_drafting_probe.py` on a copy of `data/ui-acceptance-2.db` (original byte-identical): the same 10 passages (8,808 characters, verified equal to the failed call's
recorded selection), the same model and structured output, `max_tokens` 1,500, the production 60 s client timeout, a job budget of one request (so no second attempt), no search, no fetch,
no verification. The optional 150 s diagnostic was not run and no product default was changed.

| | Result |
|---|---|
| Outcome | the request succeeded and the reply parsed against the schema |
| Latency | 0.3 s measured around the call (276 ms stored); client timeout 60 s |
| Returned usage | 2,751 prompt tokens, 637 completion tokens, finish reason `stop` |
| Claims drafted | 8 (the `max_claims` limit), 867 characters of claim text; none verified |
| Provider-reported charge | unavailable: the usage API returned no cost for this request; the Mesh balance was $5.1372 before and after (four decimals), so no charge is visible |

## What this does and does not show

- **It validates that request and response:** the same 10 passages, prompt, schema and 1,500-token limit produce a complete, schema-valid reply of 637 tokens. So the request itself is
  well-formed and its reply fits the limit.
- **It does not establish why the original call timed out.** The original timeout (60 s with no reply) and this success (0.3 s) are two different events; nothing here shows what the
  provider was doing during the first one.
- **It does not measure generation time.** 637 output tokens in 0.3 s (about 2,300 tokens per second) is far above any rate seen for this model in these runs (3-16 tokens per second
  that afternoon, about 10 earlier). Possible explanations, none of them tested: a stored or cached response, a much faster provider state at that moment, or something else. Which one is
  unknown. Do not read this probe as confirming caching.
- **It does not confirm anything about billing.** The usage API returned no cost for this request and the Mesh balance did not change at four decimals, so no charge is visible, but unknown
  usage and billing stay unknown. The earlier gap between the balance change ($0.0013) and the reported charges ($0.000945) remains unexplained; we do not know whether the timed-out
  request was billed.

## Smallest next change (made afterwards)

Failed model attempts, including timeouts, now store the measured client-side wait (`latency_ms`); token counts, finish reason, request id and cost stay NULL (unknown). It is covered by
deterministic tests with a controlled clock (`tests/test_deadline_retry.py`). No model, timeout, prompt or limit was changed. A latency measurement of a drafting request that is not a repeat
of an earlier one is still needed before deciding anything about the 60 s timeout; it was not run.


## Acceptance review addendum: stored attempt-4 runs and the cleanup incident (2026-10-22)

**What was checked** (React UI on a COPY of the attempt-4 database, no research started): the failed Q02 run showed "Failed", the drafting-failure explanation, preserved evidence (8 sources, 451
passages), the retry panel (not used), "This run has no report" and "No coverage assessment yet"; the empty Q05 report showed its historical-snapshot note, 7 unresolved requirements, the corrective
rounds and searches, and usage that matched the database. Reload kept the tab and URL on every tab. **Not checked:** citation navigation (neither run has a citation), so it is not claimed.
Neither run shows answer quality. Finding: the header called the empty report "Completed with gaps"; changed to "Insufficient evidence" with a collection note.

**Incident.** The cleanup after the check ended an unrelated `node.exe`. Cause: an ad-hoc command written for this check matched processes by program name and listening port. No repository code
does that (the acceptance driver stops only the backend it launched, by pid). Remedy: identity-verified ownership records (`scripts/ui_acceptance/owned.mjs`: pid + creation time + command line, refuse on
mismatch, leave alone and report when ownership cannot be established), a regression test with a real unrelated Node process, and a test that no script or document recommends name- or port-based kills.
To inspect stored runs, launch the backend and Vite with `launchOwned` and stop them with `node scripts/ui_acceptance/owned.mjs stop RECORD.json`.
