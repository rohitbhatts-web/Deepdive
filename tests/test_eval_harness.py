"""Evaluation harness orchestration. Model replies are SCRIPTED FIXTURES: these tests prove record keeping, separation of outcomes, paired
comparison on frozen drafts, budget handling and guards. Any number produced here is a harness check, never project performance."""

import json
import re

import pytest

from backend import db
from backend.budget import Budget
from backend.config import Settings
from backend.llm import LLM
from evaluation import datasets, e2e_eval, metrics, verify_eval
from evaluation.schemas import prompt_hashes, settings_snapshot, write_schemas
from tests.fakes import OK_ASPECTS, FakeMesh
from tests.test_phase4_loop import happy_mesh, run_complete, search, world  # noqa: F401 (world is a fixture)

S = Settings(mesh_api_key="", planner_model="fx-planner", researcher_model="fx-researcher", drafter_model="fx-drafter", verifier_model="fx-verifier",
             passage_target_chars=300, passage_max_chars=1500)


class PromptDrivenMesh(FakeMesh):
    """FIXTURE verifier: answers by claim text from a script ({claim text: verdict}); cites the first passage id it was shown."""

    def __init__(self, script, default="supported", **kw):
        super().__init__(**kw)
        self.script, self.default = script, default

    def _create(self, **kwargs):
        from types import SimpleNamespace

        self.calls.append(kwargs)
        user = kwargs["messages"][1]["content"]
        claim = re.search(r"<claim>(.*?)</claim>", user, re.DOTALL).group(1)
        verdict = self.script.get(claim, self.default)
        if verdict == "INVALID":
            text = "not json"
        else:
            used = re.findall(r'<evidence passage_id="([^"]+)"', user)[:1]
            text = json.dumps({"verdict": verdict, "explanation": "fixture", "unsupported_details": [], "conflicts": [], "evidence_used": used,
                               "aspects": OK_ASPECTS})
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
                               usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120))


def examples(n=6):
    return datasets.claim_examples(available_only=True)[:n]


def test_frozen_run_records_every_example_with_separated_outcomes(tmp_path):
    ex = examples(4)
    broken = json.loads(json.dumps(ex[3]))
    broken["evidence"][0]["quote"] = "this quote does not occur in the cited passage at all"
    ex[3] = broken
    mesh = PromptDrivenMesh({ex[0]["claim"]: "supported", ex[1]["claim"]: "unsupported", ex[2]["claim"]: "INVALID"})
    records, errors = verify_eval.run_frozen(ex, LLM(mesh, S), S, labels={}, db_path=tmp_path / "f.db")
    assert [r["id"] for r in records] == [e["id"] for e in ex]  # a record for EVERY example, in order
    assert [r["outcome"] for r in records] == ["supported", "unsupported", "model_error", "integrity_failed"]
    assert records[3]["model_calls"] == 0 and "does not occur" in records[3]["integrity_reason"]  # integrity failures never reach the model
    assert records[2]["model_calls"] == 2 and records[2]["failed_calls"] == 2  # an invalid reply is retried once, both attempts counted
    assert records[0]["prompt_tokens"] == 100 and records[0]["completion_tokens"] == 20 and records[0]["latency_seconds"] is not None
    assert any("model_error" in e for e in errors) and any("integrity_failed" in e for e in errors)
    assert all(r["gold"] is None for r in records)  # no human label: never filled from anything else


def test_the_frozen_claim_and_evidence_reach_the_verifier_unchanged(tmp_path):
    ex = examples(1)
    mesh = PromptDrivenMesh({})
    verify_eval.run_frozen(ex, LLM(mesh, S), S, labels={}, db_path=tmp_path / "f.db")
    prompt = mesh.calls[0]["messages"][1]["content"]
    assert f"<claim>{ex[0]['claim']}</claim>" in prompt and ex[0]["evidence"][0]["passage_text"] in prompt
    assert ex[0]["subquestion"] not in prompt and "<subquestion>" not in prompt  # the verifier sees the claim and its evidence only (attribution-v5)
    assert "proposed" not in prompt.lower() and ex[0]["proposed"]["rationale"] not in prompt  # the provisional label never reaches the model


def test_paired_comparison_reuses_the_same_drafts_and_isolates_the_verifier(tmp_path):
    ex = datasets.claim_examples(available_only=True)[::5]  # CE01, 06, 11, ... : every label is present
    gold = {e["id"]: e["proposed"]["label"] for e in ex}  # FIXTURE gold: stands in for human labels inside this test only
    lenient = PromptDrivenMesh({}, default="supported")
    strict = PromptDrivenMesh({e["claim"]: ("supported" if gold[e["id"]] == "supported" else "unsupported") for e in ex})
    a, _ = verify_eval.run_frozen(ex, LLM(lenient, S), S, labels=gold, db_path=tmp_path / "a.db")
    b, _ = verify_eval.run_frozen(ex, LLM(strict, S), S, labels=gold, db_path=tmp_path / "b.db")
    assert [r["id"] for r in a] == [r["id"] for r in b]  # identical claim set in both arms
    for x, y in zip(a, b):
        assert lenient.calls and x["id"] == y["id"]
    assert {c["messages"][1]["content"] for c in lenient.calls} == {c["messages"][1]["content"] for c in strict.calls}  # byte-identical inputs
    pa, pb = metrics.paired_filter_comparison(a), metrics.paired_filter_comparison(b)
    assert pa["same_claims"] == pb["same_claims"] == len(ex)  # 8 on the full dataset, 5 on the public export's available subset
    assert pa["precision_after_filter"]["value"] == pa["precision_before_filter"]["value"]  # an all-yes verifier changes nothing
    assert pb["precision_after_filter"]["value"] == 1.0 and pb["supported_claims_retained"]["value"] == 1.0
    assert pb["not_fully_supported_in_output_after"] == 0 < pa["not_fully_supported_in_output_after"]


def test_budget_stop_leaves_the_rest_not_judged_and_visible(tmp_path):
    ex = examples(5)
    budget = Budget(S.model_copy(update={"max_model_requests": 2}))
    records, errors = verify_eval.run_frozen(ex, LLM(PromptDrivenMesh({}), S), S, labels={}, db_path=tmp_path / "f.db", budget=budget)
    assert [r["outcome"] for r in records] == ["supported", "supported", "not_judged", "not_judged", "not_judged"]
    assert "budget stop" in records[2]["semantic_reason"] and "earlier example hit the budget" in records[3]["semantic_reason"]
    assert len(errors) == 3
    assert metrics.verification_metrics([{**r, "gold": "supported"} for r in records])["counts"]["not_judged"] == 3


def test_result_without_human_labels_is_na_and_says_so(tmp_path, empty_label_store):
    ex = examples(3)
    records, errors = verify_eval.run_frozen(ex, LLM(PromptDrivenMesh({}), S), S, db_path=tmp_path / "f.db")  # labels from the shipped (empty) store
    res = verify_eval.build_result(records, errors, S, split="dev", executed=False, fixture=True, budget_limits={"max_model_requests": 20})
    assert res.metadata.label_basis == "none" and res.metadata.fixture is True and res.metadata.executed is False
    assert res.metrics["supported_precision"]["status"] == "N/A" and res.metrics["counts"]["labelled"] == 0
    assert "PROVISIONAL" in res.agreement_with_provisional["label_basis"]
    assert any("N/A" in n for n in res.metadata.notes)
    assert res.usage["prompt_tokens"]["total"] == 300 and res.usage["provider_reported_cost_usd"]["status"] == "Unavailable"
    json.loads(res.model_dump_json())  # serialisable


def test_metadata_records_what_is_needed_to_reproduce_and_no_secrets():
    secret = Settings(mesh_api_key="sk-should-not-appear", tavily_api_key="tvly-should-not-appear", verifier_model="fx-verifier")
    snap = settings_snapshot(secret)
    assert "sk-should-not-appear" not in json.dumps(snap) and "tvly-should-not-appear" not in json.dumps(snap)
    assert not any(k for k in snap if "key" in k.lower()) and snap["max_model_requests"] == secret.max_model_requests
    from evaluation.schemas import run_metadata

    meta = run_metadata("verification", secret, split="dev", budget={"max_model_requests": 60}, run_ids=[1, 2], executed=False, fixture=True, label_basis="none")
    assert meta.models["verifier"] == "fx-verifier" and meta.rubric == "attribution-v5" and meta.run_ids == [1, 2]
    assert set(meta.prompt_sha256) == {"planner", "relevance", "drafter", "verifier", "obligations", "coverage", "reviser", "synthesizer"}
    assert set(meta.dataset_sha256) == {"questions.json", "claim_evidence.json", "split.lock.json"} and meta.budget == {"max_model_requests": 60}
    assert prompt_hashes() == prompt_hashes()  # deterministic


def test_result_schemas_can_be_written(tmp_path):
    paths = write_schemas(tmp_path)
    assert {p.name for p in paths} == {"verification_result.schema.json", "e2e_result.schema.json"}
    assert "metadata" in json.loads(paths[0].read_text())["properties"]


def test_live_commands_are_dry_runs_unless_confirmed(tmp_path, empty_label_store):
    plan = verify_eval.plan("dev", S)
    assert plan["examples"] == 30 and plan["with_authoritative_label"] == 0 and plan["expected_model_requests"]["worst_case"] == 60
    assert verify_eval.plan("all", S)["examples"] == 40
    with pytest.raises(SystemExit, match="refusing to run live"):
        e2e_eval.execute("dev", S, tmp_path / "e.db", tmp_path / "e.json", confirm=False)
    p = e2e_eval.plan("dev", S)
    assert len(p["questions"]) == 8 and p["total_worst_case"]["model_requests"] == 8 * S.max_model_requests


# --- end-to-end collection, on a run made by the scripted loop (fixture) ---------------------------------------------------------------


@pytest.fixture
def finished(client, complete_env, world):  # noqa: F811
    r, _ids = world
    mesh = happy_mesh(r)
    assert run_complete(client, complete_env, mesh, search()).status_code == 202
    conn = db.connect(client.db_path)
    yield conn, mesh
    conn.close()


def test_collect_outcome_from_a_finished_run(finished):
    conn, mesh = finished
    o = e2e_eval.collect_outcome(conn, 1, {"id": "QFIX", "split": "dev"})
    assert o["report_outcome"] == "claims_all_requirements_addressed" and o["report_claims"] == 5 and o["errors"] == []
    # initial drafts = the first drafting (claims 1-4); repairs and corrective candidates are not initial
    assert [r["claim_id"] for r in o["initial"]] == [1, 2, 3, 4]
    assert [r["outcome"] for r in o["initial"]] == ["supported", "integrity_failed", "partially_supported", "contradicted"]
    assert all(r["gold"] is None for r in o["initial"] + o["final"])
    final = {r["claim_id"]: r for r in o["final"]}
    assert set(final) == {1, 6, 8, 7, 9} and final[6]["lineage"] == 2 and final[7]["lineage"] == 3  # repairs keep their lineage
    assert [final[i]["from_initial"] for i in (1, 6, 7, 8, 9)] == [True, True, True, False, False]
    # citation re-check from stored data, not from the stored verdicts
    assert o["citations"] and all(c["passage_exists"] and c["quote_found"] and c["url_matches"] and c["text_matches"] for c in o["citations"])
    assert metrics.citation_integrity(o["citations"])["valid"]["value"] == 1.0
    assert o["system_coverage"] and {"requirement_key", "status", "gap"} <= set(o["system_coverage"][0])
    u = o["usage"]
    assert u["model_calls"] == len(mesh.calls) and u["prompt_tokens"] == 100 * len(mesh.calls) and u["completion_tokens"] == 20 * len(mesh.calls)
    assert u["calculated_cost_usd"] is None and u["provider_reported_cost_usd"] is None  # no prices given: Unavailable, not zero
    assert o["budget_used_of_limit"]["model_requests"][0] == len(mesh.calls) and o["corrective_rounds"] == 1


def test_citation_recheck_detects_a_corrupted_stored_passage(finished, client):
    conn, _ = finished
    ok = e2e_eval.collect_outcome(conn, 1)
    pid = ok["citations"][0]["passage_id"]
    raw = db.connect(client.db_path)
    raw.execute("DROP TRIGGER IF EXISTS passages_no_update")  # a test-only way to simulate a damaged database
    triggers = [r[0] for r in raw.execute("SELECT name FROM sqlite_master WHERE type='trigger' AND tbl_name='passages'")]
    for t in triggers:
        raw.execute(f"DROP TRIGGER {t}")
    raw.execute("UPDATE passages SET text = 'damaged text' WHERE id = ?", (pid,))
    broken = e2e_eval.collect_outcome(raw, 1)
    assert not all(c["quote_found"] for c in broken["citations"] if c["passage_id"] == pid)
    r = metrics.citation_integrity(broken["citations"])
    assert r["failures"]["quote_found"] >= 1 and r["failures"]["passage_exists"] == 0  # a missing quote is not a missing passage
    raw.close()


def test_result_for_a_collected_run_has_no_correctness_metrics_without_human_labels(finished):
    conn, _ = finished
    o = e2e_eval.collect_outcome(conn, 1, {"id": "Q02", "split": "dev"})
    res = e2e_eval.build_result([o], S, split="dev", executed=False, fixture=True)
    mm = res.metrics
    assert mm["completeness"]["fully_addressed"]["status"] == "N/A" and mm["completeness"]["unlabelled_excluded"] == 4
    assert mm["initial_vs_final"]["final_report_precision"]["status"] == "N/A"
    assert (mm["initial_vs_final"]["lineage_retention"]["numerator"], mm["initial_vs_final"]["lineage_retention"]["denominator"]) == (3, 4)
    assert mm["citation_integrity"]["valid"]["value"] == 1.0 and mm["reports_stored"]["value"] == 1.0
    assert mm["abstention_and_retention"]["answerable_with_no_report_claims"]["numerator"] == 0
    assert res.metadata.label_basis == "none" and res.metadata.fixture and not res.metadata.executed


def test_the_same_initial_drafts_can_be_reverified_by_another_configuration(finished, tmp_path):
    conn, _ = finished
    drafts = e2e_eval.frozen_drafts(conn, 1, "QFIX")
    assert [d["id"] for d in drafts] == ["QFIX-C1", "QFIX-C2", "QFIX-C3", "QFIX-C4"]
    assert drafts[0]["evidence"][0]["passage_text"] and drafts[0]["tags"] == ["frozen_initial_draft"]
    records, _ = verify_eval.run_frozen(drafts, LLM(PromptDrivenMesh({}), S), S, labels={}, db_path=tmp_path / "re.db")
    assert [r["outcome"] for r in records][1] == "integrity_failed"  # claim 2's quote was never in its passage: same drafts, same finding
    assert len(records) == 4


def test_zero_reports_and_zero_citations_are_na_not_perfect():
    r = metrics.citation_integrity([])
    assert r["valid"]["status"] == "N/A"
    res = e2e_eval.build_result([], S, split="dev", executed=False, fixture=True)
    assert res.metrics["reports_stored"]["status"] == "N/A" and res.metrics["questions"] == 0
