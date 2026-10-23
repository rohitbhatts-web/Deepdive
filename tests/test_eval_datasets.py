"""Evaluation datasets: structure, provenance, the recorded split, and the rule that model-proposed labels are never authoritative.
These tests read the shipped dataset files; temporary files stand in for human labels (FIXTURES)."""

import csv
import json
from collections import Counter

import pytest

from evaluation import datasets, review
from evaluation import metrics as m
from tests.conftest import requires_full_evidence


def test_questions_are_valid_varied_and_split():
    qs = datasets.load_questions()
    assert datasets.validate_questions(qs) == []
    assert len(qs) == 12
    assert Counter(q["split"] for q in qs) == {"dev": 8, "heldout": 4}
    assert len({q["domain"] for q in qs}) >= 10
    assert any("database" in q["domain"] for q in qs) and sum("database" not in q["domain"] for q in qs) >= 10  # not just databases
    for q in qs:
        assert q["answer_requirements"] and q["scope"]["exclusions"] and q["scope"]["as_of"]
    assert not any(q["used_for_tuning"] for q in qs if q["split"] == "heldout")
    q01 = next(q for q in qs if q["id"] == "Q01")
    assert q01["split"] == "dev" and q01["used_for_tuning"] and "passage_selection_crowd_out" in q01["categories"]


def test_every_required_question_category_is_present_in_development_and_in_the_heldout_set_where_sensible():
    qs = datasets.load_questions()
    dev = {c for q in qs if q["split"] == "dev" for c in q["categories"]}
    held = {c for q in qs if q["split"] == "heldout" for c in q["categories"]}
    assert datasets.REQUIRED_CATEGORIES <= dev | held
    assert {"conflicting_sources", "insufficient_evidence_abstention", "compound_claims"} <= held  # the held-out set tests the hard cases too


def test_no_question_asks_for_personal_advice():
    for q in datasets.load_questions():
        low = q["question"].lower()
        assert not any(w in low for w in ("should i", "my doctor", "my symptoms", "my taxes", "my lawyer", "should we invest"))
        assert "advice on whether any particular organisation" in " ".join(q["scope"]["exclusions"]) or q["domain"] != "law_information"


def test_claim_dataset_is_valid_and_has_about_forty_balanced_examples():
    data = datasets.load_claim_dataset()
    assert datasets.validate_claim_dataset(data) == []
    ex = data["examples"]
    assert len(ex) == 40
    by_label = Counter(e["proposed"]["label"] for e in ex)
    assert set(by_label) == set(m.VERDICTS) and min(by_label.values()) >= 8
    assert Counter(e["split"] for e in ex) == {"dev": 30, "heldout": 10}
    for label in m.VERDICTS:  # the held-out set has every label
        assert any(e["split"] == "heldout" and e["proposed"]["label"] == label for e in ex)


def test_every_quote_is_verbatim_and_every_source_is_traceable():
    data = datasets.load_claim_dataset()
    for e in data["examples"]:
        for item in e["evidence"]:
            p = data["passages"][item["passage"]]
            if p.get("withheld"):  # public export: the text is not published, only its hashes and lengths
                assert p["text"] is None and item["quote"] is None and len(p["text_sha256"]) == 64 and len(item["quote_sha256"]) == 64
                assert item["quote_chars"] >= datasets.MIN_QUOTE
                continue
            assert item["quote"] in p["text"] and len(item["quote"]) >= datasets.MIN_QUOTE
    for s in data["sources"].values():
        assert s["url"].startswith("https://") and s["retrieved_at"] and len(s["content_hash"]) >= 16
        assert s["provenance"]["database"].endswith(".db") and s["provenance"]["source_id"] >= 1 and len(s["provenance"]["database_sha256"]) == 64
    for p in data["passages"].values():
        assert p["source"] in data["sources"] and p["stored_passage_id"].startswith("S")


def test_synthetic_alterations_are_explicitly_labelled():
    data = datasets.load_claim_dataset()
    altered_tags = {"changed_quantity", "changed_date", "swapped_entity", "negation", "forecast_to_fact", "dropped_qualification"}
    for e in data["examples"]:
        if altered_tags & set(e["tags"]) or (e["synthetic"] and e["synthetic"]["kind"]):
            assert e["synthetic"] and e["synthetic"]["kind"] and e["synthetic"]["detail"], e["id"]
    kinds = {e["synthetic"]["kind"] for e in data["examples"] if e["synthetic"]}
    assert {"changed_quantity", "changed_date", "forecast_to_fact", "negation"} <= kinds
    tags = {t for e in data["examples"] for t in e["tags"]}
    assert {"genuine_but_irrelevant_quote", "compound", "attribution_correct", "date_qualified", "number"} <= tags


def test_proposed_labels_are_provisional_and_never_authoritative(empty_label_store):
    data = datasets.load_claim_dataset()
    for e in data["examples"]:
        assert e["proposed"]["status"] == "provisional" and "not a human reviewer" in e["proposed"]["proposed_by"]
    assert "PROVISIONAL" in data["label_policy"]
    # with no human label store, so NOTHING is authoritative, whatever the proposals say
    assert datasets.authoritative_labels() == {}
    records = [{"id": e["id"], "gold": datasets.authoritative_labels().get(e["id"]), "outcome": e["proposed"]["label"]} for e in datasets.claim_examples()]
    r = m.verification_metrics(records)
    assert r["counts"]["labelled"] == 0 and r["supported_precision"]["status"] == "N/A" and r["four_verdict_accuracy"]["status"] == "N/A"


def test_the_shipped_end_to_end_label_lists_are_empty_until_a_human_fills_them():
    store = json.loads(datasets.HUMAN.read_text(encoding="utf-8"))
    assert store["requirement_labels"] == [] and store["report_claim_labels"] == []


def _labels_file(tmp_path, monkeypatch, entries):
    path = tmp_path / "human_labels.json"
    path.write_text(json.dumps({"claim_labels": entries}), encoding="utf-8")
    monkeypatch.setattr(datasets, "HUMAN", path)
    return path


def test_only_complete_human_entries_with_a_matching_claim_hash_count(tmp_path, monkeypatch):
    ex = datasets.claim_examples()
    good = {"example_id": "CE01", "label": "supported", "reviewer": "R. Reviewer", "source": "human", "claim_sha256": datasets.claim_hash(ex[0])}
    _labels_file(tmp_path, monkeypatch, [
        good,
        {**good, "example_id": "CE02", "claim_sha256": datasets.claim_hash(ex[1]), "reviewer": "  "},  # no reviewer
        {**good, "example_id": "CE03", "claim_sha256": "0" * 64},  # claim changed since it was labelled
        {**good, "example_id": "CE04", "claim_sha256": datasets.claim_hash(ex[3]), "label": "mostly"},  # not a verdict
        {**good, "example_id": "CE99"},  # unknown example
        {**good, "example_id": "CE05", "claim_sha256": datasets.claim_hash(ex[4]), "source": "model"},  # not marked as a human judgement
        {**good, "example_id": "CE06", "claim_sha256": datasets.claim_hash(ex[5]), "reviewer": "Claude"},  # an AI name is not a human reviewer
    ])
    assert datasets.authoritative_labels(ex) == {"CE01": "supported"}


def test_split_lock_matches_the_files_and_detects_tampering(monkeypatch):
    assert datasets.verify_split_lock() == []
    lock = json.loads(datasets.LOCK.read_text(encoding="utf-8"))["lock"]
    assert lock["questions"]["heldout_ids"] == ["Q09", "Q10", "Q11", "Q12"] and len(lock["claim_examples"]["heldout_ids"]) == 10
    # editing a held-out question changes the recorded hash
    edited = json.loads(json.dumps(datasets.load_questions()))
    next(q for q in edited if q["id"] == "Q10")["question"] += " (reworded after seeing results)"
    monkeypatch.setattr(datasets, "load_questions", lambda: edited)
    assert any("heldout_sha256" in d for d in datasets.verify_split_lock())
    # moving a question from dev to held-out also breaks the lock
    moved = json.loads(json.dumps(datasets.load_questions()))
    next(q for q in moved if q["id"] == "Q02")["split"] = "heldout"
    monkeypatch.setattr(datasets, "load_questions", lambda: moved)
    assert any("heldout_ids" in d for d in datasets.verify_split_lock())


def test_editing_a_heldout_claim_example_breaks_the_lock(monkeypatch):
    real = datasets.claim_examples

    def edited(split=None):
        out = real(split)
        for e in out:
            if e["id"] == "CE22":
                e["claim"] = e["claim"] + " and more"
        return out

    monkeypatch.setattr(datasets, "claim_examples", edited)
    assert any("claim_examples.heldout_sha256" in d for d in datasets.verify_split_lock())


def test_heldout_items_cannot_appear_in_the_tuning_log(tmp_path, monkeypatch):
    assert datasets.tuning_violations() == []  # nothing has been tuned
    log = tmp_path / "tuning_log.json"
    monkeypatch.setattr(datasets, "TUNING", log)
    log.write_text(json.dumps({"entries": [{"date": "2026-11-01", "change": "reworded verifier prompt", "evaluated_on": ["CE01", "Q10"]}]}), encoding="utf-8")
    assert datasets.tuning_violations() and "Q10" in datasets.tuning_violations()[0]
    log.write_text(json.dumps({"entries": [{"date": "2026-12-01", "kind": "final_heldout_run", "change": "none", "evaluated_on": ["Q10"]}]}), encoding="utf-8")
    assert datasets.tuning_violations() == []


def test_a_question_used_for_tuning_cannot_be_validly_heldout():
    qs = json.loads(json.dumps(datasets.load_questions()))
    next(q for q in qs if q["id"] == "Q01")["split"] = "heldout"
    assert any("used for tuning cannot be held out" in p for p in datasets.validate_questions(qs))


@requires_full_evidence
def test_review_sheet_shows_proposals_but_leaves_human_fields_empty(tmp_path):
    examples = review.select(review.BATCH1)
    assert len(examples) == 12 and all(e["split"] == "dev" for e in examples)
    md = review.render_markdown(examples, "t")
    assert md.count("**Proposed (PROVISIONAL, not a label):**") == 12 and md.count("human label: ______________") == 12
    for e in examples:
        assert e["claim"] in md and e["evidence"][0]["url"] in md and e["evidence"][0]["quote"] in md
    path = tmp_path / "sheet.csv"
    review.write_csv(examples, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    assert len(rows) == 12 and all(r["human_label"] == "" and r["reviewer"] == "" for r in rows)
    assert all(r["proposed_label_PROVISIONAL"] in m.VERDICTS for r in rows)


@requires_full_evidence
def test_importing_a_review_sheet_only_accepts_complete_valid_rows(tmp_path):
    examples = review.select(review.BATCH1)
    path = tmp_path / "sheet.csv"
    review.write_csv(examples, path)
    rows = list(csv.DictReader(path.open(encoding="utf-8")))
    rows[0].update(human_label="supported", reviewer="R. Reviewer")
    rows[1].update(human_label="partially_supported", reviewer="")  # no reviewer: rejected
    rows[2].update(human_label="maybe", reviewer="R. Reviewer")  # invalid label: rejected
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=review.COLUMNS)
        w.writeheader()
        w.writerows(rows)
    store = tmp_path / "human.json"
    out = review.import_labels(path, store, examples, confirm_human=True)
    assert out["added"] == [rows[0]["id"]] and {s[0] for s in out["skipped"]} == {rows[1]["id"], rows[2]["id"]}
    saved = json.loads(store.read_text(encoding="utf-8"))["claim_labels"]
    assert len(saved) == 1 and saved[0]["label"] == "supported" and saved[0]["source"] == "human"
    assert saved[0]["claim_sha256"] == datasets.claim_hash(examples[0])
    again = review.import_labels(path, store, examples, confirm_human=True)  # importing twice never overwrites a label
    assert again["added"] == [] and any("already labelled" in s[1] for s in again["skipped"])


@pytest.mark.parametrize("name", ["claim_review_batch1.md", "claim_review_batch1.csv"])
def test_the_shipped_review_batch_exists_and_has_no_human_labels(name):
    path = datasets.DATA.parent / "review" / name
    text = path.read_text(encoding="utf-8")
    assert "CE01" in text and "PROVISIONAL" in text
    if name.endswith(".csv"):
        assert all(r["human_label"] == "" for r in csv.DictReader(path.open(encoding="utf-8")))
