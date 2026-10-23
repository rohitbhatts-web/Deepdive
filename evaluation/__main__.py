"""Evaluation commands.   uv run python -m evaluation <command> --help

Everything is a dry run unless a command says `--execute`. Live commands spend credits and are not run by the tests.
"""

import argparse
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from backend.config import get_settings
from evaluation import (
    datasets,
    e2e_eval,
    e2e_review,
    overlap,
    pilot,
    review,
    verify_eval,
)
from evaluation.schemas import write_schemas

ROOT = Path(__file__).parent


def cmd_validate(_a) -> int:
    problems = datasets.validate_questions(datasets.load_questions()) + datasets.validate_claim_dataset(datasets.load_claim_dataset())
    problems += datasets.verify_split_lock() + datasets.tuning_violations()
    report = {"problems": problems, "label_entries_not_in_force": datasets.label_problems(), "authoritative_claim_labels": len(datasets.authoritative_labels()),
              "examples": len(datasets.claim_examples()), "questions": len(datasets.load_questions())}
    if datasets.load_claim_dataset().get("public_export"):
        h = datasets.hash_report()
        report["public_export"] = {"variant": datasets.load_claim_dataset()["public_export"]["variant"],
                                   "unavailable_examples": h["examples"]["reasons"], "hash_verification": {k: h[k] for k in ("passages", "labels", "split_lock")}}
        report["public_export"]["hash_verification"]["examples"] = {"recomputable": len(h["examples"]["recomputable"]), "retained_only": len(h["examples"]["retained_only"])}
    print(json.dumps(report, indent=1))
    return 1 if problems else 0


def cmd_lock(a) -> int:
    if datasets.LOCK.exists() and not a.force:
        print("split.lock.json already exists; the split is recorded once, before tuning. Refusing to overwrite.", file=sys.stderr)
        return 1
    datasets.LOCK.write_text(json.dumps({"recorded": date.today().isoformat(), "statement": (
        "Held-out membership and hashes recorded before any tuning against the evaluation datasets. An item already used for tuning is not held out."),
        "lock": datasets.compute_lock()}, indent=1) + "\n", encoding="utf-8")
    print("recorded", datasets.LOCK)
    return 0


def cmd_review_sheet(a) -> int:
    ids = review.BATCH1 if a.batch == 1 else [e["id"] for e in datasets.claim_examples(a.split)]
    examples = review.select(ids)
    stem = ROOT / "review" / (f"claim_review_batch{a.batch}" if a.batch == 1 else f"claim_review_{a.split}")
    stem.with_suffix(".md").write_text(review.render_markdown(examples, f"Claim-evidence label review: {len(examples)} examples"), encoding="utf-8")
    review.write_csv(examples, stem.with_suffix(".csv"))
    print("wrote", stem.with_suffix(".md"), "and", stem.with_suffix(".csv"))
    return 0


def cmd_import_labels(a) -> int:
    print(json.dumps(review.import_labels(Path(a.csv), confirm_human=a.i_am_the_human_reviewer, correct=a.correct, provenance=a.provenance), indent=1))
    return 0


def cmd_label_history(a) -> int:
    print(json.dumps({"example": a.example, "current": datasets.current_label_entries().get(a.example), "all_versions": datasets.label_history(a.example)},
                     indent=1, ensure_ascii=False))
    return 0


def cmd_overlap(_a) -> int:
    print("wrote", overlap.write())
    return 0


def cmd_pilot(a) -> int:
    plan = pilot.plan()
    if not a.execute:
        print(json.dumps(plan, indent=1))
        return 0
    if a.claims_only and a.e2e_only:
        print("--claims-only and --e2e-only exclude each other", file=sys.stderr)
        return 1
    run_claims, run_e2e = not a.e2e_only, not a.claims_only
    out = ROOT / "results"
    tag = "" if a.attempt == 1 else f"_attempt{a.attempt}"  # a failed attempt's files are kept; the next attempt writes its own
    arms = [(f"pilot_e2e_evaluation_budget{tag}", pilot.E2E_LIMITS)] + ([(f"pilot_e2e_default_budget{tag}", pilot.DEFAULT_ARM_LIMITS)] if a.include_default_budget_arm else [])
    targets = ([out / "pilot_verification.json", out / "pilot_verification.db"] if run_claims else []) \
        + ([out / f"{n}{ext}" for n, _ in arms for ext in (".json", ".db", ".partial.json")] if run_e2e else [])
    existing = [t.name for t in targets if t.exists()]
    if existing:  # checked BEFORE anything is built or sent: a stored result is never overwritten, extended or silently repeated
        print(f"refusing: these results already exist and are never overwritten or repeated: {existing}", file=sys.stderr)
        return 1
    pilot.require_ready()  # all 12 claim examples need authoritative human labels
    if run_claims:
        datasets.require_evidence(pilot.examples(available_only=a.available_evidence_only), "the pilot claim verification")  # before anything is built or sent
    from backend.budget import Budget
    from backend.services import build_services

    out.mkdir(exist_ok=True)
    if run_claims:
        s = get_settings().model_copy(update=pilot.VERIFY_LIMITS)
        svc = build_services(s, mode="verify")
        records, errors = pilot.run_verification(svc.llm, s, available_only=a.available_evidence_only, budget=Budget(s), db_path=out / "pilot_verification.db")
        res = verify_eval.build_result(records, errors, s, split="dev", executed=True, fixture=False, budget_limits=pilot.VERIFY_LIMITS,
                                       notes=[f"pilot: {len(records)} of 12 human-reviewed development examples" + (" (available-evidence subset of the public export; the others' evidence is withheld)" if a.available_evidence_only else "")])
        (out / "pilot_verification.json").write_text(res.model_dump_json(indent=1), encoding="utf-8")
        print("labels:", res.metadata.label_statement)
    if run_e2e:
        for name, limits in arms:
            e2e_eval.execute("dev", get_settings().model_copy(update=limits), out / f"{name}.db", out / f"{name}.json", confirm=True,
                             question_ids=pilot.PILOT_QUESTIONS)
    print("pilot finished; results in", out)
    return 0


def cmd_e2e_review_sheet(a) -> int:
    """Read-only: review sheets (final-report claims, answer requirements) from a finished end-to-end result and its database."""
    result = json.loads(Path(a.result).read_text(encoding="utf-8"))
    outcomes = [(q["question_id"], q["run_id"]) for q in result["questions"] if q.get("run_id")]
    notes_path = Path(a.annotations) if a.annotations else Path(a.result).with_suffix(".annotations.json")
    annotations = json.loads(notes_path.read_text(encoding="utf-8")) if notes_path.exists() else None
    for path in e2e_review.build(Path(a.db), outcomes, Path(a.stem), annotations):
        print("wrote", path)
    return 0


def cmd_verify(a) -> int:
    settings = get_settings()
    plan = verify_eval.plan(a.split, settings, available_only=a.available_evidence_only)
    if not a.execute:
        print(json.dumps({"dry_run": True, **plan}, indent=1))
        return 0
    if plan["with_authoritative_label"] == 0:
        print("note: no authoritative human labels exist for this split; correctness metrics will be N/A", file=sys.stderr)
    if a.split in ("heldout", "all") and not a.final_heldout:
        print("held-out examples may be run once, as a final evaluation: pass --final-heldout to confirm no tuning will follow", file=sys.stderr)
        return 1
    examples = datasets.claim_examples(None if a.split == "all" else a.split, available_only=a.available_evidence_only)
    datasets.require_evidence(examples, "frozen claim verification")  # before any client is built or any request is sent
    from backend.budget import Budget
    from backend.services import build_services

    settings = settings.model_copy(update={"max_model_requests": a.max_requests, "max_total_output_tokens": a.max_output_tokens,
                                           "max_elapsed_seconds": a.max_seconds})
    svc = build_services(settings, mode="verify")
    records, errors = verify_eval.run_frozen(examples, svc.llm, settings, budget=Budget(settings),
                                             prices=json.loads(Path(a.prices).read_text()) if a.prices else None)
    result = verify_eval.build_result(records, errors, settings, split=a.split, executed=True, fixture=False,
                                      budget_limits={"max_model_requests": a.max_requests, "max_total_output_tokens": a.max_output_tokens,
                                                     "max_elapsed_seconds": a.max_seconds})
    Path(a.out).write_text(result.model_dump_json(indent=1), encoding="utf-8")
    print("wrote", a.out)
    print("labels:", result.metadata.label_statement)
    return 0


def _e2e_settings(a):
    """Per-question limits for an end-to-end evaluation run (one shared Budget per question, as in production)."""
    return get_settings().model_copy(update={"max_model_requests": a.max_requests, "max_search_queries": a.max_searches, "max_page_fetches": a.max_fetches,
                                             "max_total_output_tokens": a.max_output_tokens, "max_elapsed_seconds": a.max_seconds})


def cmd_e2e_plan(a) -> int:
    print(json.dumps({"dry_run": True, **e2e_eval.plan(a.split, _e2e_settings(a))}, indent=1))
    return 0


def cmd_e2e_execute(a) -> int:
    if a.split in ("heldout", "all") and not a.final_heldout:
        print("pass --final-heldout to run held-out questions (once, after all tuning is finished)", file=sys.stderr)
        return 1
    e2e_eval.execute(a.split, _e2e_settings(a), Path(a.db), Path(a.out), confirm=a.execute)
    return 0


def cmd_e2e_collect(a) -> int:
    conn = sqlite3.connect(f"file:{Path(a.db).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    q = next((x for x in datasets.load_questions() if x["id"] == a.question), None)
    outcome = e2e_eval.collect_outcome(conn, a.run_id, q)
    outcome.pop("report_markdown", None)
    print(json.dumps(outcome, indent=1, default=str)[:20000])
    return 0


def cmd_schema(_a) -> int:
    for p in write_schemas(ROOT / "schemas"):
        print("wrote", p)
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="python -m evaluation")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("validate", help="validate datasets, split lock and tuning log").set_defaults(fn=cmd_validate)
    s = sub.add_parser("lock", help="record the dev/held-out split (once, before tuning)")
    s.add_argument("--force", action="store_true")
    s.set_defaults(fn=cmd_lock)
    s = sub.add_parser("review-sheet", help="write the human review sheet (markdown + csv)")
    s.add_argument("--batch", type=int, default=1)
    s.add_argument("--split", default="dev")
    s.set_defaults(fn=cmd_review_sheet)
    s = sub.add_parser("import-labels", help="import a filled review csv into human_labels.json (human judgements only)")
    s.add_argument("csv")
    s.add_argument("--i-am-the-human-reviewer", action="store_true", help="confirm that the labels in the file are your own judgements")
    s.add_argument("--correct", action="store_true", help="append a new VERSION for already-labelled ids (rows need a correction_reason); nothing is overwritten")
    s.add_argument("--provenance", default=datasets.LABEL_PROVENANCE, help="how these labels were made; shown on every evaluation report")
    s.set_defaults(fn=cmd_import_labels)
    s = sub.add_parser("label-history", help="show the current label and every stored version (reviewer, date, reason) for one example")
    s.add_argument("example")
    s.set_defaults(fn=cmd_label_history)
    s = sub.add_parser("verify", help="A. frozen claim-evidence verification evaluation (dry run unless --execute)")
    s.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    s.add_argument("--execute", action="store_true", help="LIVE: spends model credits")
    s.add_argument("--final-heldout", action="store_true")
    s.add_argument("--max-requests", type=int, default=60)
    s.add_argument("--max-output-tokens", type=int, default=30000)
    s.add_argument("--max-seconds", type=float, default=900)
    s.add_argument("--prices", help="JSON {model: {prompt_usd_per_1m, completion_usd_per_1m}} for a CALCULATED cost")
    s.add_argument("--available-evidence-only", action="store_true", help="public export: run only examples whose evidence is published (smaller, stated denominator)")
    s.add_argument("--out", default=str(ROOT / "results" / "verification.json"))
    s.set_defaults(fn=cmd_verify)
    def limits(sp):
        sp.add_argument("--max-requests", type=int, default=40, help="model requests per question")
        sp.add_argument("--max-searches", type=int, default=8)
        sp.add_argument("--max-fetches", type=int, default=12)
        sp.add_argument("--max-output-tokens", type=int, default=30000)
        sp.add_argument("--max-seconds", type=float, default=600)

    s = sub.add_parser("e2e-plan", help="B. print the end-to-end plan and limits (dry run)")
    s.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    limits(s)
    s.set_defaults(fn=cmd_e2e_plan)
    s = sub.add_parser("e2e-execute", help="B. LIVE end-to-end run per question (needs --execute)")
    s.add_argument("--split", choices=["dev", "heldout", "all"], default="dev")
    s.add_argument("--execute", action="store_true")
    s.add_argument("--final-heldout", action="store_true")
    limits(s)
    s.add_argument("--db", default=str(ROOT / "results" / "e2e.db"))
    s.add_argument("--out", default=str(ROOT / "results" / "e2e.json"))
    s.set_defaults(fn=cmd_e2e_execute)
    s = sub.add_parser("e2e-collect", help="read one finished run from a database (read-only) and print its collected outcome")
    s.add_argument("--db", required=True)
    s.add_argument("--run-id", type=int, required=True)
    s.add_argument("--question", default="")
    s.set_defaults(fn=cmd_e2e_collect)
    s = sub.add_parser("e2e-review-sheet", help="read-only: write review sheets (final-report claims, answer requirements) for a finished end-to-end result")
    s.add_argument("--db", required=True)
    s.add_argument("--result", required=True, help="the end-to-end result json")
    s.add_argument("--stem", required=True, help="path prefix for the four files written")
    s.add_argument("--annotations", help="optional json {QUESTION@RUN: {classification, evidence}}; default: <result>.annotations.json if it exists")
    s.set_defaults(fn=cmd_e2e_review_sheet)
    sub.add_parser("schema", help="write the JSON schemas of the result files").set_defaults(fn=cmd_schema)
    sub.add_parser("overlap", help="write the dev/held-out source and passage overlap report").set_defaults(fn=cmd_overlap)
    s = sub.add_parser("pilot", help="the pilot (12 reviewed dev claim examples + 2 dev questions): plan, or run with --execute")
    s.add_argument("--execute", action="store_true", help="LIVE: spends credits; refuses until all 12 claim examples have human labels")
    s.add_argument("--claims-only", action="store_true", help="with --execute: ONLY the 12 frozen claim verifications (no end-to-end questions)")
    s.add_argument("--e2e-only", action="store_true", help="with --execute: ONLY the 2 end-to-end development questions (no claim verification)")
    s.add_argument("--attempt", type=int, default=1, help="with --e2e-only: attempt number; attempt N>1 writes *_attemptN files so an earlier (failed) attempt is kept")
    s.add_argument("--include-default-budget-arm", action="store_true", help="also run the 2 questions at the product-default budget (separate approval)")
    s.add_argument("--available-evidence-only", action="store_true", help="public export: verify only the pilot claims whose evidence is published (10 of 12), reported as such")
    s.set_defaults(fn=cmd_pilot)
    args = p.parse_args(argv)
    try:
        return args.fn(args)
    except datasets.WithheldEvidenceError as err:
        print(f"refused: {err}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
