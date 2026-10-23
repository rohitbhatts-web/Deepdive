# Demo script: recorded runs (no live research)

Everything below is shown from **recorded runs**: stored databases from real research and development runs made on 21-22 Oct 2026. Nothing is researched during the demo, no model, search or page fetch is
called, and no credits are spent. Say so on screen: "recorded run, not live".

## Before recording

The run databases hold fetched page text, so they are git-ignored and exist only on the machine that made them. Work on **copies** (opening a database with the current code adds tables to it):

```
mkdir data/demo
cp data/q02-compact-continuation.db      data/demo/continuation.db     # real question, report with gaps, citations
cp evaluation/results/pilot_e2e_evaluation_budget_attempt4.db data/demo/attempt4.db   # a failed run and an empty report
cp data/relevance-check-a1a2-v5.db       data/demo/followup.db         # direct answer versus related context
```

Serve one copy at a time (two terminals; stop each with Ctrl+C, never by program name or port):

```
DATABASE_PATH=data/demo/continuation.db uv run uvicorn backend.main:app --port 8000
cd frontend && npm run dev          # http://localhost:5173
```

Do not click "Retry from stored evidence" or any start button: those spend credits.

## Beats

1. **Question and run status** (`continuation.db`, run 1). Question: "When were HTTP/2 and HTTP/3 published as RFCs, and how do their transport layers differ?" Header: *Stopped; results are partial*; the reason is that retrieval was
   switched off for this continuation. Say: recorded 22 Oct; one compact retry from stored evidence; provider-reported charge $0.037 for that continuation.
2. **An exact claim and its citation.** Report tab, first section: "The HTTP/2 specification was published as RFC 9113 in June 2022." Click citation [1] (RFC 9113 - HTTP/2 · S1-P0).
3. **Evidence inspector.** The quote is highlighted inside its passage; the passage also shows "Obsoletes: 7540, 8740", i.e. that date belongs to a revision. Say: this report was assembled before answer-relevance
   existed, so it is labelled *not judged* and still lists a 2018 renaming claim under the HTTP/3 date question; that is the problem the next beat addresses.
4. **Direct answer versus related context** (`followup.db`, run 1; a development check on stored evidence, its question text is "[relevance check] networking", not a real research question). Section for the *current revision*: the claim is the answer.
   Section for the *original* publication: "No direct answer was found for this subquestion"; the same claim is listed under "Related context (verified, but it does not answer this subquestion)" and the requirement is named as open. State plainly: two cases,
   agreement with AI-assisted development expectations, not general accuracy.
5. **Unresolved requirements.** Back in `continuation.db`: the Coverage tab and the list "Evidence requirements not fully addressed" (the HTTP/3 date and number, and the HTTP/2 transport protocol name).
6. **Usage and failure transparency** (`attempt4.db`). Run 1: *Failed*, "Drafting failed after collection finished", the exact timeout reason, collected evidence preserved. Run 2: *Insufficient evidence*, the note "What the run collected"
   (no page was fetched; PDF results are unsupported) and the statement that this does not show the information does not exist. Usage tab: calls, tokens (unknown stays unknown); cost is not stored in the UI.

## Checks done on copies of the stored databases (22 Oct 2026, no research started)

Playwright on Edge against the real React UI; every request was a GET, no HTTP error status, no console error; axe (WCAG 2 A/AA) reported no violations on any screen below.

- **`followup.db` run 1 (beat 4):** one answered claim, "No direct answer was found for this subquestion" once, one "Related context" group with its "Context, not an answer" label. Both citations select their passage with the quote highlighted and "Obsoletes" shown; the selection survives a reload (`?claim=2`).
  No horizontal scroll at 390 px. The new context block alone: 0 axe violations.
- **`attempt4.db` run 2 (beat 6):** "Insufficient evidence" with the note "The run sent 8 search queries and stored 0 pages. For 3 of 3 subquestion(s) no URL was selected to fetch at any research step. 4 search result(s) were skipped because PDF documents are not supported."
  The first version of this note said "5 of 5" (it counted research steps, including correction rounds); that wording defect is fixed (`frontend/src/lib/collection.ts`, tests added) and the real data now reads 3 of 3. Run 1 reads "Failed", 11 model calls (2 failed).
- **`continuation.db` run 1:** Evidence tab lists All (6), Report-eligible (0), Not judged / error (5); claims verified under the older contract show "Needs re-judge" rather than a verdict. Usage tab: 23 stored calls, 3 failed, 39,264 prompt and 4,181 completion tokens.

Known display points to say rather than hide:
- **No dark theme.** With the browser set to dark, the UI stays light (page background unchanged); the dark-mode check therefore only confirms nothing breaks (0 axe violations). Do not claim dark-mode support.
- **Runs made by the development-check script** (`followup.db`) have the header "Run 1: Collected" and "Latest job requests used / limit: Unavailable", because no completion job ran. "Collected" there is not a research outcome; use `continuation.db` for the status beat.
- Cost is not shown in the UI; quote only provider-reported charges from the docs.
