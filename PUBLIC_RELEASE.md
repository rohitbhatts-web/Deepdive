# Public export: what is withheld, what is unchanged, what it means for the evaluation

This repository is the **public export** of DeepDive, dataset variant **`public-export-v1`** (recorded in `evaluation/data/claim_evidence.json` under `public_export`).
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

The hosted recorded demo (`docs/HOSTING.md`) is seeded from `demo/seed.sql.gz`, a sanitized dump of two recorded runs. It is not a copy of a private run database: `scripts/build_demo_seed.py` inserts rows into a fresh database and, for every source outside an allow-list
(Wikipedia, IETF RFCs, PostgreSQL documentation), replaces the page text, its passages, the quotes taken from it and its search snippets by a marker, keeping URL, title, retrieval date and content hash. The four rights-reserved evaluation sources are withheld there too.
The seed is a gzip SQL dump, not a `.db` file. Its text, and every GET response of the running demo, contain no phrase of six or more words from a withheld page (the remaining five-word overlap is a URL fragment).

## How this export was checked

A scan of every tracked file for word runs shared with the nine original passages (run before publication) found no run of six words or more. The tree and its history contain no databases, logs, `.env` files, secrets or local absolute paths.
The history is the initial commit plus one export commit on top of it; the private development history is not part of it.
