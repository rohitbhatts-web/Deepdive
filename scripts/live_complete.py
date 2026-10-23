"""Bounded LIVE acceptance for Phase 4 (real Mesh, real Tavily, real web). Not part of the automated suite.

    uv run python scripts/live_complete.py --check   # configuration, models, the bounds, expected path, cost ESTIMATE; no inference
    uv run python scripts/live_complete.py           # ONE run on a new isolated copy; refuses to run twice

The source database (data/live-verify.db: the natural SQLite/PostgreSQL run with its Phase 3 claims) is never modified; it is
copied to data/live-complete.db. Nothing is injected to force a loop. The bounds below are fixed in this file and are not
raised automatically. Credentials are read by the app from .env and never printed.
"""

import argparse
import hashlib
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE_DB = ROOT / "data" / "live-verify.db"
DB_PATH = ROOT / "data" / "live-complete.db"
PORT = 8770
RUN_ID = 1
LIMITS = {
    "MAX_CORRECTION_ROUNDS": "2",       # at most 2 corrective rounds after the initial assessment
    "MAX_SEARCH_QUERIES": "2",          # at most 2 NEW searches in this job
    "MAX_PAGE_FETCHES": "4",            # at most 4 NEW page fetches
    "MAX_ACTIVE_CLAIMS": "3",           # RETIRED setting, ignored since the Phase 4 audit (see MAX_CANDIDATE_CLAIMS)
    "MAX_MODEL_REQUESTS": "14",         # TOTAL, including retries, failed requests, coverage, relevance and synthesis
    "MAX_ELAPSED_SECONDS": "240",       # deadline for the whole job
    "MAX_OUTPUT_TOKENS": "1500",        # per call
    "MAX_TOTAL_OUTPUT_TOKENS": "6000",  # whole job, from reported usage
    "DRAFT_INPUT_CHARS": "6000", "VERIFIER_CONTEXT_CHARS": "1200", "COVERAGE_INPUT_CHARS": "6000",
    "REVISER_INPUT_CHARS": "4000",      # per-call input is bounded by characters, not tokens
    "MAX_TASKS_PER_ROUND": "4", "QUERIES_PER_TASK": "1", "RESULTS_PER_QUERY": "5", "URLS_PER_SUBQUESTION": "2",
}
ASSUMED_PROMPT_TOKENS = 3000  # per call; an ESTIMATE: inputs are capped in characters, not tokens


def digest(path: Path) -> str:
    c = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    h = hashlib.sha256()
    for line in c.iterdump():
        h.update(line.encode())
    c.close()
    return h.hexdigest()[:16]


def check() -> int:
    from backend.config import get_settings
    from backend.mesh_client import get_mesh_client
    from backend.services import check_models, missing_configuration, model_catalog

    settings = get_settings()
    missing = missing_configuration(settings, "complete")
    print("still missing for completing an existing run:", missing or "nothing")
    if missing:
        return 1
    catalog = model_catalog(get_mesh_client())
    roles = {"researcher (queries, URL selection)": settings.researcher_model, "drafter / reviser": settings.drafter_model,
             "verifier / re-judge": settings.verifier_model, "coverage": settings.effective_coverage_model,
             "synthesizer": settings.effective_synthesizer_model}
    check_models(catalog, set(roles.values()))
    worst = 0.0
    print("models (all available and chat-capable):")
    for role, model in roles.items():
        p = catalog[model]["pricing"]
        print(f"  {role}: {model}  ${float(p['prompt_usd_per_1m']):g}/M in, ${float(p['completion_usd_per_1m']):g}/M out, "
              f"structured_output={catalog[model].get('supports_structured_output')}, thinking={catalog[model].get('supports_thinking')}")
    dearest_in = max(float(catalog[m]["pricing"]["prompt_usd_per_1m"]) for m in roles.values())
    dearest_out = max(float(catalog[m]["pricing"]["completion_usd_per_1m"]) for m in roles.values())
    n = int(LIMITS["MAX_MODEL_REQUESTS"])
    worst = (n * ASSUMED_PROMPT_TOKENS * dearest_in + int(LIMITS["MAX_TOTAL_OUTPUT_TOKENS"]) * dearest_out) / 1e6
    print("\nBOUNDS (fixed in this file):")
    for k, v in LIMITS.items():
        print(f"  {k}={v}")
    print(f"\nCOST ESTIMATE (not a ceiling): at most {n} requests x ~{ASSUMED_PROMPT_TOKENS} prompt tokens at the dearest input price "
          f"plus {LIMITS['MAX_TOTAL_OUTPUT_TOKENS']} output tokens at the dearest output price = ~${worst:.3f}. Input is capped in "
          "characters, not tokens, so this is an estimate. Mesh-reported charges (usage API cost_usd) are printed after the run, "
          "separately from the figure calculated from reported tokens and catalog prices. Tavily: at most 2 credits.")
    print("""
EXPECTED PATH under these bounds (an analysis, not a promise):
  1. re-judge the two Phase 3 'supported' claims (older rubric)                 2 requests
  2. coverage assessment 0                                                      1
  3. correction round 1: repair the misquoted claim and any claim that is no longer supported, up to 2 targeted
     searches for unresolved requirements (query + selection each), re-verify repairs     ~6-8
  4. coverage assessment 1                                                      1
  5. a second corrective round needs more than the 3 requests held back for coverage and synthesis, so it will
     most likely be refused for budget; synthesis                                1
  LIMITS OF THE TEST: the 3-active-claim cap is already full (3 natural claims), so pages found by targeted research
  cannot be drafted into new claims here; and 14 requests probably cannot fit a second corrective round. The test can
  therefore show re-judging, repair/exclusion, targeted research tasks, re-verification and report assembly; it cannot
  show new evidence entering the report or two full corrective rounds.""")
    return 0


def run() -> int:
    from backend.mesh_client import get_mesh_client
    from backend.services import model_catalog

    if DB_PATH.exists():
        print(f"{DB_PATH} already exists; refusing to overwrite or repeat. Remove it yourself to run again.")
        return 2
    before = {p.name: digest(p) for p in (SOURCE_DB, ROOT / "data" / "live-test-2.db", ROOT / "data" / "live-test.db")}
    src = sqlite3.connect(f"file:{SOURCE_DB.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(DB_PATH)
    src.backup(dst)
    dst.close()
    src.close()
    print(f"copied {SOURCE_DB.name} -> {DB_PATH.name} (source untouched)")
    env = {**os.environ, **LIMITS, "DATABASE_PATH": str(DB_PATH)}
    log = (ROOT / "data" / "live-complete-server.log").open("w")
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)],
                              cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
        for _ in range(30):
            try:
                c.get("/health")
                break
            except httpx.HTTPError:
                time.sleep(1)
        started = c.post(f"/runs/{RUN_ID}/complete")
        print(f"start -> HTTP {started.status_code}", started.json() if started.status_code != 202 else "")
        if started.status_code != 202:
            return 1
        t0 = time.monotonic()
        while time.monotonic() - t0 < 280:
            done = c.get(f"/runs/{RUN_ID}/completion").json()
            if done["complete_status"] != "completing":
                break
            time.sleep(3)
        print(f"\ncomplete_status={done['complete_status']} stop_reason={done['stop_reason']!r} wall={time.monotonic() - t0:.0f}s")
        print("corrective rounds run:", done["corrective_rounds"], "| coverage:", done["coverage"])
        print("budget used / limit:", done["budget_used_of_limit"])
        corr = c.get(f"/runs/{RUN_ID}/corrections").json()
        for rnd in corr["rounds"]:
            print(f"\nplanned round {rnd['planned_round']} (corrective round {rnd['corrective_round']}, executed={rnd['executed']}): "
                  f"coverage before={rnd['coverage_before']} after={rnd['coverage_after']}")
            for t in rnd["tasks"]:
                print(f"   task {t['id']} {t['kind']:<17} {t['status']:<8} claim={t['claim_id']} req={t['requirement_key']} -> {str(t['outcome'])[:220]}")
        for r in range(3):
            cov = c.get(f"/runs/{RUN_ID}/coverage?round={r}").json()
            if cov["round"] is None:
                continue
            print(f"\ncoverage assessment {r}: {cov['summary']}")
            for q in cov["requirements"]:
                print(f"   {q['requirement_key']} {q['status']:<19} supported by {q['supporting_claim_ids']} gap={q['gap'][:110]!r} kind={q['missing_kind']}")
        print("\nclaim lineages and states:")
        claims = {x["id"]: x for x in c.get(f"/runs/{RUN_ID}/claims").json()}
        for x in claims.values():
            print(f"   #{x['id']} v{x['version']} parent={x['parent_claim_id']} [{x['origin']}] status={x['status']} eligible={x['eligible_for_report']} "
                  f"superseded_by={x['superseded_by']} excluded={x['excluded'] and x['excluded']['reason'][:80]!r}")
            print(f"        {x['text'][:150]}")
        searches = c.get(f"/runs/{RUN_ID}/searches").json()
        print("\nsearches (all in the run):", [(s["id"], s["query"][:70]) for s in searches])
        print("sources:", [(s["id"], s["requested_url"][:80]) for s in c.get(f"/runs/{RUN_ID}/sources").json()])
        rep = c.get(f"/runs/{RUN_ID}/report")
        if rep.status_code == 200:
            rep = rep.json()
            print(f"\nREPORT {rep['id']}: outcome={rep['outcome']} fully_addressed={rep['fully_addressed']} ordering={rep['ordering']} "
                  f"({rep['ordering_reason']}) stop={rep['stop_reason']!r}")
            mismatches = 0
            for s in rep["sections"]:
                for it in s["claims"]:
                    stored = claims[it["claim_id"]]
                    ok = (it["text"] == stored["text"] and it["claim_version"] == stored["version"]
                          and it["citations"] == stored["citations"] and stored["eligible_for_report"])
                    mismatches += not ok
            print("report claims whose text/version/citations differ from the stored eligible claim:", mismatches)
            print("unresolved requirements:", [(u["requirement_key"], u["status"], u["gap"][:90]) for u in rep["unresolved_requirements"]])
            print("omissions:", rep["omissions"])
            print("\n--- report as rendered from stored rows ---")
            print(rep["markdown"])
        else:
            print("\nno report:", rep.status_code)
        calls = c.get(f"/runs/{RUN_ID}/model-calls").json()
        new_calls = [m for m in calls if m["agent"] in ("verifier", "coverage", "reviser", "researcher", "drafter", "synthesizer", "relevance")
                     and m["id"] > MAX_OLD_CALL_ID]
        catalog = model_catalog(get_mesh_client())
        calc = billed = 0.0
        print("\nmodel calls made by THIS job:")
        for m in new_calls:
            pr = catalog[m["model"]]["pricing"]
            cost = ((m["prompt_tokens"] or 0) * float(pr["prompt_usd_per_1m"]) + (m["completion_tokens"] or 0) * float(pr["completion_usd_per_1m"])) / 1e6
            calc += cost
            line = (f"   {m['agent']:<11} {m['model']} attempt={m['attempt']} ok={m['ok']} tokens(in/out)={m['prompt_tokens']}/{m['completion_tokens']} "
                    f"{m['latency_ms']}ms structured={m['structured_output']} calc=${cost:.6f}")
            try:
                ev = (get_mesh_client().post("/usage/events", cast_to=object, body={"org_id": "-", "request_id": m["request_id"], "limit": 1}).get("events") or [{}])[0]
                line += f" | Mesh cost_usd={ev.get('cost_usd')}"
                billed += float(ev.get("cost_usd") or 0)
            except Exception as exc:  # noqa: BLE001 - report a failed lookup instead of hiding it
                line += f" | usage lookup failed: {type(exc).__name__}"
            print(line + (f" ERROR {m['error'][:80]}" if m["error"] else ""))
        print(f"   requests={len(new_calls)}; calculated from reported tokens x catalog prices ${calc:.5f}; reported by Mesh usage API ${billed:.5f}")
        after = {p: digest(ROOT / "data" / p) for p in before}
        print("\nprior databases unchanged:", {k: before[k] == after[k] for k in before})
        return 0 if done["complete_status"] in ("completed", "stopped") else 1
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


def _old_call_id() -> int:
    c = sqlite3.connect(f"file:{SOURCE_DB.as_posix()}?mode=ro", uri=True)
    n = c.execute("SELECT coalesce(max(id), 0) FROM model_calls").fetchone()[0]
    c.close()
    return n


MAX_OLD_CALL_ID = _old_call_id() if SOURCE_DB.exists() else 0

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sys.exit(check() if a.check else run())
