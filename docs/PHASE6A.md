# Phase 6A: evaluation datasets and harness (prepared, paid evaluation paused)

> **Public export (`public-export-v1`).** Figures in this file about the claim-evidence dataset (23 passages from six sources, all 40 examples, the pilot's 12 claims, overlap and hash statistics) describe the **full private dataset**. In this repository the text of 9 passages (15 examples, including pilot examples CE30 and CE31) is withheld and commands that need it refuse; see [PUBLIC_RELEASE.md](../PUBLIC_RELEASE.md).

Nothing was run live: no model call, search or fetch was made by the evaluation, no paid batch was executed, and no existing live database was
modified (they were opened read-only to copy stored evidence and to read measured token counts). All numbers produced by the tests are harness
checks on labelled fixtures and are not project performance. Details, commands and metric definitions: `evaluation/README.md`. This file also holds
the preparation fixes made after the first version (section "Preparation fixes") and the pilot budget.

## What exists

| Item | Path |
|---|---|
| 12 questions with scope and answer requirements (8 dev, 4 held-out) | `evaluation/data/questions.json` |
| 40 claim-evidence examples (30 dev, 10 held-out), provisional labels, label rules, review log | `evaluation/data/claim_evidence.json`, authored in `evaluation/claim_examples_source.py`, built by `evaluation/build_claim_dataset.py` |
| Split recorded before tuning (hashes of held-out sets) | `evaluation/data/split.lock.json` |
| Human labels (empty), tuning log (empty) | `evaluation/data/human_labels.json`, `evaluation/data/tuning_log.json` |
| Dev / held-out source and passage overlap | `evaluation/data/overlap_report.json` (`python -m evaluation overlap`) |
| The exact text the verifier receives for an example | `evaluation/verifier_view.py` |
| Review sheet, first 12 development examples | `evaluation/review/claim_review_batch1.md` and `.csv` |
| Pilot plan and guard | `evaluation/pilot.py` (`python -m evaluation pilot`) |
| A. frozen verification, B. end-to-end, metrics, schemas, review, estimate | `evaluation/verify_eval.py`, `e2e_eval.py`, `metrics.py`, `schemas.py`, `review.py`, `estimate.py` |
| Read-only allowance check | `scripts/check_allowance.py` |
| Tests | `tests/test_eval_metrics.py`, `test_eval_datasets.py`, `test_eval_harness.py`, `test_eval_prep.py` |

## Preparation fixes (2026-10-21, before any run or human review)

**1. Contradiction precedence and ambiguous labels.** Seven label rules (R1-R7) are stored with the dataset and printed at the top of the review sheet.
The decisive ones (as first written, rubric `claim-labels-v1`; R3 and R4 were revised the same day, see "Rubric v2" below): R3 `contradicted` needs text that
explicitly states something incompatible with the claim and no part of the claim established (implication or silence is never contradiction); R4 when at
least one part is established and another is missing, stronger, broader, more certain or explicitly contradicted, the label was `partially_supported`
(v1; superseded); R5 a central fact established only in weaker form (a suggestion stated as a rule, a list entry stated as the
largest) is `partially_supported`; R6 a central fact not established (off-topic, plan restated as an event, typical use restated as a limit) is
`unsupported`. The review sheet now shows EXACTLY the evidence text the verifier receives (rendered by the real evidence-block code), not an excerpt.
For all 40 examples nothing is cut at the verifier's 1,500-character limit and the quote is visible; frozen examples have no neighbouring passages,
which the production pipeline adds. Applying the rules changed two provisional labels and left the others: "Facebook is SQLite's largest customer"
(CE27, held-out) and "Tinybird says teams should always run Postgres locally through Docker" (CE30) moved from `unsupported` to
`partially_supported` (R5). Both changes are in `label_review_log`. The CE27 change is to a held-out item's provisional label, made before any run
or human review; the lock hashes claims and evidence, not labels, so it is unchanged. Labels are now: supported 10, partially_supported 12,
unsupported 8, contradicted 10. Five ambiguity notes (CE08, CE12, CE19, CE24, CE36) each name the deciding rule; the wording of the remaining reason
for hesitation is kept so you can disagree.

**2. Labels stay provisional.** Every AI-suggested label is `provisional` and no AI-suggested label is stored anywhere authoritative. An
authoritative entry needs `source: human`, a reviewer name that does not look like an AI or model (`Claude`, `GPT`, `assistant`, `bot` ... are
rejected), a valid label and a matching claim hash; `import-labels` refuses to run without `--i-am-the-human-reviewer`. `human_labels.json` is empty.
I (the assistant) have not entered, and will not enter, any label there.

**3. Overlap, and what this claim set is.** This claim set is single-domain (SQLite/PostgreSQL evidence). It is NOT a cross-domain evaluation and
says nothing about other domains. `overlap_report.json` records, for the 10 held-out examples: 7 use a source that development examples also use; 6
cite a passage that a development example also cites (CE29's passage is cited by 4 development examples); 1 cites a passage that earlier
development-run claims cited (CE22). All 6 sources, including the one source no development example uses (dev.to), come from the stored databases of
the Phase 1-4 development runs, so none is unseen by development. A good held-out claim score therefore means "new CLAIMS about familiar pages", not
generalisation to new sources or domains. Source-independent evidence comes only from the end-to-end questions Q09-Q12, whose sources are found by
live search.

**4. Q05.** The original question and its abstention expectation assumed, without checking, that no public figure exists. It is now
evidence-seeking: "Has Mistral AI publicly reported a net profit or loss for fiscal year 2024, and if so, what is the figure and which primary
source states it?" (`expected_outcome: evidence_seeking`). A human accepts either a figure from a primary source or an honest not-found with the
run's real search record, and rejects a secondary-source figure presented as reported, a different year or quantity, or an answer with no primary
source. The revision is recorded in the question (`revisions`) with the reason. It was made before any run or review; Q05 is a development question,
so the held-out set and the lock are unaffected. Nobody has searched for the figure, so whether it exists is still unknown. Q10 (a 2035 forecast) is
now the only clear abstention question and remains held-out; the abstention metric no longer counts Q05.

## Pilot (planned only; not executed)

Scope: the 12 development claim examples in `claim_review_batch1` (frozen verification) and development questions Q02 (HTTP/2 and HTTP/3 dates) and Q05
(revised, evidence-seeking). No held-out item is included (the plan refuses one). The pilot cannot run until all 12 claim examples have a human label:
`python -m evaluation pilot --execute` stops with the missing ids. It is blocked now.

| Part | Requests (typical / cap) | Searches / fetches (cap) | Output tokens (cap) | Time (typical / cap) | Calculated cost (typical / cap) |
|---|---|---|---|---|---|
| Frozen verification, 12 examples | 12 / 24 | 0 | 36,000 | 50 s / 6 min | $0.045 / $0.102 |
| End to end, Q02 and Q05, 40 requests each | about 52 / 80 | 16 / 24 | 60,000 | about 6 min / 20 min | about $0.11 / $0.34 |
| **Pilot total (incremental)** | **about 64 / 104** | **16 / 24** | **96,000** | **about 7 min / 26 min** | **about $0.16 / $0.44** |
| Optional, separate approval: Q02 and Q05 again at the product default (20 requests, 15,000 tokens, 300 s each) | cap 40 | 16 / 24 | 30,000 | cap 10 min | cap about $0.17 |

Per-question limits (end to end): 40 requests, 8 searches, 12 page fetches, 30,000 output tokens, 600 s, 4 corrective candidates, 2 correction rounds.
Verification step: 24 requests, 36,000 output tokens, 360 s. The caps on requests, searches, tokens and time are hard limits; the dollar caps are not (they
assume every request is the dearest measured role). Costs are CALCULATED from the average tokens returned in earlier stored runs times catalog prices,
not provider-reported charges (in earlier runs the provider-reported figure for Haiku-class calls was about 0.90 of the calculated one, cause not
established). The "typical" end-to-end figure (about 26 requests, about $0.056 per question) is a guess from two earlier runs. Tavily search credits are
not priced.

**Remaining allowance, checked read-only (`scripts/check_allowance.py`, only GET requests, nothing changed):** Mesh balance available $5.1386
(reserved $0.0000; the service's own `updated_at` is 2026-10-20T20:22 UTC, the end of the last billed run, so it may not include later usage);
Tavily account usage 8 of a plan limit of 1,000 search credits. The pilot's incremental cost (about $0.16 expected, $0.44 at the caps, $0.61 with the
optional arm) is well under the balance, and its 16 (or 32) search credits are well under the 992 remaining.

## Why evaluation uses 40 requests while production defaults to 20

The product default (`MAX_MODEL_REQUESTS` = 20, `MAX_TOTAL_OUTPUT_TOKENS` = 15,000, `MAX_ELAPSED_SECONDS` = 300) was set in Phase 2 as a small,
safe budget for collection, and the later jobs were each given their own budget in the live runs. The stored runs show what each job needed:

| Job (stored live run) | Model requests used / limit | Searches | Fetches |
|---|---|---|---|
| Collection (run `live-complete`) | 6 of 10 | 2 | 3 |
| Verification (same run) | 3 of 6 | 0 | 0 |
| Completion, correction loop and report (same run) | 13 of 14 | 2 | 3 |
| Completion on a constructed scenario (`live-corrective`) | 12 of 15 | 2 | 3 |

The three jobs of the one real run used 22 requests in total (the stored `model_calls` table has 24 rows for that run; the difference was not
investigated), each under its own budget. The end-to-end option (`?complete=true`) puts collection, verification, correction and report under ONE
budget. At the default 20 that total would be exceeded, and the loop's own rules then end the run early (it reserves 2 requests for the final
coverage and report, and skips drafting from new evidence when requests are short, recording the reason). The end-to-end job at the default budget was
**not measured live**; the statement that 20 is too small is an inference from the separate jobs above, not a measurement.

Evaluation therefore uses 40 so that a run is limited by the pipeline's own stop rules and not by a request cap that ends it early. Consequences, all
enforced in the result files: every result records each limit that differs from the product default and carries the note "not default product
performance"; `budget_class` is `evaluation_budget_differs_from_production`; results at 40 must not be presented as how the product performs by
default. The optional default-budget arm of the pilot measures exactly the difference on the same two questions: run it before deciding what to
report about default behaviour.

## Full live evaluation proposal (unchanged, not approved)

Stages after the pilot, each only after review of the previous one: A on the remaining 18 development examples; B on the other 6 development
questions; then, once tuning has stopped, A on the 10 held-out examples and B on Q09-Q12, once each (`--final-heldout`). Earlier whole-program
estimate: about 352 typical / 560 capped requests and about $0.82 / $2.35 calculated, including the pilot; it will be re-stated when the pilot's
measured per-question figures exist.

## Still needed from you

1. Label the 12 examples in `evaluation/review/claim_review_batch1.md` (or the CSV). They are provisional suggestions until you do.
2. Approve (or change) the pilot limits and say whether to include the optional default-budget arm.
3. Not built: review sheets for end-to-end requirement and report-claim labels (they need a collected run first); a prices file for live cost
   (`--prices` takes JSON per-million-token prices; none is generated).


## Rubric v2, versioned labels and label provenance (2026-10-21, before any label was imported)

**Why.** v1 R4 (a contradicted part next to a supported part is `partially_supported`) conflicted with the reviewer's decision for CE16 (one figure matches,
one is explicitly contradicted: `contradicted`). The rule was reconciled rather than the decision overridden: *an explicit evidential contradiction takes
precedence over partial support; `partially_supported` applies when support is incomplete or overstated without explicit contradiction.* The rubric is now
`claim-labels-v2`; v1 is kept in `label_rubric_history`. Only R3 and R4 changed. Merely implied or unmentioned content is still never a contradiction, so CE12 stays
`partially_supported`.

**Affected labels.** No human label existed, so no confirmed label changed. Provisional development labels that depend on v1 R4 are flagged
`needs_human_review` and NOT edited: CE13 (provisional `partially_supported`, v2 would give `contradicted`) and CE16 (same; matches the reviewer's stated
decision). Held-out CE19 has the same shape and is deliberately left untouched (its example text still quotes v1 R4); the held-out split lock verifies unchanged.
When v2 was adopted the verifier's bundled-claim sentence conflicted with it; that was fixed in the next step (below).

**Versioned corrections.** Previously an already-labelled id was skipped on import and could only be changed by editing `human_labels.json`, overwriting the
old label and reviewer. Labels are now append-only versions (see `evaluation/README.md`); `import-labels --correct` with a `correction_reason` appends a
version, nothing is overwritten, `label-history` shows the chain, and the readers use the highest valid version. The same rule applies to requirement and
report-claim labels when they are entered. Also fixed: the rebuild guard in `build_claim_dataset.py` looked for a `labels` key and so never detected existing
`claim_labels`; it now does.

**Provenance on reports.** Result files (schema version 2) state how the labels were made next to the numbers: "AI-assisted human review; not independent or
blinded." Checks run: the evaluation test files only (87 tests) and lint; no label was imported and no paid call was made.

## Verifier rubric attribution-v2 (2026-10-22)

**Change.** The verifier prompt's bundled-claim instruction now matches `claim-labels-v2`: an explicit contradiction of any asserted part takes precedence
(`contradicted`, even when other parts are supported); otherwise incomplete or overstated support is `partially_supported`; an implication or silence is never a
contradiction. The `partially_supported` and `contradicted` definitions were reworded to agree. Nothing else in the prompt, the schema, the model or any limit changed.

**Versioning.** `claims.CURRENT_RUBRIC` is `attribution-v2`; `claims.RUBRIC_HISTORY` keeps the v1 wording and v1's prompt hash. Evaluation result files already record
the rubric and the verifier prompt hash, so v1 and v2 results are distinguishable.

**Historical verdicts are preserved.** Stored verdicts are never rewritten and keep their `attribution-v1` stamp. A `supported` verdict required every part to be
established, so this change cannot invalidate it: `SUPPORTED_TRUSTED_RUBRICS` lets v1 and v2 `supported` verdicts stay report-eligible without a paid re-judge. Verdicts with
no or an unknown rubric (Phase 3 rows) still need a re-judge, as before. `rubric_current` stays literal (v1 verdicts report false); eligibility uses the new `rubric_trusted`.
Non-supported v1 verdicts are history only. A `partially_supported` verdict on a bundle that contained an explicit contradiction is therefore not retroactively changed.

**What the test shows.** `tests/test_verifier_contract.py` pins the instruction text, the four-verdict schema, the rubric history and the treatment of stored verdicts. It is
scripted and calls no model: it does NOT show that a model follows the instruction. That needs an evaluation against human labels, not yet run. Existing code still
never upgrades a verdict (a `supported` reply with a mismatch/missing aspect is downgraded to `partially_supported`, never to `contradicted`).

## Pilot A: frozen claim verification, first live result (2026-10-21)

Run once with `python -m evaluation pilot --execute --claims-only` on the 12 labelled development claim examples (no search, no fetch, no end-to-end question, no held-out example).
Verifier `anthropic/claude-haiku-4.5`, verifier rubric `attribution-v2`, label rubric `claim-labels-v2`; limits 24 requests, 36,000 output tokens, 360 s (not the product defaults).
The result is frozen: `evaluation/results/pilot_verification.json` (sha256 `9a7eb1c9c0a5b14376bd66b2429fc04ba301e31520890fb2424905eabf58f3c1`) and its database
`pilot_verification.db` (sha256 `7d2461af65d89539544ac87b5f2724161b347ed43ef14dddab76748a37ab63a6`). The labels were not changed after the run and no prompt was tuned. The CLI refuses to
overwrite or repeat it.

**Result.** The verifier agreed with the reviewed label on 9 of 12 examples. This is agreement with **AI-assisted human review labels (not independent or blinded)** on a
12-example, single-domain (SQLite/PostgreSQL), frozen-evidence set. It is not independent accuracy, not a cross-domain result and not end-to-end or default-product performance.
Other numbers (numerators/denominators, confusion matrix) are in the result file; none should be read to more precision than 12 examples allow.

**The three disagreements.**
- **CE08** (reviewed `partially_supported`, verifier `supported`): an unresolved context/label ambiguity. The reviewed label rests on the context (the date comes from a file-format
  infobox rather than the software-release claim) while the verifier judged the quoted infobox entry as an exact match. This pilot cannot say whether the label or the verifier is wrong.
- **CE23** (reviewed `unsupported`, verifier `partially_supported`) and **CE30** (reviewed `partially_supported`, verifier `contradicted`): non-supported verdict-boundary disagreements. Both
  sides agree the claim is not fully supported; they differ on where it falls among the non-supported verdicts (announcement of intent vs no support; "always" vs a conditional framing).
  Neither is a supported-versus-not-supported error.

**Accept-everything baseline.** The "accept everything" figures (supported precision 1/12 before filtering, 1/2 after) compare the verifier with a synthetic acceptance policy that
accepts every drafted claim in these frozen examples. They are not a comparison with a real unverified research report.

**Time.** The 35.2 s is the SUM of per-call model latency over 14 calls (12 first attempts, 2 retries). Wall-clock time of the whole run was not measured and is not reported.

**Charges.** Provider-reported usage charges summed to $0.047619 over the 14 request ids (the Mesh balance fell $5.1372 to $5.0896); no calculated cost (no price table was supplied).

## Pilot B (Q02, Q05 end to end): prepared, not run

`python -m evaluation pilot --execute --e2e-only` runs only the two development questions at the extended evaluation limits (40 requests, 8 searches, 12 fetches, 30,000 output tokens, 600 s per
question; product defaults unchanged at 20 requests, held-out questions untouched). It never runs the claim verification, and it refuses to start if any result file it would write exists.
Estimate (calculated from earlier measured averages, not charges): about 52 requests and $0.11 typical, caps 80 requests, 16 searches, 24 fetches, 60,000 output tokens, $0.34 (cost cap
assumes every request is the dearest measured role; the request/search/token/time caps are the hard limits).

After collection, `python -m evaluation e2e-review-sheet --db DB --result JSON --stem PREFIX` (read-only) writes review sheets for the final-report claims and the answer requirements, keyed
`QUESTION@RUN_ID`. They show the report's claims with cited passage text, the requirement, the written acceptability note for Q05 and the searches actually sent, and deliberately omit the
pipeline's verdicts, coverage status and gaps: completeness is judged by a human, and the pipeline's own coverage judgements are never ground truth. Checked by tests on a fixture run only.

### Pilot B attempt 1: crashed before research started (2026-10-21)

**Cause (confirmed from the stored artifacts and the code).** `e2e_eval.execute` created the run in the evaluation database (`pilot_e2e_evaluation_budget.db`) but built the services with the
unchanged settings, so `svc.open_db()` opened the application's default `settings.database_path`. The job's first write (`research_started`) referenced run 1, which exists only in the evaluation
database, so the foreign key failed. The failure handler in `graph._run_job` then called `db.get_run` on that missing run and subscripted `None`, hiding the original error behind a `TypeError`.

**Spend.** The kept database has 1 run (claimed, `researching`), 1 event (`run_created`), 0 model calls, 0 searches, 0 sources; nothing before `research_started` calls a provider. The Mesh balance
was $5.0896 before and after (four decimals). This is from the stored records, not from the traceback. The application database held no rows.

**Fix.** The evaluation database path is now written into the settings the services are built from; before any provider call the run is checked to be visible on the job's own connection (otherwise
the pilot stops with a clear error); and the failure handler re-raises the original exception when the run does not exist. Foreign keys are unchanged. Regression tests run the actual pilot CLI to the
real graph job on a temporary database with stubbed providers (`tests/test_eval_pilot_cli.py`) and assert the application database is untouched; one was checked to fail without the fix.

**Artifacts and restart.** Attempt 1's database is kept as evidence and its overwrite protection stays on. A new attempt writes `*_attempt2.*` files (`--attempt 2`). Nothing was rerun.

### Pilot B attempt 2: stopped mid-run; runtime, deadline and interruption fixes (2026-10-21)

**What attempt 2 left.** Q02 only: collection finished (4 subquestions, 8 searches, 9 sources), 9 successful model calls, then the process ended about 37 s into the next model call
(source relevance). No model-call failure was recorded and the run stayed `researching`: neither a timeout (the 60 s limit had not elapsed) nor an ordinary exception can leave that state, an
interruption can. The traceback itself was not available, so this remains an inference. Attempts 1 and 2 are kept unchanged (sha256 of their databases is recorded in the session notes; none was
opened for writing).

**Runtime.** The environment had silently been created with Python 3.14.6 (no pin; `requires-python >= 3.12`). `.python-version` now pins 3.12; Python 3.12.13 was installed with uv and the
environment re-synced from the unchanged `uv.lock` (`uv sync --frozen`: no dependency was upgraded; openai 3.35.1, httpx 0.28.1, httpcore 1.0.9, anyio 4.15.1, pydantic 2.14.1, langgraph 1.2.16
as before). README setup corrected. All validation before this point had run on 3.14; the suite was rerun on 3.12.

**Deadlines.** The SDK/httpx `timeout=` bounds silence on the socket per phase, not total time: a local probe showed a reply trickling at 0.25 s per byte completing after 61 s under `timeout=3`.
Real services now send each model request through an async client (`LLM._asend`, one `AsyncOpenAI` per request, `max_retries=0`) under `asyncio.timeout(call_timeout)`, where `call_timeout` is the
smaller of the Mesh timeout and the job's remaining time. At the deadline the task is cancelled, httpx closes the connection and the client is closed in a `finally`; no thread is abandoned, no late
database write is possible and nothing retries. A failed request is recorded with its measured wait, the request is counted once, usage stays NULL and the per-call maximum is charged to the output
cap. Local deterministic servers (silent and trickling) in `tests/test_deadline_transport.py` show the deadline, the server seeing the connection close, one connection only, no leaked thread, and the
same for an interrupt. **Limits:** cancelling the client does not prove the provider stopped generating or stopped billing; those stay unknown. Tests with fixture clients (no async factory) use the
old synchronous path and prove nothing about transport. The async path needs a thread without a running event loop (every job thread is one). A new connection (TLS handshake) is made per request.

**Interruption.** `graph._run_job` now also catches `BaseException` (KeyboardInterrupt, SystemExit, cancellation): it rolls back any open transaction, marks the run's open stages failed as
`interrupted: <signal>` with an event, closes the connection and re-raises. It never turns an interrupt into success. Startup recovery (`mark_interrupted_runs`) remains for crashes where this cannot run.

**Evaluation observability.** `e2e_eval.execute` prints flushed progress (question started/finished, stage events and a 30 s heartbeat read from a read-only connection) and writes
`<result>.partial.json` after every finished question (`status`, `completed_questions`, `interrupted_question`). On interruption the question in flight is collected best effort, the partial file
says `interrupted`, no final result file is created, and the interrupt is re-raised.

### Pilot B attempt 3 and the fixes that followed (2026-10-22)

**Attempt 3 is kept unchanged** (`pilot_e2e_evaluation_budget_attempt3.{db,json,partial.json}`; hashes verified before and after). Q02 completed with a report that has gaps; Q05 failed during collection.
A separate file, `pilot_e2e_evaluation_budget_attempt3.annotations.json`, records Q05 as **environment_interrupted** (the failed relevance call waited 895.75 s against a 60 s deadline while the
Windows System log shows Modern Standby from 21:21:39 to 21:36:42 IST) and as a failed run, not an abstention. The result file itself is not edited.

**1. Search allocation.** `corrective_search_reserve` (default 2) is held back INSIDE the unchanged `max_search_queries` (8): initial collection may use 6, corrective research may use whatever of the
total is left. Collection also gives each waiting subquestion a fair share of the remaining initial allocation, so an early subquestion cannot take it all. `research_started`, `research_finished`
and `completion_finished` events record `search_allocation` (total / initial / corrective). In attempt 3 collection used all 8 searches before any correction, so three missing-evidence tasks were
skipped "no search budget left".

**2. Optional association.** A model/transport failure (`ModelCallError`, `AgentOutputError`) in the cross-subquestion association step records an `association_failed` warning, keeps the direct
source-subquestion links, and the run continues only if `Budget.call_timeout()` still permits a request (otherwise it stops honestly). Database, integrity and programming errors are not caught.

**3. Planning, scope and coverage.**
- The planner now states `asks` (verbatim quotes of what the question asks) and each subquestion `covers` them. Deterministic checks (`planner.plan_problems`) reject a plan when an ask quotes words not in the
  question, a clause of the question (split at "?", ";" and ", and <question word>") is touched by no ask, an ask is covered by no subquestion or `covers` points at nothing. One repair request is made;
  a plan that still drops part of the question is refused. This stops a clause being silently dropped; it cannot judge the quality of the subquestions. **Q02's own question does not mention head-of-line
  blocking** (the dataset requirement R4 does, the question given to the pipeline does not), so the observed omission is a dataset/question mismatch, not a planner defect on that question; the regression
  test uses a question that asks for it.
- Original versus revision: the planner, drafter and verifier prompts now require the original publication and later revisions to be kept apart (no facts hard-coded; a test asserts none of the RFC numbers,
  protocol names or years appear in a prompt). Verifier rubric is `attribution-v3`. Q02's stored passage for claim 4 shows "Obsoletes: 7540, 8740", i.e. June 2022 is the date of a revision. v1/v2 `supported`
  verdicts were not judged against the new rule (see the compatibility correction below). Whether a model follows the new wording is not shown by any test.
- Coverage explanations that cite "claim N" are rejected when N is not a shown eligible claim, or when an acronym, number or protocol name attributed to claim N is not in that CLAIM's text (Q02's
  "claim 3 mentions ... QUIC" is the model case: that text is in the cited passage, not in the claim). A rejected explanation is replaced and an `addressed` that rests on it becomes `unresolved`.

**4. Evaluation reporting.** `failure_info` records the stage, classification (`environment_interrupted_suspected` when a failed call waited more than 3x its deadline plus 30 s, `transport_timeout`,
`validation_failure`, `budget_exhaustion`, `provider_or_transport_error`) and last failed call for any run without a report. Failed runs are counted in `run_completion` and are excluded from every
abstention and "answer with no claims" figure. The requirements review sheet shows the failure, stage and classification, and a `run_status` column.

### Compatibility correction: rubric trust and requirement versions (2026-10-22)

**Rubric trust.** Only a `supported` verdict made under the CURRENT verifier rubric (`attribution-v3`) qualifies a claim for a NEW report (`claims.SUPPORTED_TRUSTED_RUBRICS`). v1/v2 verdicts are not
rewritten and keep their stamp; such a claim is listed as `needs_rejudge` and, in a new report's omissions, with the reason "its supported verdict was made under verifier rubric X, not attribution-v3,
and has not been re-verified". The completion step's existing re-judge (a visible, budgeted model request that appends a new result) is the way to re-verify. This replaces the earlier choice that kept
v1/v2 verdicts eligible. Stored reports are snapshots: they still render, and each report claim now reports the rubric its verdict was made under (`verifier_rubric`, `verifier_rubrics`).

**Requirement versions.** Q02's R4 (head-of-line blocking) is annotated `beyond_explicit_question`: the question given to the pipeline does not ask for it. Its text and id are unchanged. Q02 now has
`requirement_sets`: `requirements-v1` (original, R1-R4, the default) and `requirements-v2` (R1-R3). Results report completeness under both (`completeness_by_requirement_set`); `completeness` stays the
original set. Labels are keyed by requirement id, so one labelling serves both sets. Held-out questions are not modified, and the validator refuses requirement versions on them. No earlier result was edited.

### Pilot B attempt 4 (2026-10-22): recorded as it happened

Frozen: `pilot_e2e_evaluation_budget_attempt4.{db,json,partial.json}`; the interpretation is in `pilot_e2e_evaluation_budget_attempt4.annotations.json` (the result files are not edited).

- **Q02: failed.** Collection finished (4/4 subquestions, 6 searches, 8 sources, 451 passages); the drafter then reached its 60 s deadline. No claim, no report. An optional relevance call timed
  out earlier and was a warning only. The first-draft passage selection had offered the drafter passages for only 3 of the 4 subquestions.
- **Q05: completed with an EMPTY report** (`insufficient_evidence`, 0 claims, 7 requirements unresolved). 0 pages were fetched: no URL was selected for any subquestion and PDF results are unsupported.
  This does not show the search was exhaustive or that the figure is unavailable, and it is not a successful abstention.
- **Not demonstrated:** answer quality, original-versus-revision handling, or citation navigation (no citation exists in either run).
- **Usage:** Q02 11 calls (2 failed), 317 s; Q05 12 calls, 180 s. Provider-reported charges $0.000708 (Q02, 9 of 11 calls) and $0.003281 (Q05, 12 of 12).

### Stabilization after attempt 4 (nothing re-run)

- **Process cleanup.** The unrelated `node.exe` was ended by an ad-hoc shell command written for the UI check (a kill by image name plus a kill by listening port), not by repository code
  (the existing acceptance driver already stops only the backend it started). `scripts/ui_acceptance/owned.mjs` now launches/records and stops only a process whose pid, creation time and command
  line still match the launch record; anything else is left alone with a reason. `owned.test.mjs` runs a real unrelated Node process and checks it survives, that a recycled/mismatched pid is
  refused, and that no script or document recommends name- or port-based kills.
- **Empty-report wording.** The run header shows "Insufficient evidence" (was "Completed with gaps") and the Report tab adds a "What the run collected" note built from recorded events (searches,
  pages stored, subquestions with no URL selected, PDF results skipped). The stored report text is untouched.
- **Drafting.** First-draft passage selection is now round-robin (every subquestion gets its best passage before any gets a second); before, subquestions were served in order from one character cap and
  the last could get nothing. One opt-in retry profile, `compact` (below), is previewed offline and never applied automatically.

**Proposed stored-evidence drafting test (NOT executed).** Against the stored evidence of attempt-4 Q02 (8 sources, 451 passages, 0 claims; the run is retry-eligible):
`GET /runs/1/retry?profile=compact` (free) previews the selection; then one paid, explicitly confirmed `POST /runs/1/retry {"confirm_paid_retry": true, "profile": "compact"}` on a COPY of the
database. Settings: `max_claims` 8 to 5, `draft_input_chars` 9000 to 5000, `draft_passages_per_subquestion` 4 to 2, `draft_context_passages` 1 to 0. Timeout (60 s), models, token limits and the
one-request-per-attempt rule are unchanged; no search, no fetch, no automatic retry. Offline preview on the real evidence: configured limits offer 10 passages / 8,942 characters to all 4
subquestions (3/3/2/2 passages); the compact profile offers 6 passages / 4,556 characters, still to all 4 (2/1/2/1). Trade-offs: roughly half the prompt, so a lower timeout risk (not
a guarantee) and fewer claims to verify; but fewer independent passages per subject, at most 5 claims for 4 subjects, no neighbouring context for fragment passages, and the
original-versus-revision judgement then rests on fewer passages. Expected spend is small (about 1 drafting call plus up to 5 verifications and 2 to 3 coverage calls, under the retry job's 20-request cap).

### Q02 compact continuation from stored evidence (2026-10-22, one approved run)

Recorded in `evaluation/results/q02_compact_continuation.json` (the database copy `data/q02-compact-continuation.db` is not committed). Drafting succeeded in 58.5 s of its 60 s limit (5 claims).
Verification: 5 of 6 claims supported, 1 partially supported (and revised once); report `claims_with_gaps`, 5 claims, stopped because retrieval was disabled for the retry. Usage: 12 calls
(1 failed schema validation, retried), 104.9 s, provider-reported charges $0.037169. The report is not a good answer: HTTP/3 has no RFC date or number claim (the HTTP/3 date section holds a 2018
renaming proposal), HTTP/2's transport is named only indirectly, and the HTTP/2 claim "published as RFC 9113 in June 2022" is a revision date (the cited passage says it obsoletes RFC 7540) with
no mention of the original RFC. Citation navigation in the React UI worked for all 5 citations (each opened its passage with the quoted text highlighted; deep link and reload kept the selection).

### Answer relevance (2026-10-22): support and relevance judged separately

**Why.** In the Q02 compact continuation every claim was supported, yet the HTTP/3 date section held a 2018 renaming proposal and the HTTP/2 date section held a revision date presented without saying so.
Support and relevance had been collapsed into one question.

**What changed (no new model stage, no new request).**
- The existing coverage assessment call now also states, per obligation, which shown claims are a `direct_answer` and which only `relevant_context` (with a reason under 160 characters). A claim that is
  supported but does not state what is asked is context. The verifier is unchanged in what it judges, except that its prompt now says the subquestion is context, not a standard (rubric
  `attribution-v4`; it can only turn a non-supported verdict into supported, so `attribution-v3` supported verdicts stay trusted).
- Code enforces what the model cannot be trusted with: `addressed` / `partially_addressed` need a direct answer; references are validated (unknown or ineligible claim ids are dropped and noted; the database
  refuses a judgment about another run's claim or under the wrong requirement); a date obligation is not answered by a claim whose cited document uses supersession wording ("obsoletes", "supersedes",
  "replaces", "updates:") unless the claim says so, and then only partially, with the gap "only the date of a later revision is established; the original publication date is not". The wording checks are
  generic (`backend/temporal.py`) and name no document, protocol, number or date. A false positive demotes a date answer to context; it never creates an answer.
- Persisted in new tables (schema version 8, added to older databases on connect): `claim_relevance` (obligation assessment, requirement key, claim id, role, reason, origin model|rule) and, per report,
  `report_claim_roles` and `report_relevance`.
- Report assembly: a section is filled only with its direct-answer claims; a section with none says "No direct answer to this subquestion was found."; relevant context is listed apart ("Related context (verified,
  but it does not answer this subquestion)") and is never counted as an answer; a claim that answers none of its subquestion's requirements is omitted with that reason; each answer notes which requirement it
  directly answers and each section lists its requirements still open. Claim text and citations are copied verbatim from the verified rows; no summary sentence is generated. A report made with no relevance
  judgment (older reports; coverage never ran) keeps its previous selection and says `relevance_judged: false`. The drafter marks passages whose document uses supersession wording
  (`revision_notice="yes"`) and its prompt requires the claim to say so.
- The API and React report now carry `context`, `answered`, `open_requirements`, `relevance_judged`, `verifier_rubric` and `verifier_rubrics` (the earlier `verifier_rubric` fields were in the report dict
  but were not exposed by the API until now).

**Request budget.** None added. The coverage call's reply is longer (a `claim_roles` list per obligation): each reply carries at most a few short roles per obligation; at the 1,500-token per-call cap a very long reply
would fail schema validation and cost one retry request (the existing rule). The run's 20- and 40-request caps are unchanged.

**Offline check on the stored Q02 continuation (fixture judgments, no model).** `scripts/offline_assembly_check.py data/q02-compact-continuation.db` copies the database and builds a report with hand-written, labelled
FIXTURE judgments. Before: every section held one or two claims (the renaming proposal answered the HTTP/3 date section; the revision date answered the HTTP/2 date section). After: the HTTP/2 date requirement is open
(revision date only), the HTTP/3 date section says no direct answer and lists the renaming claim as context, the HTTP/2 transport section says no direct answer and lists the TLS claim as context, and the HTTP/3 transport
section is answered. These are routing results, not a measurement of any model.

**Limits.** Relevance is still a model judgment (a wrong `direct_answer` for a non-date requirement is not caught); the supersession check reads only the cited passages and a few generic phrases; a claim is placed only in
its own subquestion's section (an answer that sits in a neighbouring subquestion is not routed); the older v1/v2 verdicts still need re-verification; nothing here was run against a live model.

### Temporal boundary and the development-only relevance check (2026-10-22, prepared, not run)

**Boundary.** The original-versus-revision restriction now depends on what the requirement asks (`temporal.date_scope`, read from the requirement, its dimension and its subquestion): an explicit request for the
ORIGINAL / first date never accepts a revision date (a revision-dating claim becomes context, even one that says it is a revision); an explicit request for the date of the REVISED / current document accepts that
date as a direct answer; anything else is ambiguous and keeps the distinction (a revision date alone is partial, a claim that hides the revision is context). The supersession wording is narrower too (a header
"Obsoletes:" / "Updates:", "this document obsoletes/supersedes/replaces ...", "replaces the previous edition"), because a stored PostgreSQL passage ("an earlier revision of the control record") and a gaming
blog passage matched the first version.

**Check (not executed).** `uv run python -m evaluation.relevance_check` prints the plan; `--execute` runs it once. Six development cases over two domains (networking: stored Q02 evidence; databases: development
examples CE01, CE03) with expectations written before the run in `evaluation/data/relevance_check.json`, labelled AI-assisted development expectations, not human ground truth. The real verifier, obligation analyst,
coverage/relevance assessment and report assembly run on scratch runs built from stored passages: no search, no fetch. The databases domain has no stored evidence with supersession wording, so the two
revision cases exist only for networking (reported, not invented). Limits per domain run: 8 requests, 5,000 output tokens, 150 s, 60 s per call; two domain runs. Estimate: about 10 requests, about $0.035
typical, cap 16 requests and about $0.07 (calculated from earlier per-call charges).

### Relevance-check follow-up: what the recorded failures showed (2026-10-22; offline, nothing re-run)

The result JSON and scratch database are unchanged (hashes verified). A corrected reading is in `evaluation/results/relevance_check.interpretation.json` (`python -m evaluation.relevance_check --interpret`).

- **A1 (verifier "contradicted").** Claims 1 (A1) and 2 (A2) have identical text, identical quote and identical passage; only the subquestion differs ("...original... first published?" versus "...current revision..."), and the verdicts are
  contradicted versus supported. The stored reason says the claim "implies it is the original", an implication the claim does not make; the only source of "original" in that input is the subquestion. So the verdict
  followed the REQUESTED answer, against the rubric's own instruction. Whether the claim's unqualified "the HTTP/2 specification" should still be `partially_supported` is not settled by the record and is left unresolved. The
  expectation was not changed; the verifier was not changed.
- **A2 (subject backstop).** `correction.mentions` required every 3+-letter word of the derived subject to appear in the claim, so "the current revision of the HTTP/2 specification" failed on "current" and "revision". It also
  dropped bare numbers, so a subject "HTTP/3" accepted a claim about HTTP/2. Fixed generically: version/currency qualifiers and glue words are not required (`temporal.VERSION_QUALIFIERS`), and any token containing a digit must appear
  exactly. An identifying word is still required, so a wrong or differently named subject is still rejected.
- **Reporting.** Results now separate semantic eligibility, whether relevance was assessed for the intended claim, the relevance decision, the requirement status and the report placement. A case blocked before relevance, or one
  with no recorded judgment, is "relevance not exercised", never a pass. Re-read: A3, A4, B2 pass; A2 differs (requirement, now fixed in code but not re-run); A1 and B1 are relevance-not-exercised (B1's omission from the report
  came from a missing judgment, not from a context classification).

### Verifier input correction, rubric attribution-v5 (2026-10-22; prepared, nothing re-run)

**Why.** The relevance check showed an identical claim with identical evidence judged `contradicted` or `supported` depending only on the subquestion shown to the verifier (A1 versus A2).

**Change.** The semantic verifier's whole user message is now `verifier.USER_TEMPLATE` over the claim and its evidence block: no research question, no subquestion, no requested answer. The claim's own wording
(including "first published") is untouched, and so are the evidence block, source metadata and surrounding passages. The review sheet (`verifier_view`) uses the same function, so it still shows exactly what the verifier receives. The template is part of
the prompt hash (`prompt_hashes` now includes `USER_TEMPLATE`), and the v4 hash is recorded in `claims.RUBRIC_HISTORY`.

**Coverage/relevance keeps the question.** Its request now carries `<question>` and each obligation's `subquestion`, and its prompt says those decide what is asked, including original versus a later revision; the deterministic
date-scope rule reads the same texts. Missing requested information stays a coverage gap.

**Trust.** The input contract changed, and a verdict made with the subquestion in view can differ from one made without it in either direction, so `SUPPORTED_TRUSTED_RUBRIC` is now only `attribution-v5`: v3/v4 supported verdicts are kept as history,
are shown as "Needs re-judge", and are not used for new reports until the completion step re-verifies them (a visible, budgeted model request; none is made automatically, none was made now). Stored reports are unchanged.

**Tests establish input separation, not model accuracy:** identical claims and evidence under different subquestions give identical verifier messages; a "first published" claim keeps that assertion in the request; coverage receives the question and
tells original from revision; the subject backstop accepts the intended subject and rejects a different numbered one. No verdict is forced for A1.

**Prepared, not executed: `uv run python -m evaluation.relevance_check --follow-up a1a2-v5`** (add `--execute` to run). Cases A1 and A2 with the expectations recorded before the first run, unchanged. New artifacts
`evaluation/results/relevance_check_a1a2_v5.json` and `data/relevance-check-a1a2-v5.db` (it refuses if either exists). Limits: 6 model requests (retries included), 3,000 output tokens, 90 s, 60 s per call, 0 searches, 0 fetches.
Estimate (calculated): about 4 requests (2 verifier, 1 obligation analysis, 1 coverage), about $0.014 typical, cap about $0.026 (not a dollar ceiling). The result reports semantic eligibility, whether relevance was exercised for the intended
claim, the relevance decision, requirement status and report placement separately; if A1 is blocked again it is recorded as relevance not exercised.

### A1/A2 follow-up result (2026-10-22; one approved run, not repeated)

Run once with `python -m evaluation.relevance_check --follow-up a1a2-v5 --execute` (`evaluation/results/relevance_check_a1a2_v5.json`; the scratch database is not committed). **Both cases passed with relevance exercised for the intended claim.**
A1: the claim was judged `supported` (the first run's `contradicted` did not recur), classified `relevant_context` by the assessor, requirement unresolved with the original date stated as missing, placed as related context. A2: `supported`,
`direct_answer`, requirement addressed, placed as the section's answer. 4 model requests of 6 (no failures, no retries, no searches or fetches), 606 output tokens, 8.4 s of model time; provider-reported charge $0.010775 (4 of 4 calls).

**What this is.** Agreement with two AI-assisted development expectations that were written before the first run and not changed. It is not a measure of general accuracy, not independent human ground truth, and two cases from one run cannot show reliability.
In particular the assessor, not the deterministic revision rule, kept A1's claim out of the answer, and the verifier still sees only the claim and its evidence, so it could not tell the two claims apart; the distinction is drawn at coverage, from the question.

**The earlier results stand unchanged.** The first run (`relevance_check.json`) had A1 blocked by a `contradicted` verdict and A2's requirement rejected by the subject backstop; its corrected reading (`relevance_check.interpretation.json`: A3, A4, B2 pass; A2 differs; A1 and B1 relevance not exercised)
and the interpretation of those failures above are not revised by this follow-up.
