"""Rubric versioning, versioned label corrections and label provenance on reports. No model call, no real label file is written."""

import csv
import json

import pytest

from backend.config import Settings
from evaluation import datasets, review, verify_eval
from evaluation.schemas import run_metadata

PROV = "AI-assisted human review; not independent or blinded."


def _csv(path, rows):
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["id", "claim", "human_label", "reviewer", "reviewed_at", "note", "correction_reason"])
        w.writeheader()
        for r in rows:
            w.writerow({"claim": "", "reviewed_at": "2026-10-22", "note": "", "correction_reason": "", **r})


def _settings():
    return Settings(_env_file=None, mesh_api_key="")


@pytest.fixture
def store(tmp_path, monkeypatch):
    path = tmp_path / "human_labels.json"
    monkeypatch.setattr(datasets, "HUMAN", path)
    return path


def test_rubric_is_versioned_and_the_superseded_text_is_kept_verbatim():
    data = datasets.load_claim_dataset()
    assert data["label_rubric_version"] == "claim-labels-v2"
    hist = {h["version"]: h for h in data["label_rubric_history"]}
    assert set(hist) == {"claim-labels-v1", "claim-labels-v2"}
    assert "does NOT make the whole claim contradicted" in hist["claim-labels-v1"]["changed_rules"]["R4"]
    assert "No human label existed" in hist["claim-labels-v2"]["labels_affected"] and "attribution-v2" in hist["claim-labels-v2"]["verifier_note"]
    assert datasets.validate_claim_dataset(data) == []


def test_flags_never_name_a_heldout_example():
    data = json.loads(json.dumps(datasets.load_claim_dataset()))
    data["rubric_flags"]["CE19"] = dict(data["rubric_flags"]["CE13"])
    assert any("CE19" in p and "held-out" in p for p in datasets.validate_claim_dataset(data))


def test_review_sheet_shows_rubric_version_provenance_and_flags():
    md = review.render_markdown(review.select(["CE13", "CE01"]), "t")
    assert "claim-labels-v2" in md and PROV in md
    assert md.count("FLAGGED FOR REVIEW") == 1  # CE01 is not flagged


def test_import_stamps_version_rubric_and_provenance_and_a_second_import_never_overwrites(tmp_path, store):
    ex = datasets.claim_examples()
    src = tmp_path / "a.csv"
    _csv(src, [{"id": "CE16", "human_label": "contradicted", "reviewer": "R. Reviewer"}])
    assert review.import_labels(src, store, ex, confirm_human=True)["added"] == ["CE16"]
    entry = json.loads(store.read_text(encoding="utf-8"))["claim_labels"][0]
    assert entry["version"] == 1 and entry["supersedes"] is None and entry["rubric_version"] == "claim-labels-v2" and entry["provenance"] == PROV
    _csv(src, [{"id": "CE16", "human_label": "partially_supported", "reviewer": "R. Reviewer"}])
    out = review.import_labels(src, store, ex, confirm_human=True)
    assert out["added"] == [] and out["corrected"] == [] and "--correct" in out["skipped"][0][1]
    assert datasets.authoritative_labels(ex) == {"CE16": "contradicted"}


def test_a_correction_is_a_new_version_that_keeps_the_old_entry_and_reviewer(tmp_path, store):
    ex = datasets.claim_examples()
    src = tmp_path / "a.csv"
    _csv(src, [{"id": "CE16", "human_label": "partially_supported", "reviewer": "First Reviewer"}])
    review.import_labels(src, store, ex, confirm_human=True)
    _csv(src, [{"id": "CE16", "human_label": "contradicted", "reviewer": "Second Reviewer"}])  # no reason: refused
    assert "correction_reason" in review.import_labels(src, store, ex, confirm_human=True, correct=True)["skipped"][0][1]
    _csv(src, [{"id": "CE16", "human_label": "contradicted", "reviewer": "Second Reviewer", "correction_reason": "rubric v2: explicit contradiction wins"}])
    assert review.import_labels(src, store, ex, confirm_human=True, correct=True)["corrected"] == ["CE16"]
    rows = datasets.label_history("CE16")
    assert [r["version"] for r in rows] == [1, 2] and [r["label"] for r in rows] == ["partially_supported", "contradicted"]
    assert rows[0]["reviewer"] == "First Reviewer" and rows[1]["supersedes"] == 1 and rows[1]["previous_reviewer"] == "First Reviewer"
    assert datasets.authoritative_labels(ex) == {"CE16": "contradicted"}
    assert datasets.label_summary()["corrected"] == ["CE16"]
    _csv(src, [{"id": "CE16", "human_label": "contradicted", "reviewer": "Second Reviewer", "correction_reason": "again"}])
    assert "unchanged" in review.import_labels(src, store, ex, confirm_human=True, correct=True)["skipped"][0][1]


def test_an_invalid_correction_leaves_the_previous_version_in_force(store):
    ex = datasets.claim_examples()
    h = datasets.claim_hash(next(e for e in ex if e["id"] == "CE01"))
    v1 = {"example_id": "CE01", "label": "supported", "reviewer": "R. Reviewer", "source": "human", "claim_sha256": h, "version": 1}
    for bad in ({"version": 2, "label": "unsupported", "reviewer": "Claude", "correction_reason": "x"},  # AI reviewer
                {"version": 2, "label": "unsupported", "correction_reason": ""},  # no reason
                {"version": 3, "label": "unsupported", "correction_reason": "skipped v2"}):  # skipped version
        store.write_text(json.dumps({"claim_labels": [v1, {**v1, **bad}]}), encoding="utf-8")
        assert datasets.authoritative_labels(ex) == {"CE01": "supported"}
        assert any("CE01" in p and "not in force" in p for p in datasets.label_problems(ex))


def test_a_later_valid_version_wins_even_when_listed_first(store):
    ex = datasets.claim_examples()
    h = datasets.claim_hash(next(e for e in ex if e["id"] == "CE01"))
    v1 = {"example_id": "CE01", "label": "supported", "reviewer": "R. Reviewer", "source": "human", "claim_sha256": h}
    v2 = {**v1, "label": "partially_supported", "version": 2, "supersedes": 1, "correction_reason": "reread"}
    store.write_text(json.dumps({"claim_labels": [v2, v1]}), encoding="utf-8")
    assert datasets.authoritative_labels(ex) == {"CE01": "partially_supported"}


def test_reports_carry_label_provenance_rubric_and_versions(store):
    ex = datasets.claim_examples()
    h = datasets.claim_hash(next(e for e in ex if e["id"] == "CE01"))
    store.write_text(json.dumps({"claim_labels": [{"example_id": "CE01", "label": "supported", "reviewer": "R. Reviewer", "source": "human",
                                                    "claim_sha256": h, "version": 1, "rubric_version": "claim-labels-v2", "provenance": PROV}]}),
                     encoding="utf-8")
    meta = run_metadata("verification", _settings(), split="dev", budget={}, run_ids=[], executed=False, fixture=True,
                        label_basis="authoritative-human", label_ids=["CE01"])
    assert meta.label_provenance == [PROV] and meta.label_rubric_versions == ["claim-labels-v2"] and meta.label_versions == {"CE01": 1}
    assert PROV in meta.label_statement and "not independent, blinded" in meta.label_statement
    assert meta.notes[0] == meta.label_statement and meta.dataset_label_rubric == "claim-labels-v2"
    none = run_metadata("verification", _settings(), split="dev", budget={}, run_ids=[], executed=False, fixture=True, label_basis="none")
    assert none.label_provenance == [] and "N/A" in none.label_statement


def test_a_result_file_puts_the_statement_next_to_the_metrics():
    res = verify_eval.build_result([], [], _settings(), split="dev", executed=False, fixture=True, budget_limits={})
    assert res.metrics["label_statement"] == res.metadata.label_statement
