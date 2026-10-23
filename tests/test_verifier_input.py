"""The semantic verifier's request contains the claim and its evidence only; relevance and original-versus-revision stay with the coverage/relevance stage.

These tests establish INPUT SEPARATION. They do not show that a model judges any claim correctly, and they force no verdict for any case."""

import pytest

from backend import claims, correction, db, temporal
from backend.agents import verifier
from backend.agents.verifier import verify_claim
from backend.budget import Budget
from backend.llm import LLM
from evaluation.schemas import prompt_hashes
from tests.fakes import FakeMesh, verdict
from tests.loop_helpers import add
from tests.seed import seed_collected_run
from tests.test_answer_relevance import QUESTION, assess, build_world, statuses


def two_subquestions_one_claim(conn, settings, text, quote):
    """The same claim, citing the same passage, filed under two different subquestions of one run (so the passage ids are identical)."""
    r = seed_collected_run(conn)
    sq_other = db.add_subquestion(conn, r["run"], "A completely different subquestion that asks for something else?", ["whatever"])
    a = add(conn, r, "sq1", text, r["p"](0), quote, settings)
    b = add(conn, {**r, "other": sq_other}, "other", text, r["p"](0), quote, settings)
    return r, a, b


def messages_for(conn, settings, claim_ids, run_id):
    mesh = FakeMesh(verify=[verdict("supported", used=["S1-P0"])])
    llm, budget = LLM(mesh, settings), Budget(settings)
    for cid in claim_ids:
        verify_claim(conn, llm, budget, settings, cid)
    return [c["messages"] for c in mesh.calls]


def test_identical_claims_and_evidence_under_different_subquestions_produce_identical_verifier_messages(conn, settings):
    text = "SQLite is a C library embedded directly in the application process."
    r, a, b = two_subquestions_one_claim(conn, settings, text, "SQLite is a C library embedded directly in the application process.")
    first, second = messages_for(conn, settings, [a, b], r["run"])
    assert first == second  # system AND user message, character for character
    joined = first[0]["content"] + first[1]["content"]
    assert "How is SQLite deployed and released?" not in joined and "completely different subquestion" not in joined and "<subquestion" not in joined
    assert first[1]["content"].startswith(f"<claim>{text}</claim>")  # the user message opens with the claim: no research question or requested answer precedes it


def test_a_claim_that_asserts_first_publication_keeps_that_assertion_in_the_verifier_input(conn, settings):
    r = seed_collected_run(conn)
    text = "Version 3.45 was first published in January 2024."
    cid = add(conn, r, "sq1", text, r["p"](1), "Version 3.45 was released in January 2024.", settings)
    (msgs,) = messages_for(conn, settings, [cid], r["run"])
    assert f"<claim>{text}</claim>" in msgs[1]["content"] and "first published" in msgs[1]["content"]
    assert "presents a date as when something was first published" in msgs[0]["content"]  # the verifier is still told how to treat such an assertion


def test_the_whole_user_message_is_a_fixed_template_over_the_claim_and_the_evidence_block():
    assert verifier.USER_TEMPLATE == "<claim>{claim}</claim>\n{evidence}\nJudge the claim against this evidence only."
    assert verifier.user_message("A claim {with} braces", "<evidence/>") == "<claim>A claim {with} braces</claim>\n<evidence/>\nJudge the claim against this evidence only."
    assert "<subquestion>" not in verifier.SYSTEM and "{question}" not in verifier.USER_TEMPLATE


def test_the_changed_input_contract_changes_the_recorded_prompt_hash_and_the_rubric_version(monkeypatch):
    before = prompt_hashes()["verifier"]
    assert before != claims.RUBRIC_HISTORY["attribution-v4"]["verifier_prompt_sha256"]  # v5 is not v4
    monkeypatch.setattr(verifier, "USER_TEMPLATE", verifier.USER_TEMPLATE + " Also consider the question.")
    assert prompt_hashes()["verifier"] != before  # the input template is part of the hash, not only the system prompt
    assert claims.CURRENT_RUBRIC == "attribution-v5" and claims.SUPPORTED_TRUSTED_RUBRICS == ("attribution-v5",)


def test_no_older_supported_verdict_is_trusted_for_a_new_report_but_it_stays_as_history(conn, settings):
    r = seed_collected_run(conn)
    cid = add(conn, r, "sq1", "SQLite is a C library embedded directly in the application process.", r["p"](0), "SQLite is a C library embedded directly in the application process.", settings)
    with db.tx(conn):
        claims.record_result(conn, cid, "semantic", "supported", "an older verdict", model="m", passages=[(r["p"](0), "claim_evidence")],
                             details={"rubric": "attribution-v4", "evidence_used": [r["p"](0)], "aspects": {}, "conflicts": [], "unsupported_details": []},
                             evidence_ids=[e["id"] for e in claims.claim_evidence(conn, cid)])
    c = claims.get_claim(conn, r["run"], cid)
    assert c["semantic_verdict"] == "supported" and c["semantic"]["details"]["rubric"] == "attribution-v4"  # kept as written
    assert c["rubric_trusted"] is False and c["eligible_for_report"] is False and c["needs_rejudge"] is True  # visible, not silently re-judged or trusted


# --- coverage still receives the question and tells the original apart from a revision ----------------------------------------------------------------

def test_coverage_receives_the_question_and_every_subquestion(conn, settings):
    w = build_world(conn, settings, ["Publication date of the specification"], date2_question="When was the HTTP/2 specification first published?")
    rows = [("Publication date of the specification", "addressed", [(w["ids"]["date2"], "direct_answer", "FIXTURE")], "", "none")]
    mesh = assess(conn, settings, w["run"], rows)
    user = mesh.calls[-1]["messages"][1]["content"]
    assert f"<question>{QUESTION}</question>" in user and 'subquestion="When was the HTTP/2 specification first published?"' in user
    assert "The <question> and each obligation's subquestion say what is being asked" in mesh.calls[-1]["messages"][0]["content"]


def test_the_original_versus_revision_distinction_comes_from_the_question_not_from_the_verifier(conn, settings):
    """Neutral requirement wording; the subquestion asks for the FIRST publication. The claim dates a revision. Coverage, not the verifier, keeps it out."""
    w = build_world(conn, settings, ["Publication date of the specification"], date2_question="When was the HTTP/2 specification first published?")
    rows = [("Publication date of the specification", "addressed", [(w["ids"]["date2"], "direct_answer", "FIXTURE: states the date of the document")], "", "none")]
    assess(conn, settings, w["run"], rows)
    a = statuses(conn, w["run"])["Publication date of the specification"]
    assert a["notes"]["temporal_scope"] == "original" and a["status"] == "unresolved" and a["gap"] == temporal.REVISION_ONLY_GAP
    assert claims.get_claim(conn, w["run"], w["ids"]["date2"])["semantic_verdict"] == "supported"  # support was never in question


# --- the subject backstop (the A2 fix) ----------------------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("subject,text,expected", [
    ("the current revision of the HTTP/2 specification", "The HTTP/2 specification was published as RFC 9113 in June 2022.", True),
    ("the current revision of the HTTP/3 specification", "The HTTP/2 specification was published as RFC 9113 in June 2022.", False),
])
def test_the_subject_backstop_accepts_the_intended_subject_and_rejects_a_different_numbered_one(subject, text, expected):
    assert correction.mentions(subject, text) is expected

