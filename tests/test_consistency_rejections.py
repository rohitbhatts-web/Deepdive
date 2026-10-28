"""Rejected consistency replies: per-finding validation and local storage of rejected replies. OFFLINE: scripted replies only.

Motivated by the one live attempt of 27 Oct 2026, which was rejected as a whole because a single finding's explanation exceeded the former 240-character limit, and
whose reply text could not be recovered afterwards (only a truncated error string was stored). These tests prove validation and bookkeeping; they say nothing about
whether a real model detects real contradictions.
"""

import json

import pytest

from backend import db
from backend.agents import consistency
from backend.budget import Budget
from backend.llm import LLM
from backend.redact import REDACTED
from tests.fakes import FakeMesh
from tests.test_consistency_check import (
    check,
    conflict_finding,
    make_world,
    reply,
    report,
)

MARKER = "ZEBRA-7731-MODEL-TEXT"  # a distinctive string inside model output: it must never reach model_calls.error, a report or the API


@pytest.fixture
def w(conn, settings):
    return make_world(conn, settings)


def rejections(conn, run):
    return db.model_call_rejections(conn, run)


def never_clean(chk, rep):
    assert chk["status"] != "reviewed" and rep["consistency"]["status"] != "reviewed"
    assert "flagged no conflicts" not in rep["markdown"]


def finding(w, a, b, explanation, kind="conflict"):
    return {"claim_ids": [w["ids"][a], w["ids"][b]], "kind": kind, "explanation": explanation, "evidence": []}


# --- one overlong explanation beside a valid conflict ---------------------------------------------------------------------------------------------------

def test_an_overlong_explanation_rejects_only_its_own_finding_and_the_valid_conflict_stands(conn, settings, w):
    long_text = MARKER + " " + "x" * consistency.EXPLANATION_MAX_CHARS
    mesh = FakeMesh(consistency=reply(w, [conflict_finding(w), finding(w, "private", "shared", long_text, kind="condition")]))
    chk, _ = check(conn, settings, w, mesh)
    assert len(mesh.calls) == 1
    assert chk["status"] == "partial" and chk["cause"] == "model" and [f["kind"] for f in chk["findings"]] == ["conflict"]
    [bad] = [d for d in chk["dropped_findings"] if d["item"] == "finding"]
    assert bad["index"] == 1 and bad["claim_ids"] == [w["ids"]["private"], w["ids"]["shared"]]
    assert bad["errors"] == [{"loc": "explanation", "type": "string_too_long", "msg": f"String should have at most {consistency.EXPLANATION_MAX_CHARS} characters"}]
    assert MARKER not in json.dumps(chk)  # the model's text is not repeated in the stored assessment
    [rej] = rejections(conn, w["run"])
    assert rej["stage"] == "items" and MARKER in rej["content"] and rej["truncated"] is False and rej["model_call_id"] == chk["model_call_id"]
    rep = report(conn, settings, w)
    never_clean(chk, rep)
    assert rep["consistency"]["has_conflicts"] is True and MARKER not in json.dumps(rep)


def test_an_explanation_at_the_bound_is_kept_whole_never_trimmed(conn, settings, w):
    exact = "y" * consistency.EXPLANATION_MAX_CHARS
    chk, _ = check(conn, settings, w, FakeMesh(consistency=reply(w, [finding(w, "r1", "r6", exact)])))
    assert chk["status"] == "reviewed" and chk["findings"][0]["explanation"] == exact and rejections(conn, w["run"]) == []
    assert str(consistency.EXPLANATION_ASK_CHARS) in consistency.SYSTEM  # the prompt states the length it asks for


# --- several malformed findings ------------------------------------------------------------------------------------------------------------------------

def test_each_malformed_finding_is_rejected_on_its_own_with_its_reason(conn, settings, w):
    ids = w["ids"]
    findings = [
        conflict_finding(w),                                                                              # 0 valid
        {"claim_ids": [ids["r1"], ids["merge"]], "kind": "contradiction", "explanation": "bad kind", "evidence": []},   # 1 unknown kind
        {"claim_ids": f"{ids['r1']},{ids['merge']}", "kind": "conflict", "explanation": "ids as a string", "evidence": []},  # 2 not a list
        {"claim_ids": [True, ids["merge"]], "kind": "conflict", "explanation": "a boolean is not a claim id", "evidence": []},  # 3 strict ints
        "not an object",                                                                                   # 4 beyond the limit of 4
        finding(w, "private", "shared", "valid but fifth"),                                                # 5 beyond the limit of 4
    ]
    mesh = FakeMesh(consistency=reply(w, findings))
    chk, _ = check(conn, settings, w, mesh)
    assert len(mesh.calls) == 1 and chk["status"] == "partial"
    assert [f["claim_ids"] for f in chk["findings"]] == [sorted([ids["r1"], ids["r6"]])]
    by_index = {d["index"]: d for d in chk["dropped_findings"] if d["item"] == "finding"}
    assert sorted(by_index) == [1, 2, 3, 4, 5]
    assert by_index[1]["errors"][0]["loc"] == "kind" and by_index[1]["errors"][0]["type"] == "literal_error"
    assert by_index[2]["errors"][0]["loc"] == "claim_ids" and by_index[2]["claim_ids"] is None  # a malformed value is not repeated
    assert by_index[3]["errors"][0]["loc"] == "claim_ids.0" and by_index[3]["errors"][0]["type"] == "int_type"
    assert "beyond the limit" in by_index[4]["reason"] and "beyond the limit" in by_index[5]["reason"]
    assert rejections(conn, w["run"])[0]["stage"] == "items"


def test_unknown_claims_and_evidence_not_shown_are_still_rejected_after_schema_validation(conn, settings, w):
    unknown = {"claim_ids": [w["ids"]["r1"], 999_999], "kind": "conflict", "explanation": "names a claim that does not exist", "evidence": []}
    wrong_pair = conflict_finding(w, evidence=[{"claim_id": w["ids"]["r1"], "passage_id": w["pid"]["r6"]}])
    chk, _ = check(conn, settings, w, FakeMesh(consistency=reply(w, [unknown, wrong_pair])))
    assert chk["status"] == "partial" and len(chk["findings"]) == 1 and chk["findings"][0]["evidence"] == []
    reasons = [d["reason"] for d in chk["dropped_findings"]]
    assert any("not shown to the model" in r and "999999" in r for r in reasons) and any(d["item"] == "evidence" for d in chk["dropped_findings"])


# --- missing or malformed envelopes ---------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    f"not json {MARKER}",
    "[1, 2]",
    {"findings": []},
    {"claims_reviewed": [1, 2]},
    {"claims_reviewed": f"all {MARKER}", "findings": []},
    {"claims_reviewed": [1, "2"], "findings": []},
    {"claims_reviewed": [1], "findings": f"{MARKER}"},
    '{"claims_reviewed": [1, 2], "findings": [{"claim_ids": [1, 2], "kind": "conflict", "explanation": "cut off mid',
])
def test_an_envelope_that_cannot_be_parsed_safely_is_failed_kept_locally_and_never_echoed(conn, settings, w, bad):
    mesh = FakeMesh(consistency=bad)
    chk, _ = check(conn, settings, w, mesh)
    assert len(mesh.calls) == 1 and chk["status"] == "failed" and chk["findings"] == []
    [rej] = rejections(conn, w["run"])
    assert rej["stage"] == "envelope" and rej["content"] == (bad if isinstance(bad, str) else json.dumps(bad))
    assert rej["errors"] and all(set(e) == {"loc", "type", "msg"} for e in rej["errors"])
    error = conn.execute("SELECT error FROM model_calls WHERE id = ?", (rej["model_call_id"],)).fetchone()[0]
    assert error.startswith("invalid output: ") and MARKER not in error  # the API-visible error carries no model text
    rep = report(conn, settings, w)
    never_clean(chk, rep)
    assert MARKER not in json.dumps(rep)


# --- bounded storage, truncation, secrets ---------------------------------------------------------------------------------------------------------------

def test_a_rejected_reply_is_stored_bounded_with_a_truncation_flag(conn, settings, w):
    huge = '{"claims_reviewed": "' + "A" * (db.REJECTED_REPLY_MAX_CHARS + 5000) + '"}'
    check(conn, settings, w, FakeMesh(consistency=huge))
    [rej] = rejections(conn, w["run"])
    assert len(rej["content"]) == db.REJECTED_REPLY_MAX_CHARS and rej["truncated"] is True and rej["content_chars"] == len(huge)


def test_configured_secrets_are_redacted_from_stored_replies(conn, settings, w):
    s = settings.model_copy(update={"mesh_api_key": "sk-test-SECRET-4242"})
    mesh = FakeMesh(consistency='{"claims_reviewed": "echo sk-test-SECRET-4242", "findings": []}')
    consistency.ensure_check(conn, LLM(mesh, s), Budget(s), s, w["run"])
    [rej] = rejections(conn, w["run"])
    assert "sk-test-SECRET-4242" not in rej["content"] and REDACTED in rej["content"]


def test_rejected_replies_are_immutable_and_not_in_any_report(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=f"{MARKER} garbage"))
    import sqlite3
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE model_call_rejections SET content = 'x'")
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM model_call_rejections")
    assert MARKER not in json.dumps(report(conn, settings, w))


# --- existing behaviour is kept ---------------------------------------------------------------------------------------------------------------------

def test_reuse_and_carried_findings_still_work_with_per_finding_rejection(conn, settings, w):
    overlong = finding(w, "private", "shared", "z" * (consistency.EXPLANATION_MAX_CHARS + 1))
    mesh = FakeMesh(consistency=[reply(w, [conflict_finding(w), overlong]), RuntimeError("down")])
    first, budget = check(conn, settings, w, mesh)
    assert first["status"] == "partial" and first["cause"] == "model"
    again, _ = check(conn, settings, w, mesh, budget)  # the same job: no second request
    assert again["id"] == first["id"] and len(mesh.calls) == 1
    later, _ = check(conn, settings, w, mesh, Budget(settings))  # a new job: one more attempt, which fails
    assert later["status"] == "failed" and len(mesh.calls) == 2
    assert [f["carried_from_check"] for f in later["findings"]] == [first["id"]]  # the earlier valid conflict for the SAME input is kept
    other = make_world(conn, settings)
    spent = Budget(settings)
    spent.model_requests = settings.max_model_requests
    assert check(conn, settings, other, FakeMesh(consistency=reply(other)), spent)[0]["findings"] == []  # nothing carried to another input


def test_the_prompt_version_is_part_of_the_fingerprint(conn, settings, w, monkeypatch):
    from backend import claims
    el = claims.eligible_claims(conn, w["run"])
    now = consistency.build_input(el, settings).fingerprint
    monkeypatch.setattr(consistency, "PROMPT_VERSION", "consistency-v1")
    assert consistency.build_input(el, settings).fingerprint != now  # an assessment made under the earlier prompt is never reused


def test_other_agents_keep_one_corrective_retry_and_each_rejected_reply_is_stored(conn, settings):
    run = db.create_run(conn, "q?")["id"]
    mesh = FakeMesh(consistency=[f"{MARKER} first", {"claims_reviewed": [], "findings": []}])
    LLM(mesh, settings).call(conn, run, Budget(settings), agent="consistency", model="m", system=consistency.SYSTEM, user="x",
                             schema=consistency.ConsistencyAssessment)  # default options, as every other agent calls it
    assert len(mesh.calls) == 2  # one corrective retry, unchanged
    [rej] = db.model_call_rejections(conn, run)
    assert rej["stage"] == "envelope" and MARKER in rej["content"]
    corrective = mesh.calls[1]["messages"][-1]["content"]
    assert corrective.startswith("That reply was invalid (invalid output: unparseable reply")
