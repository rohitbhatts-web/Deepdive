# Public export: what is withheld, what is unchanged, what it means for the evaluation

This repository is the **public export** of DeepDive. **Current release: the candidate of 28 Oct 2026** (database schema version 12; see "This release" at
the end). The evaluation dataset variant is **`public-export-v1`** (recorded in `evaluation/data/claim_evidence.json` under `public_export`) and is unchanged
in this release.
It was derived from a private working repository that holds the full evidence. Nothing was added to make up for what is withheld: there is no replacement evidence, no changed label, no changed claim text, no changed split.

## What is withheld

Nine of the 23 passages in the claim-evidence dataset come from four sources whose authors reserve all rights. Their text, and every quote taken from them, is **not published**:

| Source | Withheld passages |
|---|---|
| DataCamp (`datacamp`) | P5, P11 |
| Tinybird (`tinybird`) | P12, P18, P21 |
| DEV, "lovestaco" (`devto`) | P2, P4 |
| Logz.io (`logz`) | P2, P4 |

For each: the URL, title, retrieval date and the source's content hash stay; the passage is replaced by `text_sha256` and `text_chars`, and each quote by `quote_sha256` and `quote_chars`. Someone holding the full evidence can check every hash.
The other **14 passages** (8 Wikipedia, 6 PostgreSQL documentation) are published with attribution (`THIRD_PARTY_NOTICES.md`). "Six sources" is the total; "four sources" is how many are withheld.

Also replaced by a marker: the same excerpts inside `evaluation/claim_examples_source.py` and the review sheets (`evaluation/review/claim_review_batch1.md|csv`, `batch1_review_ai_assisted.md`). A short quoted fragment inside the rationale of CE26 was reworded.
`scripts/public_export.py` documents the exact transformation (it needs the private dataset to run).

## Examples affected

**15 of the 40 claim examples** cite a withheld passage and are unavailable; **25** have full published content.

| | Withheld evidence | Full evidence |
|---|---|---|
| dev (30) | CE04, CE06, CE10, CE18, CE26, CE28, CE30, CE31, CE34, CE35 (10) | 20 |
| held-out (10) | CE09, CE14, CE17, CE27, CE33 (5) | 5 |
| of the 12 reviewed pilot examples | CE30, CE31 | 10 |

Two of the 15, **CE04** and **CE10** (both dev, unlabelled), also have their **claim text withheld**: each restates one sentence of a withheld page in similar words (a nine-word run of the source), so it is treated as an excerpt and not reworded.
Their ids, split, proposed label and rationale stay; `claim` is `null`, with `claim_sha256` and `claim_chars` retained. No replacement claim and no new label exists for them.
For the other 13, claim text, proposed label, split and ids are unchanged. Every withheld example records `claim_sha256_full_evidence`, the claim-plus-evidence hash computed on the original.

## What can be verified from this export, and what cannot

`python -m evaluation validate` prints this under `public_export` (`hash_verification` and `unavailable_examples`), and `datasets.hash_report()` returns it.

| | Verifiable here (content published, hash recomputed) | Retained from the original, not verifiable here |
|---|---|---|
| Examples | 25 of 40: claim text and evidence text are published, the claim hash is recomputed | 15 of 40 (list above): the original hash is used as recorded |
| Passages | 14: text published | 9: `text_sha256` and `text_chars` only |
| Labels in force (12) | 10: CE01, 08, 11, 12, 16, 21, 23, 36, 37, 40 | 2: CE30, CE31 |
| Held-out examples in the split lock (10) | 5: CE05, 19, 22, 29, 38 | 5: CE09, 14, 17, 27, 33 |

The frozen split is preserved: ids, split assignment and `split.lock.json` are unchanged, and the aggregate held-out hash still matches. But that match is a combination: the 5 published held-out examples are recomputed, the 5 withheld ones contribute their retained hashes.
**The 5 withheld held-out examples, and the 2 withheld labels, cannot be checked from this export**; only someone holding the original dataset can verify them. Nothing here claims otherwise.
`validate` reports no problems when the withheld metadata is complete and consistent; it does not, and cannot, report that withheld hashes are correct.

## Evaluation commands

- `python -m evaluation validate` works and checks the withheld metadata (hashes present, no text or quote left in a withheld passage).
- Anything that needs the evidence text **refuses** with a message naming the examples and exits with code 2, before any client is built or request is sent: `verify --execute`, the pilot's claim arm, review-sheet rendering, and the relevance check when a case uses such an example.
- The only way around it is an explicit subset: `verify --available-evidence-only` (dev: 20 examples, 10 labelled) or `pilot --execute --available-evidence-only` (10 of the 12 pilot examples). The plan output states the reduced count, and the result file records it as a subset in its notes.
  A subset result is not comparable to a full-evidence result and must not be presented as one.
- End-to-end questions (`e2e-*`, the end-to-end arm of the pilot) fetch live pages and do not use the dataset's passages; they are unaffected.

## Which published results used the original full evidence

- `evaluation/results/pilot_verification.json` (frozen verification of the 12 reviewed pilot examples, 9 of 12 in agreement with the labels) used the **full** evidence including CE30 and CE31. It cannot be reproduced from this repository; a rerun on the available subset covers 10 of 12. The file is a **sanitized derivative**: one quoted phrase of a withheld passage inside the verifier's explanation for CE31 was replaced by `'[withheld]'` and a `sanitized_derivative` note was added to `metadata`; every measured value is unchanged. Its `metadata.dataset_sha256` is the hash of the original full dataset, not of this repository's `claim_evidence.json`.
- `evaluation/results/relevance_check*.json` use CE01 and CE03 (Wikipedia, published in full) and stored end-to-end claims; unaffected.
- `evaluation/results/pilot_e2e_*` and `q02_compact_continuation.json` come from live retrieval of Wikipedia, PostgreSQL and RFC pages; none of the withheld sources appears. `pilot_e2e_evaluation_budget_attempt3.json` and `..._attempt4.json` are
  **sanitized derivatives**: the recorded local database path was replaced by `<local path redacted>` and a `sanitized_derivative` note was added to `metadata`. Every measured value is unchanged.
- The labels are "AI-assisted human review; not independent or blinded." No held-out example has been run.

Derived statistics that were computed on the full dataset (for example `evaluation/data/overlap_report.json`, and counts in `docs/PHASE6A.md`) are kept as recorded and describe the full dataset.

## Residual overlap, stated plainly

After withholding the claim text of CE04 and CE10, a scan of every tracked file for word runs shared with the nine original passages finds none of six words or more. Runs of five words remain in three places, all in short claim or rationale text written for the dataset
(for example a five-word phrase shared with the DEV or Tinybird pages), and phrases of four words or fewer ("Postgres locally via Docker") occur in claims, rationales and tests. These are our own words and are kept.

## Hosted demo data

The hosted recorded demo (`docs/HOSTING.md`) is built from `demo/seed.sql.gz`, a sanitized dump of two recorded runs, and the update package
`demo/updates/0002-a1a2-v5.json.gz` (a recorded development check). Neither is a copy of a private run database: `scripts/build_demo_seed.py` and
`scripts/build_demo_update.py` insert rows into a fresh database and, for every source outside the cleared list (Wikipedia, IETF RFCs, PostgreSQL
documentation), replace the page text, its passages, the quotes taken from it and its search snippets by a marker, keeping URL, title, retrieval date and
content hash. The four rights-reserved evaluation sources are withheld there too. The package's only text-bearing sources are two RFC 9113 passages. The seed
and the package are gzip files, not `.db` files. Their text, and every GET response of the running demo, contain no phrase of six or more words from a
withheld page (the remaining five-word overlap is a URL fragment).

The cleared list is this project's publication policy for page text, not a legal determination. A visitor sees a withheld page's URL, title and date but
cannot read its text or check a quote against it on the site.

**The completed live run of 28 Oct 2026** (Git merge/rebase, described in the README) is **not** in this repository or in the demo data: all 8 of its
sources are outside the cleared list. It is shown only in a locally recorded walkthrough made from a private copy of its database.

## How the first export was checked

A scan of every tracked file for word runs shared with the nine original passages (run before publication) found no run of six words or more. The tree and
its history contained no databases, logs, `.env` files, secrets or local absolute paths.

## This release (candidate of 28 Oct 2026)

**What it contains.** The application code of the private working repository as of 28 Oct 2026, applied as one change on top of the previous public
version (`f7d4a5a`); the private development history is not part of the public history. New since that version:
- a request-budget policy that holds back the requests and time the final assessment needs (`docs/REQUEST_BUDGET_POLICY.md`);
- a cross-claim consistency check with report and UI warnings (`docs/CROSS_CLAIM_CONSISTENCY.md`);
- coverage bookkeeping fixes, fair drafting across subquestions, per-request transport diagnostics, a 90 s model-request deadline, and the Overview page
  with its answer preview, evidence map and request charts;
- database schema 9 to 12 (new tables only);
- the hosted demo database is now **built offline** (`python -m backend.demo_seed --build`, run while the Docker image is built) and only ever opened
  read-only; the server no longer creates or updates a database at start-up (`docs/HOSTING.md`). The demo's recorded content is unchanged;
- (added 29 Oct 2026) a coverage fix found by inspecting the second live run of 28 Oct (README): a word that the question and every subquestion contain is
  treated as a plan-wide qualifier, so a verified claim is no longer refused credit for omitting it, and the gap message names the claim that was not credited
  instead of saying no claim is about the subject. A claim naming neither structure is still refused. Synthetic regression tests in
  `tests/test_duplicate_ask_coverage.py` (9 of its 10 fail without the fix). No live run has exercised it.

**What was changed or left out for publication.**
- Test fixtures that matched text of uncleared web pages were replaced by synthetic wording that keeps each test's behaviour: the SQLite fixture paragraph
  (`tests/seed.py` and the tests that quote it), the two conflicting rebase claims of the consistency tests (`tests/test_consistency_check.py`), and the
  fixture pages of the offline acceptance test.
- `docs/CROSS_CLAIM_CONSISTENCY.md` describes the observed conflicting claims in our own words instead of quoting them. In `docs/PHASE3_REVIEW.md` the
  text of one recorded claim that closely restates a withheld page is withheld (as for CE04 and CE10), and a quotation from that page is described instead.
  `scripts/live_verify.py` uses a four-word fragment instead of a full sentence of it.
- Private engineering records (run investigations, diagnostics, submission drafts) and every run database stay in the private repository.

**Residual overlap, stated plainly.** A scan of every tracked file for runs of six or more words shared with the stored text of every uncleared page fetched
in the project's local run databases (40 databases) and with the nine withheld evaluation passages finds only short generic or structural runs: page
titles (kept by policy), number sequences and code, URL fragments, and phrases such as "what are the main differences between", "host 127.0.0.1 port",
"between HTTP/2 and HTTP/3", "both HTTP/1.1 and HTTP/2" (in recorded claims) and "merge commit that has two parents". The coverage-fix tests add one:
two adjacent planner requirements, "lists are mutable" and "tuples are immutable". These are kept.

**Checks of this version** (28 Oct 2026, offline; no model, search or page fetch):
- Backend: 810 tests passed, 8 skipped; Ruff clean; `python -m evaluation validate` reports no problems.
- Frontend: typecheck, 97 tests and the production build pass. Local tooling tests (`node --test ...`): 21 passed.
- Clean installation from an export of the staged tree (LF line endings, as a Linux checkout): `uv sync --frozen` (Python 3.12.13) and `npm ci`
  (Node 22.11.0, 245 packages), then the same suites with the same results; `python -m backend.demo_seed --build` produced schema 12 with 3 runs;
  `backend.public` started read-only on it and the 24 HTTP smoke checks passed (`scripts/smoke_hosted_demo.mjs`).
- Docker (Docker Desktop 29.7.2, Linux engine): the image builds from that export and builds the demo database (schema 12, 3 runs). Run as `render.yaml`
  configures it, with a disk at `/data` holding a database made by the previous version's own code (seed, then update 0002 at start-up): the container
  becomes healthy, the server runs as uid 10001, `/ready` is `{"status": "ready", "runs": 3}`, the 24 smoke checks pass, POSTs to start research, complete
  or retry return 403, `mesh_configured` is false and no key variable is set. As uid 10001 the database file and its folder cannot be written
  ("Permission denied", "attempt to write a readonly database"). The served file and the `/data` database were byte-identical afterwards. The only database
  in the image is the built one; no `.env` is in it.
- Misconfiguration: the new image pointed at the previous version's schema-8 file refuses to start ("older schema ... read-only mode cannot upgrade it")
  and leaves the file unchanged.
- Rollback: an image of the previous version (`f7d4a5a`), started on a copy of that disk with its own `DATABASE_PATH`, became healthy, served 3 runs and
  passed its 24 smoke checks, and left the database unchanged.
- An upgraded copy of the previous version's disk database holds exactly the same rows and ids as a fresh build (all 38 tables).
- Staged files were reviewed for secrets, local absolute paths, email addresses, request ids, databases and logs: none found.

After the coverage fix was added (29 Oct 2026, offline): backend 820 passed, 8 skipped; Ruff clean; `python -m evaluation validate` reports no problems;
frontend typecheck, 97 tests and build; tooling 21 passed; the overlap scan above repeated over every tracked file (44 local databases).

Not checked: Render itself, any deployment of this version, behaviour behind Render's proxy and TLS, and load.
