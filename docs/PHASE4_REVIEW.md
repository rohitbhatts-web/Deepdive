# Phase 4: bounded correction loop and constrained report

Status: **implemented, tested with fixtures (45 new tests, 284 in total), and run live once** on a copy of the natural
SQLite/PostgreSQL run. The live run proved part of the intended path, not all of it (see "What the live run did not show").
Coverage is a fallible model assessment, not a truth guarantee. Nothing here is an accuracy claim.

## Behaviour

Graph (explicit LangGraph nodes): `rejudge -> coverage -> [correct -> reverify -> coverage]* -> report -> finish_complete`,
reachable three ways: `POST /runs/{id}/complete` (an existing run; runs draft/verify first if it has no claims),
`POST /runs/{id}/research?complete=true` (end to end for a new run) and the existing verify entry points.

- **One budget.** A job creates one `Budget` and passes it to every node, so coverage, repairs, targeted research,
  re-verification, relevance checks, synthesis, retries and failed requests draw on the same counters; nothing resets per
  node or round. Two model requests are always held back for the final assessment and the synthesis (`RESERVE`); correction
  work that would eat them is skipped with that reason.
- **Rejudge.** A `supported` verdict made under an older verifier rubric (stamped `rubric` in the result details; Phase 3 rows have
  none) is not trusted for reports. Such claims are re-judged first; the old result stays in the claim's history.
- **Coverage.** For each planner evidence requirement the assessor sees the CURRENT eligible claims and the passages they cite
  (never source or claim counts) and returns `addressed | partially_addressed | unresolved`, supporting claim IDs and the gap.
  Code validates: only claims that are report-eligible now, in this run, may be cited; `addressed`/`partially_addressed`
  without a valid citation become `unresolved`; a skipped requirement is `unresolved`. With no eligible claim there is no model call.
- **Routing (deterministic, from stored state).** `integrity_failed` / `partially_supported` -> repair (a NEW claim version); a repaired
  version that fails again, `unsupported`, `contradicted`, repeated verification errors -> excluded (history kept);
  `verification_error` -> re-verified once. A requirement that is not `addressed` -> a targeted search task carrying exactly
  what is missing (`date`, `quantity`, `primary_documentation`, `contradiction`, `detail`, `other`). Test inputs, superseded
  versions and excluded claims are never routed.
- **Repair.** The Reviser sees the failure reasons and stored passages only and writes one replacement or abandons. The
  replacement is a new immutable row (`parent_claim_id`, `version + 1`, provenance with the reason and the failure); it has no
  verdict and goes through integrity and the semantic verifier from scratch.
- **Targeted research** goes through the existing Researcher and ingestion service, with the gap in the prompt; a query already run is
  not run again, and a gap already searched with no change is not searched again.
- **Stopping**, each recorded: all requirements addressed; corrective rounds exhausted (`MAX_CORRECTION_ROUNDS`, default 2);
  no progress (eligible claims and every requirement status unchanged); no actionable tasks; budget (model requests, search,
  fetches, tokens, time, or the search limit blocking every remaining task).
- **Report.** The Synthesizer returns claim IDs only. Code builds sections for EVERY planned subquestion (neutral labels "Section n"
  plus the planner's own question text), copies each claim's exact stored text and the passages its verdict actually relied on,
  lists every requirement's status, and records omissions. IDs are validated inside the writing transaction and again by database
  triggers. Without a model call (no eligible claim, or budget gone) the order is deterministic and the report says so.
- **Outcomes.** `insufficient_evidence`, `claims_with_gaps`, `claims_all_requirements_addressed`: a report with gaps is a normal
  result and is distinguished from full coverage. Job statuses: `completed` (the loop ended on its own criteria), `stopped`
  (a budget limit), `failed` (error, interruption); a failed job has no report.

## Invariants and where they are enforced

| Invariant | Enforcement | Evidence |
|---|---|---|
| One eligibility rule: natural, current, not excluded, integrity passed, latest verdict `supported`, current rubric | `claims.is_report_eligible`; mirrored by `report_claims_eligible` trigger | `test_phase3_audit` mixed set; report trigger test; mutations |
| A report copies exact text and real citations; fabricated, stale, excluded, test-input, unsupported IDs are rejected | `build_report` validation + triggers (text/version equality, latest result, citation must be a passage the verdict relied on) | `test_phase4_units` report tests |
| Reports are snapshots | no UPDATE/DELETE on any report table | snapshot test: a later revision and exclusion leave an old report byte-identical |
| Claims are never edited to pass | claims/results immutable (Phase 3 triggers); repairs are new rows with no inherited verdict | reviser tests, loop test |
| Exclusion keeps history; confined to its run | `claim_dispositions` (immutable, same-run trigger) | units |
| Cumulative budget and termination | one `Budget`; reserve; round cap; no-progress; search-limit stop | loop tests incl. cap 0/1/2/9, two full rounds |
| Test inputs never reach a prompt, the coverage input, routing or a report | `is_active` | loop test scans every prompt for a sentinel |

Mutation check on these rules: 20 deliberate breakages, all caught, plus 2 for the defects found live (below).

## Live acceptance (one run, real Mesh, real Tavily, real web)

Source: a NEW copy (`data/live-complete.db`) of the natural run (`data/live-verify.db`); the three prior databases are byte-identical
after the run (digests compared). Nothing was injected: the copy already held natural claims #1-#3 and the two Phase 3 test inputs #4, #5.

Bounds, fixed in `scripts/live_complete.py` and not raised: 2 corrective rounds; 2 new searches; 4 new page fetches; 3 active
claims; 14 model requests TOTAL including retries; 240 s; 1,500 output tokens per call and 6,000 for the job; input capped in
characters (draft 6,000, verifier context 1,200, coverage 6,000, reviser 4,000). Models: drafter/reviser/researcher/synthesizer
`qwen/qwen3-235b-a22b-instruct-2507`; verifier and coverage `anthropic/claude-haiku-4.5`.

**Stated before running:** under these bounds the test could not show new evidence entering the report (the 3-active-claim cap was already full)
or two full corrective rounds (14 requests do not fit them). It could show re-judging, repair/exclusion, targeted research tasks,
re-verification and report assembly.

Observed (all from stored data):

| Item | Result |
|---|---|
| Outcome | `completed`, 1 corrective round, report 1: `claims_with_gaps`; 1 of 7 requirements addressed, 3 partially, 3 unresolved |
| Requests | 13 of 14 (verifier 4, coverage 2, reviser 2, researcher 4, synthesizer 1), 0 failed, 0 retries; 2,174 output tokens; 85 s of 240 |
| Searches / fetches | 2 of 2 / 3 of 4 |
| Known misquote (#1) | repaired as #6 (v2): quote verbatim in S1-P0, `supported`; #1 untouched, superseded |
| Known unattributed claim (#3) | its Phase 3 verdict was re-judged under the current rubric: `partially_supported` (attribution mismatch); repaired as #7 (v2) "According to the comparison table, ...": `supported`, attribution `ok`. Result 5 (old `supported`) is still in #3's history |
| #2 | re-judged, still `supported` |
| Test inputs #4, #5 | untouched, excluded from routing, coverage and the report |
| Missing-evidence tasks | 2 executed (concurrency, scalability): 2 searches, 2 new sources (coddy.tech docs page, techjockey.com comparison page); 4 other tasks skipped: search limit |
| New evidence into claims | none: "no free active-claim slot" (recorded) |
| Reverified revisions used in the report | yes: the report contains claims 2, 6, 7; 0 differences in text, version or citations from the stored eligible claims |
| Cost | estimate before the run ~$0.072 (not a ceiling); calculated from reported tokens and catalog prices $0.02368; reported by Mesh usage API $0.02137. All 6 Haiku calls were reported at exactly 0.90x their calculation and all 7 Qwen calls at 1.00x; no cause established |

### What the live run did not show

- New evidence entering the report; a second corrective round (budget) and the loop's other exits (no progress, all addressed) live.
- Primary-source retrieval: the assessor labelled the gaps `other`, not `primary_documentation`, and neither retrieved page is
  primary documentation. So "a missing primary-source requirement triggers targeted research" is NOT demonstrated; "an unresolved requirement triggers targeted research" is.

### Mistakes and weaknesses observed

- Defect found by the live run, fixed afterwards and not re-run live: the loop ended with `no actionable correction tasks` and
  status `completed` although four tasks had been skipped for "no search budget left". It is now a budget stop (`stopped`, reason
  "search budget exhausted ..."). Related: a task that was skipped counted as "already searched"; only tasks that ran count now.
- Coverage assessment 0 marked "architecture type (serverless vs. client-server)" `addressed` from a PostgreSQL-only claim (#2); after #6 it
  cites [2, 6]. Premature `addressed` is the kind of error this model assessment can make.
- Repaired claim #7 still bundles two assertions (SQLite and PostgreSQL); atomicity is requested in prompts, not checked.
- Six of seven requirements remain open although stored passages S4-P6 to S4-P14 mention concurrency: they are retrieved but unclaimed, so the report correctly says no verified claim covers them. Task order, not importance, decides which gaps get the two searches.
- Page extraction noise (S5-P4 starts with popup text) is stored as passage text.

## Remaining gaps

Primary-source preference is prompt-level only; two corrective rounds are proven by fixture, not live; no semantic accuracy
measurement (Phase 6); a repair is attempted once per claim; coverage uses one model call and can be wrong either way; the
UI does not exist yet (Phase 5).

---

# Phase 4 audit (follow-up)

Schema is now v7. The earlier live databases (`live-complete.db`, `live-verify.db`, `live-test-2.db`, `live-test.db`) have identical
content digests before and after the audit run. Full suite: 318 tests, lint clean. No mutation sweep was run, as instructed.

## Confirmed gaps and the fixes

| Gap (confirmed by reading the code, the first live run, or a test) | Fix | Evidence |
|---|---|---|
| New evidence could not be used: `MAX_ACTIVE_CLAIMS` was the only drafting limit, and the 3 stored claims filled it | The active-claim cap is gone. A separate HARD cumulative cap on candidate claims (`MAX_CANDIDATE_CLAIMS`, in the shared `Budget`) bounds drafting from corrective evidence; verification of each candidate is paid from the same request budget. Drafting needs room for the draft call, every verification and the 2 held-back finalization requests; otherwise it is skipped and the reason (cap exhausted / not enough requests) is recorded | loop tests: candidates drafted, verified and reported; cap trims the drafter's output; cap 0 still researches and records the skip; an unsupported candidate stays in history, is excluded and the gap stays reported |
| The report's size was tied to nothing | `MAX_REPORT_CLAIMS` limits only report assembly; extra eligible claims become omissions "report claim limit (N) reached" and stay eligible | loop test |
| Coverage was judged per requirement, so evidence about one subject could mark a two-subject requirement addressed (it happened in the first live run: a PostgreSQL-only claim made the architecture requirement `addressed`) | Each requirement is split into persisted structured obligations (subject x dimension) by a model step (`agents/obligations.py`; `origin` = derived or fallback). The assessor judges obligations; the requirement status is DERIVED in code (`correction.aggregate`): addressed only if every obligation is. Cited claims must be eligible in this run; a lexical backstop requires a cited claim to mention the obligation's subject. No product name appears in code or prompts | unit tests: single-subject evidence gives `partially_addressed` whether the model errs or is honest; obligation IDs and supporting IDs validated; tables immutable and same-run; prompts scanned for names |
| A compound repaired claim could not become atomic children; `lineage()` followed only the first child | The Reviser may `split` a claim into 2-4 atomic children: each a new row (parent link, `version + 1`, provenance `split_into_atomic` part i of n) with no verdict, each verified afresh. A split the remaining budget cannot verify is not made. `lineage()` returns the whole family | unit and loop tests (one child supported and reported, the other unsupported and excluded) |
| Reserved finalization requests might exceed the budget | The reserve now includes the verification each repair/candidate will cost; every call still goes through the one `Budget`. Tested as a property for caps 0-16 and with every verification failing twice | `len(calls) <= cap` and a report in all 17 cases |

Checked and already correct (tests exist): search-limit skips end the job as a budget stop; skipped searches are not "already
searched"; old reports are unchanged by newer claim versions or exclusions (API-level check added); every original requirement
appears in the report, resolved or not.

## Live acceptance: can corrective evidence become usable claims? (one run)

Scenario: a CONSTRUCTED COPY of real stored rows (not a natural continuation of a run): the real sources 1-3 with their passages,
the real planner subquestions and the real claims #1, #2, #3, #6, #7 with their stored verdicts (3 of them currently eligible: #2, #6,
#7). Not copied: the two test inputs, the two pages an earlier run fetched but never drafted, and all earlier coverage, tasks,
reports and events. Nothing was edited or fabricated; the 7 requirements are the planner's own.

Bounds, stated before the run and not raised: 15 requests in total; 2 searches; 4 page fetches; 3 candidate claims; 12 report
claims; 1,500 output tokens per call, 6,000 total; 240 s; one corrective round. Cost estimate of the bound ~$0.075 (not a ceiling).

| Item | Observed |
|---|---|
| Outcome | `completed`, 1 corrective round, report 1 `claims_with_gaps` (1 of 7 requirements addressed, 5 partial, 1 unresolved) |
| Usage | 12 of 15 requests (obligations 1, coverage 2, researcher 4, drafter 1, verifier 3, synthesizer 1), 0 failed, 0 retries; 2 of 2 searches; 3 of 4 fetches; 3 of 3 candidates; 3,046 of 6,000 output tokens; 119 s of 240 s |
| Cost | tokens 19,922 in / 3,046 out; calculated from reported tokens and catalog prices $0.02672; reported by the Mesh usage API $0.02410 (all 6 Haiku calls were reported at 0.90x their calculation, all 6 Qwen-class calls at 1.00x; no cause established) |
| 1. Real gap -> targeted research | Tasks for the concurrency and scalability requirements (the model's gap text, with the unmet SUBJECT named, reached the Researcher). The generated queries asked for "official documentation": "SQLite and PostgreSQL concurrency model write locks comparison official documentation" and "... scalability limitations and capabilities compared official documentation". 2 new pages: the PostgreSQL manual (13.2 Transaction Isolation, S4, primary documentation) and a DataCamp comparison (S5). Four more tasks were skipped: no search budget left |
| 2. Passages -> candidate claims | `candidates_drafted`: claims #8, #9, #10 from passages S4-P21, S4-P3, S4-P22 (provenance `candidate: true`, round 1) |
| 3. Verification | each: integrity passed (results 16, 18, 20) and semantic `supported` (results 17, 19, 21) by the verifier, relying on its own cited passage; attribution `ok` |
| 4. Entry into the report | all three are in report 1 with their exact stored text and citations to S4 passages (postgresql.org); the check "report claim differs from the stored eligible claim (text, version, citations)" found 0 differences |
| 5. Unsupported candidate | NOT demonstrated live: none was unsupported. Covered by a fixture test only |
| Two-subject coverage | the four Q1 requirements were split into SQLite and PostgreSQL obligations. Assessment 0: architecture `addressed` only because both subjects had a claim (#6 SQLite, #2 PostgreSQL). Assessment 1: concurrency became `partially_addressed` (PostgreSQL addressed by #8, #10; SQLite unresolved) and transaction support likewise, instead of being marked addressed by PostgreSQL-only evidence. The lexical backstop never fired (the model was right) |

### Manual check of the new claims against the stored passages

#8 matches S4-P21 ("To guarantee true serializability PostgreSQL uses predicate locking ..."). #9 matches S4-P3 (Repeatable Read does not
allow phantom reads; acceptable because higher guarantees are permitted). #10 matches S4-P22 (locks based on data actually accessed;
finer locks may be combined into coarser locks "to prevent exhaustion of the memory"; shown in `pg_locks` with mode `SIReadLock`). I found no
factual error in the three.

### Weaknesses seen

- The new evidence answers the PostgreSQL side only. The SQLite side of concurrency, scalability and transactions stays open
  although the second new page (S5, DataCamp) has 7 passages that mention SQLite with concurrency or scalability. They were stored
  but never drafted: corrective drafting offers only the best 4 passages per subquestion (all four came from S4), and that ranking
  is not obligation-aware.
- #9 and #10 are compound (two and three assertions); atomicity is requested in the prompt, not checked, and a supported compound claim is not split.
- One obligation kept a joint subject ("SQLite and PostgreSQL / factors influencing choice"), so the subject split depends on the analyst.
- Only 2 of the 7 gaps got a search (the 2-search bound); the rest are reported as open.
- The script printed only 10 of the 12 requests (its "this job" threshold used the wrong database). Fixed in the script; the numbers above
  were recomputed from the stored records, not taken from that printout.

## Remaining gaps

Splitting supported compound claims (obligation-aware drafting input is done, see below); the unsupported-candidate path and a second corrective round live; no
accuracy measurement of coverage or verdicts (Phase 6).

---

# Phase 4 passage-selection fix (follow-up)

## Problem

Corrective drafting offered the best 4 passages per subquestion, ranked by overlap with the subquestion and listed source by source.
In the live run all four came from the first new source (PostgreSQL manual, S4), so the second source (S5, which discusses SQLite
concurrency and scalability) was never shown to the drafter although the SQLite obligations were unresolved.

## Change

`select_corrective_passages` (backend/agents/drafter.py) replaces the subquestion-level ranking for CORRECTIVE drafting only; the first
draft still uses the old selection. Inputs: the obligations not `addressed` in the latest coverage assessment
(`correction.unmet_obligations`).

1. Every unmet obligation is scored against the passages of the newly linked sources on its own: its dimension's content words must
   overlap the passage (at least `ASSOCIATION_MIN_OVERLAP`), and a derived obligation also needs the passage to mention its subject
   (the same lexical test as the coverage backstop). A whole-requirement fallback obligation is matched on its dimension and gap text.
2. Slots (`DRAFT_PASSAGES_PER_SUBQUESTION`) go first to the obligations, in requirement order, one distinct best passage each. When no
   distinct relevant passage is left, an obligation reuses an already chosen one (one copy offered, both obligations recorded).
3. Remaining slots are filled by relevance only. Nothing is added for source diversity: a source with no relevant passage gets none,
   and an obligation with no relevant passage gets none (recorded as `no_relevant_passage`). If nothing is relevant, drafting is skipped
   with that reason.
4. A passage already cited by an active claim ranks 2 below equally relevant unused evidence (never excluded).
5. A short passage (< 200 chars) may bring its preceding neighbour, at most `DRAFT_CONTEXT_PASSAGES` (new setting, default 1) per subquestion.
   Passages are never truncated.

Bounds are unchanged: `DRAFT_INPUT_CHARS`, `DRAFT_PASSAGES_PER_SUBQUESTION`, the candidate cap and `max_new`, the reserve, and the single
shared `Budget`; selection makes no model, search or fetch call. Only passages of this run's sources linked to this run's subquestions
can be chosen.

The `draft_input` event keeps `passages_offered` and `chars` and now adds `mode`, `limits`, `unmet_obligations`, `selection` (per
subquestion: passage, source, role `obligation_slot` / `relevance_fill` / `context`, the obligations it was offered for, score,
`already_cited`) and `obligation_outcomes` (`own_slot`, `served_by_shared_passage`, `no_relevant_passage`, `no_slot`, `no_budget`).

## Read-only check on the stored live scenario (`data/live-corrective.db`)

Replayed with no inference, search or fetch: round-0 unmet obligations, new sources {S4, S5}, subquestion 1, limits as in the run
(6000 chars, 4 slots).

| | Selected passages (subquestion 1) | Chars |
|---|---|---|
| Before (recorded in the live run, reproduced) | S4-P3, S4-P21, S4-P22, S4-P0 | 3,447 |
| After | S5-P5, S5-P11, S5-P13, S5-P18 | 3,674 |

Own slots: Q1.R2.O1 (SQLite concurrency) S5-P5, Q1.R2.O2 (PostgreSQL concurrency) S5-P11, Q1.R3.O1 S5-P18; Q1.R3.O2, Q1.R4.O1 and Q1.R4.O2
share S5-P18; S5-P13 was a relevance fill. **The selection now offers the SQLite evidence, but it also offers no S4 passage:** the S5
comparison passages score higher for the PostgreSQL obligations than the S4 manual passages do, and no source quota is imposed. Whether
a PostgreSQL manual passage would have been a better source for those obligations is not measured. These are lexical scores, not a
judgement of evidential quality.

## What this does not show

Only the passages offered changed. No drafter or verifier call was made on them, so whether SQLite claims would be drafted, verified
and reported is unknown; the earlier live run is not repeated.

## Tests (tests/test_phase4_selection.py, fixtures)

Relevant SQLite evidence is offered when the first source could fill all slots; slot assignment under a 2-slot limit; irrelevant sources
are never added; an obligation without relevant evidence gets none and is recorded; nothing relevant offers nothing; slot and character
limits; a shared passage is offered once and both obligations recorded; unused evidence is preferred over already cited evidence,
which stays usable when it is the only relevant one; another run's and unlinked sources are not used; fragment context on/off; the
drafter prompt and `draft_input` event agree. Full suite: 329 passed; lint clean.

## Phase 6 evaluation cases (recorded, not fixed here)

1. Compound claims survive as supported: live claims #9 (two assertions) and #10 (three). Atomicity is requested, not checked.
2. Joint-subject obligation: "SQLite and PostgreSQL / factors influencing choice" - one claim about one product cannot address it.
   Measure how often the analyst keeps a joint subject.
3. Premature `addressed`: a requirement marked addressed on a claim that covers only part of its dimension.
4. Analyst variability: the same requirement split into different subjects on different runs.
5. Selection quality: lexical obligation relevance versus human judgement of which passages answer an obligation, including cases
   where a comparison page outranks a primary source.
