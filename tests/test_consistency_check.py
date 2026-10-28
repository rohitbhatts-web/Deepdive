"""Cross-claim consistency check: OFFLINE tests with scripted replies and recorded claim texts.

Every claim and passage below is a SYNTHETIC fixture written for these tests. The two rebase claims ("r1" and "r6") mirror the shape of a pair observed in a live run:
each is faithfully supported by its own quote, yet they disagree (one new commit per original commit versus one combined commit). These tests prove bookkeeping,
validation, reuse, budgets and report behaviour. They say NOTHING about whether a real model would detect a real conflict: scripted replies do not measure detection
accuracy.
"""

import json
import sqlite3

import pytest

from backend import claims, db
from backend.agents import consistency
from backend.agents.schemas import SynthesisPlan
from backend.budget import Budget
from backend.llm import LLM
from backend.report import build_report, get_report
from tests.fakes import FakeMesh
from tests.loop_helpers import add, verdict

REBASE_1 = "Git rebase replays every original commit as its own new commit on the target branch."  # synthetic; conflicts with REBASE_6
REBASE_6 = "Git rebase folds all of the work into one combined commit on the target branch."  # synthetic; conflicts with REBASE_1
PRIVATE = "Rebasing is best suited to private feature branches that only one developer uses."
SHARED = "Rebasing a branch that other developers already use can overwrite their work."
MERGE = "A merge keeps the original commits and adds one merge commit."
QUOTES = {
    "r1": "Git rebase replays every original commit as its own new commit on the target branch.",
    "r6": "Git rebase folds all of the work into one combined commit on the target branch.",
    "private": "Rebasing is best suited to private feature branches that only one developer uses.",
    "shared": "Rebasing a branch that other developers already use can overwrite their work.",
    "merge": "A merge keeps the original commits and adds one merge commit.",
}
ORDER = ["r1", "r6", "private", "shared", "merge"]
TEXT = {"r1": REBASE_1, "r6": REBASE_6, "private": PRIVATE, "shared": SHARED, "merge": MERGE}
SECTION = {"r1": "a", "r6": "b", "private": "b", "shared": "b", "merge": "a"}  # claims 1 and 6 sit in DIFFERENT sections


def make_world(conn, settings, keys=ORDER):
    run = db.create_run(conn, "How do merge and rebase differ?")["id"]
    sqs = {"a": db.add_subquestion(conn, run, "How does each affect commit history?", ["effect on history"]),
           "b": db.add_subquestion(conn, run, "What does rebase do to the target branch?", ["effect on the target branch"])}
    texts = [QUOTES[k] + " (fixture passage)" for k in ORDER]
    snapshot, spans, pos = "\n\n".join(texts), [], 0
    for t in texts:
        spans.append((pos, pos + len(t)))
        pos += len(t) + 2
    with db.tx(conn):
        sid = db.insert_source(conn, run, requested_url="https://example.test/g", final_url="https://example.test/g", title="Guide", content_type="text/html",
                               content_hash="h1", snapshot=snapshot, passages=spans)
        for sq in sqs.values():
            db.add_association(conn, run, sq, sid, "selected_from_search", explanation="fixture")
        db.set_run_status(conn, run, "collection_finished")
    p = lambda i: f"S{sid}-P{i}"
    r = {"run": run, **sqs}
    ids = {}
    for k in keys:
        i = ORDER.index(k)
        ids[k] = add(conn, r, SECTION[k], TEXT[k], p(i), QUOTES[k], settings)
        verdict(conn, ids[k], "supported", passages=[(p(i), "claim_evidence")])
    with db.tx(conn):
        db.set_verify_status(conn, run, "completed")
    return {"run": run, "sq": sqs, "ids": ids, "p": p, "pid": {k: p(ORDER.index(k)) for k in ORDER}}


def conflict_finding(w, a="r1", b="r6", **kw):
    return {"claim_ids": [w["ids"][a], w["ids"][b]], "kind": "conflict", "explanation": "One says a new commit per original commit, the other says one combined commit.",
            "evidence": [{"claim_id": w["ids"][a], "passage_id": w["pid"][a]}, {"claim_id": w["ids"][b], "passage_id": w["pid"][b]}], **kw}


def reply(w, findings=(), reviewed=None):
    return {"claims_reviewed": sorted(w["ids"].values()) if reviewed is None else reviewed, "findings": list(findings)}


def check(conn, settings, w, mesh, budget=None):
    budget = budget or Budget(settings)
    return consistency.ensure_check(conn, LLM(mesh, settings), budget, settings, w["run"]), budget


def report(conn, settings, w, plan=None):
    info = build_report(conn, w["run"], plan=plan, ordering_reason="test", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0, settings=settings)
    return get_report(conn, w["run"], info["report_id"])


@pytest.fixture
def w(conn, settings):
    return make_world(conn, settings)


def all_items(rep):
    return [i for s in rep["sections"] for i in s["claims"] + s["context"]]


# --- the observed pair, and differences that are not conflicts -------------------------------------------------------------------------------------------

def test_the_observed_rebase_pair_is_flagged_across_sections_and_both_claims_are_kept(conn, settings, w):
    before = {k: claims.get_claim(conn, w["run"], i) for k, i in w["ids"].items()}
    mesh = FakeMesh(consistency=reply(w, [conflict_finding(w)]))
    chk, _ = check(conn, settings, w, mesh)
    assert chk["status"] == "reviewed" and [f["kind"] for f in chk["findings"]] == ["conflict"]
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "reviewed" and c["has_conflicts"] is True and c["check_id"] == chk["id"]
    labelled = {i["claim_id"]: i["conflicts"] for i in all_items(rep) if i.get("conflicts")}
    assert labelled == {w["ids"]["r1"]: [{"finding_id": c["findings"][0]["id"], "with_claim_ids": [w["ids"]["r6"]]}],
                        w["ids"]["r6"]: [{"finding_id": c["findings"][0]["id"], "with_claim_ids": [w["ids"]["r1"]]}]}
    sections = {s["subquestion_id"]: {i["claim_id"] for i in s["claims"]} for s in rep["sections"]}
    assert w["ids"]["r1"] in sections[w["sq"]["a"]] and w["ids"]["r6"] in sections[w["sq"]["b"]]  # cross-section: each stays in its own
    assert "POSSIBLE CONFLICT with claim" in rep["markdown"] and "does not say which claim, if either, is wrong" in rep["markdown"]
    # support is a separate question: nothing about the claims or their verdicts changed
    after = {k: claims.get_claim(conn, w["run"], i) for k, i in w["ids"].items()}
    for k in ORDER:
        assert (after[k]["semantic_verdict"], after[k]["integrity_verdict"], after[k]["eligible_for_report"], after[k]["text"], after[k]["version"]) == \
               (before[k]["semantic_verdict"], before[k]["integrity_verdict"], before[k]["eligible_for_report"], before[k]["text"], before[k]["version"])
    assert all(i["text"] == TEXT[k] for k in ORDER for i in all_items(rep) if i["claim_id"] == w["ids"][k])


def test_compatible_claims_with_different_conditions_are_recorded_but_not_flagged(conn, settings, w):
    diff = {"claim_ids": [w["ids"]["private"], w["ids"]["shared"]], "kind": "condition", "explanation": "Different branches: private versus shared.", "evidence": []}
    check(conn, settings, w, FakeMesh(consistency=reply(w, [diff])))
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "reviewed" and c["has_conflicts"] is False and [f["kind"] for f in c["findings"]] == ["condition"]
    assert not any(i.get("conflicts") for i in all_items(rep))
    assert "POSSIBLE CONFLICT" not in rep["markdown"] and "not to be conflicts" in rep["markdown"]
    assert "does not show that the claims are consistent" in rep["markdown"]  # 'no conflicts flagged' is never worded as 'consistent'


# --- references are validated against what the model was actually shown ---------------------------------------------------------------------------------

def test_unknown_unseen_claims_and_unshown_evidence_are_dropped_and_make_the_check_partial(conn, settings, w):
    bad = {"claim_ids": [w["ids"]["r1"], 999_999], "kind": "conflict", "explanation": "refers to a claim that does not exist", "evidence": []}
    wrong_evidence = conflict_finding(w, evidence=[{"claim_id": w["ids"]["r1"], "passage_id": w["pid"]["r6"]}, {"claim_id": w["ids"]["r6"], "passage_id": w["pid"]["r6"]}])
    chk, _ = check(conn, settings, w, FakeMesh(consistency=reply(w, [bad, wrong_evidence], reviewed=[*sorted(w["ids"].values()), 424_242])))
    assert chk["status"] == "partial" and chk["cause"] == "model"
    assert [f["claim_ids"] for f in chk["findings"]] == [sorted([w["ids"]["r1"], w["ids"]["r6"]])]  # the valid finding survives
    assert [e["passage_id"] for e in chk["findings"][0]["evidence"]] == [w["pid"]["r6"]]  # claim 1's reference to claim 6's passage was not shown FOR claim 1
    items = chk["dropped_findings"]
    assert any(d["item"] == "claims_reviewed" and d["claim_id"] == 424_242 for d in items)  # an ID the model was never shown, listed as reviewed
    assert any(d["item"] == "finding" and 999_999 in d["claim_ids"] and "not shown to the model" in d["reason"] for d in items)
    assert any(d["item"] == "evidence" and d["claim_id"] == w["ids"]["r1"] and d["passage_id"] == w["pid"]["r6"] for d in items)


def test_a_partial_check_still_shows_its_known_conflict_and_names_what_was_not_compared(conn, settings, w):
    listed = [w["ids"][k] for k in ("r1", "r6", "private")]
    chk, _ = check(conn, settings, w, FakeMesh(consistency=reply(w, [conflict_finding(w)], reviewed=listed)))
    assert chk["status"] == "partial" and chk["unchecked_claim_ids"] == sorted([w["ids"]["shared"], w["ids"]["merge"]])
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "partial" and c["has_conflicts"] is True and c["unchecked_claim_ids"] == chk["unchecked_claim_ids"]
    assert any(i.get("conflicts") for i in all_items(rep))
    assert "POSSIBLE CONFLICT" in rep["markdown"] and "Not compared" in rep["markdown"]  # both are said: the limitation never hides the conflict


@pytest.mark.parametrize("bad_reply", ["not json at all", {"findings": []}, {"claims_reviewed": "all", "findings": []}])
def test_a_malformed_reply_is_failed_with_one_request_and_no_retry(conn, settings, w, bad_reply):
    mesh = FakeMesh(consistency=bad_reply)
    chk, _ = check(conn, settings, w, mesh)
    assert chk["status"] == "failed" and chk["findings"] == [] and len(mesh.calls) == 1  # no corrective retry
    rep = report(conn, settings, w)
    assert rep["consistency"]["status"] == "failed" and rep["consistency"]["has_conflicts"] is False
    assert "Status: failed" in rep["markdown"] and "no conflicts" not in rep["markdown"].lower().replace("possible conflicts", "")


def test_an_empty_claims_reviewed_list_is_not_a_clean_bill(conn, settings, w):
    chk, _ = check(conn, settings, w, FakeMesh(consistency=reply(w, [], reviewed=[])))
    assert chk["status"] == "partial" and sorted(chk["unchecked_claim_ids"]) == sorted(w["ids"].values())


# --- the input: packing, shortened evidence, unseen claims -----------------------------------------------------------------------------------------------

def test_shortened_and_id_only_evidence_is_recorded_and_an_id_only_reference_is_not_called_evidence_text(conn, settings, w):
    el = claims.eligible_claims(conn, w["run"])
    sizes = [len("\n".join(consistency._block(c, lim) for c in sorted(el, key=lambda c: c["id"]))) for lim in consistency.QUOTE_TIERS]
    s = settings.model_copy(update={"coverage_input_chars": sizes[-1] + 5})  # only the ID-only form fits
    inp = consistency.build_input(el, s)
    assert inp.unseen_ids == [] and all(v["state"] == "id_only" for v in inp.claim_states.values()) and inp.text_pairs == set()
    assert "(fixture passage)" not in inp.user and all(t in inp.user for t in TEXT.values())  # claim text and passage IDs kept, quote text dropped
    chk, _ = check(conn, s, w, FakeMesh(consistency=reply(w, [conflict_finding(w)])))
    assert chk["status"] == "reviewed" and all(e["text_shown"] is False for e in chk["findings"][0]["evidence"])
    rep = report(conn, s, w)
    assert "passage ID only, its text was not shown to the model" in rep["markdown"] and "id_only" in set(rep["consistency"]["limited_evidence"].values())
    mid = settings.model_copy(update={"coverage_input_chars": sizes[2] + 5})  # the 60-character tier: every fixture quote is longer, so quotes are shortened
    states = {v["state"] for v in consistency.build_input(el, mid).claim_states.values()}
    assert states <= {"full", "shortened"} and "shortened" in states


def test_claims_that_do_not_fit_are_unseen_never_reviewed_and_the_partial_check_is_reused_not_repeated(conn, settings, w):
    el = claims.eligible_claims(conn, w["run"])
    two = len("\n".join(consistency._block(c, 0) for c in sorted(el, key=lambda c: c["id"])[:2]))
    s = settings.model_copy(update={"coverage_input_chars": two + 3})
    shown = sorted(w["ids"].values())[:2]
    mesh = FakeMesh(consistency=reply(w, [conflict_finding(w)], reviewed=shown))
    chk, _ = check(conn, s, w, mesh)
    assert chk["status"] == "partial" and chk["cause"] == "packing" and chk["shown_claim_ids"] == shown
    assert set(chk["unchecked_claim_ids"]) == set(w["ids"].values()) - set(shown)
    assert all(chk["claim_states"][str(i)]["state"] == "not_shown" for i in chk["unchecked_claim_ids"])
    again, _ = check(conn, s, w, mesh, Budget(s))  # a NEW job, identical input: the same input is cut the same way, so it is reused with its status
    assert again["id"] == chk["id"] and len(mesh.calls) == 1 and again["status"] == "partial"


# --- fingerprints and reuse -------------------------------------------------------------------------------------------------------------------------------

def test_the_fingerprint_follows_the_actual_input_not_just_database_ids(conn, settings, w):
    el = claims.eligible_claims(conn, w["run"])
    base = consistency.build_input(el, settings).fingerprint
    assert consistency.build_input(list(reversed(el)), settings).fingerprint == base  # order of arrival is irrelevant
    edited = [{**c, "text": c["text"] + " Always."} if c["id"] == w["ids"]["r6"] else c for c in el]
    assert consistency.build_input(edited, settings).fingerprint != base  # same id, same version, different text
    quote = [{**c, "evidence": [{**e, "quote": e["quote"][:-8]} for e in c["evidence"]]} if c["id"] == w["ids"]["r1"] else c for c in el]
    assert consistency.build_input(quote, settings).fingerprint != base  # selected evidence
    passage = [{**c, "evidence": [{**e, "passage": {**e["passage"], "text": e["passage"]["text"] + " changed"}} for e in c["evidence"]]} if c["id"] == w["ids"]["r1"] else c for c in el]
    assert consistency.build_input(passage, settings).fingerprint != base  # the underlying passage, even if only its ID is shown
    ineligible = [{**c, "semantic_verdict": "partially_supported"} if c["id"] == w["ids"]["r1"] else c for c in el]
    assert consistency.build_input(ineligible, settings).fingerprint != base  # eligibility state
    assert consistency.build_input(el, settings.model_copy(update={"verifier_model": "another-model"})).fingerprint != base  # model
    assert consistency.build_input(el, settings.model_copy(update={"coverage_input_chars": 9000})).fingerprint != base  # packing configuration


def test_a_matching_assessment_is_reused_with_no_second_call_and_a_stale_one_is_never_attached(conn, settings, w):
    mesh = FakeMesh(consistency=reply(w, [conflict_finding(w)]))
    first, _ = check(conn, settings, w, mesh)
    second, _ = check(conn, settings, w, mesh, Budget(settings))  # another job, same input, reviewed: no request
    assert second["id"] == first["id"] and len(mesh.calls) == 1
    # a report rebuilt with NO budget attaches exactly the matching assessment
    assert report(conn, settings, w)["consistency"]["check_id"] == first["id"]
    # the claim set changes: a new eligible claim appears
    extra = add(conn, {"run": w["run"], "a": w["sq"]["a"]}, "a", "A merge never rewrites history.", w["pid"]["merge"], QUOTES["merge"], settings)
    verdict(conn, extra, "supported", passages=[(w["pid"]["merge"], "claim_evidence")])
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "not_checked" and c["findings"] == [] and c["has_conflicts"] is False and c["check_id"] is None
    assert "different input" in c["reason"] and "not used" in c["reason"]
    assert not any(i.get("conflicts") for i in all_items(rep))  # the old conflict is not attached to the new set
    assert db_count(conn, "consistency_checks") == 1  # history kept, nothing rewritten


def db_count(conn, table):
    return conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]


def test_one_attempt_per_fingerprint_per_job_and_a_new_job_may_try_once_more_keeping_history(conn, settings, w):
    mesh = FakeMesh(consistency=[RuntimeError("down"), reply(w, [conflict_finding(w)])])
    budget = Budget(settings)
    first, _ = check(conn, settings, w, mesh, budget)
    assert first["status"] == "failed" and first["attempt"] == 1
    again, _ = check(conn, settings, w, mesh, budget)  # same job: no loop
    assert again["id"] == first["id"] and len(mesh.calls) == 1
    newer, _ = check(conn, settings, w, mesh, Budget(settings))  # a new job (an explicit user action): one more attempt
    assert newer["status"] == "reviewed" and newer["attempt"] == 2 and len(mesh.calls) == 2
    assert [r[0] for r in conn.execute("SELECT status FROM consistency_checks ORDER BY id")] == ["failed", "reviewed"]  # immutable history
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("UPDATE consistency_checks SET status = 'reviewed' WHERE id = ?", (first["id"],))
    with pytest.raises(sqlite3.IntegrityError):
        conn.execute("DELETE FROM consistency_findings")


# --- budgets: requests, time, output tokens ----------------------------------------------------------------------------------------------------------

@pytest.mark.parametrize("left,called", [(0, False), (1, True), (2, True)])
def test_requests_remaining_decide_whether_the_check_runs(conn, settings, w, left, called):
    budget = Budget(settings)
    budget.model_requests = settings.max_model_requests - left
    mesh = FakeMesh(consistency=reply(w, [conflict_finding(w)]))
    chk, _ = check(conn, settings, w, mesh, budget)
    assert len(mesh.calls) == (1 if called else 0)
    if called:
        assert chk["status"] == "reviewed" and budget.model_requests == settings.max_model_requests - left + 1
    else:
        assert chk["status"] == "not_checked" and "max_model_requests reached" in chk["reason"] and chk["findings"] == []
        rep = report(conn, settings, w)  # the report is still built, with the claims and an explicit 'not checked'
        assert rep["consistency"]["status"] in ("not_checked",) and len(all_items(rep)) == 5


def test_insufficient_time_skips_the_check_without_sending_anything(conn, settings, w):
    s = settings.model_copy(update={"max_elapsed_seconds": 300.0, "min_call_seconds": 10.0})
    now = [0.0]
    budget = Budget(s, clock=lambda: now[0])
    now[0] = 295.0  # 5 s left, 10 needed
    mesh = FakeMesh(consistency=reply(w))
    chk, _ = check(conn, s, w, mesh, budget)
    assert chk["status"] == "not_checked" and "insufficient time" in chk["reason"] and mesh.calls == [] and budget.model_requests == 0


def test_the_output_allowance_is_bounded_by_the_remaining_token_budget_and_too_little_skips_the_check(conn, settings, w):
    mesh = FakeMesh(consistency=reply(w))
    budget = Budget(settings)
    budget.output_tokens = settings.max_total_output_tokens - 1100  # fewer than the per-call cap (1500) remain, more than the 1,000 minimum
    check(conn, settings, w, mesh, budget)
    assert mesh.calls[-1]["max_tokens"] == 1100  # requested allowance = what is left, never more
    w2 = make_world(conn, settings)
    roomy = FakeMesh(consistency=reply(w2))
    chk, _ = check(conn, settings, w2, roomy, Budget(settings))
    assert roomy.calls[-1]["max_tokens"] == settings.max_output_tokens and chk["status"] in ("reviewed", "partial")
    w3 = make_world(conn, settings)
    low = Budget(settings)
    low.output_tokens = settings.max_total_output_tokens - (consistency.MIN_OUTPUT_TOKENS - 1)
    skipped = FakeMesh(consistency=reply(w3))
    chk, _ = check(conn, settings, w3, skipped, low)
    assert chk["status"] == "not_checked" and "output tokens" in chk["reason"] and skipped.calls == []
    assert settings.max_total_output_tokens == 15000 and settings.max_output_tokens == 1500  # the budgets themselves are not changed


def test_a_failed_request_is_charged_at_the_lowered_cap_not_the_global_one(conn, settings, w):
    budget = Budget(settings)
    budget.output_tokens = settings.max_total_output_tokens - 1100
    check(conn, settings, w, FakeMesh(consistency=RuntimeError("down")), budget)
    assert budget.output_tokens == settings.max_total_output_tokens - 1100 + 1100


def test_fewer_than_two_eligible_claims_means_nothing_to_compare_and_no_request(conn, settings):
    one = make_world(conn, settings, keys=["r1"])
    mesh = FakeMesh(consistency=reply(one))
    assert consistency.ensure_check(conn, LLM(mesh, settings), Budget(settings), settings, one["run"]) is None and mesh.calls == []
    rep = report(conn, settings, one)
    assert rep["consistency"]["status"] == "not_applicable" and "Cross-claim consistency" not in rep["markdown"]


# --- synthesis fallback, history, and older reports ---------------------------------------------------------------------------------------------------

def test_deterministic_fallback_and_the_synthesizer_keep_the_same_claims_and_warnings(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=reply(w, [conflict_finding(w)])))
    plain = report(conn, settings, w, plan=None)
    plan = SynthesisPlan.model_validate({"sections": [{"subquestion_id": w["sq"]["a"], "claim_ids": [w["ids"]["r1"], w["ids"]["merge"]]},
                                                      {"subquestion_id": w["sq"]["b"], "claim_ids": [w["ids"]["shared"], w["ids"]["private"]]}],
                                         "omitted": [{"claim_id": w["ids"]["r6"], "reason": f"duplicates claim {w['ids']['r1']}"}]})
    modelled = report(conn, settings, w, plan=plan)  # the synthesizer tried to drop claim 6 as a 'duplicate' of claim 1

    def view(rep):
        return sorted((i["claim_id"], json.dumps([c["with_claim_ids"] for c in i.get("conflicts", [])])) for i in all_items(rep))  # finding ids differ per report snapshot
    assert view(plain) == view(modelled) and w["ids"]["r6"] in {i["claim_id"] for i in all_items(modelled)}
    strip = lambda f: [{k: v for k, v in x.items() if k != "id"} for x in f]
    assert modelled["consistency"]["has_conflicts"] and strip(plain["consistency"]["findings"]) == strip(modelled["consistency"]["findings"])
    assert not [o for o in modelled["omissions"] if o["claim_id"] == w["ids"]["r6"]]


def test_the_synthesizer_is_told_which_claims_are_possible_conflicts(conn, settings, w):
    from backend.agents.synthesizer import plan_synthesis
    mesh = FakeMesh(synth={"sections": [], "omitted": []})
    plan_synthesis(conn, LLM(mesh, settings), Budget(settings), settings, w["run"], conflicts=[conflict_finding(w)])
    sent = mesh.calls[0]["messages"]
    assert "<possible_conflict" in sent[1]["content"] and "NOT duplicates" in sent[0]["content"]
    quiet = FakeMesh(synth={"sections": [], "omitted": []})
    plan_synthesis(conn, LLM(quiet, settings), Budget(settings), settings, w["run"])
    assert "possible_conflict" not in quiet.calls[0]["messages"][1]["content"] and "NOT duplicates" not in quiet.calls[0]["messages"][0]["content"]


def test_a_report_made_without_settings_or_before_the_tables_existed_reads_as_not_recorded(conn, settings, w):
    info = build_report(conn, w["run"], plan=None, ordering_reason="test", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0)
    rep = get_report(conn, w["run"], info["report_id"])
    assert rep["consistency"] == {"status": "not_recorded", "findings": []} and "Cross-claim" not in rep["markdown"]
    assert not any("conflicts" in i for i in all_items(rep))
    for table in ("report_consistency_findings", "report_consistency"):
        conn.execute(f"DROP TABLE {table}")
    assert get_report(conn, w["run"], info["report_id"])["consistency"] == {"status": "not_recorded", "findings": []}


# --- review round: LLM.call options, truncation, carried findings, immutability, older databases --------------------------------------------------------

def _call(conn, settings, mesh, **kw):
    run = db.create_run(conn, "a question?")["id"]
    budget = Budget(settings)
    out = LLM(mesh, settings).call(conn, run, budget, agent="consistency", model="m", system=consistency.SYSTEM, user="x", schema=consistency.ConsistencyAssessment, **kw)
    return out, budget


def test_existing_callers_keep_one_corrective_retry_and_the_global_output_limit(conn, settings):
    ok = {"claims_reviewed": [], "findings": []}
    mesh = FakeMesh(consistency=["not json", ok])
    _, budget = _call(conn, settings, mesh)  # no new options: exactly as before
    assert len(mesh.calls) == 2 and budget.model_requests == 2  # invalid reply -> one corrective retry
    assert [c["max_tokens"] for c in mesh.calls] == [settings.max_output_tokens] * 2
    failing = FakeMesh(consistency=RuntimeError("down"))
    with pytest.raises(Exception, match="down"):
        _call(conn, settings, failing)
    twice = FakeMesh(consistency=["nope", "still nope"])
    with pytest.raises(Exception, match="after 2 attempts"):
        _call(conn, settings, twice)


def test_the_new_call_options_only_narrow_the_call(conn, settings):
    ok = {"claims_reviewed": [], "findings": []}
    mesh = FakeMesh(consistency=["not json", ok])
    with pytest.raises(Exception, match="after 1 attempt$"):
        _call(conn, settings, mesh, attempts=1)
    assert len(mesh.calls) == 1
    lower = FakeMesh(consistency=ok)
    _call(conn, settings, lower, output_cap=900)
    assert lower.calls[0]["max_tokens"] == 900
    higher = FakeMesh(consistency=ok)
    _call(conn, settings, higher, output_cap=settings.max_output_tokens * 10)  # can never raise the per-call limit
    assert higher.calls[0]["max_tokens"] == settings.max_output_tokens
    for bad in (0, -5):
        with pytest.raises(ValueError):
            _call(conn, settings, FakeMesh(consistency=ok), output_cap=bad)


# --- truncated replies and output-token exhaustion never read as reviewed or as "no conflicts flagged" --------------------------------------------------

def _never_clean(chk, rep):
    assert chk["status"] != "reviewed"
    assert rep["consistency"]["status"] != "reviewed"
    assert "flagged no conflicts" not in rep["markdown"]


def test_a_reply_cut_off_mid_json_is_failed_never_reviewed(conn, settings, w):
    full = json.dumps(reply(w, [conflict_finding(w)]))
    mesh = FakeMesh(consistency=full[: len(full) // 2])
    chk, _ = check(conn, settings, w, mesh)
    assert chk["status"] == "failed" and len(mesh.calls) == 1
    _never_clean(chk, report(conn, settings, w))


def test_a_complete_looking_reply_that_hit_the_output_limit_is_partial_and_keeps_its_conflict(conn, settings):
    for how in ("finish_reason", "tokens"):
        wx = make_world(conn, settings)
        mesh = FakeMesh(consistency=reply(wx, [conflict_finding(wx)]))
        if how == "finish_reason":
            mesh.finish_reasons["consistency"] = "length"
        else:
            mesh.completion_tokens["consistency"] = settings.max_output_tokens  # used the whole allowance
        chk, _ = check(conn, settings, wx, mesh)
        assert chk["status"] == "partial" and "output limit" in chk["reason"] and [f["kind"] for f in chk["findings"]] == ["conflict"]
        rep = report(conn, settings, wx)
        _never_clean(chk, rep)
        assert rep["consistency"]["has_conflicts"] is True  # the known conflict is not hidden by the limitation


def test_exhausting_the_token_allowance_during_the_call_is_partial_and_before_the_call_is_not_checked(conn, settings, w):
    budget = Budget(settings)
    budget.output_tokens = settings.max_total_output_tokens - consistency.MIN_OUTPUT_TOKENS  # exactly the minimum remains
    mesh = FakeMesh(consistency=reply(w))
    mesh.completion_tokens["consistency"] = consistency.MIN_OUTPUT_TOKENS  # the reply used all of it
    chk, _ = check(conn, settings, w, mesh, budget)
    assert mesh.calls[0]["max_tokens"] == consistency.MIN_OUTPUT_TOKENS and chk["status"] == "partial"
    _never_clean(chk, report(conn, settings, w))
    w2 = make_world(conn, settings)
    spent = Budget(settings)
    spent.output_tokens = settings.max_total_output_tokens
    chk2, _ = check(conn, settings, w2, FakeMesh(consistency=reply(w2)), spent)
    assert chk2["status"] == "not_checked"
    _never_clean(chk2, report(conn, settings, w2))


def test_a_partial_check_without_findings_never_says_no_conflicts_were_flagged(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=reply(w, [], reviewed=[w["ids"]["r1"]])))
    md = report(conn, settings, w)["markdown"]
    assert "Status: partial" in md and "flagged no conflicts" not in md and "Not compared" in md


# --- valid findings for the exact input survive a later incomplete attempt; nothing stale is ever attached ------------------------------------------------

def test_a_later_failed_attempt_keeps_the_earlier_valid_conflict_for_the_same_input_and_stays_failed(conn, settings, w):
    mesh = FakeMesh(consistency=[reply(w, [conflict_finding(w)], reviewed=[w["ids"]["r1"]]), RuntimeError("down")])
    first, _ = check(conn, settings, w, mesh)
    assert first["status"] == "partial" and len(first["findings"]) == 1
    second, _ = check(conn, settings, w, mesh, Budget(settings))  # a new job retries the partial check and fails
    assert second["status"] == "failed" and second["id"] != first["id"]
    assert [f["kind"] for f in second["findings"]] == ["conflict"] and second["findings"][0]["carried_from_check"] == first["id"]
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "failed" and c["has_conflicts"] is True and c["findings"][0]["carried_from_check"] == first["id"]
    assert any(i.get("conflicts") for i in all_items(rep)) and "from an earlier attempt on the same input" in rep["markdown"]
    assert db_count(conn, "consistency_findings") == 1  # the carried finding is a view, not a copy: history is not rewritten


def test_a_newer_reviewed_attempt_replaces_earlier_findings_and_a_different_input_carries_nothing(conn, settings, w):
    mesh = FakeMesh(consistency=[reply(w, [conflict_finding(w)], reviewed=[w["ids"]["r1"]]), reply(w, [])])
    check(conn, settings, w, mesh)
    done, _ = check(conn, settings, w, mesh, Budget(settings))
    assert done["status"] == "reviewed" and done["findings"] == []  # the latest complete reading stands alone
    other = make_world(conn, settings)
    skipped = Budget(settings)
    skipped.model_requests = settings.max_model_requests
    chk, _ = check(conn, settings, other, FakeMesh(consistency=reply(other)), skipped)
    assert chk["status"] == "not_checked" and chk["findings"] == []  # another run's / another input's findings are never carried


# --- history and snapshots cannot be overwritten ---------------------------------------------------------------------------------------------------

def test_assessment_history_and_report_snapshots_reject_update_and_delete(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=reply(w, [conflict_finding(w)])))
    report(conn, settings, w)
    for sql in ("UPDATE consistency_checks SET reason = 'changed'", "DELETE FROM consistency_checks", "UPDATE consistency_findings SET kind = 'scope'",
                "DELETE FROM consistency_findings", "UPDATE report_consistency SET status = 'reviewed'", "DELETE FROM report_consistency",
                "UPDATE report_consistency_findings SET kind = 'scope'", "DELETE FROM report_consistency_findings"):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(sql)
    assert db_count(conn, "report_consistency_findings") == 1


# --- older databases -------------------------------------------------------------------------------------------------------------------------------

def test_a_version_10_database_gains_the_tables_on_connect_and_its_old_reports_read_as_before(tmp_path, settings):
    path = tmp_path / "old.db"
    c = db.connect(path)
    wd = make_world(c, settings)
    info = build_report(c, wd["run"], plan=None, ordering_reason="t", model=None, model_call_id=None, stop_reason=None, corrective_rounds=0)
    before = get_report(c, wd["run"], info["report_id"])
    for t in ("report_consistency_findings", "report_consistency", "consistency_findings", "consistency_checks"):
        c.execute(f"DROP TABLE {t}")
    c.execute("PRAGMA user_version = 10")
    c.close()
    ro = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)  # a read-only open of the old file, as for a preserved artifact
    ro.row_factory = sqlite3.Row
    assert get_report(ro, wd["run"], info["report_id"]) == before  # reads fine with no new tables
    ro.close()
    migrated = db.connect(path)
    assert migrated.execute("PRAGMA user_version").fetchone()[0] == db.SCHEMA_VERSION
    assert {r[0] for r in migrated.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE '%consistency%'")} == {
        "consistency_checks", "consistency_findings", "report_consistency", "report_consistency_findings"}
    assert get_report(migrated, wd["run"], info["report_id"]) == before  # the old report is unchanged by the migration
    assert migrated.execute("SELECT count(*) FROM claims").fetchone()[0] == 5
    migrated.close()


# --- the live v2 outcome reaching the reader: a reviewed check with one conflict, one terminology and one scope finding ---------------------------------------------

def _v2_shape(w):
    """The shape of the live v2 assessment (claim IDs mapped onto this fixture): one conflict, one terminology and one scope finding, all reviewed."""
    ids = w["ids"]
    return reply(w, [
        conflict_finding(w, "private", "r6"),
        {"claim_ids": [ids["r1"], ids["r6"]], "kind": "terminology", "explanation": "Different wording for what may be the same operation.", "evidence": []},
        {"claim_ids": [ids["shared"], ids["merge"]], "kind": "scope", "explanation": "Compatible descriptions at different scopes.", "evidence": []},
    ])


def test_a_reviewed_assessment_states_its_assurance_once_in_the_markdown(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=_v2_shape(w)))
    md = report(conn, settings, w)["markdown"]
    assert md.count("the model's own assertion") + md.count("its assertion, not proof") == 1  # not repeated by the stored reason
    assert "Status: reviewed. The model listed every shown claim as reviewed. That is the model's own assertion, not proof that every pair of claims was compared." in md


def test_an_incomplete_assessment_still_says_why_in_the_markdown(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=reply(w, [conflict_finding(w)], reviewed=[w["ids"]["r1"]])))
    md = report(conn, settings, w)["markdown"]
    assert "Status: partial. Only partly checked. Reason: the reply did not list claims" in md and "Not compared" in md


def test_only_the_conflict_is_labelled_and_the_terminology_and_scope_findings_are_listed_apart(conn, settings, w):
    check(conn, settings, w, FakeMesh(consistency=_v2_shape(w)))
    rep = report(conn, settings, w)
    c = rep["consistency"]
    assert c["status"] == "reviewed" and c["has_conflicts"] is True and [f["kind"] for f in c["findings"]] == ["conflict", "terminology", "scope"]
    labelled = {i["claim_id"] for i in all_items(rep) if i.get("conflicts")}
    assert labelled == {w["ids"]["private"], w["ids"]["r6"]}  # the claims of the conflict only; claim 1 (terminology) and the scope pair carry nothing
    md = rep["markdown"]
    assert md.count("POSSIBLE CONFLICT") == len([i for i in all_items(rep) if i.get("conflicts")])
    assert "Differences the model judged not to be conflicts" in md and "(terminology)" in md and "(scope)" in md
    assert md.index("Possible conflicts flagged") < md.index("Differences the model judged")  # the contradiction is listed first and separately
