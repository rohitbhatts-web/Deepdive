# Phase 3: atomic claims and two-stage verification

Status: **implemented, tested with fixtures, and exercised once live** (below). Five claims is far too few to say
anything about accuracy, and no accuracy claim is made anywhere in this repository.

## What exists

| Requirement | Where | Evidence (FIXTURE = deterministic, ORCHESTRATION = scripted model replies) |
|---|---|---|
| Migrations (schema v4), preserve Phase 1/2 rows | `db.connect`, `schema_claims.py` | FIXTURE `test_schema_v3_database_gains_phase3_tables_without_losing_rows` |
| Claims immutable, never deleted, versioned | triggers on `claims`, `claim_evidence`, `verification_results`; `parent_claim_id`, `version` | FIXTURE: edit and delete attempts rejected; a new version starts `drafted` and does not inherit a verdict |
| Run isolation | triggers (claim/subquestion/parent/evidence/result); `get_claim` filters on `run_id`; API 404 across runs | FIXTURE: cross-run reference, claim, parent and result-evidence tests |
| Drafter (distinct prompt, validated output, bounded input and claims) | `agents/drafter.py` | ORCHESTRATION: offered passages recorded in a `draft_input` event, `MAX_CLAIMS` truncation, unknown subquestion discarded and logged |
| Integrity check before any verifier call; stored apart from semantic verdicts | `claims.check_integrity`; `verification_results.stage` | FIXTURE: fabricated, cross-run, empty, short, mismatched, changed-digit and changed-case quotes; exact vs normalized labelled; integrity failures never reach the verifier |
| Documented, conservative normalization | `claims.py` docstring: whitespace, typographic quotes, zero-width characters only | FIXTURE: dashes, case and digits are NOT normalized |
| Semantic verifier (distinct prompt, configurable model, whole-claim checklist) | `agents/verifier.py` | ORCHESTRATION: compound claim, wrong date with a real quote, forecast stated as fact, contradiction, irrelevant quote |
| A failed call is an error, never support; uncited or self-flagged verdicts do not pass | `verify_claim` | ORCHESTRATION: model failure, unparseable output after one retry, verdict citing nothing shown (`error`), `supported` with listed gaps stored as `partially_supported` (model verdict kept in details) |
| Report eligibility | `claims.claim_state` | FIXTURE: only integrity-passed + latest semantic `supported` is eligible |
| LangGraph draft, integrity, verify; verify-only entry; duplicates and restarts | `graph.py`, `api/research.py` (`/verify`, `?verify=true`) | ORCHESTRATION: state transitions; 409 on duplicate or uncollected runs (including sources but collection still running); 503 listing only what verification needs; skipped verification stays startable; crash and restart end `failed` |
| Budgets include every retry | `Budget` charged per model request | ORCHESTRATION: cap reached mid-verification leaves later claims `awaiting_verification` with the stop reason stored |
| `response_format: json_schema` actually sent | `LLM.call`; recorded per call in `model_calls.structured_output` | ORCHESTRATION: request kwargs inspected for drafter and verifier; an unflagged model gets none |
| Challenge claims labelled as test input | `claims.add_challenge_claim` (`origin='controlled_challenge'`, provenance `test_input: true`) | ORCHESTRATION test; used by `scripts/live_verify.py --challenge` |

Targeted mutation check (13 deliberate breakages, each reverted): 12 were caught by a test. One survived (verification
allowed on a run with sources whose collection was still running); a new test now catches it. Also fixed along the way:
`source_passages` lacked `source_id` (found by the first pipeline test run), and a race where an abandoned DNS thread
released the wrong semaphore (it made one Phase 2 test flaky).

## Design decisions to review

- A `supported` verdict whose own checklist lists a mismatch or missing aspect, or which lists gaps or conflicts, is stored
  as `partially_supported`; the original verdict and the reason are kept. This is a deterministic guard on model sloppiness.
- Evidence cited by the verifier must be among the passages it was shown; otherwise the result is `error`.
- A minimum quote length (`MIN_QUOTE_CHARS`, 15) is a policy choice: a very short quote matches trivially.
- Verification is one job per run. Re-running, re-judging and claim revision are Phase 4.

## Limitations

- Verdict quality is unmeasured. Scripted tests prove orchestration only; the labelled evaluation is Phase 6.
- The drafter sees at most `DRAFT_INPUT_CHARS` of passages chosen by word overlap, so it can miss relevant text.
- The verifier sees the cited passage plus one neighbour on each side, truncated; support that depends on text elsewhere on
  the page can be judged `unsupported`.
- One verifier call per claim and no second opinion; a single model's blind spots are not detected.
- Atomicity of drafted claims is requested in the prompt and not machine-checked.

## Live acceptance (one run, real Mesh, no search or fetching)

Setup: drafter `qwen/qwen3-235b-a22b-instruct-2507` ($0.09 / $0.10 per 1M tokens), verifier `anthropic/claude-haiku-4.5`
($1 / $5), both flagged `supports_structured_output` in the Mesh catalog and sent `response_format: json_schema`
(recorded per call). Input: a COPY of the finished collection (`data/live-test-2.db`, the source is untouched). Limits:
3 claims, 6 requests including retries, 120 s, 1,500 output tokens per call, 4,000 total, 6,000 characters of passages for the
drafter. The pre-run cost figure (about $0.04) was an estimate; actual usage is below.

| Claim | Origin | Evidence (id:passage) | Integrity | Semantic verdict | Eligible |
|---|---|---|---|---|---|
| #1 SQLite uses a file-based, embedded architecture with no server process | drafted | 1:S3-P14 | **failed** (result 1): quote occurs in no stored passage | none (never sent to the verifier) | no |
| #2 [claim text withheld in the public export: it restates a rights-reserved page closely] (PostgreSQL's server process model) | drafted | 2:S1-P1 | passed (2) | supported (4) | yes |
| #3 SQLite is ideal for prototyping, mobile, and local apps, while PostgreSQL is ideal for production, web apps, and analytics | drafted | 3:S1-P5 | passed (3) | supported (5) | yes (see mistakes) |
| #4 PostgreSQL ... can take up to **100 MB** per connection | controlled_challenge, `test_input: true` | 4:S3-P12 (real quote says 10 MB) | passed (6) | contradicted (7) | **no** |
| #5 SQLite allows **multiple processes** to write at the same time | controlled_challenge, `test_input: true` | 5:S1-P1 (real quote: "only one writer") | passed (8) | contradicted (9) | **no** |

Claims #4 and #5 are deliberately altered test inputs with their real quotes retained (a subtle quantity change and a status
flip). They are stored with provenance `test_input: true`, and the rule that test inputs are never report-eligible, even if
`supported`, was missing: it is now enforced in `claims.get_claim` with a regression test (it fails if the rule is removed).

Manual comparison with the stored passages:

- **#1 integrity failure, correctly caught (not a semantic verdict).** The drafter's quote ("Architecture / SQLite / File Based (Embedded)") does not exist; the real
  table text in S1-P5 is "Embedded, file-based". The deterministic integrity check caught a misquoted table; the verifier never saw it, so there is no semantic verdict. The
  claim itself would probably have been supportable from S1-P1/S1-P5, but claims are immutable and revision is Phase 4.
- **#2 correct.** S1-P1 describes PostgreSQL's client-server design and its long-running server process, which is what the claim says (page text withheld in
  the public export).
- **#3 arguable.** The quote is genuine and the claim matches the "Ideal for" table row, but that row is one article's
  recommendation. The claim states it in its own voice, which the drafter prompt forbids, and the verifier marked
  entity/attribution `ok`. A stricter reading would give `partially_supported`.
- **#4, #5 correct.** Both contradictions were identified, #4 with the exact 10 MB vs 100 MB mismatch.

Usage: 5 model requests of the 8 allowed (main step 3 of 6; challenge 2 of 2), 0 failed, 0 retries; 12,584 prompt and 930
completion tokens as reported by Mesh; main run 32 s of the 120 s deadline. Cost, kept separate:
calculated from reported tokens x catalog prices $0.0143; reported by Mesh's usage API (`cost_usd` per request ID) $0.0136.
Observation only, no cause established: the Qwen call matched its calculation exactly; two of the four Haiku calls were
reported at exactly 0.90x their calculated cost and the other two (the challenge step, a separate process) at 1.00x.

## Observed mistakes and open issues

- Drafter: one fabricated table quote (#1); one opinion stated as fact (#3).
- Verifier: no clear mistake on #2, #4, #5; #3 is the arguable one. Five claims is far too few to estimate an error rate.
- The pipeline verifies only claims it drafts in that job; challenge claims must be verified explicitly (as the script does).
- Not yet exercised live: a `partially_supported`, `unsupported` or `error` outcome, the downgrade rule, and retries.

## Phase 3 audit (follow-up)

Schema is now v5 (adds `verification_result_passages`). The original live databases were never opened by the new code:
`data/live-verify.db`, `live-test-2.db` and `live-test.db` have identical content digests before and after the recheck.

Defects found and fixed (each with a test; each test fails if the fix is removed):

| Defect | Fix |
|---|---|
| Live claim #3 (a source's recommendation stated as plain fact) was `supported` | Drafter prompt separates descriptions from recommendations/forecasts (attribute the latter) and requires splitting contrasts; verifier gets an explicit `attribution` aspect with the rule "correct attribution is not penalised, not every recommendation is unsupported"; any listed mismatch downgrades `supported` |
| A neighbour passage the verifier relied on was not stored as evidence | New `verification_result_passages` (roles `claim_evidence` / `neighbour_context`, same-run trigger, immutable); `citations` on each claim resolve to exactly those passages |
| `supported` resting only on a neighbour passed | Downgraded to `partially_supported` (`support_only_in_context`) |
| Two quotes from one passage linked only one evidence row | All rows linked |
| Superseded (stale) claim versions could stay eligible | One rule, `claims.is_report_eligible`: natural (not test input), integrity passed, latest semantic `supported`, no newer version. `list`, `detail`, `summary`, `eligible_claims()` and `?eligible_only=true` all use it |
| `status` blurred integrity and semantic outcomes | `integrity_verdict` and `semantic_verdict` are separate fields; `/verification` has `by_integrity` and `by_semantic` (a never-judged claim counts under `none`, not as a semantic rejection) |
| **Found by the live recheck:** a correct `unsupported` verdict that cited nothing was stored as `error` | `unsupported` may cite nothing; `supported`/`partially_supported`/`contradicted` without a shown citation remain `error` |

Mutation check on these rules: 10 deliberate breakages, all caught (the cross-run trigger mutant was crude and broke the
schema script, so the dedicated trigger test is what covers that rule). Scripted verdict tests show how the pipeline handles
a verdict; they say nothing about model accuracy.

### Narrow live recheck (isolated copy, verifier only, no search or fetching)

3 requests of the 4 allowed, 14 s of 90 s, `claude-haiku-4.5` with `response_format: json_schema` on every call. All three
claims are labelled test inputs (`controlled_challenge`, `test_input: true`), so none is report-eligible by rule.

| Claim | Evidence | Integrity | Model's own aspects | Stored result |
|---|---|---|---|---|
| #6 original claim #3 re-submitted unchanged | evidence 6: S1-P5 | passed (10) | attribution = mismatch | **partially_supported** (11): the verifier said "supported" but listed the attribution mismatch, so the guard downgraded it |
| #7 "The guide's comparison table lists prototyping, mobile apps and local apps as the ideal uses for SQLite." | evidence 7: S1-P5 | passed (12) | attribution = ok | **supported** (13): accurate attribution is not penalised |
| #8 "The first stable release of SQLite came out in 2004." + irrelevant quote from S1-P1 | evidence 8: S1-P1 | passed (14) | date = missing | **error** (15) as stored by the code at the time; the model's verdict was `unsupported` ("no information about when SQLite's first stable release occurred"). Fixed afterwards (last row above), not re-run live, so the stored row stays `error` |

Manual comparison with the passages: #6 is the same S1-P5 table row quoted in the first run; the table is the author's
"Ideal for" judgement, so the downgrade is consistent with the intended rule. #7 restates the same row with attribution and
one subject. #8's quote is genuine and unrelated, and the model's reasoning matches the passages. The model's #6 verdict
alone ("supported") still missed the attribution issue even though its own checklist flagged it; the deterministic
downgrade rule, not the model, produced the final outcome. Three inputs support no accuracy rate.

Cost for the recheck, kept separate: calculated from reported tokens $0.0120 (3,136/195, 3,123/165, 3,073/171 tokens);
reported by Mesh's usage API $0.0108. All three calls were reported at exactly 0.90x the calculation; no cause established.

### Unresolved

- Semantic quality is still unmeasured (Phase 6). The attribution aspect depends on the model filling it in honestly.
- No machine check that drafted claims are atomic or attributed; only prompts and the verifier's judgement.
- `partially_supported`, `contradicted` and the `unsupported` fix were exercised live only through test inputs, and the
  `unsupported`-cites-nothing fix has no live confirmation.
- Claim #1's misquote and #3's lack of attribution cannot be repaired until Phase 4 introduces claim revision.
