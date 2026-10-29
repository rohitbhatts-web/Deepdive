"""Regression for the stored Python lists/tuples run (run 3 of the 28 Oct inspection database): four claims covered the mutability ask, one supported claim
covered the usage ask, and the report still said the usage ask was unanswered.

What the stored records showed: the coverage model judged the two usage requirements ADDRESSED by the one usage claim; a lexical backstop then downgraded them
to unresolved because the claim said "lists" and "tuples" but not "Python", although every subquestion of the plan already says "Python" (it is the question's
topic, not what tells one subject from another). The old run-wide-qualifier rule did not apply because it required EVERY model-derived subject to contain the
word, and two of the nine ("list elements", "tuple elements") and one more ("lists and tuples") did not. The gap then read "no cited claim is about Python
lists" although a cited claim about lists was shown in that very section as context.

The fixture reproduces the RECORDED STRUCTURE (the question, the four planner subquestions and their requirements, the nine derived obligation subjects, which
claim covered which subquestion, which claim names which words) with SYNTHETIC passages and claim wording written for this test. The coverage judgments are
scripted: they say nothing about how a real model judges. Nothing here fabricates a claim or hardcodes an answer about lists or tuples; the usage claim is a
drafted-claim fixture that exists in the run, and the tests check only that it is not silently displaced.
"""

import pytest

from backend import correction, db
from backend.agents import coverage
from backend.agents.obligations import ensure_obligations
from backend.budget import Budget
from backend.llm import LLM
from tests.fakes import FakeMesh
from tests.loop_helpers import add, verdict
from tests.test_answer_relevance import report, statuses

QUESTION = "How do Python lists and tuples differ in mutability, and when should a programmer use each?"
PLAN = [  # (subquestion, evidence requirements) exactly as the planner recorded them: three subquestions serve the mutability ask, one the usage ask
    ("What is the mutability status of Python lists and tuples?", ["lists are mutable", "tuples are immutable"]),
    ("Can elements in a Python list be modified after creation?", ["list elements can be changed", "examples of appending, removing, or changing list items"]),
    ("Can elements in a Python tuple be modified after creation?", ["tuple elements cannot be changed", "examples of immutability such as inability to append or modify"]),
    ("What are the best practices for choosing between Python lists and tuples?",
     ["use lists for homogeneous, variable-length data", "use tuples for heterogeneous, fixed-length data", "performance or semantic reasons for choosing one over the other"]),
]
SUBJECTS = {  # requirement text -> (subject, dimension) as the obligation derivation recorded them (the word "Python" is in six of the nine subjects)
    "lists are mutable": ("Python lists", "mutability status"),
    "tuples are immutable": ("Python tuples", "mutability status"),
    "list elements can be changed": ("list elements", "ability to be modified after creation"),
    "examples of appending, removing, or changing list items": ("Python lists", "examples of appending, removing, or changing items"),
    "tuple elements cannot be changed": ("tuple elements", "ability to be modified after creation"),
    "examples of immutability such as inability to append or modify": ("Python tuples", "examples of immutability such as inability to append or modify"),
    "use lists for homogeneous, variable-length data": ("Python lists", "use case for homogeneous, variable-length data"),
    "use tuples for heterogeneous, fixed-length data": ("Python tuples", "use case for heterogeneous, fixed-length data"),
    "performance or semantic reasons for choosing one over the other": ("lists and tuples", "performance or semantic reasons for choosing one over the other"),
}
PASSAGES = [  # SYNTHETIC page text
    "A list may be altered after it has been made. A tuple stays fixed for as long as it exists.",
    "Appending to a list or removing one of its items changes that same list object.",
    "Assigning to an item of a tuple raises an error, so a different tuple must be built instead.",
    "Choose a list for dynamic, homogeneous data and a tuple for fixed, heterogeneous data.",
]
CLAIMS = [  # (subquestion index, text, passage index, quote): the shapes the run stored; only the last names no "Python"
    (0, "Python lists are mutable, so their contents can change after creation.", 0, "A list may be altered after it has been made."),
    (0, "Python tuples are immutable, so they cannot be modified after creation.", 0, "A tuple stays fixed for as long as it exists."),
    (1, "Elements in a Python list can be changed after creation, for example by appending or removing items.", 1,
     "Appending to a list or removing one of its items changes that same list object."),
    (2, "Elements in a Python tuple cannot be changed after creation because tuples are immutable.", 2, "Assigning to an item of a tuple raises an error"),
    (3, "The guide recommends lists for dynamic, homogeneous data and tuples for fixed, heterogeneous data.", 3,
     "Choose a list for dynamic, homogeneous data and a tuple for fixed, heterogeneous data."),
]
USAGE = ("use lists for homogeneous, variable-length data", "use tuples for heterogeneous, fixed-length data")
TUPLE_EXAMPLES = "examples of immutability such as inability to append or modify"
PERFORMANCE = "performance or semantic reasons for choosing one over the other"


def build(conn, settings, *, usage_text=None, plan=PLAN, question=QUESTION):
    run = db.create_run(conn, question)["id"]
    sqs = [db.add_subquestion(conn, run, q, reqs) for q, reqs in plan]
    snapshot, spans, pos = "\n\n".join(PASSAGES), [], 0
    for t in PASSAGES:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 2
    with db.tx(conn):
        sid = db.insert_source(conn, run, requested_url="https://example.test/lists", final_url="https://example.test/lists", title="FIXTURE page", content_type="text/html",
                               content_hash="h1", snapshot=snapshot, passages=spans)
        for sq in sqs:
            db.add_association(conn, run, sq, sid, "selected_from_search", explanation="fixture")
        db.set_run_status(conn, run, "collection_finished")
    r = {"run": run, **{f"sq{i}": sq for i, sq in enumerate(sqs)}}
    ids = []
    for i, (sq_index, text, p_index, quote) in enumerate(CLAIMS):
        text = usage_text if (usage_text and sq_index == 3) else text
        cid = add(conn, r, f"sq{sq_index}", text, f"S{sid}-P{p_index}", quote, settings)
        verdict(conn, cid, "supported", passages=[(f"S{sid}-P{p_index}", "claim_evidence")])
        ids.append(cid)
    with db.tx(conn):
        db.set_verify_status(conn, run, "completed")
    reqs = correction.requirements(conn, run)
    mesh = FakeMesh(obligations={"requirements": [{"requirement_key": q["key"], "obligations": [
        {"subject": SUBJECTS[q["text"]][0], "dimension": SUBJECTS[q["text"]][1]}]} for q in reqs]})
    ensure_obligations(conn, LLM(mesh, settings), Budget(settings), settings, run)
    return {"run": run, "ids": ids, "sq": sqs, "reqs": {q["text"]: q["key"] for q in reqs}}


def judge(conn, settings, w):
    """The coverage judgments the stored run recorded: every obligation addressed by its own claim except one partial and the performance requirement."""
    c = w["ids"]
    by_req = {"lists are mutable": [c[0]], "tuples are immutable": [c[1]], "list elements can be changed": [c[2]],
              "examples of appending, removing, or changing list items": [c[2]], "tuple elements cannot be changed": [c[3]],
              USAGE[0]: [c[4]], USAGE[1]: [c[4]]}
    rows = []
    for o in correction.obligations(conn, w["run"]):
        text = next(t for t, k in w["reqs"].items() if k == o["requirement_key"])
        if text == TUPLE_EXAMPLES:  # recorded: judged only partly addressed by the tuple claim
            rows.append({"obligation_key": o["obligation_key"], "status": "partially_addressed", "gap": "FIXTURE: no examples of failed operations", "missing_kind": "detail",
                         "claim_roles": [{"claim_id": c[3], "role": "direct_answer", "reason": "FIXTURE: states the immutability, gives no example"}]})
        elif text in by_req:
            rows.append({"obligation_key": o["obligation_key"], "status": "addressed", "gap": "", "missing_kind": "none",
                         "claim_roles": [{"claim_id": cid, "role": "direct_answer", "reason": "FIXTURE: states it"} for cid in by_req[text]]})
        else:
            rows.append({"obligation_key": o["obligation_key"], "status": "unresolved", "gap": "FIXTURE: no claim covers this", "missing_kind": "other", "claim_roles": []})
    coverage.assess(conn, LLM(FakeMesh(coverage={"obligations": rows}), settings), Budget(settings), settings, w["run"], 0)
    return statuses(conn, w["run"])


# --- the qualifier rule ---------------------------------------------------------------------------------------------------------------------------

def _plan_texts():
    return [QUESTION, *(q for q, _ in PLAN)]


def test_a_word_every_subquestion_and_the_question_repeat_is_a_plan_wide_qualifier_even_when_a_subject_omits_it():
    subjects = [s for s, _ in SUBJECTS.values()]
    assert correction.shared_subject_stems(subjects) == set()  # the old rule: three of the nine subjects lack "Python"
    assert correction.shared_subject_stems(subjects, _plan_texts()) == {correction._stem("python")}  # "lists" and "tuples" are not in every subquestion: still required


def test_the_plan_wide_rule_needs_the_word_in_the_question_and_in_every_subquestion():
    subjects = [s for s, _ in SUBJECTS.values()]
    one_missing = [QUESTION, *(q.replace("Python ", "") if "tuple modified" in q or "Python tuple" in q else q for q, _ in PLAN)]
    assert "Python" not in one_missing[3]  # the subquestion about tuple elements no longer says it
    assert correction.shared_subject_stems(subjects, one_missing) == set()
    assert correction.shared_subject_stems(subjects, [QUESTION.replace("Python ", ""), *(q for q, _ in PLAN)]) == set()
    assert correction.shared_subject_stems(subjects, [QUESTION, PLAN[0][0]]) == set()  # one subquestion proves nothing about a plan


def test_the_plan_wide_rule_keeps_every_guard_of_the_universal_rule():
    assert correction.shared_subject_stems(["Python lists", "python lists"], _plan_texts()) == set()  # one distinct subject
    assert correction.shared_subject_stems(["Python", "Python lists", "Python tuples"], _plan_texts()) == set()  # a subject that is only the qualifier is identified by it
    only_in_plan = [s for s, _ in SUBJECTS.values() if "Python" not in s]  # a word that is in no subject cannot be a subject qualifier
    assert correction.shared_subject_stems(only_in_plan, _plan_texts()) == set()


def test_a_claim_naming_the_structure_but_not_the_plan_wide_qualifier_is_accepted_and_a_claim_naming_neither_is_not():
    subjects = [s for s, _ in SUBJECTS.values()]
    ignore = correction.shared_subject_stems(subjects, _plan_texts())
    usage = "The guide recommends lists for dynamic, homogeneous data and tuples for fixed, heterogeneous data."
    assert not correction.mentions("Python lists", usage)
    assert correction.mentions("Python lists", usage, ignore_stems=ignore)
    assert correction.mentions("Python tuples", usage, ignore_stems=ignore)
    other = "The guide recommends dynamic, homogeneous data containers and fixed, heterogeneous data records."  # carries the wording, names neither structure
    assert not correction.mentions("Python lists", other, ignore_stems=ignore)  # the structure itself is still required
    assert not correction.mentions("Python tuples", other, ignore_stems=ignore)


# --- the recorded structure through coverage and the report -----------------------------------------------------------------------------------------

def test_duplicated_coverage_of_one_ask_does_not_displace_a_distinct_ask_that_a_supported_claim_answers(conn, settings):
    w = build(conn, settings)
    s = judge(conn, settings, w)
    for text in USAGE:
        a = s[text]
        assert a["status"] == "addressed" and a["supporting_claim_ids"] == [w["ids"][4]], text  # the model's own judgment stands: nothing downgraded it
        assert "subject_not_mentioned" not in a["notes"] and "downgraded_from" not in a["notes"]
    assert s[PERFORMANCE]["status"] == "unresolved"  # a requirement nothing covers stays a gap
    assert s[PERFORMANCE]["gap"] == "FIXTURE: no claim covers this"  # in the assessor's own words
    rep = report(conn, w["run"])
    usage_section = next(sec for sec in rep["sections"] if sec["subquestion_id"] == w["sq"][3])
    assert [c["claim_id"] for c in usage_section["claims"]] == [w["ids"][4]] and usage_section["context"] == []  # a direct answer, not related context
    open_keys = {r["requirement_key"] for r in rep["unresolved_requirements"]}
    assert w["reqs"][PERFORMANCE] in open_keys and not ({w["reqs"][USAGE[0]], w["reqs"][USAGE[1]]} & open_keys)  # only what is really missing is listed as open


def test_the_four_mutability_claims_and_the_one_usage_claim_are_all_credited_to_their_own_requirements(conn, settings):
    w = build(conn, settings)
    s = judge(conn, settings, w)
    expected = {t: "addressed" for t in w["reqs"]} | {PERFORMANCE: "unresolved", TUPLE_EXAMPLES: "partially_addressed"}  # as the stored run recorded them
    assert {t: a["status"] for t, a in s.items()} == expected


def test_a_usage_claim_that_names_neither_structure_is_still_not_credited_and_the_gap_says_why(conn, settings):
    other = "The guide recommends dynamic, homogeneous data containers and fixed, heterogeneous data records."  # carries the dimension's words, names neither structure
    w = build(conn, settings, usage_text=other)
    s = judge(conn, settings, w)
    claim = w["ids"][4]
    for text, subject in ((USAGE[0], "Python lists"), (USAGE[1], "Python tuples")):
        a = s[text]
        assert a["status"] == "unresolved" and a["supporting_claim_ids"] == [] and a["notes"]["subject_not_mentioned"] == subject
        assert f"claim {claim}" in a["gap"] and subject in a["gap"] and "context" in a["gap"]  # which claim, which subject, and what became of it
        assert "no cited claim is about" not in a["gap"]  # a claim IS cited for this requirement: it is shown as context, and the gap must not deny it
    rep = report(conn, w["run"])
    usage_section = next(sec for sec in rep["sections"] if sec["subquestion_id"] == w["sq"][3])
    assert usage_section["claims"] == [] and [c["claim_id"] for c in usage_section["context"]] == [claim]


def test_the_plan_wide_qualifier_does_not_apply_when_one_subquestion_does_not_say_it(conn, settings):
    plan = [(q.replace("Python tuple", "tuple") if "tuple be modified" in q else q, reqs) for q, reqs in PLAN]
    w = build(conn, settings, plan=plan)
    s = judge(conn, settings, w)
    for text in USAGE:  # the same usage claim now fails the stricter old rule: the exception is bounded, not blanket
        assert s[text]["status"] == "unresolved" and s[text]["notes"]["subject_not_mentioned"].startswith("Python")


@pytest.mark.parametrize("claims_named,phrase", [([7], "claim 7 never names"), ([7, 9], "claims 7 and 9 never name")])
def test_the_gap_wording_names_one_claim_or_several(claims_named, phrase):
    assert coverage.subject_gap("Python lists", claims_named).startswith("not credited: " + phrase)
