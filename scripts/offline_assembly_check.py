"""OFFLINE report-assembly check on a COPY of a stored run, with FIXTURE relevance judgments. No model, search or fetch is called.

It copies the database to a temporary file (the source is never opened for writing), runs the real `coverage.assess` with a stub model that returns the
judgments below, builds a report with the real `build_report`, and prints the stored report's section selection next to the new one. The judgments are
LABELLED FIXTURES written by hand for the compact-continuation run of Q02: they exercise routing and the deterministic rules, and say nothing about how
accurate a model's judgments would be.

    uv run python scripts/offline_assembly_check.py data/q02-compact-continuation.db
"""

import shutil
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from backend import claims, correction, db
from backend.agents import coverage
from backend.agents.schemas import CoverageAssessment
from backend.budget import Budget
from backend.config import Settings
from backend.report import build_report, get_report

# FIXTURE judgments: (obligation key, status the assessor would claim, [(claim id, role, reason)], gap, missing_kind)
FIXTURE = [
    ("Q1.R1.O1", "addressed", [(6, "direct_answer", "FIXTURE: states the date of the document")], "", "none"),
    ("Q1.R2.O1", "addressed", [(6, "direct_answer", "FIXTURE: names the document number")], "", "none"),
    ("Q2.R1.O1", "addressed", [(5, "relevant_context", "FIXTURE: a naming milestone, not the publication date")], "", "none"),
    ("Q2.R2.O1", "unresolved", [], "no document number is stated", "detail"),
    ("Q3.R1.O1", "addressed", [(2, "relevant_context", "FIXTURE: TLS is not the transport protocol")], "", "none"),
    ("Q3.R2.O1", "unresolved", [], "not described", "detail"),
    ("Q4.R1.O1", "addressed", [(3, "direct_answer", "FIXTURE: names the protocol")], "", "none"),
    ("Q4.R2.O1", "addressed", [(4, "direct_answer", "FIXTURE: states QUIC instead of TCP")], "", "none"),
]


class StubLLM:
    """Returns the fixture judgments instead of calling a model."""

    last_call_id = None

    def call(self, *args, schema, **kwargs):
        return schema.model_validate({"obligations": [
            {"obligation_key": k, "status": st, "claim_roles": [{"claim_id": c, "role": r, "reason": why} for c, r, why in roles], "gap": gap, "missing_kind": kind}
            for k, st, roles, gap, kind in FIXTURE]})


def sections(report: dict) -> list[str]:
    out = []
    for s in report["sections"]:
        answered = {None: "not judged", True: "answered", False: "NO DIRECT ANSWER"}[s.get("answered")]
        out.append(f"  {s['label']} [{answered}] {s['subquestion_text'][:60]}")
        out += [f"      answer  : claim {c['claim_id']}: {c['text'][:90]}" + (f"  [directly answers: {'; '.join(c['requirement_texts'])}]" if c.get("requirement_texts") else "")
                for c in s["claims"]]
        out += [f"      context : claim {c['claim_id']}: {c['text'][:90]}" for c in s.get("context", [])]
        out += [f"      OPEN    : {r['requirement_text']} ({r['status']}: {r['gap'][:80]})" for r in s.get("open_requirements", [])]
    return out


def main(source: str) -> int:
    assert CoverageAssessment  # the stub validates its judgments against the real schema
    tmp = Path(tempfile.mkdtemp(prefix="deepdive-offline-")) / "copy.db"
    shutil.copy2(source, tmp)
    conn = db.connect(tmp)
    run_id = conn.execute("SELECT id FROM runs ORDER BY id LIMIT 1").fetchone()[0]
    before = get_report(conn, run_id)
    print(f"STORED report {before['id']} (made without relevance judgments; relevance_judged={before['relevance_judged']}):")
    print("\n".join(sections(before)))
    settings = Settings(_env_file=None, mesh_api_key="")
    coverage.assess(conn, StubLLM(), Budget(settings), settings, run_id, (correction.latest_round(conn, run_id) or 0) + 1)
    info = build_report(conn, run_id, plan=None, ordering_reason="offline check", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0)
    after = get_report(conn, run_id, info["report_id"])
    print(f"\nNEW report {after['id']} with FIXTURE judgments (outcome {after['outcome']}, relevance_judged={after['relevance_judged']}):")
    print("\n".join(sections(after)))
    print("\nrequirements after:")
    for r in correction.assessments(conn, run_id):
        print(f"  {r['requirement_key']:6s} {r['status']:19s} direct={r['supporting_claim_ids']} gap={r['gap'][:90]!r}")
    print("\nomitted:", {o["claim_id"]: o["reason"][:70] for o in after["omissions"]})
    stored = {c["id"]: c["text"] for c in claims.list_claims(conn, run_id)}
    exact = all(c["text"] == stored[c["claim_id"]] for s in after["sections"] for c in s["claims"] + s.get("context", []))
    print("claim wording identical to the stored claims:", exact)
    print(f"(working copy {tmp}; the source database was only copied)")
    return 0 if exact else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]) if len(sys.argv) > 1 else sys.exit(__doc__))
