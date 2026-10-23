"""Phase 3 deterministic behaviour: evidence integrity, immutability, run isolation, versioning, migration.
No model is involved in anything here."""

import sqlite3

import pytest

from backend import claims, db
from tests.seed import PARAS, Q0, seed_collected_run

GOOD = (Q0, Q0)  # claim text, quote


def make(conn, run, text, evidence, **kw):
    with db.tx(conn):
        return claims.add_claim(conn, run["run"], run["sq1"], text, evidence, origin="drafted", **kw)


@pytest.fixture
def run(conn):
    return seed_collected_run(conn)


def check(conn, settings, claim_id):
    return claims.check_integrity(conn, claim_id, settings)


# --- missing / fabricated / cross-run references -----------------------------------------------------------------


def test_valid_reference_passes_with_exact_match_offset(conn, settings, run):
    cid = make(conn, run, Q0, [(run["p"](0), Q0)])
    result = check(conn, settings, cid)
    assert result["verdict"] == "passed" and "does not show support" in result["reason"]
    ref = result["details"]["references"][0]
    assert ref["match"] == {"mode": "exact", "start": PARAS[0].find(Q0)} and ref["resolution"] == "resolved"


def test_fabricated_passage_is_recorded_and_rejected(conn, settings, run):
    cid = make(conn, run, Q0, [("S9-P9", Q0)])
    ev = claims.claim_evidence(conn, cid)[0]
    assert (ev["passage_id"], ev["resolution"], ev["passage_ref"]) == (None, "unknown_passage", "S9-P9")
    result = check(conn, settings, cid)
    assert result["verdict"] == "failed" and "passage does not exist" in result["reason"]
    assert claims.get_claim(conn, run["run"], cid)["status"] == "integrity_failed"


def test_cross_run_passage_never_resolves(conn, settings, run):
    other = seed_collected_run(conn, n=2)  # a second run in the same database
    cid = make(conn, run, Q0, [(other["p"](0), Q0)])
    ev = claims.claim_evidence(conn, cid)[0]
    assert ev["passage_id"] is None and ev["resolution"] == "cross_run_passage"
    assert "different run" in check(conn, settings, cid)["reason"]


def test_database_refuses_a_cross_run_passage_link_even_if_code_tried(conn, run):
    other = seed_collected_run(conn, n=2)
    cid = make(conn, run, Q0, [])
    with pytest.raises(sqlite3.IntegrityError, match="same|belong"):
        conn.execute("INSERT INTO claim_evidence (claim_id, position, passage_ref, passage_id, resolution, quote)"
                     " VALUES (?, 0, ?, ?, 'resolved', 'x')", (cid, other["p"](0), other["p"](0)))


def test_no_evidence_and_blank_claim_text_fail(conn, settings, run):
    assert "no evidence references" in check(conn, settings, make(conn, run, Q0, []))["reason"]
    assert "claim text is empty" in check(conn, settings, make(conn, run, "   ", [(run["p"](0), Q0)]))["reason"]


# --- quotes -----------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("quote,problem", [
    ("", "quote is empty"),
    ("   ", "quote is empty"),
    ("SQLite is", "shorter than"),  # a short quote proves nothing even though it occurs
    ("SQLite is a Python library embedded directly in the application process.", "does not occur"),
    ("sqlite is a c library embedded directly in the application process.", "does not occur"),  # case is not normalised
    ("The database is a single file on disk. SQLite is a C library embedded directly in the application process.", "does not occur"),
])
def test_bad_quotes_are_rejected(conn, settings, run, quote, problem):
    result = check(conn, settings, make(conn, run, Q0, [(run["p"](0), quote)]))
    assert result["verdict"] == "failed" and problem in result["reason"]


def test_quote_with_a_changed_number_or_date_does_not_match(conn, settings, run):
    ok = "Version 3.45 was released in January 2024."
    for altered in ("Version 3.54 was released in January 2024.", "Version 3.45 was released in January 2023."):
        result = check(conn, settings, make(conn, run, ok, [(run["p"](1), altered)]))
        assert result["verdict"] == "failed", altered
    assert check(conn, settings, make(conn, run, ok, [(run["p"](1), ok)]))["verdict"] == "passed"


@pytest.mark.parametrize("quote", [
    "SQLite is a C library embedded\ndirectly in the application process.",  # newline for a space
    "SQLite  is a C library embedded directly in the application process.",  # double and non-breaking space
    "SQLite is a C library\u200b embedded directly in the application process.",  # zero-width character
])
def test_conservative_normalisation_is_accepted_and_labelled(conn, settings, run, quote):
    ref = check(conn, settings, make(conn, run, Q0, [(run["p"](0), quote)]))["details"]["references"][0]
    assert ref["ok"] and ref["match"]["mode"] == "normalized"


def test_normalisation_unifies_typographic_quotes_only():
    assert claims.find_quote("It’s “fast” and small.", "It's \"fast\" and small.")["mode"] == "normalized"
    assert claims.find_quote("cost: 5–10 ms", "cost: 5-10 ms") is None  # dashes are NOT unified


def test_integrity_is_idempotent_and_stored_apart_from_semantic_verdicts(conn, settings, run):
    cid = make(conn, run, Q0, [(run["p"](0), Q0)])
    first, second = check(conn, settings, cid), check(conn, settings, cid)
    assert first["id"] == second["id"]
    assert conn.execute("SELECT count(*) FROM verification_results WHERE claim_id = ?", (cid,)).fetchone()[0] == 1
    assert claims.latest_result(conn, cid, "semantic") is None
    assert claims.get_claim(conn, run["run"], cid)["status"] == "awaiting_verification"


# --- immutability, versioning, isolation ------------------------------------------------------------------------------


def test_claims_evidence_and_results_cannot_be_edited_or_deleted(conn, settings, run):
    cid = make(conn, run, Q0, [(run["p"](0), Q0)])
    rid = check(conn, settings, cid)["id"]
    for sql in ("UPDATE claims SET text = 'edited' WHERE id = ?", "DELETE FROM claims WHERE id = ?",
                "UPDATE claim_evidence SET quote = 'edited' WHERE claim_id = ?", "DELETE FROM claim_evidence WHERE claim_id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable|never deleted"):
            conn.execute(sql, (cid,))
    for sql in ("UPDATE verification_results SET verdict = 'failed' WHERE id = ?", "DELETE FROM verification_results WHERE id = ?"):
        with pytest.raises(sqlite3.IntegrityError, match="immutable|never deleted"):
            conn.execute(sql, (rid,))
    assert conn.execute("SELECT text FROM claims WHERE id = ?", (cid,)).fetchone()[0] == Q0


def test_a_new_version_is_a_new_row_and_does_not_inherit_the_old_verdict(conn, settings, run):
    v1 = make(conn, run, Q0, [(run["p"](0), Q0)])
    check(conn, settings, v1)
    with db.tx(conn):
        claims.record_result(conn, v1, "semantic", "supported", "fixture", model="m")
    v2 = make(conn, run, "SQLite is a C library.", [(run["p"](0), Q0)], parent_claim_id=v1, version=2)
    old, new = claims.get_claim(conn, run["run"], v1), claims.get_claim(conn, run["run"], v2)
    assert old["status"] == "supported" and old["text"] == Q0  # untouched
    assert (new["status"], new["eligible_for_report"], new["version"], new["parent_claim_id"]) == ("drafted", False, 2, v1)


def test_semantic_stage_only_accepts_semantic_verdicts(conn, run):
    cid = make(conn, run, Q0, [(run["p"](0), Q0)])
    with pytest.raises(sqlite3.IntegrityError), db.tx(conn):
        claims.record_result(conn, cid, "integrity", "supported", "wrong stage")
    with pytest.raises(sqlite3.IntegrityError), db.tx(conn):
        claims.record_result(conn, cid, "semantic", "passed", "wrong stage")


def test_claims_cannot_cross_runs(conn, run):
    other = seed_collected_run(conn, n=2)
    with pytest.raises(sqlite3.IntegrityError, match="same run"), db.tx(conn):
        claims.add_claim(conn, run["run"], other["sq1"], "x" * 12, [], origin="drafted")  # subquestion of another run
    parent = make(conn, other, Q0, [])
    with pytest.raises(sqlite3.IntegrityError, match="same run"), db.tx(conn):
        claims.add_claim(conn, run["run"], run["sq1"], "x" * 12, [], origin="drafted", parent_claim_id=parent, version=2)
    assert claims.get_claim(conn, run["run"], parent) is None  # and another run's claim is invisible by ID


def test_a_result_can_only_cite_its_own_claims_evidence(conn, settings, run):
    a = make(conn, run, Q0, [(run["p"](0), Q0)])
    b = make(conn, run, Q0, [(run["p"](0), Q0)])
    foreign = claims.claim_evidence(conn, b)[0]["id"]
    with pytest.raises(sqlite3.IntegrityError, match="own claim"), db.tx(conn):
        claims.record_result(conn, a, "semantic", "supported", "x", evidence_ids=[foreign])


def test_only_a_supported_semantic_verdict_makes_a_claim_report_eligible():
    passed, failed = {"verdict": "passed"}, {"verdict": "failed"}
    assert claims.claim_state(None, None) == ("drafted", False)
    assert claims.claim_state(failed, None) == ("integrity_failed", False)
    assert claims.claim_state(passed, None) == ("awaiting_verification", False)
    for v in ("partially_supported", "unsupported", "contradicted"):
        assert claims.claim_state(passed, {"verdict": v}) == (v, False)
    assert claims.claim_state(passed, {"verdict": "error"}) == ("verification_error", False)
    assert claims.claim_state(passed, {"verdict": "supported"}) == ("supported", True)
    assert claims.claim_state(failed, {"verdict": "supported"}) == ("integrity_failed", False)  # integrity gates support


# --- migration -----------------------------------------------------------------------------------------------------------


def test_schema_v3_database_gains_phase3_tables_without_losing_rows(tmp_path):
    path = tmp_path / "v3.db"
    c = db.connect(path)
    run = seed_collected_run(c)
    c.executescript("DROP TABLE report_claim_roles; DROP TABLE report_relevance; DROP TABLE claim_relevance; DROP TABLE obligation_supporting_claims; DROP TABLE obligation_assessments; DROP TABLE coverage_obligations;"
                    " DROP TABLE report_omissions; DROP TABLE report_gaps; DROP TABLE report_citations;"
                    " DROP TABLE report_claims; DROP TABLE report_sections; DROP TABLE reports;"
                    " DROP TABLE coverage_supporting_claims; DROP TABLE coverage_assessments; DROP TABLE correction_tasks;"
                    " DROP TABLE claim_dispositions;"
                    " DROP TABLE verification_result_passages; DROP TABLE verification_result_evidence; DROP TABLE verification_results;"
                    " DROP TABLE claim_evidence; DROP TABLE claims;")
    for column in ("verify_status", "verify_stop_reason", "complete_status", "complete_stop_reason"):
        c.execute(f"ALTER TABLE runs DROP COLUMN {column}")
    c.execute("ALTER TABLE model_calls DROP COLUMN structured_output")
    c.execute("PRAGMA user_version = 3")
    c.close()

    c = db.connect(path)
    tables = {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
    assert {"claims", "claim_evidence", "verification_results", "verification_result_evidence"} <= tables
    row = c.execute("SELECT verify_status, verify_stop_reason FROM runs WHERE id = ?", (run["run"],)).fetchone()
    assert tuple(row) == ("not_started", None)
    assert len(db.list_passages(c, run["run"])) == len(PARAS) and len(db.list_associations(c, run["run"])) == 2
    assert c.execute("SELECT count(*) FROM sqlite_master WHERE type = 'trigger' AND name LIKE 'claim%'").fetchone()[0] >= 5
    assert c.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    db.connect(path).close()  # idempotent
