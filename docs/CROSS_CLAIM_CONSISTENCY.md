# Cross-claim consistency check (27 Oct 2026)

Why it exists: a preserved live run on the Git merge/rebase question (its database is private and not published) reported two claims that were each faithfully
supported by their own cited passage yet disagreed. In our words: claim 1 said a rebase makes one new commit for every original commit; claim 6 said a rebase
combines all of the changes into a single commit on the target branch. The page text they quoted is not reproduced here. The tests use synthetic claims of the
same shape (`tests/test_consistency_check.py`). The verifier judges a claim
only against its own evidence, so "supported" never meant "consistent with the other claims", and nothing compared claims with each other. Citation support does not
establish factual accuracy, and this check does not establish it either.

Everything below was built and tested offline with scripted model replies. Nothing here measures whether a real model detects real contradictions.

## What it does
- **Where:** `backend/agents/consistency.py`, called from the `report` node in `backend/graph.py` after the final coverage assessment and BEFORE synthesis.
  With fewer than two eligible claims nothing is compared and no request is made (`not_applicable`).
- **Input:** every report-eligible claim with its text, version, subquestion and cited quotes. The cap is `coverage_input_chars`, separators included. Quotes are
  shortened tier by tier (300, 120, 60 characters, then the passage ID only) before any claim is left out. Claim text and passage IDs are never cut. Claims that do
  not fit even then are recorded as unseen. Each claim's evidence state is stored: `full`, `shortened`, `absent` (no quote text), `id_only`, `none` or `not_shown`.
- **Reply:** `claims_reviewed`, plus at most 4 findings, each with claim IDs, a kind (`conflict`, or `scope` / `condition` / `time` / `terminology` for
  differences that are not conflicts), an explanation, and (claim, passage) evidence pairs. Code drops any reference to a claim, or to a passage for that claim,
  that was not in the input. An ID-only reference is marked `text_shown: false` and is never described as evidence text the model read.
- **Validation in two layers (prompt v2):** the model is shown the strict schema, but the reply is validated as an ENVELOPE first (`claims_reviewed` a list of
  integers, `findings` a list). An envelope that does not parse, or lacks either field, makes the check `failed`. Each finding is then validated ON ITS OWN (kind,
  2 to 4 integer claim IDs, explanation length, evidence pairs, at most 4 findings). A rejected finding is recorded with its reason and structured errors, the
  others stand, and the check is `partial`. Malformed output never becomes `reviewed` or "no conflicts flagged".
- **Explanation length:** the prompt asks for at most 400 characters and a finding is stored up to 600. A longer explanation is never trimmed (trimming can change
  what it says): that one finding is rejected with the reason recorded. The former single 240-character limit, with no length stated in the prompt, is what made
  the live attempt fail as a whole.
- **Two separate fields:** completeness (`reviewed`, `partial`, `failed`, `not_checked`) and findings. A partial or failed check still lists its valid conflicts.
  `reviewed` only means the model listed every shown claim; it is the model's assertion, not proof that every pair was compared. "No conflicts flagged" is
  never worded as "consistent", and it is said only for a `reviewed` check.
- **Truncation:** a reply cut mid-JSON is `failed`; a parseable reply that ended with `finish_reason = length`, or used its whole output allowance, is `partial`.
- **Rejected replies:** when a reply is rejected in whole (`envelope`) or in part (`items`), its text is kept in `model_call_rejections` for local diagnosis only.
  It is bounded to 16,000 characters, with the original length and a truncation flag; configured secrets are redacted; it is untrusted text, never served by the API
  or shown in the UI or a report. `model_calls.error`, which the API does return, now holds only a summary built from the structured errors WITHOUT input values
  (pydantic's own message quoted the model's text). This applies to every agent's rejected replies; other agents keep their one corrective retry.
- **Reuse:** an assessment belongs to one exact input. Its fingerprint hashes the rendered prompt (claim text, selected evidence), the eligibility state of each
  claim, the content of every cited passage, the model, the prompt and schema versions and the packing configuration. A stored assessment is used only for an
  identical fingerprint; otherwise the report says "not checked". While the latest record for an input is incomplete, valid findings of earlier attempts on the SAME
  input are shown with it (`carried_from_check`) and the incomplete status stays visible. Nothing is carried from another input.
- **Attempts:** at most one per fingerprint per job, one request, no corrective retry (`LLM.call(attempts=1)`; the SDK already has `max_retries=0`). A changed
  claim set gets at most one eligible attempt, subject to the remaining budget and to exact-fingerprint reuse. A `reviewed` check, or a `partial` one caused by the
  input not fitting, is reused rather than repeated.
- **Report:** `build_report(..., settings=...)` snapshots the state into `report_consistency` / `report_consistency_findings`. Flagged claims stay in the report and
  are never declared false. The synthesizer is told which claims are possible conflicts and cannot drop them as "duplicates"; the deterministic fallback keeps the
  same claims and warnings. Verifier verdicts, report eligibility, coverage gaps and omissions are not used or changed to carry conflicts.
- **API:** additive `consistency` object on the report, and `conflicts` on each report claim. Reports made before this existed read `{"status": "not_recorded"}`.
- **UI (approved scope only):** one notice on the Report page and a compact one in the Overview's answer panel, using the existing `Notice`; a "Possible conflict
  with claim N" `Pill` on every affected claim (answers, related context, cross-references, Overview answer cards); the request-count stage is labelled
  "Compare claims". Reports without consistency data, `not_recorded` and `not_applicable` render exactly as before.

## Budgets (unchanged: 20 requests, 1,500 output tokens per call, 15,000 per job, 300 s)
Since 27 Oct 2026 the consistency check is part of the REQUIRED final assessment under the request-budget policy: its request is held back from
optional work from the start of the job, not only during corrections (see `docs/REQUEST_BUDGET_POLICY.md`; the former `RESERVE = 2` is gone).
- Order at report time: final coverage (reserved) -> consistency check -> synthesis. With one request left the check takes it and synthesis falls back to the
  existing deterministic ordering, with the same claims and warnings. In practice that means tight jobs lose model ordering more often.
- The output allowance is `min(1,500, output tokens left in the job)`. Below 1,000 tokens the check is skipped as `not_checked` with the reason. The 1,000 is a design
  estimate from the schema limits (4 findings with explanations at the 600-character bound, at about 4 characters per token, plus IDs, evidence and
  `claims_reviewed`), not a measurement, and not a guarantee that the largest valid reply fits; a cut reply can never be `reviewed`. (It was 800 with the former
  240-character limit.)
- Insufficient requests or time (`min_call_seconds`) also give `not_checked` with the reason, and the report is still built.
- Because the reserve protects the final coverage plus one more request, the real graph always has at least one request at report time; the zero-requests case
  was tested by calling `ensure_check` and `build_report` directly.

## Storage
Schema version 12 adds `model_call_rejections` (append-only, see above). Schema version 11 adds `consistency_checks`, `consistency_findings`, `report_consistency` and `report_consistency_findings`. All four are append-only (triggers
reject UPDATE and DELETE) and application code only inserts into them. A copy of the preserved v10 retry backup migrated on connect with every existing table's row
count and its report unchanged; read-only opens of older files work without the tables. Historical databases and reports were not migrated or regenerated.

## Verification (offline)
- Backend: 758 tests passed (737 before the rejected-reply fix, 755 after it, 758 after the Markdown fix), Ruff clean (`tests/test_consistency_check.py`,
  `tests/test_consistency_flow.py`, `tests/test_consistency_rejections.py`, plus earlier suites). Frontend checks were not rerun after those fixes: no response
  shape changed.
- Frontend: typecheck, 81 tests and the production build passed (`frontend/src/test/consistency.test.tsx`).
- Browser: Edge + playwright-core (installed only in a scratch folder) against the simulated backend (`scripts/ui_sim_backend.py`, no model, search or fetch) on a
  disposable database seeded with five scenarios: historical report, cross-section conflict, context-only conflict, partial check with a known conflict, and
  not checked. Report and Overview at 1280 and 390 px: 98 of 113 automated checks passed. Notices and labels were readable and unclipped, claim links opened the
  inspector, and the historical report showed no consistency text.

## Known issue left as is: narrow-width overflow (pre-existing)
The 15 failing browser checks are all the same pre-existing layout issue at 390 px: the "Run history" and "Usage" header links extend past the viewport on every
page, and the Overview's "Model requests" card makes the page 433 px wide. It occurs on the historical report with no consistency element, and was reproduced on
the original frontend with the consistency edits set aside. It is not caused or fixed by this change.

## Remaining limitations
- Detection accuracy, latency, token use and cost of the check are unmeasured; scripted tests show bookkeeping, not judgment.
- A conflict the model does not flag stays unflagged.
- Shortened or ID-only evidence is recorded and shown, but the model then judges on less text.
- Historical reports get no retroactive warning by design.

## Validation record (27 Oct 2026): two single-request live tests and an offline preview
Both live tests were ONE consistency-only request each, on a disposable copy of the preserved retry database, with the production `ensure_check` path, the
configured coverage model (anthropic/claude-haiku-4.5, structured output on), all 12 eligible claims at full quotes (4,529 of 8,000 characters, none unseen), no
hint about any claim in the prompt, SDK and application retries off, a 1,500-token cap and the 90 s deadline. Raw prompts, replies and databases are kept outside the
repository, in a local diagnostics folder.

- **Prompt v1: failed.** The reply arrived complete (`finish_reason` stop, 498 output tokens) but was rejected as a whole: 3 validation errors, the first being a
  finding explanation longer than 240 characters. The other two errors and the reply text were not recoverable (only a 300-character error string was stored).
  The target pair was inconclusive. This led to the two-layer validation, the explicit explanation bound and the rejected-reply storage described above.
- **Prompt v2: reviewed, target missed.** One request (2,262 prompt and 456 completion tokens, 7.1 s, one routing attempt, no fallback, no `x-cache` header; cost
  unknown). The assessment validated as `reviewed`: every finding passed validation, nothing was dropped, nothing was unseen or shortened, and no rejection was
  stored. It contained three findings. (1) A `conflict` between claims 5 and 6. (2) A `terminology` finding for claims 1 and 6. (3) A `scope` finding for claims 2
  and 3. The predeclared target was "claims 1 and 6 flagged together as a `conflict`". It was NOT met: the pair was reported only as `terminology`. This is a
  target miss, recorded as such. The separate 5/6 conflict is a different pair and does not count as the target. Whether either rebase claim is factually correct is
  not established by this test.
- **Offline report preview (no model).** The stored v2 assessment was attached to a report built by the production builder with deterministic ordering, on a copy of
  the test database. In the API and the downloaded Markdown the conflict reaches the reader correctly: a notice names claims 5 and 6 and their explanation; claim 5
  carries its label at both places it appears (as a cross-reference in Section 1 and in its own section) and claim 6 carries its label; claims 1, 2 and 3 carry
  none; the terminology and scope findings appear only in the Markdown's "Differences the model judged not to be conflicts" list and nowhere as a contradiction.
  In the Overview the compact notice shows, but claims 5 and 6 are not among the three previewed answer cards ("View full report (5 more claims)"), so no
  Overview card carries a label in this case. Browser checks (Report and Overview at 1280 and 390 px, claim link, notices): 28 of 28 passed. One defect was found
  and fixed: the Markdown printed a `reviewed` assessment's assurance twice (the fixed status text and the stored reason); a `reviewed` check now states it once and
  every other status gives its reason after "Reason:". The preview report inherits the HISTORICAL coverage judgments of the retry run, including its stale
  "no cited claim is about Git merge" gaps, which the later coverage fixes only change for new runs.

## Readiness
- Response validation succeeded in one live test (v2). v1 had failed, and its failure was addressed.
- One conflict was flagged (claims 5 and 6), but the predefined target (claims 1 and 6 as a conflict) was missed; those two claims were classified as a terminology
  difference.
- General detection accuracy and complete end-to-end answer quality remain unproven. Two requests on one model and one claim set are anecdotes, and scripted tests
  show bookkeeping, not judgment.
- The on-screen warning shows conflicts only. A terminology or scope finding about a real disagreement, like the one on claims 1 and 6 in v2, is visible only in
  the API and the downloaded Markdown.
