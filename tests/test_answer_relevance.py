"""Answer relevance is judged apart from support, and report sections are filled from direct answers only.

The world below is the OBSERVED compact-continuation report of Q02 (claim texts verbatim from the stored result), built as a fixture. The relevance
judgments are SCRIPTED fixtures: they test routing, persistence and the deterministic rules, not how accurate a model's judgments are."""

import sqlite3

import pytest

from backend import claims, correction, db, temporal
from backend.agents import coverage, verifier
from backend.budget import Budget
from backend.llm import LLM
from backend.report import build_report, get_report
from tests.fakes import FakeMesh
from tests.loop_helpers import add, verdict

QUESTION = "When were HTTP/2 and HTTP/3 published as RFCs, and how do their transport layers differ?"
HEADER = "RFC 9113 HTTP/2 June 2022 Obsoletes: 7540, 8740 Published: June 2022 ISSN: 2070-1721"
H2_TLS = 'The string "h2" identifies the protocol where HTTP/2 uses Transport Layer Security (TLS); the string "h2c" identifies cleartext HTTP/2.'
RENAME = "On 28 October 2018 in a mailing list discussion, Mark Nottingham, Chair of the IETF HTTP and QUIC Working Groups, proposed renaming HTTP-over-QUIC to HTTP/3."
QUIC = "Both HTTP/1.1 and HTTP/2 use TCP as their transport. HTTP/3 uses QUIC, a transport layer network protocol which uses user space congestion control over UDP."
FIRST_RFC = "The first HTTP/2 specification was published in May 2015 as an earlier RFC. This newer document replaces it and was published in June 2022."
CLAIM = {
    "date2": "The HTTP/2 specification was published as RFC 9113 in June 2022.",
    "rename": "The article describes that on 28 October 2018, Mark Nottingham proposed renaming HTTP-over-QUIC to HTTP/3.",
    "tls": 'HTTP/2 uses Transport Layer Security (TLS) when identified by the "h2" string.',
    "quic": "HTTP/3 uses QUIC as its transport layer protocol.",
    "differ": "HTTP/3 differs from HTTP/2 by using QUIC over UDP instead of TCP, which provides native multiplexing and avoids head-of-line blocking.",
}


@pytest.fixture
def world(conn, settings):
    return build_world(conn, settings, ["RFC publication date for HTTP/2"])


def build_world(conn, settings, date2_requirements, fallback=True, date2_question="What is the publication date of the HTTP/2 specification as an RFC?"):
    run = db.create_run(conn, QUESTION)["id"]
    sqs = {
        "date2": db.add_subquestion(conn, run, date2_question, date2_requirements),
        "date3": db.add_subquestion(conn, run, "What is the publication date of the HTTP/3 specification as an RFC?",
                                    ["RFC publication date for HTTP/3", "RFC number for HTTP/3"]),
        "tr2": db.add_subquestion(conn, run, "Which transport layer protocol does HTTP/2 use?", ["name of the transport protocol used by HTTP/2"]),
        "tr3": db.add_subquestion(conn, run, "Which transport layer protocol does HTTP/3 use and how does it differ from HTTP/2's transport?",
                                  ["name of the transport protocol used by HTTP/3"]),
    }
    texts = [HEADER, H2_TLS, RENAME, QUIC, FIRST_RFC]
    snapshot, spans, pos = "\n\n".join(texts), [], 0
    for t in texts:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 2
    with db.tx(conn):
        sid = db.insert_source(conn, run, requested_url="https://example.test/spec", final_url="https://example.test/spec", title="Spec", content_type="text/html",
                               content_hash="h1", snapshot=snapshot, passages=spans)
        for sq in sqs.values():
            db.add_association(conn, run, sq, sid, "selected_from_search", explanation="fixture")
        db.set_run_status(conn, run, "collection_finished")
    p = lambda i: f"S{sid}-P{i}"
    ids = {}
    for key, sq, passage, quote in (("date2", "date2", 0, "RFC 9113 HTTP/2 June 2022"), ("rename", "date3", 2, "proposed renaming HTTP-over-QUIC to HTTP/3"),
                                    ("tls", "tr2", 1, '"h2" identifies the protocol where HTTP/2 uses Transport Layer Security (TLS)'),
                                    ("quic", "tr3", 3, "HTTP/3 uses QUIC, a transport layer network protocol"),
                                    ("differ", "tr3", 3, "Both HTTP/1.1 and HTTP/2 use TCP as their transport. HTTP/3 uses QUIC")):
        ids[key] = add(conn, {"run": run, **{k: v for k, v in sqs.items()}}, key if key in sqs else sq, CLAIM[key], p(passage), quote, settings)
        verdict(conn, ids[key], "supported", passages=[(p(passage), "claim_evidence")])
    with db.tx(conn):
        db.set_verify_status(conn, run, "completed")
    if fallback:
        correction.ensure_fallback_obligations(conn, run)
    return {"run": run, "sq": sqs, "ids": ids, "p": p}


def obligation_key(conn, run, text):
    return next(o["obligation_key"] for o in correction.obligations(conn, run) if text in o["dimension"] or text in o["subject"])


def reply(conn, run, rows):
    """FIXTURE judgments: rows = [(requirement text, status, [(claim_id, role, reason)], gap, kind)]."""
    return {"obligations": [{"obligation_key": obligation_key(conn, run, text), "status": status,
                             "claim_roles": [{"claim_id": c, "role": r, "reason": why} for c, r, why in roles], "gap": gap, "missing_kind": kind}
                            for text, status, roles, gap, kind in rows]}


def assess(conn, settings, run, rows, round_=1):
    mesh = FakeMesh(coverage=reply(conn, run, rows))
    coverage.assess(conn, LLM(mesh, settings), Budget(settings), settings, run, round_)
    return mesh


def report(conn, run):
    info = build_report(conn, run, plan=None, ordering_reason="test", model=None, model_call_id=None, stop_reason=None, corrective_rounds=1)
    return get_report(conn, run, info["report_id"])


def observed_judgments(w):
    i = w["ids"]
    return [("RFC publication date for HTTP/2", "addressed", [(i["date2"], "direct_answer", "states the date of the document")], "", "none"),
            ("RFC publication date for HTTP/3", "addressed", [(i["rename"], "relevant_context", "a 2018 naming milestone, not the RFC date")], "", "none"),
            ("name of the transport protocol used by HTTP/2", "addressed", [(i["tls"], "relevant_context", "TLS is not the transport protocol")], "", "none"),
            ("name of the transport protocol used by HTTP/3", "addressed", [(i["quic"], "direct_answer", "names it"), (i["differ"], "direct_answer", "names it")], "", "none")]


def statuses(conn, run):
    return {r["requirement_text"]: r for r in correction.assessments(conn, run)}


# --- 1. support and relevance are separate; persisted with requirement IDs, claim IDs and reasons ------------------------------------------------

def test_relevance_is_persisted_per_requirement_and_claim_with_reasons_and_in_one_assessment_call(conn, settings, world):
    mesh = assess(conn, settings, world["run"], observed_judgments(world))
    assert mesh.steps().count("coverage") == 1  # no new model stage: the existing assessment call carries the roles
    rows = correction.claim_roles(conn, world["run"])
    by = {(r["requirement_key"], r["claim_id"]): r for r in rows}
    i = world["ids"]
    rename = by[(next(r["key"] for r in correction.requirements(conn, world["run"]) if "HTTP/3" in r["text"] and "date" in r["text"]), i["rename"])]
    assert rename["role"] == "relevant_context" and "naming milestone" in rename["reason"] and rename["origin"] == "model" and rename["obligation_key"].endswith(".O1")
    assert {r["claim_id"] for r in rows} == {i["date2"], i["rename"], i["tls"], i["quic"], i["differ"]}
    for key in ("rename", "tls", "date2"):  # support is untouched by relevance: still supported and report-eligible
        c = claims.get_claim(conn, world["run"], i[key])
        assert c["semantic_verdict"] == "supported" and c["eligible_for_report"] is True


def test_references_and_run_ownership_are_validated(conn, settings, world):
    rows = observed_judgments(world)
    rows[0][2].append((9999, "direct_answer", "a claim that does not exist"))  # unknown id is dropped, with a note
    assess(conn, settings, world["run"], rows)
    a = statuses(conn, world["run"])["RFC publication date for HTTP/2"]
    assert a["notes"]["dropped_claim_ids"] == [9999]
    assert all(r["claim_id"] != 9999 for r in correction.claim_roles(conn, world["run"]))
    # the database refuses a judgment about a claim of another run, and one filed under the wrong requirement
    oa = conn.execute("SELECT id FROM obligation_assessments LIMIT 1").fetchone()[0]
    foreign = conn.execute("INSERT INTO runs (question, status, created_at, updated_at) VALUES ('x', 'created', 'now', 'now')").lastrowid
    foreign_sq = db.add_subquestion(conn, foreign, "foreign question here?", ["x"])
    with db.tx(conn):
        cid = conn.execute("INSERT INTO claims (run_id, subquestion_id, version, text, origin, provenance, created_at) VALUES (?, ?, 1, 't', 'drafted', '{}', 'now')",
                           (foreign, foreign_sq)).lastrowid
    with pytest.raises(sqlite3.IntegrityError, match="claim of its own run"), db.tx(conn):
        correction.save_claim_relevance(conn, oa, "Q1.R1", {cid: ("direct_answer", "x", "model")})
    with pytest.raises(sqlite3.IntegrityError, match="requirement of a relevance judgment"), db.tx(conn):
        correction.save_claim_relevance(conn, oa, "Q9.R9", {world["ids"]["quic"]: ("direct_answer", "x", "model")})
    with pytest.raises(sqlite3.DatabaseError, match="immutable"):
        conn.execute("UPDATE claim_relevance SET role = 'direct_answer'")


def test_a_missing_requested_date_is_a_coverage_gap_not_a_weaker_verdict_on_the_claim(conn, settings, world):
    assert "Whether the claim answers any particular question is decided elsewhere" in verifier.SYSTEM and "<subquestion>" not in verifier.SYSTEM
    assess(conn, settings, world["run"], observed_judgments(world))
    c = claims.get_claim(conn, world["run"], world["ids"]["date2"])
    assert c["semantic_verdict"] == "supported" and c["status"] == "supported"
    a = statuses(conn, world["run"])
    assert a["RFC publication date for HTTP/3"]["status"] == "unresolved" and a["RFC publication date for HTTP/3"]["missing_kind"] != "none"


# --- 2. regression fixture: the observed report ----------------------------------------------------------------------------------------------

def test_a_supported_but_irrelevant_milestone_cannot_satisfy_a_date_requirement(conn, settings, world):
    assess(conn, settings, world["run"], observed_judgments(world))
    a = statuses(conn, world["run"])["RFC publication date for HTTP/3"]
    assert a["status"] == "unresolved" and a["notes"]["downgraded_from"] == "addressed" and a["supporting_claim_ids"] == []  # the assessor said addressed; code refused
    rep = report(conn, world["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == world["sq"]["date3"])
    assert sec["answered"] is False and sec["claims"] == [] and [c["claim_id"] for c in sec["context"]] == [world["ids"]["rename"]]
    assert "No direct answer to this subquestion was found." in rep["markdown"].split("Section 2")[1].split("Section 3")[0]


def test_a_revision_date_alone_cannot_satisfy_original_publication_coverage(conn, settings, world):
    assess(conn, settings, world["run"], observed_judgments(world))
    a = statuses(conn, world["run"])["RFC publication date for HTTP/2"]
    assert a["status"] == "unresolved" and a["gap"] == temporal.REVISION_ONLY_GAP and a["missing_kind"] == "date"
    assert a["notes"]["temporal_demoted"] == [world["ids"]["date2"]]
    (role,) = [r for r in correction.claim_roles(conn, world["run"]) if r["claim_id"] == world["ids"]["date2"]]
    assert role["role"] == "relevant_context" and role["origin"] == "rule" and "supersedes an earlier version" in role["reason"]
    rep = report(conn, world["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == world["sq"]["date2"])
    assert sec["answered"] is False and [c["claim_id"] for c in sec["context"]] == [world["ids"]["date2"]]


def test_a_claim_that_says_it_is_a_revision_may_answer_only_partially(conn, settings, world):
    revised = add(conn, {"run": world["run"], "date2": world["sq"]["date2"]}, "date2",
                  "The HTTP/2 specification, which replaces an earlier one, was published as RFC 9113 in June 2022.", world["p"](0), "RFC 9113 HTTP/2 June 2022", settings)
    verdict(conn, revised, "supported", passages=[(world["p"](0), "claim_evidence")])
    rows = observed_judgments(world)
    rows[0] = ("RFC publication date for HTTP/2", "addressed", [(revised, "direct_answer", "states the date and that it is a revision")], "", "none")
    assess(conn, settings, world["run"], rows)
    a = statuses(conn, world["run"])["RFC publication date for HTTP/2"]
    assert a["status"] == "partially_addressed" and a["gap"] == temporal.REVISION_ONLY_GAP and a["supporting_claim_ids"] == [revised]


def test_without_supersession_wording_in_the_evidence_a_direct_date_judgment_is_not_demoted(conn, settings, world):
    """Control: the temporal rule fires only on supersession wording in the cited text. (Whether a milestone is a date answer is the assessor's call.)"""
    rows = observed_judgments(world)
    rows[1] = ("RFC publication date for HTTP/3", "addressed", [(world["ids"]["rename"], "direct_answer", "FIXTURE judgment: treated as the date")], "", "none")
    assess(conn, settings, world["run"], rows)
    a = statuses(conn, world["run"])["RFC publication date for HTTP/3"]
    assert a["status"] == "addressed" and a["supporting_claim_ids"] == [world["ids"]["rename"]] and "temporal_demoted" not in a["notes"]
    assert temporal.evidence_shows_supersession([FIRST_RFC]) and not temporal.evidence_shows_supersession([RENAME, QUIC, H2_TLS])


def test_context_cannot_silently_become_a_direct_answer(conn, settings, world):
    assess(conn, settings, world["run"], observed_judgments(world))
    a = statuses(conn, world["run"])["name of the transport protocol used by HTTP/2"]
    assert a["status"] == "unresolved" and a["notes"]["downgraded_from"] == "addressed" and a["supporting_claim_ids"] == []
    rep = report(conn, world["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == world["sq"]["tr2"])
    assert sec["claims"] == [] and sec["answered"] is False and [c["role"] for c in sec["context"]] == ["relevant_context"]


def test_direct_answers_fill_sections_missing_answers_stay_visible_and_the_wording_is_exact(conn, settings, world):
    assess(conn, settings, world["run"], observed_judgments(world))
    rep = report(conn, world["run"])
    by = {s["subquestion_id"]: s for s in rep["sections"]}
    assert by[world["sq"]["tr3"]]["answered"] is True and {c["claim_id"] for c in by[world["sq"]["tr3"]]["claims"]} == {world["ids"]["quic"], world["ids"]["differ"]}
    assert [s["answered"] for s in rep["sections"]] == [False, False, False, True]
    md = rep["markdown"]
    assert md.count("No direct answer to this subquestion was found.") == 3  # every unanswered section says so
    assert rep["outcome"] == "claims_with_gaps" and rep["relevance_judged"] is True
    # exact stored wording and citations, never rewritten
    stored = {c["id"]: c for c in claims.list_claims(conn, world["run"])}
    for sec in rep["sections"]:
        for c in sec["claims"] + sec["context"]:
            assert c["text"] == stored[c["claim_id"]]["text"] and c["citations"] and f"- {c['text']} " in md
            assert {x["passage_id"] for x in c["citations"]} == {x["passage_id"] for x in stored[c["claim_id"]]["citations"]}
    assert "Related context (verified, but it does not answer this subquestion):" in md
    assert CLAIM["rename"] in md and CLAIM["rename"] not in md.split("No direct answer")[0]
    # every claim bullet in the report is a stored claim text, verbatim; nothing else is bulleted above the requirement list
    body = md.split("## Evidence requirements not fully addressed")[0]
    bullets = [line for line in body.splitlines() if line.startswith("- ") and "(claim " in line]
    texts = {c["text"] for c in stored.values()}
    assert len(bullets) == 5 and all(any(b.startswith(f"- {t} ") for t in texts) for b in bullets)


def test_a_claim_that_answers_no_requirement_is_omitted_with_a_reason(conn, settings, world):
    rows = observed_judgments(world)
    rows[1] = ("RFC publication date for HTTP/3", "unresolved", [], "no RFC date", "date")  # the assessor does not even list the renaming claim
    assess(conn, settings, world["run"], rows)
    rep = report(conn, world["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == world["sq"]["date3"])
    assert sec["claims"] == [] and sec["context"] == [] and sec["answered"] is False
    om = {o["claim_id"]: o["reason"] for o in rep["omissions"]}
    assert "no answer-relevance judgment" in om[world["ids"]["rename"]] and CLAIM["rename"] not in rep["markdown"]


def test_all_context_and_no_direct_answer_is_insufficient_evidence(conn, settings, world):
    i = world["ids"]
    rows = [("RFC publication date for HTTP/2", "addressed", [(i["date2"], "relevant_context", "context")], "", "none"),
            ("RFC publication date for HTTP/3", "addressed", [(i["rename"], "relevant_context", "context")], "", "none"),
            ("name of the transport protocol used by HTTP/2", "addressed", [(i["tls"], "relevant_context", "context")], "", "none"),
            ("name of the transport protocol used by HTTP/3", "addressed", [(i["quic"], "relevant_context", "context")], "", "none")]
    assess(conn, settings, world["run"], rows)
    rep = report(conn, world["run"])
    assert rep["outcome"] == "insufficient_evidence" and all(s["answered"] is False for s in rep["sections"])
    assert "No claim directly answers a requirement of the research question." in rep["markdown"]


def test_a_report_made_without_any_relevance_judgment_keeps_its_claims_and_says_it_was_not_judged(conn, settings, world):
    rep = report(conn, world["run"])  # coverage never ran
    assert rep["relevance_judged"] is False and all(s["answered"] is None and s["context"] == [] for s in rep["sections"])
    assert sum(len(s["claims"]) for s in rep["sections"]) == 5


def test_the_temporal_checks_are_generic_and_name_nothing():
    import inspect
    src = inspect.getsource(temporal)
    for fact in ("RFC", "HTTP", "TCP", "QUIC", "9113", "2022"):
        assert fact not in src.replace("RFC publication", "")
    assert temporal.is_date_seeking("publication date") and temporal.is_date_seeking("when it was released") and not temporal.is_date_seeking("transport protocol name")
    assert temporal.evidence_shows_supersession(["This standard supersedes the 2001 edition."]) and not temporal.evidence_shows_supersession(["Superseded by a newer edition."])


def test_a_claim_that_answers_one_requirement_does_not_pose_as_the_answer_to_an_open_one_in_the_same_section(conn, settings):
    """The observed claim names the document number but, being a revision, only dates the revision: it directly answers the number; the date stays visibly open."""
    w = build_world(conn, settings, ["RFC publication date for HTTP/2", "RFC number for HTTP/2"])
    rows = observed_judgments(w)
    rows.append(("RFC number for HTTP/2", "addressed", [(w["ids"]["date2"], "direct_answer", "FIXTURE: names the document number")], "", "none"))
    assess(conn, settings, w["run"], rows)
    rep = report(conn, w["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == w["sq"]["date2"])
    assert sec["answered"] is True and [c["claim_id"] for c in sec["claims"]] == [w["ids"]["date2"]] and sec["context"] == []
    assert sec["claims"][0]["requirement_texts"] == ["RFC number for HTTP/2"]
    (open_req,) = sec["open_requirements"]
    assert open_req["requirement_text"] == "RFC publication date for HTTP/2" and open_req["status"] == "unresolved" and open_req["gap"] == temporal.REVISION_ONLY_GAP
    md = rep["markdown"].split("Section 2")[0]
    assert "directly answers: RFC number for HTTP/2" in md and "Not directly answered in this section:" in md and temporal.REVISION_ONLY_GAP in md


def test_the_drafter_is_told_about_superseding_documents_without_naming_any(conn, settings, world):
    from backend.agents import drafter
    assert 'revision_notice="yes"' in drafter.SYSTEM and drafter.NOTICE.strip() == 'revision_notice="yes"'
    assert "supersedes an earlier" not in drafter.SYSTEM or "9113" not in drafter.SYSTEM
    for fact in ("RFC", "HTTP", "9113", "7540"):
        assert fact not in drafter.SYSTEM + drafter.NOTICE


# --- the boundary: which questions the original-versus-revision restriction applies to ------------------------------------------------------------

@pytest.mark.parametrize("texts,scope", [
    (("Original publication date of the specification", "publication date", "When was it first published?"), "original"),
    (("Publication date of the latest revision", "publication date", "When was the current version published?"), "revision"),
    (("Publication date of the specification", "publication date", "What is the publication date of the specification as an RFC?"), "ambiguous"),
    (("first and latest publication date", "publication date", ""), "ambiguous"),  # both asked: keep the distinction
])
def test_the_scope_of_a_date_requirement_is_read_from_its_own_words(texts, scope):
    assert temporal.date_scope(*texts) == scope


@pytest.mark.parametrize("text", ["the new engine replaces the old one in this release", "read an earlier revision of the control record",
                                  "Obsoleted by: a newer document", "superseded by a later edition"])
def test_ordinary_uses_of_the_words_are_not_supersession(text):
    assert not temporal.evidence_shows_supersession([text])


def test_a_question_about_the_revised_document_accepts_its_publication_date_as_a_direct_answer(conn, settings):
    w = build_world(conn, settings, ["Publication date of the latest revision of the HTTP/2 specification"])
    rows = [("Publication date of the latest revision of the HTTP/2 specification", "addressed",
             [(w["ids"]["date2"], "direct_answer", "FIXTURE: states when this revision was published")], "", "none")]
    assess(conn, settings, w["run"], rows)
    a = statuses(conn, w["run"])["Publication date of the latest revision of the HTTP/2 specification"]
    assert a["status"] == "addressed" and a["supporting_claim_ids"] == [w["ids"]["date2"]] and a["notes"]["temporal_scope"] == "revision"
    assert "temporal_demoted" not in a["notes"] and "temporal_revision_only" not in a["notes"]
    rep = report(conn, w["run"])
    sec = next(s for s in rep["sections"] if s["subquestion_id"] == w["sq"]["date2"])
    assert sec["answered"] is True and [c["claim_id"] for c in sec["claims"]] == [w["ids"]["date2"]] and not sec["open_requirements"]


def test_a_question_about_the_original_date_never_accepts_a_revision_date_even_one_that_says_so(conn, settings):
    w = build_world(conn, settings, ["Original publication date of the HTTP/2 specification"])
    revised = add(conn, {"run": w["run"], "date2": w["sq"]["date2"]}, "date2",
                  "The HTTP/2 specification, which replaces an earlier one, was published as RFC 9113 in June 2022.", w["p"](0), "RFC 9113 HTTP/2 June 2022", settings)
    verdict(conn, revised, "supported", passages=[(w["p"](0), "claim_evidence")])
    rows = [("Original publication date of the HTTP/2 specification", "addressed",
             [(revised, "direct_answer", "FIXTURE: dates the document, says it is a revision"), (w["ids"]["date2"], "direct_answer", "FIXTURE: dates the document")], "", "none")]
    assess(conn, settings, w["run"], rows)
    a = statuses(conn, w["run"])["Original publication date of the HTTP/2 specification"]
    assert a["status"] == "unresolved" and a["supporting_claim_ids"] == [] and a["notes"]["temporal_scope"] == "original"
    assert a["gap"] == temporal.REVISION_ONLY_GAP and sorted(a["notes"]["temporal_demoted"]) == sorted([revised, w["ids"]["date2"]])
    roles = {r["claim_id"]: r for r in correction.claim_roles(conn, w["run"])}
    assert roles[revised]["role"] == roles[w["ids"]["date2"]]["role"] == "relevant_context" and "original publication date was asked for" in roles[revised]["reason"]


def test_an_ambiguous_question_keeps_the_distinction(conn, settings, world):
    assess(conn, settings, world["run"], observed_judgments(world))
    a = statuses(conn, world["run"])["RFC publication date for HTTP/2"]
    assert a["notes"]["temporal_scope"] == "ambiguous" and a["status"] == "unresolved" and a["gap"] == temporal.REVISION_ONLY_GAP


# --- the subject-mention backstop (recorded false rejection of a descriptive subject) ------------------------------------------------------------

RECORDED_SUBJECT = "the current revision of the HTTP/2 specification"  # the derived subject recorded in the relevance check, case A2
RECORDED_CLAIM = "The HTTP/2 specification was published as RFC 9113 in June 2022."


@pytest.mark.parametrize("subject,text,expected", [
    (RECORDED_SUBJECT, RECORDED_CLAIM, True),  # the recorded false rejection: qualifiers need not be repeated
    ("the original edition of the Atlas handbook", "The Atlas handbook was published in 2019.", True),
    ("the current revision of the HTTP/3 specification", RECORDED_CLAIM, False),  # the wrong numbered subject
    ("HTTP/3", "HTTP/2 uses TCP as its transport.", False),  # a bare number used to be ignored, so this was accepted
    ("HTTP/3", "HTTP/3 uses QUIC.", True),
    ("SQLite 3.45", "SQLite version 3.45 was released in January 2024.", True),
    ("SQLite 3.45", "SQLite 3.44 was released.", False),
    ("the current edition of the Borealis handbook", "The Atlas handbook was published in 2019.", False),  # a different named subject
    ("memory cache", "The cache is remote.", False),  # subject validation is not removed: an identifying word must still appear
    ("the current revision", "Anything at all.", False),  # nothing identifying left: not a basis to accept
])
def test_the_subject_backstop_accepts_descriptive_wording_and_still_rejects_the_wrong_subject(subject, text, expected):
    assert correction.mentions(subject, text) is expected


def _derived_world(conn, settings, subject):
    w = build_world(conn, settings, ["Publication date of the current revision of the HTTP/2 specification"], fallback=False)
    mesh = FakeMesh(obligations={"requirements": [{"requirement_key": "Q1.R1", "obligations": [{"subject": subject, "dimension": "publication date"}]}]})
    from backend.agents.obligations import ensure_obligations
    ensure_obligations(conn, LLM(mesh, settings), Budget(settings), settings, w["run"])
    assert next(o for o in correction.obligations(conn, w["run"]) if o["requirement_key"] == "Q1.R1")["origin"] == "derived"
    return w


def _assess_q1(conn, settings, w):
    reply = {"obligations": [{"obligation_key": "Q1.R1.O1", "status": "addressed", "gap": "", "missing_kind": "none",
                              "claim_roles": [{"claim_id": w["ids"]["date2"], "role": "direct_answer", "reason": "FIXTURE: states the date"}]}]}
    coverage.assess(conn, LLM(FakeMesh(coverage=reply), settings), Budget(settings), settings, w["run"], 1)
    return statuses(conn, w["run"])["Publication date of the current revision of the HTTP/2 specification"]


def test_a_legitimate_answer_with_a_descriptive_derived_subject_is_no_longer_rejected(conn, settings):
    w = _derived_world(conn, settings, RECORDED_SUBJECT)
    a = _assess_q1(conn, settings, w)
    assert a["status"] == "addressed" and a["supporting_claim_ids"] == [w["ids"]["date2"]] and "subject_not_mentioned" not in a["notes"]


def test_an_answer_about_the_wrong_subject_is_still_rejected_by_the_backstop(conn, settings):
    w = _derived_world(conn, settings, "the current revision of the HTTP/3 specification")  # the claim is about HTTP/2
    a = _assess_q1(conn, settings, w)
    assert a["status"] == "unresolved" and a["notes"]["subject_not_mentioned"] == "the current revision of the HTTP/3 specification"
    assert a["supporting_claim_ids"] == []
    assert a["gap"] == coverage.subject_gap("the current revision of the HTTP/3 specification", [w["ids"]["date2"]])  # names the claim, the subject and what became of it
