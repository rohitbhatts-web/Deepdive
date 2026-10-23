# Phase 1 review (audit against the original requirements)

Method: read the implementation and tests directly; probe edge cases against the real code; mutation-test the suite
(break one behaviour at a time, see whether any test fails); run a live acceptance run. Passing-test counts were not
used as evidence. Phase 2 code exists in the tree and was left in place; where it touches Phase 1 files this is noted.

Evidence labels: **FIXTURE** = deterministic test with mock HTTP/DNS/temp DB. **LIVE** = real server and real network.
**MUT** = a mutation that makes the named test fail (36 mutants run; list at the end).

## Requirement table

| # | Requirement | Implementation | Evidence / test | Result |
|---|---|---|---|---|
| 1 | Tables: runs, subquestions, searches, sources, passages, fetch attempts, audit events | `backend/db.py` `SCHEMA` | FIXTURE `test_db.py` (all tables written); `subquestions`/`searches` only smoke-tested in Phase 1, used by Phase 2 | PASS |
| 2 | Transactions | `db.tx` (explicit `BEGIN IMMEDIATE`/rollback); `ingest.py` wraps source+passages+attempt+event in one | FIXTURE `test_transaction_rolls_back_on_error`; MUT "rollback commits" caught | PASS |
| 3 | Foreign keys enforced | `PRAGMA foreign_keys = ON` in `db.connect` | FIXTURE `test_foreign_keys_enforced` (4 tables); MUT caught | PASS |
| 4 | Safe repeated initialisation | `CREATE ... IF NOT EXISTS` + `user_version` | FIXTURE `test_persists_across_connections`; **added** `test_reinitialising_and_upgrading_an_older_schema_keeps_data` (v1 to v2 upgrade keeps rows). LIVE: server restarted, data identical | PASS |
| 5 | Local DB files ignored by Git | `.gitignore` (`*.db`, `*.sqlite*`, `-wal/-shm/-journal`) | `git check-ignore` confirmed for `.db`, `.db-wal`, `.db-shm`, `.sqlite3` | PASS |
| 6 | Run isolation | every query filters by `run_id`; `UNIQUE(run_id, ...)` | FIXTURE `test_runs_are_isolated`, `test_same_url_in_different_runs...`; **added** cross-run `source_id` filter test. LIVE: run 2 ingested the same URL independently, disjoint passage IDs. MUT caught | PASS |
| 7 | HTTP(S) retrieval with connect/read timeouts | `web.py` `Fetcher` | FIXTURE timeout mapping tests. **Gap found:** nothing checked the timeouts were applied (MUT "timeouts removed" survived); **added** `test_configured_timeouts_are_applied_to_the_http_client` (now caught). Real slow servers were not exercised | PASS |
| 8 | Redirect count limit | `fetch_max_redirects`, manual loop | FIXTURE `test_redirect_limit`; MUT caught | PASS |
| 9 | Response-size limit | declared length + streamed/decoded count | **Gap found:** streamed-without-Content-Length path survived mutation (the declared-size check masked it). **Added** streamed and gzip-bomb tests (now caught) | PASS |
| 10 | Reject credentials, localhost, loopback, private, link-local, other non-public | `web.validate_url`, `_is_public` | FIXTURE ~30 parametrised URLs. **Defect fixed:** `http://[64:ff9b::7f00:1]/` (NAT64 form of 127.0.0.1) was accepted. LIVE: `http://127.0.0.1/` rejected by the address rule on default port 80. MUT: each rule caught except three equivalent ones (see below) | PASS (after fix) |
| 11 | Validate redirect destinations before fetching | `Fetcher.fetch` validates every hop | FIXTURE: private, loopback, non-http scheme, bad port, credentials, whitespace, later hop in a chain; each asserts the unsafe hop was never requested. MUT caught | PASS |
| 12 | Account for DNS resolution; document remaining limits | resolver checks every answer; docstring + README | FIXTURE mixed public/private answers rejected. **Defect fixed:** over-long IDNA label raised `UnicodeEncodeError` (would be HTTP 500). Limits documented: rebinding; **newly documented:** DNS lookup has no timeout, proxy env vars, port restriction | PARTIAL (rebinding cannot be closed without pinning the connection IP) |
| 13 | Readable HTML text; plain text | `extract.py` (stdlib `html.parser`) | FIXTURE `test_extract.py`; MUT "script not skipped" caught. LIVE: Wikipedia page gave 27,455 chars. Quality limit: navigation boxes appear as passages (e.g. `S1-P3`) | PASS (quality limits documented) |
| 14 | Unsupported content types and failures logged honestly | `ingest._failed`, `fetch_attempts` | **Gap found:** Phase 1 tests only checked events and HTTP codes, not the attempt rows (MUT "attempt not persisted" was caught only by a Phase 2 test). **Added** `test_every_outcome_is_persisted_as_attempt_and_event` (exact rows, status codes, reasons, IDs) | PASS |
| 15 | Split on paragraph/sentence boundaries with surrounding context | `extract.split_passages` | FIXTURE exact-slice, size and sentence-boundary tests. "Surrounding context" is implemented as packing adjacent short paragraphs; there is no overlap or separate context field | PARTIAL (interpretation of an ambiguous requirement) |
| 16 | Exact passage text and offsets in the snapshot | `db.insert_source` | FIXTURE slice-equality test, MUT (2 variants) caught. LIVE: 32/32 passages equal `snapshot[start:end]` read straight from SQLite, ordered, non-overlapping, IDs `S1-P0..S1-P31` | PASS |
| 17 | Deduplicate within a run, keep provenance | URL check before fetch, content hash after; `UNIQUE` constraints | FIXTURE `test_repeated_ingestion...`. Provenance: the duplicate URL is kept in `fetch_attempts` (linked to the original `source_id`) and an event; no second source row. LIVE: same URL gave 200 `duplicate_url`; `#History` variant gave `duplicate_content` (but was fetched again, fragments are not stripped). **Defect fixed:** two concurrent requests could raise `IntegrityError` (500); now reported as a duplicate (FIXTURE race test) | PASS (after fix) |
| 18 | No LLM for extraction or splitting | `web.py`, `extract.py`, `ingest.py` | grep: no model client imports in these modules | PASS |
| 19 | Six endpoints with validation and response schemas | `api/runs.py` | FIXTURE `test_api_runs.py`. **Defects fixed:** whitespace-only question was stored as an empty string; run or source IDs above 2^63 gave HTTP 500; a URL with control characters gave HTTP 500. Now 422 | PASS (after fix) |
| 20 | Clear HTTP errors, documented | `FAILURE_STATUS`, README | FIXTURE asserts 201/200/404/413/415/422/502/504 mapping. README corrected (blank input and bad IDs are 422; a target answering 404 maps to 502) | PASS |
| 21 | URL ingestion not presented as autonomous research | README, endpoint docs | Read | PASS |
| 22 | Tests use temp DBs and controlled fixtures; live checks separate | `tests/conftest.py` | No test touches the network (the one real-resolver test fails locally before any lookup) | PASS |
| 23 | Tests protect real behaviour | mutation run | Baseline: 26 of 28 mutants caught, 2 survived (rows 7, 9). After additions all behavioural mutants are caught except 3 equivalent ones | PASS |
| 24 | Lint and tests through uv | `uv run ruff check .`, `uv run pytest` | Lint clean; 118 tests pass | PASS |
| 25 | One real public-page ingestion | live | LIVE acceptance run below | PASS |
| 26 | README accurate about limitations | `README.md` | Corrected as described in rows 12, 17, 20 | PASS (after edits) |

## Defects found and fixed

| Defect | Fix | Test |
|---|---|---|
| NAT64 address `64:ff9b::7f00:1` (embeds 127.0.0.1) passed the private-address rule | `_is_public` checks the embedded IPv4 of NAT64, 6to4, mapped; rejects Teredo | `test_ipv6_and_embedded_ipv4_forms...` |
| Over-long IDNA hostname label raised `UnicodeError` (500) | caught in `default_resolver` | `test_overlong_idna_label...` |
| URL containing control characters or whitespace raised `httpx.InvalidURL` (500) | rejected in `validate_url`; `InvalidURL` also caught as a fallback | `test_whitespace_and_control_characters_rejected`, redirect variant |
| Whitespace-only question stored as `''` | strip before length validation (`StringConstraints`) | `test_blank_inputs_and_out_of_range_ids_are_422_not_500` |
| Run/source IDs above SQLite's 64-bit range gave 500; `0` and negatives gave 404 | `Path/Query(ge=1, le=2**63-1)` on all run routes (also applied to the Phase 2 routes in `api/research.py`) | same test |
| Concurrent duplicate ingestion could raise `IntegrityError` (500) | handled in `ingest_url`, reported as duplicate | `test_concurrent_duplicate_is_reported_not_crashed` |

Behaviour change to note: `GET /runs/0` and `/runs/-1` now return 422 instead of 404.

## Live acceptance run (LIVE)

Isolated DB in the scratchpad (`acceptance.db`), real uvicorn on port 8765, real network, no AI calls, server logs
contained no errors. Script: `acceptance.py` in the session scratchpad.

- Created run 1; ingested `https://en.wikipedia.org/wiki/SQLite`: HTTP 201, title "SQLite - Wikipedia", 27,455
  snapshot chars, 32 passages, 2 events.
- Stopped and restarted the server: sources, passages and events were identical (compared in full).
- Read the SQLite file directly: 32/32 passages equal `snapshot[start:end]`; ordered, no overlaps; IDs `S1-P0`..`S1-P31`.
- Repeated the same URL: HTTP 200 `duplicate_url`, source 1. `URL#History`: HTTP 200 `duplicate_content`, source 1.
  Still 1 source and 32 passages.
- Run 2, same URL: HTTP 201, new source 2, 32 passages with IDs disjoint from run 1; run 1 unchanged; run 2 events
  only `run_created`, `source_ingested`; filtering run 2 by run 1's source ID returned nothing.
- `http://127.0.0.1/` (default port 80): HTTP 422, reason `host '127.0.0.1' resolves to non-public address 127.0.0.1`
  (address rule, not the port rule). Persisted as fetch attempt 5 (`outcome=blocked`, no source) with a matching
  `fetch_failed` event.

## Mutation results

Mutants (each applied alone, the suite run, then reverted): private-address check, redirect hops, port rule,
credentials, localhost name, only-first-DNS-answer, streamed size, declared size, total deadline, redirect cap,
httpx timeouts, timeout mapping, content-type filter, relative redirect, foreign keys, rollback, run filter,
passage text, passage offsets, URL dedup, content dedup, attempt persistence, event persistence, empty-text check,
paragraph splitting, script skipping, duplicate status code, blocked status code, NAT64 rule, mapped/6to4 rule,
Teredo rule, control-character check, IDNA handling, race handling, ID bound, input stripping.

Surviving after the additions (all behaviourally equivalent, not test gaps):
- *content-hash dedup off*: the `UNIQUE(run_id, content_hash)` constraint plus the race handler yields the same result.
- *mapped/6to4 rule off* and *Teredo rule off*: Python 3.12's `is_global` already rejects those addresses; the explicit
  rules are defence in depth against version differences.

## Remaining limitations (unresolved)

- DNS rebinding between validation and connect; DNS lookup has no timeout; proxy variables bypass the address checks.
- Real network timeouts were not observed live (only configuration and exception mapping are tested).
- Boilerplate such as Wikipedia navigation boxes is stored as passages; no JavaScript rendering, PDFs or robots.txt.
- Fragments are not stripped, causing a redundant fetch before content-hash dedup.
- The API does not expose the snapshot text; offsets can be checked only against the database (inspection by API is a Phase 5 concern).
- Python 3.12 only was tested.

## Follow-up (fetch gaps closed after this review)

Environment proxies are now ignored (`trust_env=False`), DNS lookups are bounded by `fetch_dns_timeout` and recorded as
`timeout` failures, and the total deadline is also checked at each redirect hop. Tests: `tests/test_fetch_hardening.py`.
Rebinding remains documented and open; the DNS thread cannot be cancelled (see `backend/web.py` docstring).

## Verdict

Phase 1 meets its requirements: 24 PASS and 2 PARTIAL (rows 12 and 15; neither blocks later phases) after fixing
6 defects. It was ready for Phase 2, and Phase 2 is already built on it; its status is unchanged by this audit
except for the ID validation applied to its routes.
