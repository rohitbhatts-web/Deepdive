"""A. Frozen claim-evidence verification evaluation.

Each dataset example (one claim + its exact cited passages) goes through the REAL pipeline stages for one claim: the deterministic integrity
check, then the semantic verifier. Nothing else varies: the claims and the evidence are frozen, so differences between runs of this
evaluation come from the verifier (model, prompt, rubric) alone. It does not search, draft or correct.

Validity limits, stated so they are not forgotten:
- The frozen source holds only the cited passages; the pipeline's neighbouring-passage context is absent.
- Correctness uses human labels only (datasets.authoritative_labels). With none, metrics are N/A and only the labelled-as-provisional
  agreement is reported.
- A budget stop leaves the remaining examples `not_judged`; they are never silently dropped.
"""

import sqlite3
import tempfile
from collections.abc import Callable
from pathlib import Path

from backend import claims, db
from backend.agents.verifier import verify_claim
from backend.budget import Budget, BudgetExceeded
from backend.config import Settings
from backend.llm import LLM
from evaluation import metrics
from evaluation.datasets import (
    authoritative_labels,
    claim_examples,
    require_evidence,
    withheld_ids,
)
from evaluation.schemas import VerificationRecord, VerificationResult, run_metadata

PRICE_KEYS = ("prompt_usd_per_1m", "completion_usd_per_1m")


def prepare(conn: sqlite3.Connection, example: dict, settings: Settings) -> tuple[int, int]:
    """Store ONE example as a run: a source per cited web page holding exactly the cited passages, then the claim with its evidence.
    Returns (run_id, claim_id). Passage references are the ones the drafter would have written (S<source>-P<n>)."""
    run_id = db.create_run(conn, f"[evaluation {example['id']}] {example['subquestion']}")["id"]
    sq = db.add_subquestion(conn, run_id, example["subquestion"], [])
    by_source: dict[str, list[dict]] = {}
    for item in example["evidence"]:
        by_source.setdefault(item["source_key"], [])
        if item["passage_text"] not in [p["passage_text"] for p in by_source[item["source_key"]]]:
            by_source[item["source_key"]].append(item)
    refs: dict[tuple[str, str], str] = {}
    with db.tx(conn):
        for key, items in by_source.items():
            items.sort(key=lambda i: i["ordinal"])
            spans, pos = [], 0
            for it in items:
                spans.append((pos, pos + len(it["passage_text"])))
                pos += len(it["passage_text"]) + 2
            sid = db.insert_source(conn, run_id, requested_url=items[0]["url"], final_url=items[0]["url"], title=items[0]["title"],
                                   content_type="text/html", content_hash=items[0]["content_hash"],
                                   snapshot="\n\n".join(i["passage_text"] for i in items), passages=spans)
            for n, it in enumerate(items):
                refs[(key, it["passage_text"])] = f"S{sid}-P{n}"
    evidence = [(refs[(i["source_key"], i["passage_text"])], i["quote"]) for i in example["evidence"]]
    with db.tx(conn):
        claim_id = claims.add_claim(conn, run_id, sq, example["claim"], evidence, origin="drafted",
                                    provenance={"evaluation_example": example["id"], "frozen": True})
    return run_id, claim_id


def _usage(conn: sqlite3.Connection, run_id: int, prices: dict | None, model: str) -> dict:
    rows = conn.execute("SELECT ok, latency_ms, prompt_tokens, completion_tokens FROM model_calls WHERE run_id = ?", (run_id,)).fetchall()
    if not rows:
        return {"model_calls": 0, "failed_calls": 0, "prompt_tokens": None, "completion_tokens": None, "latency_seconds": None,
                "calculated_cost_usd": None, "provider_reported_cost_usd": None}

    def total(col: str):
        vals = [r[col] for r in rows if r[col] is not None]
        return sum(vals) if len(vals) == len(rows) else None  # a partial sum would understate; report Unavailable instead

    p, c, lat = total("prompt_tokens"), total("completion_tokens"), total("latency_ms")
    cost = None
    if prices and model in prices and p is not None and c is not None:
        cost = (p * float(prices[model][PRICE_KEYS[0]]) + c * float(prices[model][PRICE_KEYS[1]])) / 1_000_000
    return {"model_calls": len(rows), "failed_calls": sum(1 for r in rows if not r["ok"]), "prompt_tokens": p, "completion_tokens": c,
            "latency_seconds": None if lat is None else lat / 1000, "calculated_cost_usd": cost, "provider_reported_cost_usd": None}


def evaluate_example(conn: sqlite3.Connection, llm: LLM, budget: Budget, settings: Settings, example: dict, gold: str | None,
                     prices: dict | None = None) -> dict:
    run_id, claim_id = prepare(conn, example, settings)
    integrity = claims.check_integrity(conn, claim_id, settings)
    rec = {"id": example["id"], "split": example["split"], "gold": gold, "proposed": example["proposed"]["label"], "run_id": run_id,
           "claim_id": claim_id, "integrity_reason": integrity["reason"], "semantic_reason": None}
    if integrity["verdict"] != "passed":
        rec["outcome"] = "integrity_failed"  # never sent to the verifier; counted separately from every semantic outcome
    else:
        try:
            result = verify_claim(conn, llm, budget, settings, claim_id)
            rec["semantic_reason"] = result["reason"]
            rec["outcome"] = "model_error" if result["verdict"] == "error" else result["verdict"]
        except BudgetExceeded as exc:
            rec["outcome"] = "not_judged"
            rec["semantic_reason"] = f"budget stop: {exc.reason}"
    return {**rec, **_usage(conn, run_id, prices, settings.verifier_model)}


def run_frozen(examples: list[dict], llm: LLM, settings: Settings, *, labels: dict[str, str] | None = None, prices: dict | None = None,
               db_path: Path | None = None, budget: Budget | None = None) -> tuple[list[dict], list[str]]:
    """Evaluate examples in order against one shared budget. Returns (records, errors). Records exist for EVERY example."""
    require_evidence(examples, "frozen claim verification")  # nothing is built or sent when the public export withholds some evidence
    labels = authoritative_labels(examples) if labels is None else labels
    budget = budget or Budget(settings)
    path = db_path or Path(tempfile.mkdtemp(prefix="deepdive-eval-")) / "frozen.db"
    conn = db.connect(path)
    records, errors = [], []
    try:
        for ex in examples:
            if any(r["outcome"] == "not_judged" and "budget stop" in (r["semantic_reason"] or "") for r in records[-1:]):
                records.append({"id": ex["id"], "split": ex["split"], "gold": labels.get(ex["id"]), "proposed": ex["proposed"]["label"],
                                "outcome": "not_judged", "semantic_reason": "not run: an earlier example hit the budget stop",
                                "integrity_reason": None, "run_id": None, "claim_id": None, "model_calls": 0, "failed_calls": 0,
                                "prompt_tokens": None, "completion_tokens": None, "latency_seconds": None,
                                "calculated_cost_usd": None, "provider_reported_cost_usd": None})
                continue
            records.append(evaluate_example(conn, llm, budget, settings, ex, labels.get(ex["id"]), prices))
        errors += [f"{r['id']}: {r['outcome']}: {r['semantic_reason'] or r['integrity_reason']}" for r in records
                   if r["outcome"] in ("model_error", "not_judged", "integrity_failed")]
    finally:
        conn.close()
    return records, errors


def build_result(records: list[dict], errors: list[str], settings: Settings, *, split: str, executed: bool, fixture: bool,
                 budget_limits: dict, notes: list[str] | None = None) -> VerificationResult:
    labelled = any(r["gold"] is not None for r in records)
    meta = (
        run_metadata("verification", settings, split=split, budget=budget_limits, run_ids=[r["run_id"] for r in records if r["run_id"]],
                              executed=executed, fixture=fixture, label_basis="authoritative-human" if labelled else "none",
                              label_ids=[r["id"] for r in records if r["gold"] is not None],
                              notes=(notes or []) + (["no authoritative human labels: correctness metrics are N/A"] if not labelled else [])))
    return VerificationResult(
        metadata=meta,
        records=[VerificationRecord(**{k: v for k, v in r.items() if k in VerificationRecord.model_fields}) for r in records],
        metrics={"label_statement": meta.label_statement, **metrics.verification_metrics(records), "paired_accept_all_vs_verified": metrics.paired_filter_comparison(records)},
        agreement_with_provisional=metrics.agreement_with_provisional(records),
        usage=metrics.usage_summary(records), errors=errors)


def plan(split: str, settings: Settings, *, available_only: bool = False) -> dict:
    """What a live run would do, without doing it."""
    examples = claim_examples(None if split == "all" else split, available_only=available_only)
    labels = authoritative_labels(examples)
    return {"split": split, "examples": len(examples), "with_authoritative_label": len(labels),
            "evidence_withheld_in_this_selection": withheld_ids(examples), "available_evidence_only": available_only,
            "expected_model_requests": {"typical": len(examples), "worst_case": 2 * len(examples),
                                        "note": "1 per example; 2 when the model's first reply is invalid; integrity failures need none"},
            "verifier_model": settings.verifier_model or "(not configured)"}


GuardFn = Callable[[], None]
