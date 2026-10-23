"""Contract of the verifier's bundled-claim instruction (rubric attribution-v2).

These tests pin the TEXT of the instruction, the output schema, the rubric version/history and how stored verdicts of earlier rubrics are treated.
They are scripted: they do not call a model and cannot show that a model follows the instruction. Whether it does is an evaluation question
(evaluation/, with human labels), not something these assertions establish."""

import typing

import pytest

from backend import claims, db
from backend.agents import verifier
from backend.agents.schemas import VerifierVerdict
from evaluation.schemas import prompt_hashes
from tests.loop_helpers import add
from tests.seed import Q0, seed_collected_run

V1_SENTENCE = "any that the evidence does not establish makes the claim"


def test_the_instruction_gives_explicit_contradiction_precedence_over_partial_support():
    s = verifier.SYSTEM
    assert "An explicit contradiction of any asserted part takes precedence" in s
    assert "contradicted even when other parts are supported" in s
    assert "merely incomplete or overstated make it partially_supported" in s
    assert "only implied, or not mentioned, is never a contradiction" in s
    assert V1_SENTENCE not in s  # the superseded wording must not survive in the live prompt
    assert "no part is explicitly contradicted" in s  # the partially_supported definition agrees with the bundled-claim sentence


def test_the_four_verdict_vocabulary_and_the_example_match_the_schema():
    assert set(typing.get_args(VerifierVerdict.model_fields["verdict"].annotation)) == set(claims.SEMANTIC_VERDICTS)
    assert VerifierVerdict.model_validate(verifier.EXAMPLE).verdict in claims.SEMANTIC_VERDICTS


def test_the_verifier_rubric_is_versioned_and_the_previous_wording_and_hash_are_kept():
    assert claims.CURRENT_RUBRIC == "attribution-v5" and set(claims.RUBRIC_HISTORY) == {"attribution-v1", "attribution-v2", "attribution-v3", "attribution-v4", "attribution-v5"}
    old = claims.RUBRIC_HISTORY["attribution-v1"]
    assert V1_SENTENCE in old["bundled_claim_instruction"] and len(old["verifier_prompt_sha256"]) == 64
    assert prompt_hashes()["verifier"] != old["verifier_prompt_sha256"]  # a changed prompt is visible in result metadata
    assert claims.SUPPORTED_TRUSTED_RUBRICS == ("attribution-v5",)  # v5 changed the verifier INPUT contract: no older verdict is trusted for a new report


def _record(conn, claim_id, passage, *, rubric):
    details = {"model_verdict": "supported", "aspects": {}, "unsupported_details": [], "conflicts": [], "evidence_used": [passage]}
    if rubric:
        details["rubric"] = rubric
    with db.tx(conn):
        claims.record_result(conn, claim_id, "semantic", "supported", "fixture", model="m", details=details, passages=[(passage, "claim_evidence")],
                             evidence_ids=[e["id"] for e in claims.claim_evidence(conn, claim_id) if e["passage_id"] == passage])


@pytest.mark.parametrize("rubric,trusted,eligible", [("attribution-v1", False, False), ("attribution-v2", False, False), ("attribution-v3", False, False), ("attribution-v4", False, False), ("attribution-v5", True, True), (None, False, False),
                                                      ("attribution-v0", False, False)])
def test_earlier_supported_verdicts_keep_their_stamp_and_only_the_current_rubric_is_eligible_for_new_reports(conn, settings, rubric, trusted, eligible):
    r = seed_collected_run(conn)
    cid = add(conn, r, "sq1", Q0, r["p"](0), Q0, settings)
    _record(conn, cid, r["p"](0), rubric=rubric)
    c = claims.get_claim(conn, r["run"], cid)
    assert c["semantic"]["details"].get("rubric") == rubric  # the stored verdict is not rewritten
    assert c["rubric_trusted"] is trusted and c["eligible_for_report"] is eligible and c["needs_rejudge"] is (not trusted)
    assert c["rubric_current"] is (rubric == claims.CURRENT_RUBRIC)  # the literal fact stays visible
