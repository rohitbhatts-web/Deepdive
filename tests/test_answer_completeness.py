"""Answer-completeness repair, regression-tested with FIXTURES only (no network, no model: replies are scripted). These prove selection, routing, budget and
report invariants; they say nothing about whether a real model would draft or judge well."""

import sqlite3

import pytest

from backend import asks, claims, correction, db
from backend.agents import coverage as coverage_agent
from backend.agents import drafter
from backend.agents.schemas import ClaimDraft
from backend.budget import Budget
from backend.config import Settings
from backend.llm import LLM
from backend.report import get_report
from tests.fakes import FakeMesh, FakeSearch, claim, coverage, draft, synth, verdict
from tests.loop_helpers import Q0, add, seed_mixed
from tests.loop_helpers import verdict as verdict_row
from tests.seed import seed_collected_run
from tests.test_phase4_units import make_report

# ---------------------------------------------------------------------------------------------------------------------------------------
# 1. drafting inputs

TWO_ASKS = {"asks": [{"quote": "how X differs", "label": "differences"}, {"quote": "when to use each", "label": "use cases"}],
            "covers": {1: [0], 2: [0], 3: [1]}, "recorded": True}


def passage(pid, source_id, ordinal, text):
    return {"id": pid, "source_id": source_id, "ordinal": ordinal, "text": text}


def patch_db(monkeypatch, subquestions, passages_by_source, associations=None):
    monkeypatch.setattr(drafter.db, "list_subquestions", lambda conn, run_id: subquestions)
    monkeypatch.setattr(drafter.db, "list_associations", lambda conn, run_id: associations if associations is not None else [
        {"subquestion_id": sq["id"], "source_id": sid, "passage_ids": []} for sq in subquestions for sid in passages_by_source])
    monkeypatch.setattr(drafter.db, "source_passages", lambda conn, source_id: passages_by_source[source_id])


def sq(i, question, reqs):
    return {"id": i, "question": question, "evidence_requirements": reqs}


def prose(*words, n=8):
    return (" ".join(words) + " and you can read more about it in the guide that follows this paragraph. ") * n


def test_navigation_text_is_recognised_by_structure_and_short_or_code_evidence_is_never_flagged():
    toc = "\n\n".join(f"Getting Started With {w} Widgets" for w in ("Basic", "Advanced", "Custom", "Shared", "Quick", "Remote", "Local", "Hosted", "Managed", "Secure"))
    assert drafter.navigation_like(toc)
    assert not drafter.navigation_like(prose("alpha", "beta"))  # ordinary sentences
    assert not drafter.navigation_like("Table Of Contents Home About Contact")  # short: kept, however it looks
    code = "\n".join(f"item_{i} = [Alpha, Beta]" for i in range(10)) + " Alpha Beta Gamma " * 20
    assert not drafter.navigation_like(code)  # code has few sentences but is not navigation


def test_candidates_are_ranked_across_sources_and_the_trace_says_why_each_passage_was_chosen(monkeypatch):
    s = Settings(_env_file=None, mesh_api_key="", draft_passages_per_subquestion=2, draft_input_chars=6000)
    weak = [passage(f"S1-P{i}", 1, i, prose("alpha", f"filler{i}", "unrelated")) for i in range(6)]  # source 1: many weak candidates
    strong = [passage("S2-P0", 2, 0, prose("alpha", "beta", "gamma", "delta"))]  # source 2: the best passage of all
    patch_db(monkeypatch, [sq(1, "alpha beta gamma delta", ["alpha"])], {1: weak, 2: strong})
    offered, trace = drafter.select_draft_passages_traced(None, 1, s, provenance=drafter.NO_PROVENANCE)
    ids = [p["id"] for p in offered[1]]
    assert "S2-P0" in ids  # concatenating each source's candidates would have put source 1 first
    first = next(e for e in trace["selection"] if e["passage_id"] == "S2-P0")
    assert first["reason"].startswith("ranked about #1 of ") and first["word_overlap"] == 4 and first["chars"] == len(strong[0]["text"])
    assert offered[1][ids.index("S2-P0")]["text"] == strong[0]["text"]  # the text is offered exactly as stored


def test_the_existing_budget_is_shared_fairly_across_explicit_asks(monkeypatch):
    s = Settings(_env_file=None, mesh_api_key="", draft_passages_per_subquestion=4, draft_input_chars=4000)
    pool = {1: [passage(f"S1-P{i}", 1, i, prose("alpha", "beta", "gamma")[:900]) for i in range(8)],
            2: [passage(f"S2-P{i}", 2, i, prose("delta", "epsilon", "zeta")[:900]) for i in range(8)]}
    subs = [sq(1, "alpha beta gamma", ["alpha"]), sq(2, "alpha beta gamma again", ["beta"]), sq(3, "delta epsilon zeta", ["delta"])]
    patch_db(monkeypatch, subs, pool)
    _offered, trace = drafter.select_draft_passages_traced(None, 1, s, provenance=TWO_ASKS)
    # ask 0 is covered by subquestions 1 and 2, ask 1 by subquestion 3: the budget is split per ASK, not per subquestion
    assert trace["shares"] == {"1": 1000, "2": 1000, "3": 2000} and "per explicit ask" in trace["share_rule"]
    fair = [e for e in trace["selection"] if e["phase"] == "fair_share"]
    assert sum(e["chars"] for e in fair if e["subquestion_id"] == 3) <= 2000 and sum(e["chars"] for e in fair if e["subquestion_id"] in (1, 2)) <= 2000
    assert trace["chars_used"] <= 4000
    assert len({e["passage_id"] for e in trace["selection"]}) == len(trace["selection"])  # a passage is offered once


def test_a_run_without_recorded_provenance_splits_equally_and_says_so(monkeypatch):
    s = Settings(_env_file=None, mesh_api_key="", draft_input_chars=4000)
    patch_db(monkeypatch, [sq(1, "alpha", ["alpha"]), sq(2, "alpha", ["alpha"])], {1: [passage("S1-P0", 1, 0, prose("alpha"))]})
    _offered, trace = drafter.select_draft_passages_traced(None, 1, s, provenance=drafter.NO_PROVENANCE)
    assert trace["share_rule"].startswith("equal split") and "provenance" in trace["share_rule"] and trace["provenance_recorded"] is False


def test_filler_that_merely_fits_is_not_offered_and_navigation_text_ranks_below_prose(monkeypatch):
    s = Settings(_env_file=None, mesh_api_key="", draft_passages_per_subquestion=4, draft_input_chars=9000)
    toc = "\n\n".join(f"Alpha Beta Gamma Delta Guide {w}" for w in ("One", "Two", "Three", "Four", "Five", "Six", "Seven", "Eight", "Nine", "Ten"))
    pool = {1: [passage("S1-P0", 1, 0, toc), passage("S1-P1", 1, 1, prose("alpha", "beta", "gamma", "delta", "epsilon", "zeta")), passage("S1-P2", 1, 2, "alpha stray mention. " * 3)]}
    patch_db(monkeypatch, [sq(1, "alpha beta gamma delta epsilon zeta", ["alpha"])], pool)
    offered, trace = drafter.select_draft_passages_traced(None, 1, s, provenance=drafter.NO_PROVENANCE)
    ids = [p["id"] for p in offered[1]]
    assert "S1-P1" in ids and "S1-P2" not in ids  # a weak short mention is below the relevance floor
    nav = next(e for e in trace["selection"] if e["passage_id"] == "S1-P0") if "S1-P0" in ids else None
    assert nav is None or (nav["navigation_like"] and nav["score"] < next(e for e in trace["selection"] if e["passage_id"] == "S1-P1")["score"])


def test_the_drafting_task_names_the_asks_the_passages_and_allows_no_claim(conn, settings):
    r = seed_collected_run(conn)
    db.add_event(conn, r["run"], "plan_created", "planner", {"asks": TWO_ASKS["asks"][:2], "subquestions": [
        {"id": r["sq1"], "question": "q", "rationale": "r", "covers": [0]}, {"id": r["sq2"], "question": "q", "rationale": "r", "covers": [1]}]})

    class Capture:
        last_call_id = None

        def call(self, conn, run_id, budget, *, agent, model, system, user, schema, example):
            self.system, self.user = system, user
            return schema.model_validate({"claims": []})  # nothing usable: the drafter may write nothing

    llm = Capture()
    assert drafter.run_drafter(conn, llm, Budget(settings), settings, r["run"]) == []  # no claim is forced
    assert '<asks>' in llm.user and "1. differences" in llm.user and "2. use cases" in llm.user and 'serves_asks="2"' in llm.user
    assert "Evidence wanted:" in llm.user and "<passage id=" in llm.user
    assert "use case" in llm.system and "write no claim" in llm.system and "never invent" in llm.system and "ALWAYS attribute" in llm.system
    event = next(e for e in db.list_events(conn, r["run"]) if e["event_type"] == "draft_input")
    assert event["payload"]["mode"] == "ranked_fair_share" and event["payload"]["selection"] and "reason" in event["payload"]["selection"][0]


# ---------------------------------------------------------------------------------------------------------------------------------------
# 2. consistent report sections: a direct answer recorded for another subquestion is referenced, never copied


def coverage_world(conn, settings, *, role_for_q2="direct_answer"):
    r, ids = seed_mixed(conn, settings)
    reply = {"obligations": [
        {"obligation_key": "Q1.R1.O1", "status": "addressed", "claim_roles": [{"claim_id": ids["A"], "role": "direct_answer", "reason": "answers it"}], "gap": "", "missing_kind": "none"},
        {"obligation_key": "Q1.R2.O1", "status": "unresolved", "claim_roles": [], "gap": "release date missing", "missing_kind": "date"},
        {"obligation_key": "Q2.R1.O1", "status": "addressed" if role_for_q2 == "direct_answer" else "unresolved",
         "claim_roles": [{"claim_id": ids["A"], "role": role_for_q2, "reason": "judged for the other subquestion"}], "gap": "" if role_for_q2 == "direct_answer" else "no answer", "missing_kind": "none"}]}
    coverage_agent.assess(conn, LLM(FakeMesh(coverage=[reply]), settings), Budget(settings), settings, r["run"], 0)
    return r, ids


def test_a_direct_answer_for_another_subquestion_is_referenced_with_the_same_claim_and_citations(conn, settings):
    r, ids = coverage_world(conn, settings)
    info = make_report(conn, r, None)
    rep = get_report(conn, r["run"], info["report_id"])
    first, second = rep["sections"]
    assert [c["claim_id"] for c in first["claims"]] == [ids["A"]] and "cross_reference" not in first["claims"][0]
    ref = second["claims"][0]
    assert ref["claim_id"] == ids["A"] and ref["cross_reference"] == {"owner_label": "Section 1", "owner_subquestion_id": r["sq1"]}
    assert ref["text"] == first["claims"][0]["text"] and ref["citations"] == first["claims"][0]["citations"] and ref["requirement_keys"] == ["Q2.R1"]
    assert second["answered"] is True and "the same verified claim as in Section 1, where it was drafted" in rep["markdown"]
    assert conn.execute("SELECT COUNT(*) FROM report_claims WHERE report_id = ? AND claim_id = ?", (info["report_id"], ids["A"])).fetchone()[0] == 1  # one report claim
    assert conn.execute("SELECT COUNT(*) FROM report_answer_refs WHERE report_id = ?", (info["report_id"],)).fetchone()[0] == 1
    ev = next(e for e in db.list_events(conn, r["run"]) if e["event_type"] == "report_created")
    assert ev["payload"]["answer_refs"] == [{"claim_id": ids["A"], "subquestion_id": r["sq2"], "requirement_keys": ["Q2.R1"]}]


def test_a_context_only_association_never_populates_an_answer(conn, settings):
    r, _ids = coverage_world(conn, settings, role_for_q2="relevant_context")
    info = make_report(conn, r, None)
    rep = get_report(conn, r["run"], info["report_id"])
    second = rep["sections"][1]
    assert second["claims"] == [] and second["answered"] is False and [c["claim_id"] for c in second["context"]] == []  # context is shown only in the claim's own section
    assert conn.execute("SELECT COUNT(*) FROM report_answer_refs").fetchone()[0] == 0
    assert "No direct answer to this subquestion was found." in rep["markdown"]
    with pytest.raises(sqlite3.IntegrityError, match="direct-answer judgment"):  # and the database refuses a forged reference
        conn.execute("INSERT INTO report_answer_refs (report_id, section_id, report_claim_id) VALUES (?, ?, ?)",
                     (info["report_id"], conn.execute("SELECT id FROM report_sections WHERE report_id = ? AND position = 2", (info["report_id"],)).fetchone()[0],
                      conn.execute("SELECT id FROM report_claims WHERE report_id = ?", (info["report_id"],)).fetchone()[0]))


def test_the_database_refuses_references_across_reports_runs_and_to_the_claims_own_section(conn, settings):
    r, _ids = coverage_world(conn, settings)
    info = make_report(conn, r, None)
    sec1, sec2 = (row[0] for row in conn.execute("SELECT id FROM report_sections WHERE report_id = ? ORDER BY position", (info["report_id"],)))
    rc = conn.execute("SELECT id FROM report_claims WHERE report_id = ?", (info["report_id"],)).fetchone()[0]
    with pytest.raises(sqlite3.IntegrityError, match="other than the claim's own"):
        conn.execute("INSERT INTO report_answer_refs (report_id, section_id, report_claim_id) VALUES (?, ?, ?)", (info["report_id"], sec1, rc))
    other = seed_collected_run(conn, n=2)  # another run, with its own (empty) report
    other_report = make_report(conn, other, None)["report_id"]
    with pytest.raises(sqlite3.IntegrityError, match="must belong to this report"):  # a section of another run's report
        conn.execute("INSERT INTO report_answer_refs (report_id, section_id, report_claim_id) VALUES (?, ?, ?)",
                     (info["report_id"], conn.execute("SELECT id FROM report_sections WHERE report_id = ?", (other_report,)).fetchone()[0], rc))
    with pytest.raises(sqlite3.IntegrityError, match="immutable"):
        conn.execute("UPDATE report_answer_refs SET reason = 'x'")
        conn.execute("INSERT INTO report_answer_refs (report_id, section_id, report_claim_id) VALUES (?, ?, ?)", (info["report_id"], sec2, rc))  # the valid one exists already
        conn.execute("UPDATE report_answer_refs SET reason = 'edited'")


def test_older_reports_and_databases_without_references_read_unchanged(conn, settings):
    r, ids = coverage_world(conn, settings)
    first = make_report(conn, r, None)["report_id"]
    before = get_report(conn, r["run"], first)
    conn.execute("DROP TRIGGER report_answer_refs_no_delete")
    conn.execute("DROP TABLE report_answer_refs")  # a database from before references existed
    after = get_report(conn, r["run"], first)
    assert [c["claim_id"] for s in after["sections"] for c in s["claims"]] == [ids["A"]]  # the reference is simply absent; nothing else changed
    assert after["sections"][0] == before["sections"][0] and "cross_reference" not in str(after["sections"])


# ---------------------------------------------------------------------------------------------------------------------------------------
# 3. coverage honesty: the claim's own text must carry what is credited


def derived(conn, r, key, sq_id, subject, dimension):
    with db.tx(conn):
        correction.save_obligations(conn, r["run"], key, sq_id, [(subject, dimension)], origin="derived", model=None, model_call_id=None)


def assess_with(conn, settings, r, key, claim_id, status="addressed"):
    reply = {"obligations": [{"obligation_key": f"{key}.O1", "status": status, "gap": "", "missing_kind": "none",
                              "claim_roles": [{"claim_id": claim_id, "role": "direct_answer", "reason": "States tuples can be used as dictionary keys"}]}]}
    coverage_agent.assess(conn, LLM(FakeMesh(coverage=[reply]), settings), Budget(settings), settings, r["run"], 0)
    return conn.execute("SELECT cr.role, cr.origin, oa.status FROM claim_relevance cr JOIN obligation_assessments oa ON oa.id = cr.obligation_assessment_id "
                        "JOIN coverage_obligations o ON o.id = oa.obligation_id WHERE o.obligation_key = ?", (f"{key}.O1",)).fetchone()


def one_claim_run(conn, settings):
    r = seed_collected_run(conn)  # claim A: "SQLite runs as a library inside the process of the calling program."
    a = add(conn, r, "sq1", Q0, r["p"](0), Q0, settings)
    verdict_row(conn, a, "supported", passages=[(r["p"](0), "claim_evidence")])
    return r, {"A": a}


def test_a_credit_the_claim_text_does_not_carry_is_downgraded_to_context(conn, settings):
    r, ids = one_claim_run(conn, settings)
    derived(conn, r, "Q2.R1", r["sq2"], "SQLite", "use as dictionary keys and for returning multiple values from functions")
    role = assess_with(conn, settings, r, "Q2.R1", ids["A"])
    assert (role["role"], role["origin"], role["status"]) == ("relevant_context", "rule", "unresolved")  # wording that lives only in the evidence is not the claim's assertion
    notes = correction.assessments(conn, r["run"], 0)
    q2 = next(a for a in notes if a["requirement_key"] == "Q2.R1")
    detail = q2["notes"]["dimension_not_in_claim"][0]
    assert detail["claim_id"] == ids["A"] and "dictionary" in detail["claim_lacks"] and q2["status"] == "unresolved"


def test_a_credit_stays_when_the_claim_states_it_or_the_dimension_is_too_abstract_to_check(conn, settings):
    r, ids = one_claim_run(conn, settings)
    derived(conn, r, "Q2.R1", r["sq2"], "SQLite", "embedded library process application directly")  # the claim says these things
    assert assess_with(conn, settings, r, "Q2.R1", ids["A"])["role"] == "direct_answer"
    assert correction.claim_carries_dimension("mutability", "Tuples are immutable.") == (True, [])  # one word: not checked
    assert correction.claim_carries_dimension("publication date", "The HTTP/2 specification was published in June 2022.") == (True, [])
    ok, lacking = correction.claim_carries_dimension("use as dictionary keys and for returning multiple values", "Tuples are immutable.")
    assert not ok and "dictionary" in lacking


# ---------------------------------------------------------------------------------------------------------------------------------------
# 4. corrective priorities and the stored-evidence route (pure routing)

ROWS = [{"requirement_key": "Q1.R1", "subquestion_id": 1, "requirement_text": "background", "status": "unresolved", "gap": "g1", "missing_kind": "detail"},
        {"requirement_key": "Q2.R1", "subquestion_id": 2, "requirement_text": "the ask", "status": "unresolved", "gap": "g2", "missing_kind": "detail"},
        {"requirement_key": "Q3.R1", "subquestion_id": 3, "requirement_text": "answered", "status": "addressed", "gap": "", "missing_kind": "none"}]


def plan_event(conn, run, covers):
    db.add_event(conn, run, "plan_created", "planner", {"asks": [{"quote": "q0", "label": "first ask"}, {"quote": "q1", "label": "second ask"}], "subquestions": [
        {"id": i, "question": "q", "rationale": "r", **({"covers": c} if c is not None else {})} for i, c in covers.items()]})


def test_unmet_explicit_asks_come_first_and_unrecorded_provenance_is_reported_not_guessed(conn, settings):
    r = seed_collected_run(conn)
    plan_event(conn, r["run"], {1: None, 2: None, 3: None})  # an older run: the asks exist, `covers` was not recorded
    pri = correction.ask_priority(conn, r["run"], ROWS)
    assert pri["recorded"] is False and pri["priority"] == {} and "cannot be told" in pri["note"]
    order = [p["requirement_key"] for p in correction.propose_tasks(conn, r["run"], ROWS, settings, 5) if p["kind"] == "missing_evidence"]
    assert order == ["Q1.R1", "Q2.R1"]  # the original order, with the limitation recorded in each task
    r2 = seed_collected_run(conn, n=2)
    plan_event(conn, r2["run"], {1: [0], 2: [1], 3: [0]})  # ask 0 is met by subquestion 3's addressed requirement; ask 1 (subquestion 2) is not
    pri = correction.ask_priority(conn, r2["run"], ROWS)
    assert pri["recorded"] is True and pri["unmet_asks"] == ["second ask"] and pri["priority"] == {1: 1, 2: 0, 3: 1}
    tasks = [p for p in correction.propose_tasks(conn, r2["run"], ROWS, settings, 5) if p["kind"] == "missing_evidence"]
    assert [t["requirement_key"] for t in tasks] == ["Q2.R1", "Q1.R1"]  # the unmet ask first, background after
    assert tasks[0]["spec"]["priority"] == {"serves_unmet_ask": True, "ask_provenance_recorded": True, "ask_provenance_source": "planner"}


def test_stored_evidence_is_offered_before_searching_costs_no_search_and_is_not_repeated_on_unchanged_evidence(conn, settings):
    r = seed_collected_run(conn)
    plan_event(conn, r["run"], {1: [0], 2: [1], 3: [0]})
    probe = lambda sq_id: {"passage_ids": ["S1-P3"], "fingerprint": f"fp{sq_id}"} if sq_id == 2 else {"passage_ids": [], "fingerprint": None}
    out = correction.propose_tasks(conn, r["run"], ROWS, settings, 0, stored_probe=probe)  # NO searches left
    by_key = {p["requirement_key"]: p for p in out if p["kind"] == "missing_evidence"}
    stored = by_key["Q2.R1"]
    assert stored["route"] == "stored_evidence" and not stored.get("skip") and stored["spec"]["route"]["passage_ids"] == ["S1-P3"]
    assert "never offered to the drafter" in stored["spec"]["route"]["why"]
    assert by_key["Q1.R1"]["spec"]["route"]["chosen"] == "search" and by_key["Q1.R1"]["skip"] == "no search budget left"  # the search route still respects the search budget
    # the attempt happened (even if it drafted nothing): the same evidence is not tried again, and the reason for searching instead is recorded
    with db.tx(conn):
        tid = correction.add_task(conn, r["run"], 0, "missing_evidence", subquestion_id=2, requirement_key="Q2.R1", spec=stored["spec"])
        correction.finish_task(conn, tid, "done", {"route": "stored_evidence", "claim_ids": []})
    again = {p["requirement_key"]: p for p in correction.propose_tasks(conn, r["run"], ROWS, settings, 3, stored_probe=probe) if p["kind"] == "missing_evidence"}
    assert again["Q2.R1"]["spec"]["route"] == {"chosen": "search", "why": "stored-evidence drafting was already tried on this same evidence"}
    assert not again["Q2.R1"].get("repeat")  # a stored attempt does not count as "already searched"


def test_one_stored_task_serves_all_unmet_requirements_of_a_subquestion(conn, settings):
    r = seed_collected_run(conn)
    rows = [{**ROWS[1], "requirement_key": "Q2.R1"}, {**ROWS[1], "requirement_key": "Q2.R2", "gap": "g3"}]
    probe = lambda sq_id: {"passage_ids": ["S1-P3"], "fingerprint": "fp"}
    out = correction.propose_tasks(conn, r["run"], rows, settings, 5, stored_probe=probe)
    assert [bool(p.get("skip")) for p in out] == [False, True] and "handled together with Q2.R1" in out[1]["skip"]


# ---------------------------------------------------------------------------------------------------------------------------------------
# 5. the real loop: stored-evidence drafting charged to the shared budget


STORED = "How Postgres can handle concurrency: its concurrency model is multiversion, so readers never block writers."
STORED_QUOTE = "its concurrency model is multiversion, so readers never block writers"
STORED_CLAIM = "Postgres handles concurrency with a multiversion model, so readers never block writers."


@pytest.fixture
def stored_world(client, settings):
    conn = db.connect(client.db_path)
    r, ids = seed_mixed(conn, settings)
    with db.tx(conn):
        sid = db.insert_source(conn, r["run"], requested_url="https://example.test/extra", final_url="https://example.test/extra", title="Extra page", content_type="text/html",
                               content_hash="extra", snapshot=STORED, passages=[(0, len(STORED))])
        db.add_association(conn, r["run"], r["sq2"], sid, "selected_from_search", explanation="fixture")
    plan_event(conn, r["run"], {r["sq1"]: [0], r["sq2"]: [1]})
    for name in ("B", "C", "D"):  # settle the fixture's other claims so that only the stored-evidence task is in play
        claims.exclude_claim(conn, r["run"], ids[name], "fixture: settled", 0)
    conn.close()
    return r, ids, sid


def stored_mesh(sid, *, drafted=True):
    gap = "concurrency model not established"
    return FakeMesh(
        coverage=[coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "addressed", [1], "", "none"), ("Q2.R1", "unresolved", [], gap, "detail")),
                  coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "addressed", [1], "", "none"), ("Q2.R1", "addressed", [6], "", "none"))],
        draft=draft(claim(2, STORED_CLAIM, (f"S{sid}-P0", STORED_QUOTE))) if drafted else draft(),
        verify=[verdict("supported", used=[f"S{sid}-P0"])], synth=synth((1, [1]), (2, [6])))


def test_the_loop_drafts_from_stored_evidence_without_searching_and_charges_the_shared_budget(client, complete_env, stored_world):
    _r, _ids, sid = stored_world
    mesh, srch = stored_mesh(sid), FakeSearch({})
    complete_env(mesh, srch)
    client.post("/runs/1/complete")
    assert srch.queries == []  # no search, no fetch
    done = client.get("/runs/1/completion").json()
    assert done["complete_status"] == "completed" and done["budget_used_of_limit"]["search_queries"][0] == 0
    agents = [c["messages"][0]["content"] for c in mesh.calls]
    assert done["budget_used_of_limit"]["model_requests"][0] == len(mesh.calls) == 6  # coverage, drafting, verification, coverage, cross-claim consistency check, synthesis: all on ONE budget
    assert sum(a.startswith("You are the Drafter") for a in agents) == 1 and sum(a.startswith("You are the Verifier") for a in agents) == 1
    routed = next(e for e in client.get("/runs/1/events").json() if e["event_type"] == "coverage_routed")["payload"]
    task = next(t for t in routed["tasks"] if t["requirement_key"] == "Q2.R1")
    assert task["route"] == "stored_evidence" and "never offered to the drafter" in task["route_why"]
    stored_task = next(t for rd in client.get("/runs/1/corrections").json()["rounds"] for t in rd["tasks"] if t["requirement_key"] == "Q2.R1")
    assert stored_task["status"] == "done" and stored_task["spec"]["route"]["passage_ids"] == ["S1-P2", f"S{sid}-P0"]  # S1-P2 ("... concurrent clients") now matches "concurrency" by stem and stored_task["outcome"]["claim_ids"] == [6]
    new = next(c for c in client.get("/runs/1/claims").json() if c["id"] == 6)
    assert new["text"] == STORED_CLAIM and new["provenance"]["origin"] == "stored_evidence" and new["provenance"]["candidate"] is True
    assert (new["integrity_verdict"], new["semantic_verdict"], new["eligible_for_report"]) == ("passed", "supported", True)  # verification was not relaxed
    prompts = [c["messages"][1]["content"] for c in mesh.calls if "You are the Drafter" in c["messages"][0]["content"]]
    assert len(prompts) == 1 and "Not yet answered: concurrency model (concurrency model not established)" in prompts[0] and STORED_CLAIM not in prompts[0]
    rep = client.get("/runs/1/report").json()
    assert [c["claim_id"] for c in rep["sections"][1]["claims"]] == [6] and rep["sections"][1]["answered"] is True


def test_a_drafting_that_yields_nothing_is_recorded_and_the_loop_ends_without_inventing_a_claim(client, complete_env, stored_world):
    _r, _ids, sid = stored_world
    mesh = stored_mesh(sid, drafted=False)
    mesh.replies["coverage"] = [mesh.replies["coverage"][0]] * 3
    complete_env(mesh, FakeSearch({}))
    client.post("/runs/1/complete")
    task = next(t for rd in client.get("/runs/1/corrections").json()["rounds"] for t in rd["tasks"] if t["requirement_key"] == "Q2.R1" and t["status"] == "done")
    assert task["outcome"]["claim_ids"] == []
    assert not any(c["id"] == 6 for c in client.get("/runs/1/claims").json())
    rep = client.get("/runs/1/report").json()
    assert rep["sections"][1]["answered"] is False and rep["outcome"] == "claims_with_gaps"  # still honest: the requirement stays open


@pytest.mark.parametrize("overrides,reason", [({"max_model_requests": 4}, "reserving model requests"), ({"max_candidate_claims": 0}, "candidate-claim budget exhausted")])
def test_an_exhausted_budget_skips_the_stored_task_records_why_and_still_reports(client, complete_env, stored_world, overrides, reason):
    _r, _ids, sid = stored_world
    mesh = stored_mesh(sid)
    mesh.replies["coverage"] = [mesh.replies["coverage"][0]] * 3
    complete_env(mesh, FakeSearch({}), **overrides)
    client.post("/runs/1/complete")
    task = next(t for rd in client.get("/runs/1/corrections").json()["rounds"] for t in rd["tasks"] if t["requirement_key"] == "Q2.R1")
    assert task["status"] == "skipped" and reason in task["outcome"]["reason"]
    assert not any("You are the Drafter" in c["messages"][0]["content"] for c in mesh.calls)  # nothing was drafted
    assert len(mesh.calls) <= overrides.get("max_model_requests", 20)
    rep = client.get("/runs/1/report").json()
    assert rep["outcome"] == "claims_with_gaps" and [c["claim_id"] for s in rep["sections"] for c in s["claims"]] == [1]


def test_stored_candidates_exclude_passages_already_offered_or_cited(conn, settings):
    r, _ids = seed_mixed(conn, settings)
    with db.tx(conn):
        sid = db.insert_source(conn, r["run"], requested_url="https://example.test/e", final_url="https://example.test/e", title="E", content_type="text/html", content_hash="e",
                               snapshot=STORED, passages=[(0, len(STORED))])
        db.add_association(conn, r["run"], r["sq2"], sid, "selected_from_search", explanation="fixture")
    found = drafter.stored_candidates(conn, r["run"], settings, r["sq2"])
    assert found["passage_ids"] == [f"S{sid}-P0"] and found["fingerprint"]
    db.add_event(conn, r["run"], "draft_input", "drafter", {"passages_offered": {str(r["sq2"]): [f"S{sid}-P0"]}})  # once it was offered, it is no longer "unused"
    assert drafter.stored_candidates(conn, r["run"], settings, r["sq2"])["passage_ids"] == []
    assert asks.provenance(conn, r["run"]) == {"asks": [], "covers": None, "recorded": False}
    assert ClaimDraft.model_validate({"claims": []}).claims == [] and Settings(_env_file=None).draft_input_chars == 9000  # defaults and limits are unchanged


# ---------------------------------------------------------------------------------------------------------------------------------------
# 6. explicit continuation of a finished run: own budget, no retrieval, rounds numbered after the stored ones, earlier reports untouched


def test_operator_supplied_ask_provenance_is_labelled_and_used_only_when_the_planner_recorded_none(conn):
    r = seed_collected_run(conn)
    plan_event(conn, r["run"], {r["sq1"]: None, r["sq2"]: None})
    assert asks.provenance(conn, r["run"])["recorded"] is False
    db.add_event(conn, r["run"], "ask_provenance_supplied", "continuation", {"covers": {str(r["sq1"]): [0], str(r["sq2"]): [1]}, "supplied_by": "operator"})
    prov = asks.provenance(conn, r["run"])
    assert prov["recorded"] is True and prov["source"] == "supplied by the operator" and prov["covers"] == {r["sq1"]: [0], r["sq2"]: [1]}
    rows = [{"requirement_key": "Q1.R1", "subquestion_id": r["sq1"], "requirement_text": "t", "status": "unresolved", "gap": "g", "missing_kind": "detail"}]
    pri = correction.ask_priority(conn, r["run"], rows)  # the routing records WHO supplied the mapping it acted on
    assert pri["source"] == "supplied by the operator"
    spec = correction.propose_tasks(conn, r["run"], rows, Settings(_env_file=None, mesh_api_key=""), 5)[0]["spec"]
    assert spec["priority"]["ask_provenance_source"] == "supplied by the operator"
    r2 = seed_collected_run(conn, n=2)
    plan_event(conn, r2["run"], {r2["sq1"]: [1], r2["sq2"]: [0]})  # the planner recorded it: a supplied event cannot override that
    db.add_event(conn, r2["run"], "ask_provenance_supplied", "continuation", {"covers": {str(r2["sq1"]): [0], str(r2["sq2"]): [1]}})
    assert asks.provenance(conn, r2["run"])["covers"] == {r2["sq1"]: [1], r2["sq2"]: [0]} and asks.provenance(conn, r2["run"])["source"] == "planner"


def test_a_continuation_is_refused_unless_a_finished_job_left_requirements_open(conn, settings):
    from backend import continuation

    r = seed_collected_run(conn)
    p = continuation.plan(conn, r["run"], settings)
    assert p["allowed"] is False and any("neither finished nor stopped" in x or "no claim" in x for x in p["reasons"])
    with pytest.raises(LookupError):
        continuation.start(conn, r["run"], settings)
    assert p["new_job_limits"]["search_queries"] == 0 and p["new_job_limits"]["page_fetches"] == 0 and p["cost"]["calculated_usd"] is None


def test_a_finished_run_can_be_continued_from_stored_evidence_under_its_own_budget_without_touching_the_earlier_report(client, complete_env, stored_world):
    from backend import continuation
    from backend.graph import execute_completion

    _r, _ids, sid = stored_world
    first = stored_mesh(sid)
    first.replies["coverage"] = [first.replies["coverage"][0]] * 3
    complete_env(first, FakeSearch({}), max_candidate_claims=0)  # the first job cannot draft: it ends with the requirement open
    client.post("/runs/1/complete")
    assert client.get("/runs/1/completion").json()["complete_status"] == "completed"
    old_report = client.get("/runs/1/report").json()
    conn = db.connect(client.db_path)
    plan = continuation.plan(conn, 1, Settings(_env_file=None, mesh_api_key=""))
    assert plan["allowed"] and plan["round_base"] == 1 and plan["unmet_requirements"] == ["Q2.R1"]  # the first job no longer repeats an unchanged assessment
    assert [t["route"] for t in plan["tasks"]] == ["stored_evidence"] and plan["new_job_limits"]["search_queries"] == 0
    srch = FakeSearch({})
    second = FakeMesh(
        coverage=[coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "addressed", [1], "", "none"), ("Q2.R1", "unresolved", [], "concurrency model not established", "detail")),
                  coverage(("Q1.R1", "addressed", [1], "", "none"), ("Q1.R2", "addressed", [1], "", "none"), ("Q2.R1", "addressed", [6], "", "none"))],
        draft=draft(claim(2, STORED_CLAIM, (f"S{sid}-P0", STORED_QUOTE))), verify=[verdict("supported", used=[f"S{sid}-P0"])], synth=synth((1, [1]), (2, [6])))
    svc = complete_env(second, srch, max_search_queries=0, max_page_fetches=0)
    started = continuation.start(conn, 1, svc.settings)
    conn.close()
    execute_completion(1, svc, False, round_base=started["round_base"])
    assert srch.queries == [] and len(second.calls) == 6  # ... and the cross-claim consistency check
    conn = db.connect(client.db_path)
    assert [x[0] for x in conn.execute("SELECT DISTINCT round FROM coverage_assessments ORDER BY round")] == [0, 1, 2]  # the new job's rounds follow the stored one
    lineage = next(e for e in db.list_events(conn, 1) if e["event_type"] == "job_continuation_started")["payload"]
    assert lineage["round_base"] == 1 and lineage["previous_job"]["status"] == "completed" and lineage["new_job_limits"]["model_requests"] == 20
    new = conn.execute("SELECT provenance FROM claims WHERE id = 6").fetchone()[0]
    assert '"origin": "stored_evidence"' in new and '"round": 2' in new
    assert conn.execute("SELECT COUNT(*) FROM reports WHERE run_id = 1").fetchone()[0] == 2  # a NEW report; the earlier one stays
    assert get_report(conn, 1, old_report["id"])["markdown"] == old_report["markdown"]
    assert client.get("/runs/1/report").json()["sections"][1]["answered"] is True
    conn.close()
