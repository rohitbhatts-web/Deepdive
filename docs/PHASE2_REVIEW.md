# Phase 2 review

Scope: Phase 2 against `docs/PLAN.md` plus two Phase 1 fetch gaps. All evidence below is **FIXTURE** (scripted Mesh,
scripted search or `MockTransport`, temp DB). **No live Phase 2 run has happened**: the local `.env` lacks real keys.
After the fixes below, the targeted checks (12 mutants, one per fix) all make a test fail.

## Requirement table

| Requirement | Implementation | Evidence | Status |
|---|---|---|---|
| Planner and Researcher are distinct and run as LangGraph nodes | `graph.py` `StateGraph`; `agents/planner.py`, `agents/researcher.py` | `test_planner_and_researcher_execute_as_langgraph_nodes` streams node order `planner, researcher, researcher, finish`; flow tests | PASS |
| Every model call uses the shared Mesh client | `mesh_client.get_mesh_client` -> `llm.LLM`; `services.build_services` | `test_only_the_shared_client_module_constructs_openai_clients` (source scan: one constructor, one `chat.completions` call site) | PASS |
| Mesh client does not retry invisibly or hang | **Defect fixed:** the OpenAI SDK defaults (2 hidden retries, 600 s timeout) bypassed the budget and `model_calls`. Now `max_retries=0`, `MESH_TIMEOUT=60` | `test_mesh_client_has_no_hidden_retries_and_a_bounded_timeout` | PASS (after fix) |
| Structured output validated, retries bounded | `LLM.call`: Pydantic validation, one corrective retry, then `AgentOutputError` | `test_invalid_output_retried_once_then_succeeds`, `test_two_invalid_outputs_raise` | PASS |
| Researcher selects only URLs the search returned | exact-match check against this subquestion's hits; rejects logged as `selection_rejected` | `test_hallucinated_url_is_rejected` | PASS |
| Sources linked to subquestions, isolated per run | `research_selections` (run, subquestion, search, source, fetch attempt) | **added** `test_sources_stay_linked...` (two runs, shared page linked to two subquestions via `duplicate_url`, SQL check that no selection points at another run's source) | PASS |
| Limits count retries and failed requests | `Budget` charges before each model request/attempt, search and fetch | `test_model_request_budget_enforced`; **added** failed model request, failed searches and a blocked fetch each consume budget | PASS |
| Token limit survives a provider that reports no usage | **Defect fixed:** missing `usage` counted as 0, so `MAX_TOTAL_OUTPUT_TOKENS` could never trigger. Now assumes `MAX_OUTPUT_TOKENS` per call; stored usage stays NULL | `test_missing_usage_does_not_disable_the_token_limit` | PASS (after fix) |
| Duplicate starts, failures, restarts have correct stored status | atomic `claim_run_for_research`; `finish` node; `execute_research` catch-all; startup `mark_interrupted_runs` | existing duplicate-start, failure and budget tests; **fixed** crash left subquestions `researching`/`open`; **fixed** restart recovery had no audit event and left subquestion statuses; **added** `test_unexpected_crash...`, `test_startup_marks_interrupted_runs...` (real lifespan) | PASS (after fixes) |
| Search provider asks for no generated answers | Tavily body sets `include_answer`, `include_raw_content`, `include_images`, `auto_parameters` to false (`basic` depth, 1 credit) | `test_tavily_request_disables_generated_answers_and_extras` (previously only the default, not explicit) | PASS |
| Credentials never logged or returned | **Defect fixed:** provider/SDK error text and unexpected exceptions were stored verbatim in events, `model_calls.error` and `stop_reason`. `redact.py` now scrubs configured keys | `test_secrets_never_reach_storage_or_api_responses` (3 scenarios; scans every API response and the raw SQLite + WAL bytes) | PASS (after fix) |
| Missing configuration is an error, not fake results | `services.missing_configuration`, 503, run stays startable | `test_missing_configuration_gives_503...` | PASS |
| Statuses and associations (quality fix) | see the section below | `test_phase2_quality.py` (statuses, associations, PDF skipping, query sanitising, structured output, request IDs) | PASS |
| Model IDs verified against Mesh before a run | `services.model_catalog`/`check_models`. **Defect found live:** Mesh `/models` is a bare JSON list, so the SDK's `models.list()` crashed and would have blocked every run; now read raw via the shared client, also rejecting non-chat models | catalog fetched against real Mesh (1,286 entries, no inference); fixture tests for both response shapes, unavailable and non-chat models | PASS (catalog read live; no inference yet) |
| Live end-to-end research | n/a | not run (no keys) | NOT TESTED |

## Live acceptance (one run, 2026-10-20; real Mesh + Tavily + web)

Model: `qwen/qwen3-235b-a22b-instruct-2507` for both roles. Limits: 2 subquestions, 2 searches, 7 model requests, 4 page
fetches, 4,000 output tokens, 150 s. Question: SQLite vs PostgreSQL differences and when each is appropriate.

| Measure (from stored data) | Result |
|---|---|
| Final status | `researched`, no stop reason; wall time 105 s (budget clock: 103 s of 150) |
| Model calls | 7 of 7 allowed; 2 failed validation on the first attempt and were retried (planner: missing fields; researcher: extra text after the JSON) |
| Tokens reported by Mesh | 3,480 in / 770 out; actual cost about $0.0004 (pre-run estimate about $0.002) |
| Tavily | 2 searches = 2 credits |
| Fetches | 4 attempts: 3 ingested (54 passages), 1 `unsupported_content_type` (a PostgreSQL concurrency PDF) |
| Persistence | server restarted on the same DB: API counts identical, DB digest unchanged, 0 passage-offset mismatches |
| Credentials | 0 occurrences of either key in the DB, WAL or server logs; 0 error lines in logs |

Relevance (read from the stored passages): subquestion 2 (use cases) is reasonably covered by two vendor comparison
blogs (Tinybird, Strapi). Subquestion 1 (architecture/concurrency/transactions) is weakly covered: its only ingested
source is one SQLite forum thread about a user's lock problem, which never mentions PostgreSQL; the one authoritative
PostgreSQL source was a PDF (unsupported in Phase 1). The Tinybird page does answer subquestion 1 but is linked only to
subquestion 2. No primary documentation (sqlite.org / postgresql.org docs) was ingested.

Diagnosis: (1) the model added `site:` operators to queries; Tavily honoured the first (4 of 5 hits were sqlite.org
forum posts) and ignored the second (no Stack Overflow results), so operators are unreliable and steered results toward
anecdotal pages; (2) `researched` means "at least one page ingested", not "requirements covered" (Phase 4 coverage
routing is meant to catch this); (3) 2 of 7 replies needed a retry, which used the entire 7-request limit.

## Quality fix and second live run (same question, new database `data/live-test-2.db`; run 1 kept untouched)

Changes: plain-language queries (prompt forbids operators; code strips any that slip through and logs
`query_sanitized`); `.pdf` results are skipped and logged (`result_skipped`) before the model sees them; selection prefers
official/primary HTML documentation; subquestion statuses are now `evidence_collected` / `collection_incomplete` /
`failed` and the run ends as `collection_finished` (none of them mean covered or verified); a relevance check links an
already-collected source to another subquestion when its passages overlap that subquestion and the model cites passage
IDs (stored as `source_associations`, always `unverified`); responses use Mesh's documented
`response_format: json_schema` for models whose catalog entry has `supports_structured_output`, with Pydantic validation
and the same bounded retry; the live-test cap is 10 model requests including retries and the relevance call.

| Measure | Run 1 (before) | Run 2 (after) |
|---|---|---|
| Model requests (cap) | 7 of 7 | 6 of 10 (planner 1, researcher 4, relevance 1) |
| Validation failures / retries | 2 (planner missing fields; researcher extra text) | 0 |
| Structured output | no | yes (`json_schema`), recorded in the `research_started` event |
| Tokens reported by Mesh (in / out) | 3,480 / 770 | 3,629 / 668 |
| Elapsed (budget clock) | 103 s | 38 s (model latency 27 s) |
| Cost calculated from tokens x catalog price | $0.00039 | $0.00039 |
| Cost reported by Mesh usage API (`cost_usd`, per request ID) | not available (request IDs were not stored then) | $0.00039 (6 requests, $0.0003938) |
| Pre-run estimate (assumes 3,000 prompt tokens per call; input is not hard-capped) | about $0.002 | about $0.003 |
| Tavily | 2 credits | 2 credits |
| Fetches | 4 attempts, 1 failed (PDF) | 3 attempts, 0 failed, 1 duplicate URL |
| Sources collected (passages) | 3 (54) | 3 (51) |
| Primary documentation retrieved | none (one sqlite.org forum thread) | none |
| Associations | n/a | 6: 4 from search selection, 2 from the relevance check (unverified) |

The two cost figures agree to within rounding ($0.00039 calculated vs $0.0003938 reported); the reported figure comes from
Mesh's usage API, the calculated one from tokens and the catalog price, so they are not independent of the same token counts.

**Evidence by subquestion (stored passage IDs; judged by reading the passages, associations are unverified)**

- Subquestion 1 (architecture, concurrency, scalability, transactions). Run 1: weak, one SQLite forum thread (`S1-P0`) that
  never discusses PostgreSQL. Run 2: three direct comparisons. Architecture: `S2-P10` (embedded library, single file, no
  server vs client-server process). Concurrency and transactions: `S1-P2` (MVCC, readers do not block writers; SQLite
  single-writer serialization and journaling/WAL). Multi-user scalability: `S3-P3` (SQLite lacks multi-user access
  control), `S1-P5` (comparison table). Scalability is covered qualitatively only; no figures or limits.
- Subquestion 2 (use cases). Similar in both runs: `S3-P14` (PostgreSQL for ACID/concurrency, SQLite for embedded and IoT),
  `S2-P10`, `S1-P5`. Named application categories (mobile apps, embedded/IoT, SaaS/multi-user) appear; named products and
  team-size or data-volume guidance are thin.
- One association is loosely justified: the check linked `S1` to subquestion 2 citing `S1-P2` and `S1-P5`, but `S1-P2` is
  mainly about concurrency and transactions (subquestion 1's topics). It stays labelled unverified.

**What did not improve.** No primary documentation (official SQLite/PostgreSQL docs) was retrieved in either run. The
plain queries returned only secondary articles: none of the 10 returned hits was primary documentation, so the selector had
nothing better to pick. The model's queries did not follow the "say 'official documentation' in words" instruction.
All 3 sources are blogs or community posts, and one (Tinybird) is a vendor with its own interest in the comparison. The PDF
skip rule was not exercised live (no PDFs appeared).

**Remaining collection gaps.** Primary sources; quantitative scalability evidence; named real-world applications;
independence of sources. Run 2 has one run per question, so the retry-rate improvement (2 of 7 to 0 of 6) is an observation, not a
measured rate.

**Readiness.** The collection pipeline is reliable and cheap enough to build Phase 3 on (no failures, about $0.0004 per run,
39 s, persisted and inspectable). Evidence quality is adequate for drafting and verifying claims from secondary sources, but
the primary-source objective is unmet; that belongs to the Phase 4 coverage router (targeted corrective queries), not to
more prompt tuning now.

## Phase 1 fetch gaps (closed)

| Gap | Fix | Test |
|---|---|---|
| Environment proxies honoured by the page fetcher | `httpx.Client(trust_env=False)` | `test_http_client_ignores_environment_proxies` |
| DNS lookup unbounded | `bounded_resolve`: daemon thread, `FETCH_DNS_TIMEOUT` (5 s), max 8 in flight; recorded as failed `timeout` attempt and event; API 504 | `test_dns_lookup_is_bounded...`, `..._persisted_as_failed_attempt_and_event`, redirect-hop and API variants, slot exhaustion |
| Total deadline checked only while reading the body | also checked at the start of every redirect hop | `test_total_deadline_is_enforced_between_redirect_hops` |

Honest limits: the OS DNS call cannot be cancelled (it finishes on an abandoned daemon thread); a blocked socket read
can overrun the total timeout by up to the read timeout; DNS rebinding remains open. Documented in `backend/web.py` and README.

## Remaining gaps

- No live run; real Mesh behaviour is unverified: `max_tokens` vs `max_completion_tokens` acceptance, whether `usage` is
  returned, and whether the model replies with plain JSON as prompted.
- A transient Mesh error (429/5xx) fails the run; there is no bounded explicit retry.
- Research jobs live in the server process; there is no resume (an interrupted run is marked failed).
- `MAX_ELAPSED_SECONDS` can be overrun by one in-flight step.
- Query/URL quality of the agents is untested and unmeasured.
