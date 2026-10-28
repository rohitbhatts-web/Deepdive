# Request-budget policy (27 Oct 2026)

Every job has ONE budget of model requests (`max_model_requests`, 20), plus search, fetch, output-token and time limits. None of these limits changed. This
document records why the final assessment kept being starved, the policy that now prevents it, how it was tested offline, and what it does not do.

## The recurring root cause (evidence: preserved databases, read-only)
| Job | Requests | By stage | Outcome |
|---|---|---|---|
| Run 1, original end-to-end job | 11 of 20 | planner 1, researcher 8, relevance 1, drafter 1 (failed: timeout during Windows standby) | failed before verification |
| Run 1, retry job (fresh budget, no research) | 19 of 20 | drafter 2, verifier 12, coverage 4, synthesizer 1 | completed; two coverage calls repeated an unchanged claim set |
| Run 2, end-to-end job | 20 of 20 | planner 1, researcher 8, relevance 1, drafter 1, verifier 9 (7 claims + 2 validation retries) | stopped in verification: no coverage, no consistency check, deterministic ordering |

- The only reservation (`graph.RESERVE = 2`, "one final coverage assessment and one synthesis") was consulted inside the correction loop, i.e. AFTER the first
  coverage assessment. Planning, research, relevance, drafting, verification (including validation retries), re-judging and the first coverage assessment's
  obligation derivation were not bound by it. The earlier statement that the final assessment always had capacity was therefore false: it held only for the
  correction loop, and the scripted tests behind it never made verification expensive.
- In ONE 20-request end-to-end job the intended workflow does not fit: collection and drafting typically take 11 requests, verifying 8 drafted claims takes 8
  more, and the final assessment needs at least 3 (obligation derivation once per run, coverage, the consistency check). Run 1 would have hit this had it not
  failed earlier; the retry only succeeded because it was a separate job with a fresh budget and no research. Run 2's two validation retries (the verifier's
  `explanation` exceeded its 500-character limit twice) made it immediate: the eighth claim (ID 20) was never verified and nothing was assessed.
- Two further leaks in the correction loop: a round whose tasks changed no claim was still followed by another coverage assessment of the same claims (the retry
  run's calls 28/29), and when the loop stopped on its budget check the synthesizer was skipped even with a request still free.
- Reporting then made the gap look like a finding: requirements with no assessment (`not_assessed`) were counted and shown as "unresolved", "Open" and "still
  not fully answered".
- Effective configuration of the Run 2 server (process started 15:16:17Z from the clean committed tree e086514, no auto-reload): limits 20 / 8 / 12 / 15,000 /
  300 s, planner/researcher/drafter qwen3-235b, verifier and coverage claude-haiku-4.5, SDK retries 0, 90 s request deadline.

## The policy (backend/budget_policy.py, enforced in backend/budget.py)
- **Central enforcement.** `Budget.charge_model_request` runs before every request attempt: first attempts, validation retries and requests that then fail.
  It refuses an OPTIONAL request that would use held capacity (`ReserveReached`). No stage can bypass it.
- **Required work** is the final assessment: obligation derivation (once per run), the coverage assessment and the consistency check. It runs inside
  `budget.required()` and may use held capacity. A coverage validation retry may therefore use the request held for the consistency check; the check is then
  reported as not checked.
- **Optional work** is everything else: planning, research, relevance, drafting, verification, re-judging, corrections, re-verification and synthesis.
  Synthesis only orders claims and falls back to deterministic ordering; it is attempted whenever a request is left.
- **Holds per phase** (recorded as `budget_held` events):
  - collection holds the final assessment + 1 drafting request + verification of at least one claim per subquestion;
  - drafting, verification, re-judging and corrections hold the final assessment;
  - the report phase holds nothing.
- **Claim production is bounded:** drafting may produce at most as many claims as can still be verified, keeping one request of slack for a retry or failure
  (corrective drafting already reserved its verifications).
- **Reaching a hold** ends that optional stage, records a `budget_curtailed` event and continues to the required work. Unresearched subquestions are marked
  incomplete; unverified claims stay `awaiting_verification`, are never marked supported and are listed as not eligible in the report. The verification stop
  reason names the claims left unverified and what the requests were held for; the completion and report stop reasons add "partial result: ..." with everything
  left undone (and so does the collection reason when research was cut).
- **No repeated assessment:** when a correction round changes no claim (by eligible claim ID and version) and drafts none, the loop ends and the latest
  assessment stands.
- **Time is held too (28 Oct 2026):** while requests are held for the final assessment, 15 s of job time per held final-assessment request is held as well
  (`budget_policy.FINAL_CALL_SECONDS`, an estimate from the seven recorded final-assessment calls, 2.2 to 10.5 s). An optional call may neither start nor
  run into that time: its deadline ends where the held time begins. **Output tokens are not reserved**; they stop the job wherever they run out and the
  report says what was not assessed.
- **Fair drafting (28 Oct 2026):** when the drafting cap cuts the drafter's claims, they are kept one per subquestion first (`drafter.fair_take`) and
  stored in that order, so a later budget cut falls on a second claim, not a subquestion's only claim. Passage matching counts inflected forms as the same
  word (`relevance.content_words`), so pages that say "merging" are offered for a subquestion about "merge".
- Also fixed: `continuation.plan` computed `round_base` as `(latest_round or -1) + 1`, which returned 0 again when the latest stored round is 0.

## Status reporting
- The report keeps three groups apart: requirements assessed as answered, assessed as NOT (fully) answered (`unresolved_requirements`), and NOT ASSESSED
  (`unassessed_requirements`; `coverage_assessed` is false when no assessment exists at all). An absent assessment is never presented as "unanswered".
- UI wording only, existing components and styles: a neutral "Not assessed" status, "Requirements not assessed" as the report outcome label when nothing was
  assessed, the Overview's "Still unanswered" no longer lists unassessed requirements as "Open" and says they are unknown, and the Overview headline counts only
  requirements assessed as unanswered. The Markdown lists unassessed requirements under their own heading.

## Offline demonstration (tests/test_budget_policy.py: the production graph through the API, scripted replies only)
- Run 2's sequence with two validation retries: exactly 20 requests. Drafting is capped at 5 claims (8 returned); 4 are verified and the 5th is left unverified
  and reported as such. Then obligations, coverage and the consistency check run. Synthesis gets nothing (deterministic ordering). The report is assessed and
  marked "partial result".
- The same job with the holds switched off reproduces the failure: 20 requests, no coverage, and a report that now correctly says the requirements were NOT
  ASSESSED rather than unresolved.
- A transport failure in verification: counted, recorded as a verification error, never supported; the final assessment still runs.
- More drafted claims than capacity: capped, every drafted claim verified, synthesis gets the remaining request.
- Correction loop swept over request limits 3 to 16: the cap is never exceeded; a coverage assessment follows any correction work; no two assessments of an
  unchanged claim set back to back; the consistency check runs whenever at least two claims are eligible.
- Output tokens, or time, running out before the final assessment: the job stops, nothing unverified is called supported, and the report says the requirements
  were not assessed and consistency was not checked.
- A job that fits: every stage runs, nothing is curtailed, outcome "all requirements addressed".
- Browser check of Run 2's stored report (a copy, the real backend app, no job started): the corrected wording on Report and Overview at 1280 and 390 px,
  13 of 13 checks.

These tests prove budgeting, ordering and reporting with scripted replies. They do not show research quality, answer accuracy or how often a real run fits.

## Remaining limitations
- 20 requests are tight for collection + verification + assessment in one job. The policy makes the outcome deliberate and honest; it does not make a complete
  answer fit. Typical end-to-end jobs will now verify fewer claims (or research fewer subquestions when there are many) in exchange for an assessment.
- The verification floor (one claim per subquestion) and the drafting slack (one request) are design choices, not measured optima.
- Output tokens are not reserved. The time hold is an estimate; a slower final-assessment call can still miss the deadline.
- Validation retries remain one per invalid reply. The verifier's 500-character explanation limit caused Run 2's retries; it was left unchanged here.
- Reports stored before this change keep their stored statuses; only their rendering (labels and Markdown headings) reflects the not-assessed distinction.
