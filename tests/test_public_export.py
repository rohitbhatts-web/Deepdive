"""The PUBLIC export (public-export-v1): evidence from rights-reserved sources is withheld, and nothing silently changes meaning.
These tests run on the public dataset; on a full private dataset the withheld-evidence ones are skipped."""

import importlib.util
import json
from pathlib import Path

import pytest

from evaluation import __main__ as cli
from evaluation import datasets, pilot, verify_eval
from tests.conftest import requires_full_evidence

ROOT = Path(__file__).parent.parent
PUBLIC = bool(datasets.withheld_ids(datasets.claim_examples()))
public_only = pytest.mark.skipif(not PUBLIC, reason="runs on the public export only")

WITHHELD_PASSAGES = {"datacamp:P5", "datacamp:P11", "tinybird:P12", "tinybird:P18", "tinybird:P21", "devto:P2", "devto:P4", "logz:P2", "logz:P4"}


@public_only
def test_the_nine_flagged_passages_are_withheld_and_the_fourteen_others_are_published():
    data = datasets.load_claim_dataset()
    withheld = {k for k, p in data["passages"].items() if p.get("withheld")}
    assert withheld == WITHHELD_PASSAGES and len(data["passages"]) == 23
    for k, p in data["passages"].items():
        assert (p["text"] is None) == (k in withheld)
    meta = data["public_export"]
    assert meta["variant"] == "public-export-v1" and (meta["passages_withheld"], meta["passages_retained"]) == (9, 14)
    assert (meta["examples_total"], meta["examples_with_withheld_evidence"], meta["examples_with_full_evidence"]) == (40, 15, 25)


@public_only
def test_labels_and_the_split_lock_keep_their_meaning_and_nothing_is_silently_dropped():
    assert datasets.validate_claim_dataset(datasets.load_claim_dataset()) == []
    assert datasets.verify_split_lock() == []  # the held-out hashes still verify, including held-out examples with withheld evidence
    assert len(datasets.claim_examples()) == 40
    assert len(datasets.authoritative_labels()) == 12 and datasets.label_problems() == []
    assert {"CE30", "CE31"} <= set(datasets.withheld_ids(datasets.claim_examples()))  # pilot examples whose labels still match


@public_only
def test_nothing_that_needs_the_withheld_text_runs_without_an_explicit_subset():
    with pytest.raises(datasets.WithheldEvidenceError, match=r"CE30.*CE31|CE31"):
        verify_eval.run_frozen(pilot.examples(), None, None)
    with pytest.raises(datasets.WithheldEvidenceError):
        datasets.require_evidence(datasets.claim_examples("dev"), "x")
    subset = datasets.claim_examples(available_only=True)
    assert len(subset) == 25 and datasets.withheld_ids(subset) == []
    assert len(pilot.examples(available_only=True)) == 10  # 10 of the 12, a stated smaller denominator
    assert len(datasets.claim_examples("dev", available_only=True)) == 20


@public_only
def test_the_cli_refuses_before_any_client_is_built(capsys, monkeypatch):
    def boom(*a, **k):
        raise AssertionError("a model client must not be built for withheld evidence")

    from backend import services
    monkeypatch.setattr(services, "build_services", boom)
    assert cli.main(["verify", "--split", "dev", "--execute"]) == 2
    assert "withholds" in capsys.readouterr().err
    cli.main(["verify", "--split", "dev", "--available-evidence-only"])
    plan = json.loads(capsys.readouterr().out)
    assert plan["examples"] == 20 and plan["with_authoritative_label"] == 10 and plan["evidence_withheld_in_this_selection"] == []
    assert pilot.plan()["ready_to_execute"] is False and pilot.plan()["claim_evidence_withheld"] == ["CE30", "CE31"]


@public_only
def test_two_claim_texts_are_withheld_not_reworded_and_cannot_be_evaluated():
    data = datasets.load_claim_dataset()
    assert data["public_export"]["claims_withheld"] == ["CE04", "CE10"]
    by_id = {e["id"]: e for e in data["examples"]}
    for i in ("CE04", "CE10"):
        e = by_id[i]
        assert e["claim"] is None and e["claim_withheld"] and len(e["claim_sha256"]) == 64 and e["claim_chars"] > 0 and e["evidence_withheld"]
        assert i not in datasets.authoritative_labels()  # no label exists, and none was created
    reasons = datasets.unavailable_reasons(datasets.claim_examples())
    assert reasons["CE04"] == ["evidence text withheld", "claim text withheld"] and reasons["CE06"] == ["evidence text withheld"]
    assert len(reasons) == 15
    with pytest.raises(datasets.WithheldEvidenceError, match="claim text of .*CE04.* is withheld"):
        datasets.require_evidence([e for e in datasets.claim_examples() if e["id"] == "CE04"], "x")
    assert "CE04" not in {e["id"] for e in datasets.claim_examples(available_only=True)}


@public_only
def test_the_hash_report_separates_recomputable_from_retained_only():
    h = datasets.hash_report()
    assert len(h["examples"]["recomputable"]) == 25 and len(h["examples"]["retained_only"]) == 15
    assert set(h["examples"]["recomputable"]).isdisjoint(h["examples"]["retained_only"])
    assert h["passages"] == {"published_text": 14, "withheld_hash_retained": 9}
    assert h["labels"]["in_force"] == 12 and h["labels"]["retained_hash_only"] == ["CE30", "CE31"] and len(h["labels"]["verifiable"]) == 10
    lock = h["split_lock"]
    assert lock["aggregate_matches"] is True and lock["fully_verifiable_here"] is False
    assert lock["heldout_retained_only"] == ["CE09", "CE14", "CE17", "CE27", "CE33"] and len(lock["heldout_recomputable"]) == 5
    assert "cannot be verified from this export" in lock["statement"]
    # a recomputable example really is recomputed: tampering with its published claim text breaks the match
    ex = next(e for e in datasets.claim_examples() if e["id"] in h["examples"]["recomputable"])
    assert datasets.claim_hash({**ex, "claim": ex["claim"] + " (edited)"}) != datasets.claim_hash(ex)
    # a retained-only hash is used as recorded, so editing what is published for it cannot be detected here
    wd = next(e for e in datasets.claim_examples() if e["id"] == "CE30")
    assert datasets.claim_hash({**wd, "claim": "anything"}) == datasets.claim_hash(wd) == wd["claim_sha256_full_evidence"]


@public_only
def test_validate_reports_the_unavailable_items_and_the_hash_verification(capsys):
    assert cli.main(["validate"]) == 0
    out = json.loads(capsys.readouterr().out)
    pe = out["public_export"]
    assert out["problems"] == [] and len(pe["unavailable_examples"]) == 15 and pe["hash_verification"]["split_lock"]["fully_verifiable_here"] is False
    assert pe["hash_verification"]["examples"] == {"recomputable": 25, "retained_only": 15}


@public_only
def test_the_published_tree_holds_no_withheld_text_hashes_match_length_only():
    data = datasets.load_claim_dataset()
    for k in WITHHELD_PASSAGES:
        p = data["passages"][k]
        assert set(p) >= {"text_sha256", "text_chars", "withheld_reason"} and p["text_chars"] > 0 and len(p["text_sha256"]) == 64
    sheets = (ROOT / "evaluation/review/claim_review_batch1.md").read_text(encoding="utf-8")
    assert "withheld: rights-reserved" in sheets and "PUBLIC EXPORT" in sheets


def test_the_export_script_hashes_a_claim_exactly_as_the_harness_does():
    spec = importlib.util.spec_from_file_location("public_export", ROOT / "scripts" / "public_export.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    data = datasets.load_claim_dataset()
    ex = next(e for e in datasets.claim_examples(available_only=True))  # a published-evidence example
    raw = next(e for e in data["examples"] if e["id"] == ex["id"])
    assert mod._hash_example(raw, data["passages"]) == datasets.claim_hash(ex)


@requires_full_evidence
def test_on_the_full_dataset_every_example_is_available():
    assert len(datasets.claim_examples(available_only=True)) == len(datasets.claim_examples()) == 40
