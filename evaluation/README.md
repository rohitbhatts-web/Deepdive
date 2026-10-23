# DeepDive evaluation (Phase 6A: datasets and harness, nothing run live)

> **Public export (`public-export-v1`).** Figures in this file about the claim-evidence dataset (23 passages from six sources, all 40 examples, the pilot's 12 claims, overlap and hash statistics) describe the **full private dataset**. In this repository the text of 9 passages (15 examples, including pilot examples CE30 and CE31) is withheld and commands that need it refuse; see [PUBLIC_RELEASE.md](../PUBLIC_RELEASE.md).

Two separate evaluations, because they answer different questions.

| | A. Frozen claim-evidence verification | B. End-to-end research and report |
|---|---|---|
| Question answered | Given a claim and its exact cited passages, does the verifier give the right verdict? | Does the whole pipeline produce a complete, correctly cited report, and what does the correction loop change? |
| Input | `data/claim_evidence.json` (40 examples) | `data/questions.json` (12 questions, explicit answer requirements) |
| What varies between runs | only the verifier (model, prompt, rubric) | everything, including new searches and new evidence |
| Command | `python -m evaluation verify` | `python -m evaluation e2e-execute` / `e2e-collect` |
| Spends credits | model calls only (about 1 per example) | model, search and fetch |

Do not read B as a verification measurement: the correction loop adds evidence, so a difference between initial drafts and the final report mixes
verification, repair and new research. To compare verifier configurations fairly, export the INITIAL drafts of a B run
(`e2e_eval.frozen_drafts`) and re-verify those very claims with A under each configuration (a paired comparison on identical inputs).

## Rules the code enforces (and tests check)

1. **No model-proposed label is ever ground truth.** `claim_evidence.json` carries `proposed` labels marked `provisional`. Correctness metrics
   read only `data/human_labels.json`, only for entries with a reviewer name and a claim hash that still matches. With no human labels every
   correctness metric is N/A and the harness says so. The only thing computed from proposed labels is `agreement_with_provisional`, named and
   documented as not accuracy.
2. **The system's own verdict is never a correctness label.** Coverage statuses from the pipeline are carried in results as `system_coverage`, for
   comparison only; completeness comes from human labels.
3. **Held-out items are locked.** `data/split.lock.json` was recorded before any tuning against these datasets and holds the held-out ids and
   hashes. `python -m evaluation validate` fails if a held-out question or example changes or moves. Every change made in response to results goes
   in `data/tuning_log.json` with the ids it was evaluated on; held-out ids may appear only in a `final_heldout_run` entry. A question already used
   for tuning (Q01, which drove Phases 3 and 4) is development-only.
4. **Denominators are always reported, zero denominators are N/A**, never 0 and never 1. Unlabelled records are counted and excluded.
5. **Outcomes stay separate:** integrity failure (never reaches the model), semantic rejection (unsupported, contradicted), partial support, model
   error and "not judged" (budget stop) are different columns of the confusion matrix and different counters.
6. **Deleting everything cannot look good:** supported recall, lineage retention, `answerable_with_no_report_claims` and the paired
   accept-all-versus-verified table sit next to every precision-style number.
7. **Live commands are dry runs unless `--execute`.** Held-out splits additionally require `--final-heldout`.

## Datasets

- `data/questions.json`: 12 questions over 12 domains (databases, networking, energy, security, a private company, programming languages, privacy
  law, EV markets, cosmology, technology forecasting, DevOps, batteries). 8 development, 4 held-out (Q09-Q12). Categories: factual comparison, dates
  and numbers, conflicting sources, attributed recommendations, abstention (Q05, Q10), compound claims (Q06, Q11), multi-subject obligations,
  and relevant-evidence-crowded-out-in-passage-selection (Q01, the observed SQLite/PostgreSQL case). Requirements say what an answer must contain; they
  assert no facts. Q05 states an assumption (no public audited figure) that a reviewer must check before scoring.
- `data/claim_evidence.json`: 40 claim-evidence examples, 30 development and 10 held-out. Evidence is REUSED stored evidence (URL, retrieval date,
  content hash, exact passage text, database and source id of origin, SHA-256 of that database). 15 claims are deliberate synthetic alterations
  (changed quantity or date, swapped entity, negation, plan turned into fact, dropped qualification, added unsupported detail), each labelled with what
  was altered. Others test genuine-but-irrelevant quotes, attribution, qualified dates and compound assertions. `proposed` labels are provisional.
  **Scope: this claim set is single-domain (SQLite/PostgreSQL evidence). It is not a cross-domain evaluation** and says nothing about other domains.
  All 6 sources are from the Phase 1-4 development runs. Of the 10 held-out examples, 7 use a source that development examples also use and 6 cite a
  passage a development example also cites (`data/overlap_report.json`, `python -m evaluation overlap`): held-out claims are new CLAIMS about
  familiar pages, not unseen sources.
  **Label rules R1-R7** (stored in the dataset, printed on the review sheet) decide ambiguous cases: R3 contradicted: an explicit evidential
  contradiction of any assertion takes precedence over partial support (rubric `claim-labels-v2`); R4 partially_supported means support that is incomplete
  or overstated WITHOUT explicit contradiction; R5 a weaker core is
  partially_supported; R6 a central fact not established is unsupported. The sheet shows exactly the text the verifier receives.
- `review/claim_review_batch1.md|csv`: the first 12 development examples for human labelling. Fill `human_label`, `reviewer`, then
  `python -m evaluation import-labels evaluation/review/claim_review_batch1.csv`.
- `review/batch1_review_ai_assisted.md|csv`: the same batch as reviewed with AI assistance (suggestions shown last); see the next section.

## Rubric version, label versions and provenance

- **Rubric version.** The label rules are versioned (`label_rubric_version` and `label_rubric_history` in `claim_evidence.json`, source in
  `claim_examples_source.py`). `claim-labels-v1` is kept word for word. `claim-labels-v2` (2026-10-21) changed R3 and R4 only: *an explicit
  evidential contradiction takes precedence over partial support; partially_supported applies when support is incomplete or overstated without
  explicit contradiction.* It was adopted before any label was imported, so no confirmed label changed. The product verifier was aligned afterwards (verifier rubric `attribution-v2`, see `docs/PHASE6A.md`); results from before that carry
  `attribution-v1` and a different prompt hash, so they must not be pooled with later ones as if the prompt were the same.
- **Flags.** Provisional development labels made under v1 that v2 would change are flagged `needs_human_review` (`rubric_flags`; CE13 and CE16, shown
  on the review sheets). The proposed labels are not edited. Held-out CE19 has the same shape; it is left exactly as written and reviewed with the
  held-out set.
- **Versioned corrections.** Labels are append-only. An entry has `version` (absent = 1), `rubric_version`, `provenance`. To change an existing label
  import with `--correct` and a `correction_reason` column: a new entry (version n+1, `supersedes`, `previous_label`, `previous_reviewer`, the new
  reviewer and date) is appended; earlier entries stay in the file. The current label is the highest valid version; an invalid correction (AI-like
  reviewer, no reason, skipped version) leaves the previous version in force, and `validate` lists it. `python -m evaluation label-history CE16`
  prints every version. Before this change an already-labelled id could only be changed by hand-editing the file, which lost the old label.
- **Provenance on reports.** Every imported label carries `provenance` (default: *AI-assisted human review; not independent or blinded.*, override
  with `--provenance` only if a review really was independent). Result files (schema `/2`) put `label_statement`, `label_provenance`,
  `label_rubric_versions` and `label_versions` in the metadata, repeat the statement in `notes` and next to the metrics, and the CLI prints it.
  AI-assisted labels support no inter-annotator-agreement or unbiased-accuracy claim.

## Labels, review and the pilot

Only a person creates an authoritative label: `data/human_labels.json` entries need `source: human`, a non-AI reviewer name and a matching claim hash,
and `import-labels` needs `--i-am-the-human-reviewer`. The pilot (`python -m evaluation pilot`) is 12 human-reviewed development claim examples plus
development questions Q02 and Q05; it refuses held-out ids and refuses to run until all 12 are labelled. Q05 was revised to evidence-seeking (the original
assumed, unverified, that no figure is public); the revision is recorded in the question. End-to-end runs use 40 requests per question against a product
default of 20: every result records each limit that differs from the default (`budget_class`, `budget_differences`) and is not default product
performance. See docs/PHASE6A.md for the reasons and the pilot budget.

## Commands (all from the repository root)

```
uv run python -m evaluation validate                 # datasets, split lock, tuning log; exit 1 on any problem
uv run python -m evaluation review-sheet --batch 1   # (re)write the human review sheet
uv run python -m evaluation import-labels FILE.csv   # add labelled rows (needs a valid label AND a reviewer) to human_labels.json
uv run python -m evaluation verify --split dev       # A, dry run: prints the plan
uv run python -m evaluation verify --split dev --execute --max-requests 60 --out evaluation/results/verification.json   # LIVE
uv run python -m evaluation e2e-plan --split dev     # B, dry run: per-question and total worst-case limits
uv run python -m evaluation e2e-execute --split dev --execute --db evaluation/results/e2e.db   # LIVE
uv run python -m evaluation e2e-collect --db FILE.db --run-id 1 --question Q02               # read one finished run (read-only)
uv run python -m evaluation schema                   # write the JSON schemas of the result files
uv run python -m evaluation lock                     # record the split ONCE (refuses to overwrite)
uv run pytest tests/test_eval_metrics.py tests/test_eval_datasets.py tests/test_eval_harness.py
```

## Metrics

A (labelled records only): `supported_precision` (of claims called supported, the share gold-supported); `supported_recall` (of gold-supported, the
share called supported; also `_judged_only`); `unsafe_leakage` (of gold partial/unsupported/contradicted, the share called supported); rejection
detection, defined as gold unsupported or contradicted: `semantic_rejection_recall` (called unsupported or contradicted),
`blocked_from_report_recall` (anything not supported, a weaker number that rewards errors), `integrity_blocked`, `model_error_rate`;
`four_verdict_accuracy` over judged records; the 4 x 7 confusion matrix; per-class precision and recall; `paired_accept_all_vs_verified`.

B: `completeness` (human labels per requirement: addressed, partially_addressed, missing, abstained_appropriately, abstention_failed),
`citation_integrity` (deterministic re-check from stored data: passage exists in this run, quote found, URL matches the source, report text equals
the stored claim version), `initial_vs_final` (by claim lineage: retention, new-since-initial, precision against human labels),
`abstention_and_retention`, `usage`.

Usage fields (latency, calls, returned tokens, cost) are reported only when stored: a total over some rows says how many rows reported, and a field
nothing reported is "Unavailable". `calculated_cost_usd` (tokens x a price table) and `provider_reported_cost_usd` are different quantities and never
merged; the provider figure is Unavailable unless supplied.

## Result files

`results/*.json` follow `schemas/verification_result.schema.json` and `schemas/e2e_result.schema.json`. Every result carries `metadata`: models per
role, rubric version, SHA-256 of each agent prompt, non-secret settings, budgets, run ids, dataset hashes, whether anything was executed, whether
replies came from a test fixture, and the label basis (`authoritative-human` or `none`). Fixture results are harness checks, not project performance, and
must not be published as such.

## Validity limits to remember

- A (frozen): only the cited passages are stored for each example, so the pipeline's neighbouring-passage context is absent, and two passages of one
  source cited by one claim would see each other as context. All shipped examples cite one passage.
- Examples written as faithful restatements and examples altered on purpose do not estimate the error rate of real drafted claims. Use B's initial
  drafts (human-labelled) for that.
- Run each configuration on the same split; never mix development and held-out numbers; run the held-out split once, after tuning ends.
- Provisional labels, once a human disagrees, are simply wrong; keep the human label.
