"""Preparation fixes before any paid evaluation: label rules and the verifier-visible review context, human-only label authority, source overlap,
the revised Q05, the pilot plan and the budget disclosure. Fixtures only; nothing here calls a model, search or the network."""

import csv
import json
import pathlib

import pytest

from backend.config import Settings
from evaluation import datasets, overlap, pilot, review, verifier_view
from evaluation import metrics as m
from evaluation.schemas import budget_differences, run_metadata
from tests.conftest import requires_full_evidence

# --- 1. label rules and the exact verifier-visible context ---------------------------------------------------------------------------------

def test_label_rules_are_written_down_and_every_rule_cited_by_an_example_exists():
    data = datasets.load_claim_dataset()
    assert [r["id"] for r in data["label_rules"]] == [f"R{i}" for i in range(1, 8)]
    text = {r["id"]: r["text"] for r in data["label_rules"]}
    assert "takes precedence over partial support" in text["R3"] and "WITHOUT explicit contradiction" in text["R4"]
    assert "does NOT make the whole claim contradicted" not in text["R4"]  # the superseded v1 wording must not survive in the current rules
    for e in data["examples"]:
        assert e["rule"] is None or e["rule"] in text


def test_every_example_with_an_ambiguity_note_names_the_rule_that_decides_it():
    for e in datasets.load_claim_dataset()["examples"]:
        if e["ambiguity"]:
            assert e["rule"], f"{e['id']}: ambiguity without a deciding rule"
    ids = {e["id"]: e for e in datasets.load_claim_dataset()["examples"]}
    assert {i for i, e in ids.items() if e["ambiguity"]} >= {"CE08", "CE12", "CE19", "CE24", "CE30", "CE27", "CE36"}


def test_precedence_decisions_follow_the_stated_rules():
    ex = {e["id"]: e for e in datasets.load_claim_dataset()["examples"]}
    # Rubric v2: a contradicted part next to a supported part is contradicted. The provisional labels made under v1 still say partially_supported;
    # the development ones are FLAGGED for review (not silently changed) and the held-out one is left exactly as written.
    flags = datasets.load_claim_dataset()["rubric_flags"]
    assert set(flags) == {"CE13", "CE16"} and all(f["v2_would_give"] == "contradicted" for f in flags.values())
    assert ex["CE19"]["proposed"]["label"] == "partially_supported" and ex["CE19"]["rule"] == "R4" and ex["CE19"]["split"] == "heldout" and "CE19" not in flags
    assert ex["CE13"]["proposed"]["label"] == "partially_supported" and ex["CE16"]["proposed"]["label"] == "partially_supported"
    # R5: the central fact established in a weaker form is partially_supported
    assert ex["CE30"]["proposed"]["label"] == "partially_supported" and ex["CE27"]["proposed"]["label"] == "partially_supported"
    # R3: a single assertion explicitly opposed is contradicted; an implication or silence is not
    assert ex["CE36"]["proposed"]["label"] == "contradicted" and ex["CE24"]["proposed"]["label"] == "unsupported"
    assert ex["CE12"]["proposed"]["label"] == "partially_supported"
    # R6: a plan restated as an event is unsupported
    assert ex["CE23"]["proposed"]["label"] == "unsupported"
    for e in ex.values():  # no 'contradicted' example is a bundle with a supported part (that would break R3/R4)
        if e["proposed"]["label"] == "contradicted":
            assert "compound" not in e["tags"], e["id"]


def test_every_label_review_change_is_logged_and_labels_stay_provisional():
    data = datasets.load_claim_dataset()
    log = data["label_review_log"]
    assert any(x["from"] == "unsupported" and x["to"] == "partially_supported" and "largest customer" in x["example_line"] for x in log)
    assert all(e["proposed"]["status"] == "provisional" for e in data["examples"])


@requires_full_evidence
def test_the_review_sheet_shows_exactly_the_text_the_verifier_receives_and_none_is_cut():
    for e in datasets.claim_examples():
        v = verifier_view.view(e)
        assert v["context_passages"] == []  # a frozen example has no neighbouring passages (the sheet says so)
        for c in v["cited"]:
            assert c["quote_visible"] and not c["truncated"], f"{e['id']}: the verifier would not see the decisive text"
            assert c["shown_text"] in v["prompt"]
    ex = review.select(review.BATCH1)
    md = review.render_markdown(ex, "t")
    for e in ex:
        shown = verifier_view.view(e)["cited"][0]["shown_text"]
        assert shown in md  # the sheet's evidence block IS the verifier's, character for character
        assert f"<claim>{e['claim']}</claim>" in verifier_view.view(e)["prompt"]
    assert md.count("nothing is cut") == 12 and "WARNING" not in md
    assert "## Rules that decide ambiguous cases" in md and "**R4 Precedence when parts disagree.**" in md
    assert "Deciding rule: R4." in md and "Why a reviewer might hesitate" in md


# --- 2. AI-suggested labels stay provisional until a person reviews them --------------------------------------------------------------------

def test_the_only_shipped_authoritative_labels_are_the_reviewed_batch_one_development_examples():
    store = json.loads(datasets.HUMAN.read_text(encoding="utf-8"))
    held = set(datasets._read(datasets.LOCK)["lock"]["claim_examples"]["heldout_ids"])
    ids = [e["example_id"] for e in store["claim_labels"]]
    assert sorted(ids) == sorted(review.BATCH1) and not held & set(ids)
    assert all(e["source"] == "human" and datasets.valid_reviewer(e["reviewer"]) and e["version"] == 1 and e["rubric_version"] == "claim-labels-v2"
               and e["provenance"] == datasets.LABEL_PROVENANCE for e in store["claim_labels"])
    assert set(datasets.authoritative_labels()) == set(review.BATCH1)
    assert datasets.authoritative_requirement_labels() == {} and datasets.authoritative_report_claim_labels() == {}
    assert store["requirement_labels"] == [] and store["report_claim_labels"] == []  # end-to-end labels are entered only after the pilot is collected


@pytest.mark.parametrize("name", ["Claude", "claude-sonnet", "GPT-4", "AI reviewer", "language model", "assistant", "Qwen", "auto-labeler", "review bot", ""])
def test_ai_like_reviewer_names_are_rejected(name):
    assert not datasets.valid_reviewer(name)


@pytest.mark.parametrize("name", ["R. Reviewer", "Maria Garcia", "Aidan O'Neil", "Priya"])
def test_ordinary_human_names_are_accepted(name):
    assert datasets.valid_reviewer(name)


@requires_full_evidence
def test_import_needs_explicit_human_confirmation_and_a_human_name(tmp_path):
    ex = review.select(review.BATCH1)
    path = tmp_path / "s.csv"
    review.write_csv(ex, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0].update(human_label="supported", reviewer="Claude")
    rows[1].update(human_label="supported", reviewer="R. Reviewer")
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=review.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    store = tmp_path / "h.json"
    with pytest.raises(SystemExit, match="human confirms"):
        review.import_labels(path, store, ex)
    assert not store.exists()  # nothing was written
    out = review.import_labels(path, store, ex, confirm_human=True)
    assert out["added"] == [rows[1]["id"]] and any("AI or model name" in reason for _id, reason in out["skipped"])


@requires_full_evidence
def test_a_proposed_label_never_reaches_a_metric_or_the_verifier_prompt(empty_label_store):
    for e in datasets.claim_examples():
        prompt = verifier_view.view(e)["prompt"]
        assert e["proposed"]["rationale"] not in prompt and "PROVISIONAL" not in prompt and "Deciding rule" not in prompt
        assert not e["ambiguity"] or e["ambiguity"] not in prompt  # nothing written for human reviewers reaches the model
    assert m.verification_metrics([{"id": e["id"], "gold": datasets.authoritative_labels().get(e["id"]), "outcome": e["proposed"]["label"]}
                                   for e in datasets.claim_examples()])["counts"]["labelled"] == 0


# --- 3. overlap and scope ------------------------------------------------------------------------------------------------------------------

def test_the_dataset_declares_itself_single_domain_not_cross_domain():
    data = datasets.load_claim_dataset()
    assert data["evaluation_scope"] == "single_domain_database_evidence" and "NOT a cross-domain evaluation" in data["domain_limitation"]
    for path in ("evaluation/README.md", "docs/PHASE6A.md"):
        text = pathlib.Path(path).read_text(encoding="utf-8").lower()
        assert "cross-domain" in text and "not a cross-domain" in text.replace("*", "")  # described only as NOT cross-domain


def test_the_overlap_report_matches_the_datasets_and_states_the_consequence():
    report = json.loads(overlap.REPORT.read_text(encoding="utf-8"))
    assert report == json.loads(json.dumps(overlap.compute())), "run `python -m evaluation overlap` after changing the datasets"
    assert report["heldout_examples"] == 10 and report["heldout_with_source_also_in_dev"] == 7 and report["heldout_with_passage_also_in_dev"] == 6
    assert report["sources_retrieved_by_development_runs"]["sources"] == report["sources_retrieved_by_development_runs"]["of_total"] == 6
    assert "not a cross-domain" in report["statement"].lower() and "new CLAIMS" in report["consequence"]
    rows = {r["example"]: r for r in report["per_heldout_example"]}
    assert rows["CE29"]["dev_examples_citing_same_passage"] == ["CE01", "CE16", "CE39", "CE40"] and rows["CE09"]["passage_also_in_dev"] == []


# --- 4. Q05 -------------------------------------------------------------------------------------------------------------------------------

def test_q05_is_evidence_seeking_and_its_revision_is_recorded_honestly():
    q = next(x for x in datasets.load_questions() if x["id"] == "Q05")
    assert q["split"] == "dev" and q["expected_outcome"] == "evidence_seeking"
    assert "no audited" not in q["question"].lower() and "exact net profit" not in q["question"]
    assert "NOT assumed" in q["abstention_expectation"] and "primary" in q["abstention_expectation"]
    rev = q["revisions"][0]
    assert rev["from"]["expected_outcome"] == "abstain_expected" and "unverified" in rev["reason"] and "held-out" in rev["status"]
    assert "Nobody has searched" in rev["status"]
    assert datasets.verify_split_lock() == []  # a development question changed; the held-out lock is untouched
    assert q["id"] not in json.loads(datasets.LOCK.read_text(encoding="utf-8"))["lock"]["questions"]["heldout_ids"]
    assert datasets.validate_questions(datasets.load_questions()) == []


def test_q10_remains_the_only_clear_abstention_question_and_is_heldout():
    qs = {q["id"]: q for q in datasets.load_questions()}
    assert [i for i, q in qs.items() if q["expected_outcome"] == "abstain_expected"] == ["Q10"] and qs["Q10"]["split"] == "heldout"


def test_evidence_seeking_is_scored_by_a_human_and_is_not_counted_as_answerable_or_abstention():
    qs = [{"id": "Q05", "expected": "evidence_seeking", "report_claims": 0, "report_outcome": "insufficient_evidence", "abstention_gold": None, "seeking_gold": None},
          {"id": "Q02", "expected": "answerable", "report_claims": 3, "report_outcome": "claims_with_gaps", "abstention_gold": None}]
    r = m.abstention_and_retention(qs)
    assert r["answerable_questions"] == 1 and r["abstain_expected_questions"] == 0 and r["evidence_seeking_questions"] == 1
    assert r["evidence_seeking_acceptable"]["status"] == "N/A"  # nobody has judged it yet
    qs[0]["seeking_gold"] = "not_found_reported_honestly"
    judged = m.abstention_and_retention(qs)
    assert judged["evidence_seeking_acceptable"]["value"] == 1.0 and judged["evidence_seeking_found_with_primary_source"]["value"] == 0.0
    qs[0]["seeking_gold"] = "unacceptable"
    assert m.abstention_and_retention(qs)["evidence_seeking_acceptable"]["value"] == 0.0
    qs[0]["seeking_gold"] = "maybe"
    with pytest.raises(ValueError):
        m.abstention_and_retention(qs)


# --- 5/6. pilot and budget -----------------------------------------------------------------------------------------------------------------

def test_the_pilot_is_twelve_development_claims_and_two_development_questions_and_nothing_held_out():
    plan = pilot.plan()
    assert plan["dry_run"] is True and len(plan["claim_verification"]["examples"]) == 12 and plan["end_to_end"]["questions"] == ["Q02", "Q05"]
    lock = json.loads(datasets.LOCK.read_text(encoding="utf-8"))["lock"]
    held = set(lock["questions"]["heldout_ids"]) | set(lock["claim_examples"]["heldout_ids"])
    assert not held & (set(pilot.PILOT_CLAIMS) | set(pilot.PILOT_QUESTIONS))
    assert all(e["split"] == "dev" for e in pilot.examples()) and all(q["split"] == "dev" for q in datasets.load_questions() if q["id"] in pilot.PILOT_QUESTIONS)


def test_the_pilot_refuses_a_heldout_item(monkeypatch):
    monkeypatch.setattr(pilot, "PILOT_QUESTIONS", ["Q02", "Q10"])
    with pytest.raises(SystemExit, match="held-out"):
        pilot.plan()
    monkeypatch.setattr(pilot, "PILOT_QUESTIONS", ["Q02", "Q05"])
    monkeypatch.setattr(pilot, "PILOT_CLAIMS", ["CE01", "CE05"])
    with pytest.raises(SystemExit, match="held-out"):
        pilot.plan()


@requires_full_evidence
def test_the_pilot_is_blocked_until_all_twelve_claims_have_human_labels(monkeypatch, empty_label_store):
    plan = pilot.plan()
    assert plan["ready_to_execute"] is False and "12 of 12 pilot claim examples have no authoritative human label" in plan["blocked_by"][0]
    with pytest.raises(SystemExit, match="pilot blocked"):
        pilot.require_ready()
    labels = {i: "supported" for i in pilot.PILOT_CLAIMS}
    monkeypatch.setattr(datasets, "authoritative_labels", lambda examples=None: labels)
    assert pilot.plan()["ready_to_execute"] is True and pilot.require_ready() is None
    del labels["CE40"]
    with pytest.raises(SystemExit, match="CE40"):
        pilot.require_ready()


def test_the_pilot_limits_and_estimates_add_up():
    p = pilot.plan()
    v, e, t = p["claim_verification"], p["end_to_end"], p["total"]
    assert v["requests"] == {"typical": 12, "cap": 24} and v["output_tokens_cap"] == 24 * 1500
    assert e["requests"] == {"typical": 52, "cap": 80} and e["searches_cap"] == 16 and e["fetches_cap"] == 24 and e["output_tokens_cap"] == 60000
    assert e["seconds"]["cap"] == 1200 and e["per_question_limits"]["max_model_requests"] == 40
    assert t["requests"] == {"typical": 64, "cap": 104} and t["search_credits_cap"] == 16 and t["output_tokens_cap"] == 96000
    assert t["seconds"]["cap"] == 360 + 1200 and t["seconds"]["typical"] == 50 + 360
    assert t["calculated_usd"]["typical"] == pytest.approx(0.045 + 0.112, abs=0.006) and t["calculated_usd"]["cap"] == pytest.approx(0.102 + 0.341, abs=0.006)
    assert "not a provider-reported charge" in p["cost_meaning"] and "not default product performance" in p["budget_note"]
    arm = p["optional_default_budget_arm"]
    assert arm["limits"]["max_model_requests"] == 20 and arm["requests"]["cap"] == 40 and "separate approval" in arm["what"]


def test_every_budget_limit_that_differs_from_the_product_default_is_recorded_and_disclosed():
    assert budget_differences(Settings()) == {}
    s = Settings(**pilot.E2E_LIMITS)
    diff = budget_differences(s)
    assert diff["max_model_requests"] == {"evaluation": 40, "production_default": 20} and diff["max_total_output_tokens"]["production_default"] == 15000
    assert diff["max_elapsed_seconds"] == {"evaluation": 600.0, "production_default": 300.0}
    meta = run_metadata("e2e", s, split="dev", budget={}, run_ids=[], executed=False, fixture=True, label_basis="none")
    assert meta.budget_class == "evaluation_budget_differs_from_production" and meta.budget_differences == diff
    assert any("not default product performance" in n for n in meta.notes)
    default = run_metadata("e2e", Settings(), split="dev", budget={}, run_ids=[], executed=False, fixture=True, label_basis="none")
    assert default.budget_class == "production_defaults" and default.budget_differences == {}
    assert not any("not default product performance" in n for n in default.notes)


def test_the_documented_reason_for_40_versus_20_is_present_and_marks_what_is_unmeasured():
    text = pathlib.Path("docs/PHASE6A.md").read_text(encoding="utf-8")
    for needle in ("Why evaluation uses 40 requests", "20", "6 of 10", "13 of 14", "not measured", "default-budget arm"):
        assert needle in text, needle
