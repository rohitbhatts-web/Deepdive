"""A small DEVELOPMENT-ONLY live check of answer relevance, on STORED evidence. No search, no fetch.

For each case in `data/relevance_check.json` it builds a scratch run from a stored claim and its stored passage, has the REAL verifier judge the claim (so it is
eligible honestly, under the current rubric), derives obligations with the REAL obligation analyst, runs the REAL coverage/relevance assessment and builds the
REAL report. It then compares what happened with the expectations written BEFORE the run. Those expectations are AI-assisted development expectations, not
independent human ground truth: agreement is not accuracy and disagreement may mean the expectation was wrong.

    uv run python -m evaluation.relevance_check            # dry run: limits, expectations and estimated cost; nothing is sent
    uv run python -m evaluation.relevance_check --execute  # LIVE, paid, ONCE (refuses if a result or scratch database exists)

Historical databases are opened read-only; nothing is written to them.
"""

import json
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from backend import claims, correction, db
from backend.agents import coverage
from backend.agents.obligations import ensure_obligations
from backend.agents.verifier import verify_claim
from backend.budget import Budget
from backend.config import Settings
from backend.report import build_report, get_report
from evaluation import datasets

CASES_FILE = ROOT / "evaluation" / "data" / "relevance_check.json"
RESULT = ROOT / "evaluation" / "results" / "relevance_check.json"
SCRATCH = ROOT / "data" / "relevance-check.db"
# Estimated from measured per-call charges of earlier runs (verifier / coverage calls about $0.0035, the dearest measured call $0.0043).
USD_TYPICAL_PER_CALL, USD_DEAREST_PER_CALL = 0.0035, 0.0043


def load_cases() -> dict:
    return json.loads(CASES_FILE.read_text(encoding="utf-8"))


def follow_up(cases: dict, name: str) -> tuple[dict, Path, Path]:
    """The same cases and expectations restricted to a follow-up's cases, with its own limits and NEW artifact paths (the first run's files are never reused)."""
    fu = cases["follow_ups"][name]
    domains = [{**d, "cases": [c for c in d["cases"] if c["id"] in fu["cases"]]} for d in cases["domains"]]
    domains = [d for d in domains if d["cases"]]
    missing = set(fu["cases"]) - {c["id"] for d in domains for c in d["cases"]}
    if missing:
        raise SystemExit(f"follow-up {name!r} names unknown cases {sorted(missing)}")
    derived = {**cases, "domains": domains, "limits_per_domain": fu["limits"], "follow_up": {"name": name, "purpose": fu["purpose"]}}
    return derived, ROOT / fu["result"], ROOT / fu["scratch"]


def stored_claim_evidence(db_path: Path, claim_id: int) -> dict:
    """Claim text, its first cited passage text and quote, and the source, read from a stored database opened read-only."""
    c = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    try:
        claim = c.execute("SELECT text FROM claims WHERE id = ?", (claim_id,)).fetchone()
        ev = c.execute("SELECT * FROM claim_evidence WHERE claim_id = ? ORDER BY position LIMIT 1", (claim_id,)).fetchone()
        p = c.execute("SELECT text, source_id FROM passages WHERE id = ?", (ev["passage_id"],)).fetchone()
        s = c.execute("SELECT final_url, title, content_hash FROM sources WHERE id = ?", (p["source_id"],)).fetchone()
        return {"claim": claim["text"], "passage_text": p["text"], "quote": ev["quote"], "url": s["final_url"], "title": s["title"], "content_hash": s["content_hash"]}
    finally:
        c.close()


def evidence_for(case: dict, domain: dict) -> dict:
    if "stored_claim" in case:
        return stored_claim_evidence(ROOT / "data" / "q02-compact-continuation.db", case["stored_claim"])
    ex = next(e for e in datasets.claim_examples() if e["id"] == case["example"])
    if ex["split"] != "dev":
        raise SystemExit(f"{ex['id']} is not a development example; the check uses development evidence only")
    datasets.require_evidence([ex], "the answer-relevance check")
    item = ex["evidence"][0]
    return {"claim": ex["claim"], "passage_text": item["passage_text"], "quote": item["quote"], "url": item["url"], "title": item["title"], "content_hash": item["content_hash"]}


def estimate(cases: dict) -> dict:
    per_domain = [len(d["cases"]) + 2 for d in cases["domains"]]  # one verifier request per claim + obligations + coverage
    typical = sum(per_domain)
    cap = cases["limits_per_domain"]["max_model_requests"] * len(cases["domains"])
    return {"typical_requests": typical, "cap_requests": cap, "typical_usd": round(typical * USD_TYPICAL_PER_CALL, 3), "cap_usd": round(cap * USD_DEAREST_PER_CALL, 3)}


def classify_case(expect: dict, *, verdict: str | None, eligible: bool, roles: list[dict], requirement_status: str, answered: bool,
                  claim_id: int | None, answer_ids: list[int], context_ids: list[int], omission: str | None) -> dict:
    """Five separate facts about one case, and one status that never lets a case blocked before relevance count as a relevance pass.

    semantic_eligibility -> relevance (was it ASSESSED for the intended claim and requirement, and what was decided) -> requirement status -> report placement.
    A missing judgment is not a classification: it is 'not exercised', and an expectation about the claim's role is then neither met nor failed."""
    if not eligible:
        exercised = {"exercised": False, "why": f"blocked before relevance: the verifier's verdict was {verdict!r}, so the claim is not eligible"}
    elif not roles:
        exercised = {"exercised": False, "why": "the assessor recorded no relevance judgment for this claim and requirement (a missing judgment is not a context classification)"}
    else:
        exercised = {"exercised": True, "why": "the assessor recorded a role for this claim and requirement"}
    decision = None
    if exercised["exercised"]:
        decision = "direct_answer" if any(r["role"] == "direct_answer" for r in roles) else "not_direct_answer"
    placement = "answer" if claim_id in answer_ids else "context" if claim_id in context_ids else f"omitted: {omission}" if omission else "not shown"
    observed = {"claim_role": decision, "requirement": "addressed" if requirement_status == "addressed" else "not_addressed", "section": "answered" if answered else "no_direct_answer"}
    components = {"claim_role": ("not exercised" if decision is None else "matches" if decision == expect["claim_role"] else "differs"),
                  "requirement": "matches" if observed["requirement"] == expect["requirement"] else "differs",
                  "section": "matches" if observed["section"] == expect["section"] else "differs"}
    if not exercised["exercised"]:
        status = "relevance_not_exercised"
    else:
        status = "pass" if all(v == "matches" for v in components.values()) else "differs"
    return {"semantic_eligibility": {"verdict": verdict, "eligible": eligible},
            "relevance": {**exercised, "decision": decision, "roles": [{"role": r["role"], "origin": r["origin"], "reason": r["reason"]} for r in roles]},
            "requirement_status": requirement_status, "report_placement": placement,
            "expected": expect, "components": components,
            "outcome_matches_expectation": all(observed[k] == v for k, v in expect.items() if k != "claim_role"),
            "status": status,
            "note": ("the requirement and section fields match only because no eligible, judged claim existed; they are not evidence about relevance"
                     if status == "relevance_not_exercised" and all(v == "matches" for k, v in components.items() if k != "claim_role") else "")}


def run_domain(conn, llm, settings: Settings, domain: dict) -> dict:
    """The real pipeline stages on one scratch run: claims -> integrity -> REAL verifier -> REAL obligations -> REAL coverage/relevance -> REAL report."""
    budget = Budget(settings)
    run_id = db.create_run(conn, f"[relevance check] {domain['id']}")["id"]
    items = []
    for case in domain["cases"]:
        ev = evidence_for(case, domain)
        with db.tx(conn):
            sq = db.add_subquestion(conn, run_id, case["subquestion"], [case["requirement"]])
            sid = db.insert_source(conn, run_id, requested_url=f"{ev['url']}#{case['id']}", final_url=ev["url"], title=ev["title"], content_type="text/html",
                                   content_hash=f"{ev['content_hash']}#{case['id']}", snapshot=ev["passage_text"], passages=[(0, len(ev["passage_text"]))])
            db.add_association(conn, run_id, sq, sid, "selected_from_search", explanation="stored evidence")
            cid = claims.add_claim(conn, run_id, sq, ev["claim"], [(f"S{sid}-P0", ev["quote"])], origin="drafted", provenance={"relevance_check": case["id"]})
        claims.check_integrity(conn, cid, settings)
        items.append({"case": case, "sq": sq, "claim_id": cid})
    for it in items:
        if claims.get_claim(conn, run_id, it["claim_id"])["integrity_verdict"] == "passed":
            verify_claim(conn, llm, budget, settings, it["claim_id"])
    with db.tx(conn):
        db.set_run_status(conn, run_id, "collection_finished")
        db.set_verify_status(conn, run_id, "completed")
    ensure_obligations(conn, llm, budget, settings, run_id)
    coverage.assess(conn, llm, budget, settings, run_id, 0)
    info = build_report(conn, run_id, plan=None, ordering_reason="relevance check", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0)
    report = get_report(conn, run_id, info["report_id"])
    roles, assessed = correction.claim_roles(conn, run_id, 0), {a["requirement_key"]: a for a in correction.assessments(conn, run_id, 0)}
    results = []
    for it in items:
        case, cid = it["case"], it["claim_id"]
        claim = claims.get_claim(conn, run_id, cid)
        req = next(r for r in correction.requirements(conn, run_id) if r["subquestion_id"] == it["sq"])
        mine = [r for r in roles if r["claim_id"] == cid and r["requirement_key"] == req["key"]]
        a = assessed[req["key"]]
        sec = next(s for s in report["sections"] if s["subquestion_id"] == it["sq"])
        observed = {
            "claim_semantic_verdict": claim["semantic_verdict"], "claim_eligible": claim["eligible_for_report"],
            "claim_role": ("direct_answer" if any(r["role"] == "direct_answer" for r in mine) else "not_direct_answer"),
            "roles": [{"role": r["role"], "origin": r["origin"], "reason": r["reason"]} for r in mine],
            "requirement": "addressed" if a["status"] == "addressed" else "not_addressed", "requirement_status": a["status"], "gap": a["gap"],
            "temporal_scope": next((o["notes"].get("temporal_scope") for o in a["obligations"] if o["notes"].get("temporal_scope")), None),
            "section": "answered" if sec["answered"] else "no_direct_answer",
            "section_answer_claims": [c["claim_id"] for c in sec["claims"]], "section_context_claims": [c["claim_id"] for c in sec["context"]]}
        exp = case["expect"]
        agrees = {k: observed[k] == v for k, v in exp.items()}
        omission = next((o["reason"] for o in report["omissions"] if o["claim_id"] == cid), None)
        separated = classify_case(exp, verdict=claim["semantic_verdict"], eligible=claim["eligible_for_report"], roles=mine, requirement_status=a["status"],
                                  answered=bool(sec["answered"]), claim_id=cid, answer_ids=observed["section_answer_claims"], context_ids=observed["section_context_claims"],
                                  omission=omission)
        results.append({"id": case["id"], "kind": case["kind"], "requirement": case["requirement"], "claim_id": cid, "expected": exp, "observed": observed,
                        "agrees_with_expectation": agrees, "all_agree": all(agrees.values()) and observed["claim_eligible"], "separated": separated})
    return {"domain": domain["id"], "run_id": run_id, "model_requests": budget.model_requests, "output_tokens": budget.output_tokens, "elapsed_seconds": round(budget.elapsed(), 1),
            "report_outcome": report["outcome"], "cases": results}


def interpret(result: dict, db_path: Path) -> dict:
    """Re-read a saved result with the separated classification, using the scratch database (opened read-only) for what the result file did not record: the claim
    ids and the omission reasons. Returns a NEW structure; the saved result is not touched."""
    c = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro&immutable=1", uri=True)
    c.row_factory = sqlite3.Row
    try:
        claim_of = {json.loads(r["provenance"]).get("relevance_check"): r["id"] for r in c.execute("SELECT id, provenance FROM claims")}
        out = {"schema": "deepdive-relevance-check-interpretation/1", "applies_to": "evaluation/results/relevance_check.json (unchanged)", "domains": []}
        for dom in result["domains"]:
            cases = []
            for case in dom["cases"]:
                cid, o = claim_of[case["id"]], case["observed"]
                omission = c.execute("SELECT reason FROM report_omissions WHERE claim_id = ?", (cid,)).fetchone()
                sep = classify_case(case["expected"], verdict=o["claim_semantic_verdict"], eligible=o["claim_eligible"], roles=o["roles"], requirement_status=o["requirement_status"],
                                    answered=o["section"] == "answered", claim_id=cid, answer_ids=o["section_answer_claims"], context_ids=o["section_context_claims"],
                                    omission=omission["reason"] if omission else None)
                cases.append({"id": case["id"], "claim_id": cid, **sep})
            out["domains"].append({"domain": dom["domain"], "cases": cases})
    finally:
        c.close()
    flat = [x for d in out["domains"] for x in d["cases"]]
    out["summary"] = {k: sum(1 for x in flat if x["status"] == k) for k in ("pass", "differs", "relevance_not_exercised")}
    out["provenance"] = "AI-assisted development expectations; this is a corrected reading of the original result, not a new experiment and not human ground truth"
    return out


def charges(conn) -> dict:
    """Provider-reported charges for the scratch database's calls, where the usage API returns them (best effort; never invented)."""
    from backend.mesh_client import get_mesh_client
    total, n, calls = 0.0, 0, conn.execute("SELECT request_id FROM model_calls").fetchall()
    for (rid,) in [tuple(r) for r in calls]:
        if not rid:
            continue
        try:
            ev = (get_mesh_client().post("/usage/events", cast_to=object, body={"org_id": "-", "request_id": rid, "limit": 1}).get("events") or [{}])[0]
            if ev.get("cost_usd") is not None:
                total, n = total + float(ev["cost_usd"]), n + 1
        except Exception:  # noqa: BLE001, S112 - a charge the usage API does not return stays unknown
            continue
    return {"provider_reported_usd": round(total, 6), "calls_with_charge": n, "calls": len(calls)}


def execute(llm, settings: Settings, out: Path = RESULT, scratch: Path = SCRATCH, with_charges: bool = True, cases: dict | None = None) -> dict:
    cases = cases or load_cases()
    if out.exists() or scratch.exists():
        raise SystemExit(f"refusing: {out.name} or {scratch.name} already exists; a check result is never overwritten or silently repeated")
    lim = cases["limits_per_domain"]
    settings = settings.model_copy(update={k: lim[k] for k in ("max_model_requests", "max_total_output_tokens", "max_elapsed_seconds", "max_search_queries", "max_page_fetches")})
    conn = db.connect(scratch)
    result = {"schema": "deepdive-relevance-check-result/1", "provenance": cases["provenance"], "limits_per_domain": lim, "domains": [],
              **({"follow_up": cases["follow_up"]} if "follow_up" in cases else {})}
    try:
        for domain in cases["domains"]:
            result["domains"].append(run_domain(conn, llm, settings, domain))
        result["totals"] = {"model_requests": sum(d["model_requests"] for d in result["domains"]), "output_tokens": sum(d["output_tokens"] for d in result["domains"]),
                            "elapsed_seconds": round(sum(d["elapsed_seconds"] for d in result["domains"]), 1)}
        result["charges"] = charges(conn) if with_charges else None
    finally:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, indent=1, ensure_ascii=False, default=str) + "\n", encoding="utf-8")  # even a partial result is kept
        conn.close()
    return result


def describe(cases: dict) -> str:
    est = estimate(cases)
    lim = cases["limits_per_domain"]
    head = [f"FOLLOW-UP {cases['follow_up']['name']}: {cases['follow_up']['purpose']}", ""] if "follow_up" in cases else []
    lines = [*head, cases["provenance"], f"limits per domain run: {lim}", f"estimate: ~{est['typical_requests']} requests (cap {est['cap_requests']}), ~${est['typical_usd']} typical, ~${est['cap_usd']} cap (calculated)",
             f"unavailable: {cases['unavailable']}", ""]
    for d in cases["domains"]:
        lines.append(f"[{d['id']}] {d['evidence']}")
        lines += [f"  {c['id']}: {c['kind']}\n      requirement: {c['requirement']}\n      expected   : {c['expect']}\n      why        : {c['why']}" for c in d["cases"]]
    return "\n".join(lines)


def main(argv: list[str]) -> int:
    cases, out_path, scratch_path = load_cases(), RESULT, SCRATCH
    if "--follow-up" in argv:
        cases, out_path, scratch_path = follow_up(cases, argv[argv.index("--follow-up") + 1])
    if "--interpret" in argv:  # offline: re-read the saved result with the separated classification; writes a NEW file, never the result
        out = interpret(json.loads(out_path.read_text(encoding="utf-8")), scratch_path)
        target = out_path.with_name(out_path.stem + ".interpretation.json")
        if target.exists():
            raise SystemExit(f"refusing: {target.name} already exists")
        target.write_text(json.dumps(out, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
        for d in out["domains"]:
            for x in d["cases"]:
                print(f"{x['id']}: {x['status'].upper()} | eligibility {x['semantic_eligibility']} | relevance exercised={x['relevance']['exercised']} decision={x['relevance']['decision']} "
                      f"| requirement {x['requirement_status']} | placement {x['report_placement']} | components {x['components']}")
        print("summary:", out["summary"])
        return 0
    if "--execute" not in argv:
        print(describe(cases))
        print(f"\nartifacts to be created (refuses if either exists): {out_path.relative_to(ROOT)} and {scratch_path.relative_to(ROOT)}")
        print("\nstored evidence (read-only check):")
        for d in cases["domains"]:
            for c in d["cases"]:
                try:
                    ev = evidence_for(c, d)
                    print(f"  {c['id']}: available - claim {ev['claim'][:70]!r}; quote {ev['quote'][:50]!r}; passage {len(ev['passage_text'])} chars")
                except (OSError, sqlite3.Error, StopIteration, TypeError) as exc:
                    print(f"  {c['id']}: UNAVAILABLE ({type(exc).__name__}); it would be reported as unavailable, not invented")
        print("\ndry run: nothing was sent")
        return 0
    from backend.services import build_services
    settings = Settings()
    svc = build_services(settings.model_copy(update={"max_search_queries": 0, "max_page_fetches": 0}), mode="retry")
    result = execute(svc.llm, svc.settings, out=out_path, scratch=scratch_path, cases=cases)
    for d in result["domains"]:
        print(f"[{d['domain']}] requests {d['model_requests']}, output tokens {d['output_tokens']}, {d['elapsed_seconds']} s, report {d['report_outcome']}")
        for c in d["cases"]:
            sep = c["separated"]
            print(f"  {c['id']}: {sep['status'].upper()} | eligibility {sep['semantic_eligibility']['eligible']} | relevance exercised {sep['relevance']['exercised']} -> {sep['relevance']['decision']} "
                  f"| requirement {sep['requirement_status']} | placement {sep['report_placement']}")
    print("totals:", result["totals"], "| charges:", result["charges"])
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
