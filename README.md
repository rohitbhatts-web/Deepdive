# DeepDive: Agent Arena 2026

A research assistant that answers a question from web evidence it can show. It plans subquestions, searches and fetches pages, drafts atomic claims with verbatim quotes, checks every quote deterministically,
has a separate verifier judge each claim against its cited passage, runs a bounded correction loop for what is missing, and assembles a report from the exact verified text. Stack: FastAPI + LangGraph +
SQLite backend, a React frontend (`frontend/`), and the OpenAI SDK pointed at Mesh API. Every model call goes through one shared Mesh client; every run has hard limits on requests, searches, page fetches, tokens and time.

## What has been demonstrated, and what has not

**Demonstrated** (development runs on 21-22 Oct 2026; the demo uses these *recorded* runs, not live research; see `docs/DEMO.md`):
- The pipeline runs end to end and records everything (searches, fetches, passages, claims, verdicts, coverage, reports, model calls, failures); failed and empty runs are kept and shown as such in the UI.
- A small, AI-assisted development evaluation: a frozen claim-verification pilot (12 reviewed claims, 9 of 12 in agreement with the reviewed labels, under an earlier verifier rubric), two end-to-end pilot questions, and a six-case answer-relevance check
  with one follow-up (both follow-up cases agreed with their pre-recorded expectations). Details and every failure are in `docs/PHASE6A.md`.

**Not demonstrated / limitations**
- **Small and AI-assisted.** The labels are "AI-assisted human review; not independent or blinded". There are 12 reviewed examples from one domain (SQLite/PostgreSQL evidence) and two end-to-end questions; no held-out example has been run. Agreement with
  expectations is not accuracy, and the runs used evaluation budgets that differ from the product defaults.
- **Incomplete answers.** The best Q02 report (HTTP/2 and HTTP/3 RFC dates and transport layers) still lacks the HTTP/3 RFC date and a direct statement of HTTP/2's transport; the Q05 run (a private company's profit figure) found no usable primary source and produced an empty,
  clearly labelled "insufficient evidence" report. An empty or partial report does not show the answer does not exist.
- **Model timeouts.** Each model request has a 60 s wall-clock limit and is never retried invisibly; a timed-out drafting call failed one pilot run. A person can retry from the stored evidence (a visible, paid action; no search or fetch).
- **PDFs are unsupported** (such results are skipped and counted), so annual reports and filings are out of reach. No JavaScript rendering.
- **Relevance and coverage are model judgements.** Support (the verifier) and answer relevance (the coverage stage) are judged separately; a wrong relevance judgement is not caught by code, and the original-versus-revision check is a narrow wording heuristic.
  Verdicts from an older verifier contract are kept as history but are not trusted for new reports until re-verified.
- **Recorded, not live.** There is no hosted instance; the demo script replays stored runs and starts no research.

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Git.

```bash
uv python install 3.12        # once; .python-version pins the project to Python 3.12 (without the pin uv picks the newest interpreter, 3.14 here)
uv sync                      # creates .venv with that Python 3.12 and installs from uv.lock
uv run python --version      # should print Python 3.12.x
cp .env.example .env         # then fill in the values under "Configuration"
cd frontend && npm ci        # Node 22.11.0 (tested path); see the frontend section
```

## Run

```bash
uv run uvicorn backend.main:app --port 8000  # API on http://127.0.0.1:8000 (docs at /docs); keys stay here, in .env
cd frontend && npm run dev                   # React UI on http://localhost:5173 (proxies /api to the backend)
```

`ui/app.py` is a minimal legacy Streamlit health page (`uv run streamlit run ui/app.py`); the React frontend is the interface.

## Checks

```bash
uv run pytest                  # deterministic; no network, no model calls (temp DBs, mock HTTP transport, fake DNS, scripted model replies)
uv run ruff check .
cd frontend && npm run check   # typecheck + Vitest + build
node --test scripts/ui_acceptance/lib.test.mjs scripts/ui_acceptance/owned.test.mjs   # UI-acceptance tooling
```

Scripted tests establish orchestration and routing, not model accuracy.

## Evaluation (`evaluation/`)

Dataset, rubric versions, versioned human labels and result schemas are described in `evaluation/README.md`; results and every failure are in `docs/PHASE6A.md`. Every command that can spend money is a dry run unless `--execute` is given, and refuses to overwrite a result.

```bash
uv run python -m evaluation validate                 # datasets, split lock, tuning log (no network)
uv run python -m evaluation verify                   # frozen claim-verification: plan only
uv run python -m evaluation pilot                    # pilot plan and estimated cost; --execute --claims-only / --e2e-only --attempt N are PAID
uv run python -m evaluation.relevance_check          # answer-relevance check: plan, limits, expectations; --execute is PAID
uv run python -m evaluation.relevance_check --interpret   # offline re-reading of a saved result
uv run python -m evaluation e2e-review-sheet --db DB --result JSON --stem PREFIX   # read-only review sheets
```

The held-out split is locked (`evaluation/data/split.lock.json`) and has not been run. Result files that contain page excerpts follow `THIRD_PARTY_NOTICES.md`.

**This is the public export (`public-export-v1`).** The text of 9 of the dataset's 23 evidence passages (four rights-reserved sources) is withheld, which affects 15 of the 40 claim examples, including 2 of the 12 pilot examples. Commands that need that text refuse; `--available-evidence-only` runs a stated, smaller subset. The published 9-of-12 pilot result used the full evidence. See [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md).

## Configuration

Set in `.env` (never commit it):

| Variable | Purpose |
|---|---|
| `MESH_API_KEY` | All model calls go through Mesh (`https://api.meshapi.ai/v1`) |
| `PLANNER_MODEL`, `RESEARCHER_MODEL` | Mesh model IDs; no defaults. Checked against Mesh `/models` before a run starts |
| `DRAFTER_MODEL`, `VERIFIER_MODEL` | Needed for drafting, verification and completion (prefer different models); `COVERAGE_MODEL` and `SYNTHESIZER_MODEL` default to the verifier and drafter models |
| `TAVILY_API_KEY` | Web search ([Tavily](https://tavily.com): 1,000 free credits/month, no card; 1 credit per search here) |

Choose model IDs from Mesh's catalog (`GET /v1/models`, which returns pricing and flags such as
`supports_structured_output` and `supports_thinking`; the SDK's `models.list()` cannot parse it, so the app reads it raw).
Avoid thinking models: reasoning tokens count against `MAX_OUTPUT_TOKENS`. Before spending anything, run
`uv run python scripts/live_test.py --check` (no inference); then `uv run python scripts/live_test.py` runs one
hard-capped live job on its own database.

If any is missing the start endpoint returns `503` listing what is missing; it never returns fake results. Limits
(`MAX_MODEL_REQUESTS`, `MAX_SEARCH_QUERIES`, `MAX_PAGE_FETCHES`, `MAX_OUTPUT_TOKENS`, `MAX_TOTAL_OUTPUT_TOKENS`,
`MAX_ELAPSED_SECONDS`, `MAX_SUBQUESTIONS`, ...) are env-configurable; defaults are in `backend/config.py`.

## React frontend (`frontend/`)

Vite + React + TypeScript + Tailwind CSS v4 + shadcn/ui (button, badge, card) + Lucide icons.

```
uv run uvicorn backend.main:app --port 8000          # backend (keys stay here, in .env)
cd frontend && npm install && npm run dev            # http://localhost:5173
```

- The dev server proxies `/api/*` to the backend (default `http://127.0.0.1:8000`; set `DEEPDIVE_API` to change it). No key or `VITE_*` variable exists; the browser only talks to one origin, so no CORS setup is needed.
- Checks: `npm run typecheck`, `npm test` (Vitest + Testing Library, fixtures labelled), `npm run build`, or `npm run check` for all three. Python: `uv run pytest`, `uv run ruff check .`.
- Installation (tested path): Node 22.11.0 with its bundled npm 10.9.0, then `cd frontend && npm ci`. `npm ci` installs exactly `package-lock.json` (verified in a clean copy: 245 packages, no engine warnings, lockfile unchanged, `npm run check` passes, `npm audit` reports 0 vulnerabilities). Every locked package's `engines.node` is satisfied by 22.11.0 (checked against the lockfile); `@testing-library/jest-dom` needs Node >= 22, so older Node is unsupported. Other Node/npm versions are untested.
- Why Vite 6: Vite 8 and oxlint need Node >= 22.12 for their native binaries, which 22.11 does not satisfy (npm silently skipped them). Vite 6, Vitest 4.1.12 and jsdom 26 run on 22.11. No JS linter is configured; TypeScript (`noUnusedLocals`) is the static check. Use `npm install <pkg>` only when deliberately changing dependencies, and commit the lockfile.
- Screens: New research (validated question, configured limits, starts the end-to-end `?complete=true` job), run page with real progress (polled every 2 s until a terminal state), and tabs Report (exact snapshot text + citations beside an evidence inspector, Markdown download), Evidence, Coverage (requirements and obligations), Run history (corrections, claim versions, searches, fetch failures, events) and Usage (provider-reported tokens, with the run total and the latest job's used / limit labelled separately; estimated, calculated and provider-charged cost are shown as Unavailable because the backend stores none of them).
- Added read endpoints: `GET /runs` (paginated), `GET /limits`; `GET /runs/{id}` and `POST /runs` also return `verify_status`, `complete_status` and `report_outcome`.
- Route state lives in the URL hash (`#/runs/1/report?claim=2`), so a refresh keeps the run, tab and open claim.

## Correction loop and report

`POST /runs/{id}/complete` takes an already collected run through re-judging of stale verdicts, a coverage assessment of each evidence
requirement against the current eligible claims, at most `MAX_CORRECTION_ROUNDS` (2) corrective rounds (repair a failed claim as a NEW
version, or run a targeted search for exactly what is missing), re-verification, and a report. `POST /runs/{id}/research?complete=true`
does it end to end for a new run. One shared budget covers every agent and round. The report is assembled by code from the exact
stored text of current, verified claims and the passages their verdicts relied on; the model only orders claim IDs. A report
with unresolved requirements is a normal, clearly labelled result (`claims_with_gaps`), distinct from `claims_all_requirements_addressed`.

```bash
curl -X POST localhost:8000/runs/1/complete        # 202; 409 if already started; 503 lists missing configuration
curl localhost:8000/runs/1/completion              # status, stop reason, rounds, coverage summary, budget used
curl localhost:8000/runs/1/coverage?round=0        # per-requirement status, supporting claim IDs, gaps (default: latest)
curl localhost:8000/runs/1/corrections             # rounds, tasks, revision lineages, exclusions
curl localhost:8000/runs/1/report                  # latest immutable report snapshot (also /reports/{id}); includes markdown
```

Settings: `MAX_CORRECTION_ROUNDS`, `MAX_CANDIDATE_CLAIMS` (hard cumulative cap on new claims drafted from corrective evidence),
`MAX_REPORT_CLAIMS` (a separate cap on report size), `MAX_REPAIRS_PER_CLAIM`, `MAX_TASKS_PER_ROUND`, `MAX_OBLIGATIONS_PER_REQUIREMENT`,
`COVERAGE_MODEL` and `SYNTHESIZER_MODEL` (default to the verifier and drafter models). Each requirement is split into coverage
obligations (a subject and a dimension); a requirement is addressed only when every obligation is. Coverage is a fallible model
judgement; primary-source preference is prompt-level; see docs/PHASE4_REVIEW.md.

## Claims and two-stage verification

After collection, a **Drafter** turns stored passages into atomic claims (each with passage IDs and verbatim quotes), a
deterministic **integrity check** validates the evidence, and a separate **Verifier** judges whether the cited evidence
supports the whole claim (`supported` / `partially_supported` / `unsupported` / `contradicted`; a failed model call is
`error`). Only claims that are naturally drafted (not test inputs), passed integrity, have a latest semantic verdict of `supported`
and have no newer version are `eligible_for_report` (one rule: `claims.is_report_eligible`). Integrity and semantic outcomes are
separate fields (`integrity_verdict`, `semantic_verdict`), and `citations` list the stored passages the verdict relied on.
Claims, evidence and results are immutable rows (rejected claims are kept); a verdict belongs to one claim version.

```bash
curl -X POST localhost:8000/runs/1/verify                  # draft + verify an already collected run (no search or fetching)
curl -X POST "localhost:8000/runs/1/research?verify=true"  # collect, then draft + verify, in one job
curl "localhost:8000/runs/1/claims?eligible_only=true"     # report-eligible claims only; also ?status=
curl "localhost:8000/runs/1/claims?status=supported"       # claims with exact evidence, integrity and semantic results
curl localhost:8000/runs/1/claims/3                        # one claim with its full result history
curl localhost:8000/runs/1/verification                    # verify_status, counts by status, usage by agent
```

Settings: `DRAFTER_MODEL`, `VERIFIER_MODEL` (no defaults; prefer different models), `MAX_CLAIMS`, `DRAFT_INPUT_CHARS`,
`VERIFIER_CONTEXT_CHARS`, `MIN_QUOTE_CHARS`. `POST .../verify` needs only the Mesh key and these two models.
Limitations: a verdict is one model's judgement (mock tests prove orchestration, not accuracy); one verification job per run
(a failed one can be continued explicitly from the stored evidence, `POST /runs/{id}/retry`); the integrity check proves a quote exists, never that it supports the claim.
Live setup and results: [docs/PHASE3_REVIEW.md](docs/PHASE3_REVIEW.md).

**Answer relevance.** Support is the verifier's question and sees only the claim and its evidence. Whether a verified claim *answers* a requirement (`direct_answer`, `relevant_context` or neither) is judged by the coverage
stage, which receives the question; reports show direct answers as answers, context apart, and say when a section has no direct answer. See `docs/PHASE6A.md`.

## Research: planner and researcher

`POST /runs/{id}/research` starts one research job (`202`; `409` if already started; `503` if not configured).
A LangGraph `StateGraph` runs **Planner** (question to validated subquestions + evidence requirements) then
**Researcher** per subquestion (model writes queries, Tavily searches, model selects URLs from the results, the
existing ingestion service fetches and stores them). The model can only pick URLs that search returned.
Everything is stored: searches with raw hits, selections and their outcomes (including failed or blocked fetches),
subquestion-to-source links, audit events, and each model call's reported token usage. Reaching any limit stops the
run with a stored `stop_reason`; unfinished subquestions are marked `collection_incomplete`.
Subquestion statuses are `evidence_collected`, `collection_incomplete` or `failed`; run status ends as `collection_finished`,
`stopped` or `failed`. None of these mean the question is covered or any claim verified. After collection, a relevance
check may link a source to another subquestion (with cited passage IDs); those links are `unverified` (`GET /runs/{id}/associations`).

```bash
curl -X POST localhost:8000/runs -H "content-type: application/json" -d '{"question": "..."}'
curl -X POST localhost:8000/runs/1/research            # returns immediately; work runs in the background
curl localhost:8000/runs/1/research                    # status, counts, token usage, limits
curl localhost:8000/runs/1/subquestions
curl localhost:8000/runs/1/searches                    # queries, hits, selections, per-URL outcomes
curl localhost:8000/runs/1/associations                # source <-> subquestion links (unverified)
curl localhost:8000/runs/1/model-calls                 # per-call model, latency, reported tokens
```

## Manual ingestion and fetch safety

You create a run (a research question), then explicitly ingest public URLs into it. Each URL is fetched, reduced to
readable text (a stored *snapshot*), and split into passages with stable IDs (`S{source}-P{n}`) and exact offsets
into the snapshot. Every fetch outcome, success or failure, is stored and appears in the run's event log.
This is manual ingestion, **not** autonomous web research.

```bash
curl -X POST localhost:8000/runs -H "content-type: application/json" \
     -d '{"question": "What is SQLite and how is it used?"}'
curl -X POST localhost:8000/runs/1/sources -H "content-type: application/json" \
     -d '{"url": "https://en.wikipedia.org/wiki/SQLite"}'
curl localhost:8000/runs/1                   # run info
curl localhost:8000/runs/1/sources           # sources (no snapshot text; counts and hashes)
curl localhost:8000/runs/1/passages          # passages; optional ?source_id=
curl localhost:8000/runs/1/events            # audit trail
```

Ingestion responses: `201` new source, `200` duplicate (same URL, or same extracted content, already in the run),
`404` unknown run, `413` too large, `415` unsupported content type, `422` blank/invalid input, out-of-range ID,
blocked URL or no readable text, `502` any other fetch problem (including the target answering 404/500), `504` timeout. Failures are recorded in `fetch_attempts` and the event log.

Fetch safety: http(s) only, ports 80/443, no embedded credentials, no localhost/private/link-local/loopback/
multicast addresses (all DNS answers are checked; IPv6 forms that embed an IPv4 address, such as NAT64 and 6to4, are checked as that IPv4 address), every redirect hop is validated before it is requested.
Limits (env/`Settings`): connect/read/total timeouts, 5 redirects, 2 MB body. Database path: `DATABASE_PATH`
(default `data/deepdive.db`, git-ignored).

## Limitations

- DNS rebinding: the host is validated by resolving it, then the HTTP client resolves it again on connect.
- Time limits are best effort. DNS lookups are bounded (`FETCH_DNS_TIMEOUT`, 5 s) and a timeout is recorded as a failed
  `timeout` attempt, but the OS lookup cannot be cancelled: it finishes on an abandoned daemon thread (at most 8 at a
  time). `FETCH_TOTAL_TIMEOUT` is checked at each redirect hop and between body chunks, so one blocked socket operation
  can overrun it by up to the connect/read timeout. A research run's `MAX_ELAPSED_SECONDS` is checked only before each
  model request, search and page fetch, so the last step can overrun it by up to that step's own timeout.
- Environment proxies (`HTTP_PROXY`, ...) are ignored by the page fetcher (`trust_env=False`); fetching from behind a
  mandatory proxy is unsupported until a proxy design is reviewed.
- Model requests use the shared Mesh client with SDK retries disabled and a 60 s timeout (`MESH_TIMEOUT`); a transient
  Mesh error fails that step (and the run) instead of being retried invisibly.
- Ports other than 80/443 are refused, which also blocks some legitimate sites.
- URL fragments are not stripped, so `page#a` after `page` is fetched again and then deduplicated by content hash.
- Passage "context" means adjacent short paragraphs are packed into one passage (up to `PASSAGE_TARGET_CHARS`); there is
  no separate context field or overlap between passages.
- Extraction is rule-based: nav/footer/aside/script/style are dropped by tag, but other boilerplate (for example
  Wikipedia navigation boxes) can still appear as passages. No JavaScript rendering, no PDFs, no robots.txt handling.
- Research jobs run in-process (FastAPI background task); a server restart marks an in-flight run `failed`.
- A fetched page is a snapshot at retrieval time; re-ingesting a known URL in the same run is skipped.

## Layout

- `backend/`: `main.py` app, `config.py` settings, `mesh_client.py`, `db.py` and the schemas, `web.py` safe fetcher, `extract.py`, `ingest.py`, `search.py`, `llm.py`, `budget.py`, `graph.py` (the LangGraph job), `claims.py`, `correction.py`, `report.py`, `retry.py`, `temporal.py`, `api/` endpoints, `agents/` (planner, researcher, drafter, verifier, coverage, reviser, synthesizer, relevance)
- `frontend/`: React + TypeScript UI (run page, report, evidence inspector, coverage, history, usage)
- `evaluation/`: datasets, rubrics, harnesses, review sheets, recorded results (`results/`)
- `scripts/`: live and acceptance tooling; `scripts/ui_acceptance/owned.mjs` stops only processes it launched
- `tests/`: pytest; `docs/`: design notes, per-stage reviews, `DEMO.md` (recorded-run demo script)
- `data/`: local SQLite files (git-ignored; they hold fetched page text)

The Mesh client lives in `backend/mesh_client.py`; use `get_mesh_client()` everywhere. Base URL:
`https://api.meshapi.ai/v1`, key from `MESH_API_KEY`.

## Documentation

Start with `docs/PLAN.md` (architecture), `docs/PHASE6A.md` (evaluation, failures and fixes), `docs/DEMO.md`, [PUBLIC_RELEASE.md](PUBLIC_RELEASE.md) (what the public export withholds) and [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md).

UI progress can be exercised without credits with `scripts/ui_sim_backend.py` (SIMULATED backend: real API and database code, scripted events, no model/search/fetch calls; see docs/PHASE5_REVIEW.md).
