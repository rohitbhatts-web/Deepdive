"""Bounded LIVE acceptance for Phase 3 (real Mesh; NO search and NO fetching). Not part of the automated suite.

    uv run python scripts/live_verify.py --check        # config, model catalog, flags, cost estimate; no inference
    uv run python scripts/live_verify.py                # draft + verify on a COPY of already collected evidence
    uv run python scripts/live_verify.py --recheck      # Phase 3 audit: 3 labelled test inputs, verifier only, isolated copy
    uv run python scripts/live_verify.py --challenge --claim "..." --passage S2-P10 --quote "..." --alteration "..."
                                                        # one controlled test claim (repeat the group, at most 2)

The source database (default data/live-test-2.db, a finished collection) is never modified: it is copied to
data/live-verify.db first. Hard limits for the main run: 3 claims, 6 model requests including retries, 120 s,
1,500 output tokens per call, 4,000 total, 6,000 characters of passages shown to the drafter. The challenge step is
capped at 2 more requests and 8 in total across both steps. Credentials are read by the app from .env and never printed.
"""

import argparse
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

SOURCE_DB = ROOT / "data" / "live-test-2.db"
DB_PATH = ROOT / "data" / "live-verify.db"
DB_RECHECK = ROOT / "data" / "live-verify-recheck.db"
PORT = 8769
RUN_ID = 1
LIMITS = {
    "MAX_CLAIMS": "3", "MAX_MODEL_REQUESTS": "6", "MAX_ELAPSED_SECONDS": "120", "MAX_OUTPUT_TOKENS": "1500",
    "MAX_TOTAL_OUTPUT_TOKENS": "4000", "DRAFT_INPUT_CHARS": "6000", "VERIFIER_CONTEXT_CHARS": "1200",
}
CHALLENGE_REQUESTS, OVERALL_REQUESTS = 2, 8
ASSUMED_PROMPT_TOKENS = {"drafter": 3000, "verifier": 2500}  # an ESTIMATE: input is bounded by characters, not tokens


def catalog_and_models():
    from backend.config import get_settings
    from backend.mesh_client import get_mesh_client
    from backend.services import model_catalog

    settings = get_settings()
    return settings, get_mesh_client(), model_catalog(get_mesh_client())


def check() -> int:
    from backend.config import get_settings
    from backend.services import check_models, missing_configuration

    missing = missing_configuration(get_settings(), "verify")
    print("still missing (verification needs no search key):", missing or "nothing")
    if missing:
        return 1
    settings, _, catalog = catalog_and_models()
    wanted = {settings.drafter_model, settings.verifier_model}
    check_models(catalog, wanted)
    print("models available and chat-capable:", sorted(wanted))
    print("verifier differs from drafter:", settings.verifier_model != settings.drafter_model,
          "(different models reduce shared blind spots; they do not guarantee accuracy)")
    worst = 0.0
    for role, model in (("drafter", settings.drafter_model), ("verifier", settings.verifier_model)):
        info = catalog[model]
        p = info["pricing"]
        pin, pout = float(p["prompt_usd_per_1m"]), float(p["completion_usd_per_1m"])
        print(f"  {role}: {model}: ${pin:g}/M in, ${pout:g}/M out, ctx {info.get('context_length')}, "
              f"structured_output={info.get('supports_structured_output')}, thinking={info.get('supports_thinking')}")
        if info.get("supports_thinking"):
            print("   WARNING: reasoning tokens may consume the 1,500-token output cap")
        # drafter: 1 call (+1 retry); verifier: up to 3 calls (+retries); the cap is 6 requests in total
        n = 2 if role == "drafter" else 4
        worst += (n * ASSUMED_PROMPT_TOKENS[role] * pin + n * int(LIMITS["MAX_OUTPUT_TOKENS"]) * pout) / 1e6
    print(f"ESTIMATE if the request cap is fully used: ~${worst:.4f} (+ up to 2 challenge requests). Input tokens are "
          "bounded only by the character limits above, so this assumes ~3,000 prompt tokens per call.")
    return 0


def used_requests() -> int:
    db = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)
    return db.execute("SELECT count(*) FROM model_calls WHERE agent IN ('drafter', 'verifier')").fetchone()[0]


def show_claims(c: httpx.Client) -> None:
    for claim in c.get(f"/runs/{RUN_ID}/claims").json():
        print(f"\nclaim #{claim['id']} [{claim['origin']}] status={claim['status']} eligible={claim['eligible_for_report']}")
        print(f"   text: {claim['text']}")
        if claim["provenance"]:
            print(f"   provenance: {claim['provenance']}")
        for ev in claim["evidence"]:
            print(f"   evidence {ev['passage_ref']} ({ev['resolution']}): quote={ev['quote'][:140]!r}")
            if ev["passage"]:
                print(f"      passage text: {ev['passage']['text'][:420]!r}")
        i, s = claim["integrity"], claim["semantic"]
        print(f"   integrity: {i and i['verdict']} - {i and i['reason'][:110]}")
        if s:
            print(f"   semantic: {s['verdict']} ({s['model']}) - {s['reason'][:300]}")
            d = s["details"]
            print(f"      aspects={d.get('aspects')} gaps={d.get('unsupported_details')} conflicts={d.get('conflicts')} "
                  f"cited={d.get('evidence_used')} downgraded={d.get('downgraded', False)}")


def report_usage(c: httpx.Client) -> None:
    from backend.mesh_client import get_mesh_client

    _, _, catalog = catalog_and_models()
    calls = [m for m in c.get(f"/runs/{RUN_ID}/model-calls").json() if m["agent"] in ("drafter", "verifier")]
    calc = billed = 0.0
    print("\nmodel calls (drafter/verifier):")
    for m in calls:
        pr = catalog[m["model"]]["pricing"]
        cost = ((m["prompt_tokens"] or 0) * float(pr["prompt_usd_per_1m"]) + (m["completion_tokens"] or 0) * float(pr["completion_usd_per_1m"])) / 1e6
        calc += cost
        line = (f"   {m['agent']:<8} {m['model']} attempt={m['attempt']} ok={m['ok']} tokens(in/out)={m['prompt_tokens']}/"
                f"{m['completion_tokens']} {m['latency_ms']}ms structured_output={m['structured_output']} {m['error'] or ''}")
        try:
            ev = (get_mesh_client().post("/usage/events", cast_to=object,
                                         body={"org_id": "-", "request_id": m["request_id"], "limit": 1}).get("events") or [{}])[0]
            line += f" | Mesh cost_usd={ev.get('cost_usd')}"
            billed += float(ev.get("cost_usd") or 0)
        except Exception as exc:  # noqa: BLE001 - report a failed lookup instead of hiding it
            line += f" | usage lookup failed: {type(exc).__name__}"
        print(line)
    print(f"   calculated from reported tokens x catalog price: ${calc:.6f}; reported by Mesh usage API: ${billed:.6f}")


def run_main() -> int:
    if DB_PATH.exists():
        print(f"{DB_PATH} already exists; refusing to overwrite. Remove it yourself to repeat the test.")
        return 2
    src = sqlite3.connect(f"file:{SOURCE_DB.as_posix()}?mode=ro", uri=True)
    dst = sqlite3.connect(DB_PATH)
    src.backup(dst)
    dst.close()
    src.close()
    print(f"copied {SOURCE_DB.name} -> {DB_PATH.name} (source untouched); no search or fetching in this mode")
    env = {**os.environ, **LIMITS, "DATABASE_PATH": str(DB_PATH)}
    log = (ROOT / "data" / "live-verify-server.log").open("w")
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
        started = c.post(f"/runs/{RUN_ID}/verify")
        print(f"start -> HTTP {started.status_code}", started.json() if started.status_code != 202 else "")
        if started.status_code != 202:
            return 1
        t0 = time.monotonic()
        while time.monotonic() - t0 < 150:
            v = c.get(f"/runs/{RUN_ID}/verification").json()
            if v["verify_status"] != "verifying":
                break
            time.sleep(2)
        print(f"\nverify_status={v['verify_status']} stop_reason={v['verify_stop_reason']} wall={time.monotonic() - t0:.0f}s")
        print("claims:", v["claims"], v["by_status"], "| eligible for report:", v["eligible_for_report"])
        show_claims(c)
        report_usage(c)
        print("\nevents:", [e["event_type"] for e in c.get(f"/runs/{RUN_ID}/events").json()][-9:])
        return 0 if v["verify_status"] in ("completed", "stopped") else 1
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


def recheck() -> int:
    """Audit recheck: three labelled test inputs through the verifier only (no drafting, no search, no fetching), on an
    isolated COPY of the first live database, which is left untouched. At most 4 model requests, 90 s."""
    from backend import claims, db
    from backend.agents.verifier import verify_pending
    from backend.budget import Budget
    from backend.services import build_services

    if DB_RECHECK.exists():
        print(f"{DB_RECHECK} already exists; refusing to overwrite it. Remove it yourself to repeat.")
        return 2
    src = sqlite3.connect(f"file:{DB_PATH.as_posix()}?mode=ro", uri=True)  # read-only: no migration of the original
    dst = sqlite3.connect(DB_RECHECK)
    src.backup(dst)
    dst.close()
    original3 = src.execute("SELECT text FROM claims WHERE id = 3").fetchone()[0]
    quote3 = src.execute("SELECT quote FROM claim_evidence WHERE claim_id = 3").fetchone()[0]
    src.close()
    inputs = [  # (text, passage, quote, alteration/label)
        (original3, "S1-P5", quote3, "regression: original natural claim #3 re-submitted unchanged (opinion stated as fact)"),
        ("The guide's comparison table lists prototyping, mobile apps and local apps as the ideal uses for SQLite.", "S1-P5",
         "Ideal for" + chr(10) * 2 + "Prototyping, mobile, local apps",
         "test: attributed, atomic version of claim #3's source recommendation"),
        ("The first stable release of SQLite came out in 2004.", "S1-P1", "Just drop the file and start querying.",
         "test: unrelated claim paired with a genuine but irrelevant quote"),
    ]
    base = build_services(mode="verify").settings
    settings = base.model_copy(update={"max_model_requests": 4, "max_elapsed_seconds": 90.0, "max_output_tokens": 1500,
                                       "max_total_output_tokens": 3000, "verifier_context_chars": 1200})
    svc = build_services(settings, mode="verify")
    conn = db.connect(DB_RECHECK)
    ids = []
    for text, pid, quote, label in inputs:
        sq = conn.execute("SELECT s.subquestion_id FROM source_associations s JOIN passages p ON p.source_id = s.source_id"
                          " WHERE p.id = ? AND s.run_id = ? LIMIT 1", (pid, RUN_ID)).fetchone()[0]
        ids.append(claims.add_challenge_claim(conn, RUN_ID, sq, text, [(pid, quote)], alteration=label, derived_from=pid))
    t0 = time.monotonic()
    counts = verify_pending(conn, svc.llm, Budget(settings), settings, RUN_ID, ids)
    print(f"verified {ids} {counts} in {time.monotonic() - t0:.0f}s (limit 90 s)")
    _, _, catalog = catalog_and_models()
    for cid in ids:
        c = claims.get_claim(conn, RUN_ID, cid)
        i, sm = c["integrity"], c["semantic"]
        print(f"{chr(10)}TEST INPUT claim #{cid} [{c['origin']}] test_input={c['provenance'].get('test_input')} -- "
              f"{c['provenance']['alteration']}")
        print(f"   text: {c['text']}")
        print(f"   evidence_id={[e['id'] for e in c['evidence']]} passage={[e['passage_ref'] for e in c['evidence']]}")
        print(f"   integrity: {i['verdict']} (result {i['id']}) | semantic: {sm and sm['verdict']} (result {sm and sm['id']}) | eligible={c['eligible_for_report']}")
        if sm:
            print(f"   reason: {sm['reason'][:420]}")
            print(f"   aspects={sm['details']['aspects']}")
            print(f"   gaps={sm['details']['unsupported_details']} conflicts={sm['details']['conflicts']}"
                  f" downgraded={sm['details'].get('downgraded', False)}")
            print(f"   passages relied on: {[(x['passage_id'], x['role']) for x in sm['passages']]}  evidence rows: {sm['evidence_ids']}")
    calls = [m for m in db.list_model_calls(conn, RUN_ID) if m["id"] in {claims.latest_result(conn, k, 'semantic')['model_call_id'] for k in ids}]
    calc = billed = 0.0
    print(f"{chr(10)}model calls:")
    for m in calls:
        pr = catalog[m["model"]]["pricing"]
        cost = ((m["prompt_tokens"] or 0) * float(pr["prompt_usd_per_1m"]) + (m["completion_tokens"] or 0) * float(pr["completion_usd_per_1m"])) / 1e6
        calc += cost
        line = (f"   {m['model']} ok={m['ok']} tokens(in/out)={m['prompt_tokens']}/{m['completion_tokens']} {m['latency_ms']}ms "
                f"structured_output={m['structured_output']} calculated=${cost:.6f}")
        try:
            from backend.mesh_client import get_mesh_client

            ev = (get_mesh_client().post("/usage/events", cast_to=object, body={"org_id": "-", "request_id": m["request_id"], "limit": 1}).get("events") or [{}])[0]
            line += f" | Mesh-reported cost_usd={ev.get('cost_usd')}"
            billed += float(ev.get("cost_usd") or 0)
        except Exception as exc:  # noqa: BLE001 - report a failed lookup instead of hiding it
            line += f" | usage lookup failed: {type(exc).__name__}"
        print(line)
    total = len(calls)
    print(f"   requests this recheck: {total} (cap 4); calculated ${calc:.6f}; Mesh-reported ${billed:.6f}")
    conn.close()
    return 0


def challenge(args) -> int:
    from backend import claims, db
    from backend.agents.verifier import verify_pending
    from backend.budget import Budget
    from backend.services import build_services

    if not DB_PATH.exists():
        print("run the main step first")
        return 2
    n = len(args.claim)
    if not (1 <= n <= CHALLENGE_REQUESTS) or not (len(args.passage) == len(args.quote) == len(args.alteration) == n):
        print(f"give 1-{CHALLENGE_REQUESTS} matching --claim/--passage/--quote/--alteration groups")
        return 2
    allowed = min(CHALLENGE_REQUESTS, OVERALL_REQUESTS - used_requests())
    if allowed < n:
        print(f"only {allowed} requests left under the {OVERALL_REQUESTS}-request overall cap")
        return 2
    base = build_services(mode="verify").settings
    settings = base.model_copy(update={"max_model_requests": allowed, "max_elapsed_seconds": 120.0,
                                       "max_output_tokens": 1500, "max_total_output_tokens": 4000,
                                       "verifier_context_chars": 1200})
    svc = build_services(settings, mode="verify")
    conn = db.connect(DB_PATH)
    ids = []
    for text, pid, quote, alt in zip(args.claim, args.passage, args.quote, args.alteration, strict=True):
        sq = conn.execute("SELECT s.subquestion_id FROM source_associations s JOIN passages p ON p.source_id = s.source_id"
                          " WHERE p.id = ? AND s.run_id = ? LIMIT 1", (pid, RUN_ID)).fetchone()[0]
        ids.append(claims.add_challenge_claim(conn, RUN_ID, sq, text, [(pid, quote)], alteration=alt, derived_from=pid))
    counts = verify_pending(conn, svc.llm, Budget(settings), settings, RUN_ID, ids)
    conn.close()
    print("challenge claims:", ids, counts)
    server_env = {**os.environ, "DATABASE_PATH": str(DB_PATH)}
    server = subprocess.Popen([sys.executable, "-m", "uvicorn", "backend.main:app", "--port", str(PORT)],
                              cwd=ROOT, env=server_env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        c = httpx.Client(base_url=f"http://127.0.0.1:{PORT}", timeout=60)
        for _ in range(30):
            try:
                c.get("/health")
                break
            except httpx.HTTPError:
                time.sleep(1)
        for cid in ids:
            claim = c.get(f"/runs/{RUN_ID}/claims/{cid}").json()
            print(f"\nCHALLENGE claim #{cid} [{claim['origin']}] test_input={claim['provenance'].get('test_input')} "
                  f"alteration={claim['provenance'].get('alteration')!r}\n   text: {claim['text']}\n   status={claim['status']} "
                  f"eligible={claim['eligible_for_report']}")
            if claim["semantic"]:
                print(f"   semantic: {claim['semantic']['verdict']} - {claim['semantic']['reason'][:300]}")
                print(f"   aspects={claim['semantic']['details'].get('aspects')}")
        report_usage(c)
    finally:
        server.terminate()
        server.wait(timeout=15)
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--challenge", action="store_true")
    ap.add_argument("--recheck", action="store_true")
    ap.add_argument("--claim", action="append", default=[])
    ap.add_argument("--passage", action="append", default=[])
    ap.add_argument("--quote", action="append", default=[])
    ap.add_argument("--alteration", action="append", default=[])
    a = ap.parse_args()
    sys.exit(check() if a.check else recheck() if a.recheck else challenge(a) if a.challenge else run_main())
