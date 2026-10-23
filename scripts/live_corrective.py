"""Bounded LIVE acceptance for the Phase 4 audit: can corrective evidence become usable claims? (real Mesh, Tavily, web)

    uv run python scripts/live_corrective.py --check   # config, models, the FIXED bounds, expected path, cost ESTIMATE; no inference
    uv run python scripts/live_corrective.py           # builds the scenario copy, runs ONCE, prints the evidence; refuses to repeat

The starting state is a CONSTRUCTED COPY of real stored data, not a natural continuation of a run: the real sources 1-3 with
their passages, the real planner subquestions, and the real claims #1, #2, #3, #6, #7 with their stored verdicts, taken from
data/live-complete.db. NOT copied: the two Phase 3 test inputs (#4, #5), the pages 4-5 that an earlier run retrieved but
never drafted, and all earlier coverage, tasks, reports and events. Nothing is fabricated or edited. The prior databases are
opened read-only and compared byte-for-byte (content digest) before and after. The bounds below are not raised automatically.
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

SOURCE_DB = ROOT / "data" / "live-complete.db"
DB_PATH = ROOT / "data" / "live-corrective.db"
PORT = 8771
RUN_ID = 1
KEEP_SOURCES = (1, 2, 3)
KEEP_CLAIMS = (1, 2, 3, 6, 7)
LIMITS = {
    "MAX_CORRECTION_ROUNDS": "1",       # one corrective round is enough to show the path; a second is not required
    "MAX_SEARCH_QUERIES": "2",          # at most 2 NEW searches
    "MAX_PAGE_FETCHES": "4",            # at most 4 NEW page fetches
    "MAX_TASKS_PER_ROUND": "2",         # exactly the two searches the search limit allows
    "MAX_CANDIDATE_CLAIMS": "3",        # hard cumulative cap on NEW candidate claims drafted from corrective evidence
    "MAX_REPORT_CLAIMS": "12",          # separate: the most claims the report may contain
    "MAX_MODEL_REQUESTS": "15",         # TOTAL, including retries, failed requests, obligations, coverage, drafting, synthesis
    "MAX_ELAPSED_SECONDS": "240",
    "MAX_OUTPUT_TOKENS": "1500", "MAX_TOTAL_OUTPUT_TOKENS": "6000",
    "DRAFT_INPUT_CHARS": "6000", "VERIFIER_CONTEXT_CHARS": "1200", "COVERAGE_INPUT_CHARS": "6000", "REVISER_INPUT_CHARS": "4000",
    "QUERIES_PER_TASK": "1", "RESULTS_PER_QUERY": "5", "URLS_PER_SUBQUESTION": "2",
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
    print("still missing:", missing or "nothing")
    if missing:
        return 1
    catalog = model_catalog(get_mesh_client())
    roles = {"researcher (queries, selection)": settings.researcher_model, "drafter (candidate claims) / reviser": settings.drafter_model,
             "verifier": settings.verifier_model, "obligations + coverage": settings.effective_coverage_model,
             "synthesizer": settings.effective_synthesizer_model}
    check_models(catalog, set(roles.values()))
    print("models (available, chat-capable):")
    for role, model in roles.items():
        p = catalog[model]["pricing"]
        print(f"  {role}: {model}  ${float(p['prompt_usd_per_1m']):g}/M in, ${float(p['completion_usd_per_1m']):g}/M out, "
              f"structured_output={catalog[model].get('supports_structured_output')}")
    din = max(float(catalog[m]["pricing"]["prompt_usd_per_1m"]) for m in roles.values())
    dout = max(float(catalog[m]["pricing"]["completion_usd_per_1m"]) for m in roles.values())
    n = int(LIMITS["MAX_MODEL_REQUESTS"])
    bound = (n * ASSUMED_PROMPT_TOKENS * din + int(LIMITS["MAX_TOTAL_OUTPUT_TOKENS"]) * dout) / 1e6
    print("\nBOUNDS (fixed in this file):")
    for k, v in LIMITS.items():
        print(f"  {k}={v}")
    print(f"""
EXPECTED PATH (an analysis, not a promise) and the request budget it needs:
  obligations derived for the 7 requirements ........................ 1   (coverage model)
  coverage assessment 0 ............................................... 1   (coverage model)
  correction round 1: 2 targeted searches = 2 x (queries + selection) .. 4   (researcher model); up to 4 page fetches
  draft candidate claims from the NEW pages (cap 3) ................... 1   (drafter model)
  integrity + semantic verification of each candidate (<= 3) .......... <=3 (verifier model)
  coverage assessment 1 ............................................... 1
  report synthesis .................................................... 1
  expected 12 requests; {n} allowed leaves 3 spare for retries. Drafting capacity for corrective evidence is explicit
  (MAX_CANDIDATE_CLAIMS=3) and independent of the 3 verified claims already stored and of the report limit.
COST: ESTIMATE of the bound, not a ceiling: {n} requests x ~{ASSUMED_PROMPT_TOKENS} prompt tokens at the dearest input price plus
  {LIMITS['MAX_TOTAL_OUTPUT_TOKENS']} output tokens at the dearest output price = ~${bound:.3f}. The expected path (6 Haiku-class calls, the
  rest Qwen-class) should cost well under half of that. Mesh-reported charges (usage API cost_usd) are printed after the run,
  separately from the figure calculated from reported tokens and catalog prices. Tavily: at most 2 credits.""")
    return 0


# ---- the scenario: a constructed copy of REAL stored rows ---------------------------------------------------------------------------


def build_scenario() -> dict:
    from backend import db

    src = sqlite3.connect(f"file:{SOURCE_DB.as_posix()}?mode=ro", uri=True)
    src.row_factory = sqlite3.Row
    dst = db.connect(DB_PATH)

    def rows(sql, args=()):
        return src.execute(sql, args).fetchall()

    def put(table, row, **override):
        cols = [c[1] for c in dst.execute(f"PRAGMA table_info({table})")]
        data = {k: row[k] for k in row.keys() if k in cols} | override  # noqa: SIM118 - sqlite3.Row iterates values
        dst.execute(f"INSERT INTO {table} ({', '.join(data)}) VALUES ({', '.join('?' * len(data))})", list(data.values()))

    marks = lambda seq: ",".join("?" * len(seq))
    sources, claims = list(KEEP_SOURCES), list(KEEP_CLAIMS)
    counts = {}
    with db.tx(dst):
        put("runs", rows("SELECT * FROM runs WHERE id = ?", (RUN_ID,))[0], status="collection_finished", stop_reason=None,
            verify_status="completed", verify_stop_reason=None, complete_status="not_started", complete_stop_reason=None)
        for r in rows("SELECT * FROM subquestions WHERE run_id = ?", (RUN_ID,)):
            put("subquestions", r)
        call_ids = {r[0] for r in rows(f"SELECT draft_call_id FROM claims WHERE id IN ({marks(claims)})", claims) if r[0]}
        results = rows(f"SELECT * FROM verification_results WHERE claim_id IN ({marks(claims)}) ORDER BY id", claims)
        call_ids |= {r["model_call_id"] for r in results if r["model_call_id"]}
        for r in rows(f"SELECT * FROM model_calls WHERE id IN ({marks(sorted(call_ids))}) ORDER BY id", sorted(call_ids)):
            put("model_calls", r)
        for r in rows(f"SELECT * FROM sources WHERE id IN ({marks(sources)}) ORDER BY id", sources):
            put("sources", r)
        for r in rows(f"SELECT * FROM passages WHERE source_id IN ({marks(sources)}) ORDER BY source_id, ordinal", sources):
            put("passages", r)
        for r in rows(f"SELECT * FROM fetch_attempts WHERE source_id IN ({marks(sources)}) ORDER BY id", sources):
            put("fetch_attempts", r)
        keep_attempts = {r[0] for r in dst.execute("SELECT id FROM fetch_attempts")}
        for r in rows("SELECT * FROM searches WHERE id IN (1, 2) ORDER BY id"):
            put("searches", r)
        for r in rows("SELECT * FROM research_selections WHERE search_id IN (1, 2) ORDER BY id"):
            put("research_selections", r, fetch_attempt_id=r["fetch_attempt_id"] if r["fetch_attempt_id"] in keep_attempts else None)
        for r in rows(f"SELECT * FROM source_associations WHERE source_id IN ({marks(sources)}) ORDER BY id", sources):
            put("source_associations", r)
        for r in rows(f"SELECT * FROM claims WHERE id IN ({marks(claims)}) ORDER BY id", claims):
            put("claims", r)
        for r in rows(f"SELECT * FROM claim_evidence WHERE claim_id IN ({marks(claims)}) ORDER BY id", claims):
            put("claim_evidence", r)
        for r in results:
            put("verification_results", r)
        rids = [r["id"] for r in results]
        for r in rows(f"SELECT * FROM verification_result_evidence WHERE result_id IN ({marks(rids)})", rids):
            put("verification_result_evidence", r)
        for r in rows(f"SELECT * FROM verification_result_passages WHERE result_id IN ({marks(rids)})", rids):
            put("verification_result_passages", r)
        counts = {t: dst.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
                  for t in ("subquestions", "sources", "passages", "claims", "verification_results", "source_associations")}
        db.add_event(dst, RUN_ID, "scenario_built", "live_corrective", {
            "kind": "CONSTRUCTED COPY of real stored rows, not a natural continuation of a run",
            "copied_from": SOURCE_DB.name, "copied": counts, "sources_kept": sources, "claims_kept": claims,
            "not_copied": ["test-input claims 4 and 5", "sources 4 and 5 (retrieved earlier, never drafted)",
                           "earlier coverage, tasks, reports and events"]})
    assert not dst.execute("PRAGMA foreign_key_check").fetchall(), "scenario copy violates a foreign key"
    dst.close()
    src.close()
    return counts


def run() -> int:
    from backend.mesh_client import get_mesh_client
    from backend.services import model_catalog

    if DB_PATH.exists():
        print(f"{DB_PATH} already exists; refusing to rebuild or repeat. Remove it yourself to run again.")
        return 2
    priors = [SOURCE_DB, ROOT / "data" / "live-verify.db", ROOT / "data" / "live-test-2.db", ROOT / "data" / "live-test.db"]
    before = {p.name: digest(p) for p in priors}
    counts = build_scenario()
    scenario = sqlite3.connect(DB_PATH)
    first_new_call = scenario.execute("SELECT coalesce(max(id), 0) FROM model_calls").fetchone()[0]  # copied history ends here
    scenario.close()
    print("scenario built from real stored rows:", counts)
    env = {**os.environ, **LIMITS, "DATABASE_PATH": str(DB_PATH)}
    log = (ROOT / "data" / "live-corrective-server.log").open("w")
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
        print("budget used / limit:", done["budget_used_of_limit"])

        print("\n=== COVERAGE OBLIGATIONS (structured, per requirement) and the assessments ===")
        for rnd in (0, 1):
            cov = c.get(f"/runs/{RUN_ID}/coverage?round={rnd}").json()
            if cov["round"] is None:
                continue
            print(f"\nassessment {rnd}: {cov['summary']}")
            for q in cov["requirements"]:
                print(f" {q['requirement_key']} [{q['status']}] {q['requirement_text'][:70]!r}  supported by {q['supporting_claim_ids']}")
                for o in q["obligations"]:
                    n = f" notes={o['notes']}" if o["notes"] else ""
                    print(f"     {o['obligation_key']} {o['subject']} / {o['dimension']}: {o['status']} by {o['supporting_claim_ids']}"
                          f" gap={o['gap'][:90]!r}{n}")

        print("\n=== CORRECTION TASKS ===")
        for rnd in c.get(f"/runs/{RUN_ID}/corrections").json()["rounds"]:
            print(f"planned round {rnd['planned_round']} (executed={rnd['executed']})")
            for t in rnd["tasks"]:
                print(f"   task {t['id']} {t['kind']:<17} {t['status']:<8} req={t['requirement_key']} claim={t['claim_id']} "
                      f"gap={str(t['spec'].get('gap', ''))[:100]!r} -> {str(t['outcome'])[:200]}")
        events = c.get(f"/runs/{RUN_ID}/events").json()
        print("\nqueries generated for tasks:")
        for e in events:
            if e["event_type"] == "queries_generated" and e["payload"].get("focus"):
                print("  ", e["payload"]["queries"], "| focus:", e["payload"]["focus"])
        for e in events:
            if e["event_type"] in ("candidates_drafted", "drafting_skipped", "draft_input", "draft_truncated", "claim_excluded", "result_skipped"):
                print(f"event {e['event_type']}: {str(e['payload'])[:300]}")
        print("\nsearches:", [(s["id"], s["query"][:80]) for s in c.get(f"/runs/{RUN_ID}/searches").json()])
        sources = c.get(f"/runs/{RUN_ID}/sources").json()
        print("sources:", [(s["id"], s["title"], s["requested_url"][:70]) for s in sources])

        print("\n=== CLAIMS (candidates drafted from NEW passages are provenance.candidate=True) ===")
        claims = {x["id"]: x for x in c.get(f"/runs/{RUN_ID}/claims").json()}
        for x in claims.values():
            tag = "CANDIDATE" if x["provenance"].get("candidate") else "stored   "
            i, sm = x["integrity"], x["semantic"]
            print(f"\n [{tag}] #{x['id']} v{x['version']} status={x['status']} eligible={x['eligible_for_report']} excluded={x['excluded'] and x['excluded']['reason'][:70]!r}")
            print(f"    text: {x['text']}")
            if tag == "CANDIDATE":
                for ev in x["evidence"]:
                    print(f"    evidence {ev['id']} {ev['passage_ref']} quote={ev['quote'][:160]!r}")
                    if ev["passage"]:
                        print(f"       passage ({ev['passage']['source_id']}): {ev['passage']['text'][:420]!r}")
                print(f"    integrity: {i and i['verdict']} (result {i and i['id']}) | semantic: {sm and sm['verdict']} (result {sm and sm['id']})")
                if sm:
                    print(f"    reason: {sm['reason'][:380]}\n    aspects={sm['details'].get('aspects')} relied on={[(p['passage_id'], p['role']) for p in sm['passages']]}")

        rep = c.get(f"/runs/{RUN_ID}/report")
        if rep.status_code == 200:
            rep = rep.json()
            print(f"\n=== REPORT {rep['id']}: outcome={rep['outcome']} fully_addressed={rep['fully_addressed']} ordering={rep['ordering']} stop={rep['stop_reason']!r}")
            bad = 0
            for s in rep["sections"]:
                for it in s["claims"]:
                    st = claims[it["claim_id"]]
                    bad += not (it["text"] == st["text"] and it["claim_version"] == st["version"] and it["citations"] == st["citations"] and st["eligible_for_report"])
            print("report claims differing from the stored eligible claim (text/version/citations):", bad)
            print("claims in the report:", [it["claim_id"] for s in rep["sections"] for it in s["claims"]])
            print("requirements:", [(q["requirement_key"], q["status"]) for q in rep["requirements"]])
            print("omissions:", rep["omissions"])
            print("\n--- report rendered from stored rows ---")
            print(rep["markdown"])
        else:
            print("\nno report:", rep.status_code)

        calls = [m for m in c.get(f"/runs/{RUN_ID}/model-calls").json() if m["id"] > first_new_call]
        catalog = model_catalog(get_mesh_client())
        calc = billed = 0.0
        print("\n=== model calls made by THIS job ===")
        for m in calls:
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
        print(f"   requests={len(calls)}; calculated from reported tokens x catalog prices ${calc:.5f}; reported by Mesh usage API ${billed:.5f}")
        after = {p.name: digest(p) for p in priors}
        print("\nprior databases unchanged:", {k: before[k] == after[k] for k in before})
        return 0 if done["complete_status"] in ("completed", "stopped") else 1
    finally:
        server.terminate()
        server.wait(timeout=15)
        log.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    sys.exit(check() if a.check else run())
