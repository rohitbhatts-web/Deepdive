"""One bounded LIVE research run (real Mesh, real Tavily, real web). Not part of the automated suite.

    uv run python scripts/live_test.py --check     # no inference: config, model availability, price ceiling
    uv run python scripts/live_test.py             # runs one small research job and prints what was stored

The run uses its own database (data/live-test.db) and a server it starts and stops itself, with hard limits
set below. Credentials are read by the app from .env; this script never prints them.
"""

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

PORT = 8768
DB_NAME = "live-test-2.db"  # the first run (live-test.db) is kept untouched for comparison
QUESTION = "What are the main differences between SQLite and PostgreSQL, and when is each one appropriate?"
# Hard ceilings for this test only (override the defaults in backend/config.py via environment variables).
LIMITS = {
    "MAX_SUBQUESTIONS": "2",
    "QUERIES_PER_SUBQUESTION": "1",
    "RESULTS_PER_QUERY": "5",
    "URLS_PER_SUBQUESTION": "2",
    "MAX_MODEL_REQUESTS": "10",  # includes every retry and the relevance-assessment call
    "MAX_SEARCH_QUERIES": "2",
    "MAX_PAGE_FETCHES": "4",
    "MAX_OUTPUT_TOKENS": "1500",
    "MAX_TOTAL_OUTPUT_TOKENS": "4000",
    "MAX_ELAPSED_SECONDS": "150",
}
ASSUMED_PROMPT_TOKENS_PER_CALL = 3000  # generous: planner prompt ~0.6k, URL-selection prompt ~1.5k


def check() -> int:
    from backend.config import get_settings
    from backend.mesh_client import get_mesh_client
    from backend.services import check_models, missing_configuration, model_catalog

    settings = get_settings()
    missing = missing_configuration(settings)
    print("still missing:", missing or "nothing")
    if missing:
        return 1
    catalog = model_catalog(get_mesh_client())
    models = {settings.planner_model, settings.researcher_model}
    check_models(catalog, models)
    print("models available and chat-capable:", sorted(models))
    calls, out_cap = int(LIMITS["MAX_MODEL_REQUESTS"]), int(LIMITS["MAX_TOTAL_OUTPUT_TOKENS"]) + int(LIMITS["MAX_OUTPUT_TOKENS"])
    worst = 0.0
    for m in sorted(models):
        info = catalog[m]
        p = info["pricing"]
        pin, pout = float(p["prompt_usd_per_1m"]), float(p["completion_usd_per_1m"])
        worst = max(worst, (calls * ASSUMED_PROMPT_TOKENS_PER_CALL * pin + out_cap * pout) / 1e6)
        print(f"  {m}: ${pin:g}/M in, ${pout:g}/M out, ctx {info.get('context_length')}, "
              f"structured_output={info.get('supports_structured_output')}, thinking={info.get('supports_thinking')}")
        if info.get("supports_thinking"):
            print("  WARNING: reasoning tokens may consume the 1500-token output cap and return empty replies")
    print(f"ESTIMATE of Mesh cost if every limit is hit: ~${worst:.4f}. Output is capped by MAX_TOTAL_OUTPUT_TOKENS "
          f"(plus at most one call's overshoot), but INPUT tokens are not hard-capped: this assumes "
          f"{ASSUMED_PROMPT_TOKENS_PER_CALL} prompt tokens per call. Actual cost is printed after the run. "
          f"Tavily: at most {LIMITS['MAX_SEARCH_QUERIES']} credits.")
    return 0


def run() -> int:
    question = sys.argv[1] if len(sys.argv) > 1 and not sys.argv[1].startswith("--") else QUESTION
    db_path = ROOT / "data" / DB_NAME
    if db_path.exists():
        print(f"{db_path} already exists; refusing to overwrite it. Remove it yourself to repeat the test.")
        return 2
    env = {**os.environ, **LIMITS, "DATABASE_PATH": str(db_path)}
    log = (ROOT / "data" / "live-test-2-server.log").open("w")
    server = subprocess.Popen(
        [sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)],
        cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT,
    )
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
        for _ in range(30):
            try:
                c.get("/health")
                break
            except httpx.HTTPError:
                time.sleep(1)
        run_id = c.post("/runs", json={"question": question}).json()["id"]
        started = c.post(f"/runs/{run_id}/research")
        print(f"run {run_id}: start -> HTTP {started.status_code}", started.json() if started.status_code != 202 else "")
        if started.status_code != 202:
            return 1
        t0 = time.monotonic()
        while time.monotonic() - t0 < 240:
            status = c.get(f"/runs/{run_id}/research").json()
            if status["status"] != "researching":
                break
            time.sleep(3)
        print(f"\nstatus={status['status']} stop_reason={status['stop_reason']} wall={time.monotonic() - t0:.0f}s")
        print("subquestions:", status["subquestions_by_status"], "| counts:", status["counts"])
        print("model usage (as reported by Mesh):", status["model_usage"])
        for sq in c.get(f"/runs/{run_id}/subquestions").json():
            print(f"\n[{sq['status']}] {sq['question']}\n   needs: {sq['evidence_requirements']}")
        for s in c.get(f"/runs/{run_id}/searches").json():
            hits = len((s["result"] or {}).get("hits", []))
            print(f"\nsearch #{s['id']} (subquestion {s['subquestion_id']}) {s['query']!r}: {hits} hits, error={s['error']}")
            for sel in s["selections"]:
                print(f"   {sel['outcome']:<18} {sel['url']}   why: {sel['reason']}")
        print("\nmodel calls:")
        calls = c.get(f"/runs/{run_id}/model-calls").json()
        from backend.mesh_client import get_mesh_client
        from backend.services import model_catalog

        prices = {k: v["pricing"] for k, v in model_catalog(get_mesh_client()).items()}
        cost = sum((m["prompt_tokens"] or 0) * float(prices[m["model"]]["prompt_usd_per_1m"]) / 1e6
                   + (m["completion_tokens"] or 0) * float(prices[m["model"]]["completion_usd_per_1m"]) / 1e6
                   for m in calls)
        print(f"   actual Mesh cost from reported tokens: ${cost:.5f} over {len(calls)} calls")
        for m in calls:
            print(f"   {m['agent']:<10} {m['model']} attempt={m['attempt']} ok={m['ok']} tokens(in/out)="
                  f"{m['prompt_tokens']}/{m['completion_tokens']} {m['latency_ms']}ms {m['error'] or ''}")
        print("\nassociations (all unverified):")
        for a in c.get(f"/runs/{run_id}/associations").json():
            print(f"   sq{a['subquestion_id']} <- S{a['source_id']} [{a['origin']}] passages={a['passage_ids']} "
                  f"{a['explanation'] or ''}")
        print("\nMesh-billed cost per request (usage API cost_usd):")
        billed = 0.0
        for m in calls:
            if not m["request_id"]:
                continue
            try:
                page = get_mesh_client().post("/usage/events", cast_to=object,
                                              body={"org_id": "-", "request_id": m["request_id"], "limit": 1})
                ev = (page.get("events") or [{}])[0]
                print(f"   {m['request_id']}: cost_usd={ev.get('cost_usd')} "
                      f"tokens={ev.get('prompt_tokens')}/{ev.get('completion_tokens')}")
                billed += float(ev.get("cost_usd") or 0)
            except Exception as exc:  # noqa: BLE001 - report, do not hide, a failed lookup
                print(f"   {m['request_id']}: lookup failed: {type(exc).__name__}: {str(exc)[:120]}")
        print(f"   provider-reported total: ${billed:.7f} (compare with the calculated figure above)")
        print("\nevents:", json.dumps([e["event_type"] for e in c.get(f"/runs/{run_id}/events").json()]))
        return 0 if status["status"] in ("collection_finished", "stopped") else 1
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


if __name__ == "__main__":
    sys.exit(check() if "--check" in sys.argv else run())
