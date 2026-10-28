"""FIXTURE helpers for the Phase 4 tests: a collected run whose claims are in known states, written straight to the
database with the verdict rows a real verifier would have produced (no model involved)."""

from backend import claims, correction, db
from tests.seed import PARAS, Q0, Q2, seed_collected_run

SINGLE_FILE = PARAS[0].split(". ")[1]  # "Each database is kept in one ordinary file."
CONCURRENCY = "It can handle hundreds of concurrent clients, though results may vary."
SENTINEL = "Test input sentinel: SQLite is written in C."
BAD_QUOTE = "SQLite keeps all of its data inside one file on disk"  # does not occur in any stored passage


def verdict(conn, claim_id, value, *, passages=(), unsupported=(), conflicts=(), aspects=None, rubric=True):
    """Record a semantic result the way verify_claim does (rubric-stamped unless rubric=False = a Phase 3 legacy row)."""
    details = {"model_verdict": value, "aspects": aspects or {}, "unsupported_details": list(unsupported),
               "conflicts": list(conflicts), "evidence_used": [p for p, _ in passages]}
    if rubric:
        details["rubric"] = claims.CURRENT_RUBRIC
    with db.tx(conn):
        claims.record_result(conn, claim_id, "semantic", value, f"fixture {value}", model="m", details=details,
                             passages=list(passages),
                             evidence_ids=[e["id"] for e in claims.claim_evidence(conn, claim_id)
                                           if any(e["passage_id"] == p for p, _ in passages)])


def add(conn, r, sq, text, ref, quote, settings, *, origin="drafted", parent=None, version=1, provenance=None):
    with db.tx(conn):
        cid = claims.add_claim(conn, r["run"], r[sq], text, [(ref, quote)], origin=origin, parent_claim_id=parent,
                               version=version, provenance=provenance)
    claims.check_integrity(conn, cid, settings)
    return cid


def seed_mixed(conn, settings):
    """Run with sources, planner subquestions Q1 (2 requirements) and Q2 (1), and these claims:
    A supported (eligible) | B integrity failed (misquote) | C partially supported | D contradicted |
    T a test input that is 'supported' (must be ignored by everything). Verification is marked completed."""
    r = seed_collected_run(conn)
    ids = {"A": add(conn, r, "sq1", Q0, r["p"](0), Q0, settings)}
    verdict(conn, ids["A"], "supported", passages=[(r["p"](0), "claim_evidence")])
    ids["B"] = add(conn, r, "sq1", "SQLite keeps all of its data in a single file.", r["p"](0), BAD_QUOTE, settings)
    ids["C"] = add(conn, r, "sq2", "Postgres handles hundreds of concurrent clients and needs no maintenance.",
                   r["p"](2), CONCURRENCY, settings)
    verdict(conn, ids["C"], "partially_supported", passages=[(r["p"](2), "claim_evidence")],
            unsupported=["needs no maintenance"], aspects={"scope": "mismatch"})
    ids["D"] = add(conn, r, "sq2", "Postgres cannot handle more than ten concurrent clients.", r["p"](2), CONCURRENCY, settings)
    verdict(conn, ids["D"], "contradicted", passages=[(r["p"](2), "claim_evidence")], conflicts=["ten vs hundreds"])
    ids["T"] = add(conn, r, "sq1", SENTINEL, r["p"](0), Q0, settings, origin="controlled_challenge",
                   provenance={"test_input": True, "alteration": "fixture", "derived_from": r["p"](0)})
    verdict(conn, ids["T"], "supported", passages=[(r["p"](0), "claim_evidence")])
    with db.tx(conn):
        db.set_verify_status(conn, r["run"], "completed")
    correction.ensure_fallback_obligations(conn, r["run"])  # one whole-requirement obligation each, no model call
    return r, ids


REQ = {"Q1.R1": "deployment model", "Q1.R2": "release facts", "Q2.R1": "concurrency model"}  # keys when subquestion ids are 1, 2
_ = Q2
