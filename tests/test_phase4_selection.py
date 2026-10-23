"""FIXTURE tests for obligation-aware selection of corrective evidence (the SQLite / PostgreSQL crowd-out seen in the live run).

These check which passages are OFFERED to the drafter. They say nothing about whether the drafter or verifier then does well."""

from backend import claims, db
from backend.agents.drafter import select_corrective_passages
from backend.config import Settings

PG = [f"PostgreSQL concurrency uses multiversion control so readers never block writers, variant {i}, with row locks." for i in range(6)]
LITE = [
    "SQLite concurrency allows many readers but a single writer holds an exclusive database lock during writes.",
    "SQLite scalability is limited because the database is one file accessed by a single process at a time.",
]
UNRELATED = ["The conference was held in a large hall with catering, music and a long keynote about the weather."] * 3


def obligation(key, subject, dimension, sq):
    return {"obligation_key": key, "requirement_key": key.rsplit(".", 1)[0], "subquestion_id": sq, "subject": subject,
            "dimension": dimension, "origin": "derived", "status": "unaddressed", "gap": f"{subject} {dimension}", "missing_kind": "evidence"}


def add_source(conn, run, sq, name, paras):
    spans, pos = [], 0
    for p in paras:
        spans.append((pos, pos + len(p)))
        pos += len(p) + 2
    with db.tx(conn):
        sid = db.insert_source(conn, run, requested_url=f"https://example.test/{name}", final_url=f"https://example.test/{name}", title=name,
                               content_type="text/html", content_hash=name, snapshot="\n\n".join(paras), passages=spans)
        db.add_association(conn, run, sq, sid, "selected_from_search", explanation="fixture")
    return sid


def scenario(conn, sources):
    run = db.create_run(conn, "How do SQLite and PostgreSQL compare?")["id"]
    sq = db.add_subquestion(conn, run, "How do they handle concurrency?", ["concurrency"])
    return run, sq, [add_source(conn, run, sq, n, paras) for n, paras in sources]


def ids(offered):
    return [p["id"] for v in offered.values() for p in v]


UNMET = [obligation("Q1.R1.O1", "SQLite", "concurrency", 1), obligation("Q1.R1.O2", "PostgreSQL", "concurrency", 1)]


def test_relevant_sqlite_evidence_is_not_crowded_out_by_postgresql(conn):
    # the observed case: the first source alone could fill every slot with PostgreSQL passages
    run, _sq, (pg, lite) = scenario(conn, [("pgdocs", PG), ("comparison", LITE)])
    offered, trace = select_corrective_passages(conn, run, Settings(draft_passages_per_subquestion=4), UNMET, only_sources={pg, lite})
    chosen = ids(offered)
    assert f"S{lite}-P0" in chosen and any(i.startswith(f"S{pg}-") for i in chosen)
    outcomes = {o["obligation_key"]: o for o in trace["obligation_outcomes"]}
    assert outcomes["Q1.R1.O1"]["passage_ids"] == [f"S{lite}-P0"]
    assert outcomes["Q1.R1.O2"]["outcome"] == "own_slot" and outcomes["Q1.R1.O2"]["passage_ids"][0].startswith(f"S{pg}-")


def test_one_slot_per_subquestion_goes_to_an_obligation_not_just_the_first_source(conn):
    run, _sq, (_pg, lite) = scenario(conn, [("pgdocs", PG), ("comparison", LITE)])
    offered, _ = select_corrective_passages(conn, run, Settings(draft_passages_per_subquestion=2), UNMET)
    assert len(ids(offered)) == 2 and f"S{lite}-P0" in ids(offered)


def test_irrelevant_evidence_is_never_added_for_diversity(conn):
    run, _sq, (pg, other) = scenario(conn, [("pgdocs", PG), ("noise", UNRELATED)])
    unmet = [obligation("Q1.R1.O2", "PostgreSQL", "concurrency", 1)]
    offered, trace = select_corrective_passages(conn, run, Settings(draft_passages_per_subquestion=4), unmet)
    assert ids(offered) and not any(i.startswith(f"S{other}-") for i in ids(offered))
    assert {e["source_id"] for e in trace["selection"]["1"]} == {pg}


def test_obligation_without_relevant_passage_gets_none_and_is_recorded(conn):
    run, _sq, (_pg,) = scenario(conn, [("pgdocs", PG)])
    offered, trace = select_corrective_passages(conn, run, Settings(), UNMET)
    out = {o["obligation_key"]: o for o in trace["obligation_outcomes"]}
    assert out["Q1.R1.O1"] == {"subquestion_id": 1, "obligation_key": "Q1.R1.O1", "outcome": "no_relevant_passage", "passage_ids": []}
    assert out["Q1.R1.O2"]["passage_ids"]
    assert not any("SQLite" in p["text"] for v in offered.values() for p in v)


def test_nothing_relevant_offers_nothing(conn):
    run, _sq, _ = scenario(conn, [("noise", UNRELATED)])
    offered, trace = select_corrective_passages(conn, run, Settings(), UNMET)
    assert offered == {} and {o["outcome"] for o in trace["obligation_outcomes"]} == {"no_relevant_passage"}


def test_selection_respects_slot_and_character_limits(conn):
    run, _sq, _ = scenario(conn, [("pgdocs", PG), ("comparison", LITE)])
    offered, _ = select_corrective_passages(conn, run, Settings(draft_passages_per_subquestion=3, draft_context_passages=0), UNMET)
    assert len(ids(offered)) <= 3
    tight = 250
    offered, _ = select_corrective_passages(conn, run, Settings(draft_input_chars=tight), UNMET)
    assert sum(len(p["text"]) for v in offered.values() for p in v) <= tight and offered


def test_a_passage_serving_two_obligations_is_offered_once_and_both_are_recorded(conn):
    both = ["SQLite and PostgreSQL concurrency differ: SQLite serialises writers while PostgreSQL lets writers proceed in parallel."]
    run, _sq, _ = scenario(conn, [("comparison", both)])
    offered, trace = select_corrective_passages(conn, run, Settings(), UNMET)
    assert len(ids(offered)) == 1
    assert set(trace["selection"]["1"][0]["obligations"]) == {"Q1.R1.O1", "Q1.R1.O2"}


def test_prefers_evidence_no_active_claim_already_cites(conn):
    run, sq, (pg,) = scenario(conn, [("pgdocs", PG)])
    unmet = [obligation("Q1.R1.O2", "PostgreSQL", "concurrency", 1)]
    one = Settings(draft_passages_per_subquestion=1, draft_context_passages=0)
    assert ids(select_corrective_passages(conn, run, one, unmet)[0]) == [f"S{pg}-P0"]
    with db.tx(conn):
        claims.add_claim(conn, run, sq, PG[0], [(f"S{pg}-P0", PG[0][:40])], origin="drafted")
    offered, trace = select_corrective_passages(conn, run, one, unmet)
    assert ids(offered) == [f"S{pg}-P1"] and not trace["selection"]["1"][0]["already_cited"]
    run2, sq2, (solo,) = scenario(conn, [("solo", PG[:1])])
    with db.tx(conn):
        claims.add_claim(conn, run2, sq2, PG[0], [(f"S{solo}-P0", PG[0][:40])], origin="drafted")
    offered, trace = select_corrective_passages(conn, run2, one, [obligation("Q1.R1.O2", "PostgreSQL", "concurrency", sq2)])
    assert ids(offered) == [f"S{solo}-P0"] and trace["selection"][str(sq2)][0]["already_cited"]  # still usable when nothing else is relevant


def test_only_newly_linked_sources_and_this_run_are_used(conn):
    run, _sq, (pg, lite) = scenario(conn, [("pgdocs", PG), ("comparison", LITE)])
    _other_run, _other_sq, (foreign,) = scenario(conn, [("other", LITE)])
    offered, _ = select_corrective_passages(conn, run, Settings(), UNMET, only_sources={pg})
    assert not any(i.startswith((f"S{lite}-", f"S{foreign}-")) for i in ids(offered))
    offered, _ = select_corrective_passages(conn, run, Settings(), UNMET)
    assert not any(i.startswith(f"S{foreign}-") for i in ids(offered))


def test_fragment_brings_one_preceding_passage_as_context(conn):
    paras = ["Background: how SQLite handles several processes touching one file.", "SQLite concurrency: one writer."]
    run, _sq, (sid,) = scenario(conn, [("short", paras)])
    unmet = [obligation("Q1.R1.O1", "SQLite", "concurrency", 1)]
    offered, trace = select_corrective_passages(conn, run, Settings(draft_context_passages=1), unmet)
    assert ids(offered) == [f"S{sid}-P0", f"S{sid}-P1"]
    assert [e["role"] for e in trace["selection"]["1"]] == ["context", "obligation_slot"]
    offered, _ = select_corrective_passages(conn, run, Settings(draft_context_passages=0), unmet)
    assert ids(offered) == [f"S{sid}-P1"]


def test_run_drafter_offers_the_selected_passages_and_records_why(conn):
    from backend.agents.drafter import run_drafter
    from backend.budget import Budget
    from backend.llm import LLM
    from tests.fakes import FakeMesh, draft

    s = Settings(mesh_api_key="", drafter_model="fixture-drafter", draft_passages_per_subquestion=3)
    run, _sq, (pg, lite) = scenario(conn, [("pgdocs", PG), ("comparison", LITE)])
    mesh = FakeMesh(draft=draft())
    run_drafter(conn, LLM(mesh, s), Budget(s), s, run, unmet=UNMET, only_sources={pg, lite})
    user = mesh.calls[0]["messages"][1]["content"]
    event = next(e for e in db.list_events(conn, run) if e["event_type"] == "draft_input")["payload"]
    assert f"S{lite}-P0" in user
    assert event["mode"] == "obligation_aware" and {o["obligation_key"] for o in event["unmet_obligations"]} == {"Q1.R1.O1", "Q1.R1.O2"}
    assert sum(len(v) for v in event["passages_offered"].values()) == user.count("<passage id=")
    assert {x["passage_id"] for x in event["selection"]["1"]} == {i for v in event["passages_offered"].values() for i in v}
