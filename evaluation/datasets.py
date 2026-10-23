"""Loading, validating and locking the evaluation datasets.

Three rules are enforced here and by the tests:
1. Held-out items are fixed by split.lock.json (hashes recorded before any tuning). Changing a held-out question or example, or moving an
   item between splits, breaks `verify_split_lock`.
2. Labels proposed by a model never count. `authoritative_labels` reads only data/human_labels.json, and only entries with a reviewer and a
   matching claim hash.
3. A held-out item must not appear in the tuning log (except a final, declared held-out run).
"""

import hashlib
import json
import re
from pathlib import Path

from evaluation.metrics import REQUIREMENT_LABELS, VERDICTS

DATA = Path(__file__).parent / "data"
QUESTIONS = DATA / "questions.json"
CLAIMS = DATA / "claim_evidence.json"
LOCK = DATA / "split.lock.json"
HUMAN = DATA / "human_labels.json"
TUNING = DATA / "tuning_log.json"
MIN_QUOTE = 15
EXPECTED_OUTCOMES = {"answerable", "answerable_with_conflict", "abstain_expected", "evidence_seeking"}
# A reviewer name that looks like an AI or model is never accepted as the human behind an authoritative label.
NON_HUMAN = re.compile(r"\b(ai|llm|gpt\w*|claude|anthropic|openai|assistant|model|bot|copilot|gemini|qwen|mistral|agent|automated|auto)\b", re.IGNORECASE)
REQUIRED_CATEGORIES = {"factual_comparison", "dates_and_numbers", "conflicting_sources", "recommendation_attribution",
                       "insufficient_evidence_abstention", "compound_claims", "multi_subject_obligations", "passage_selection_crowd_out"}


def valid_reviewer(name: object) -> bool:
    """True for a non-empty name that does not look like an AI, model or automation."""
    text = str(name or "").strip()
    return bool(text) and not NON_HUMAN.search(text)


def _human_entry(entry: dict) -> bool:
    """An authoritative entry must be marked source=human and name a human reviewer."""
    return entry.get("source") == "human" and valid_reviewer(entry.get("reviewer"))


def _read(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()


def load_questions() -> list[dict]:
    return _read(QUESTIONS)["questions"]


def load_claim_dataset() -> dict:
    return _read(CLAIMS)


class WithheldEvidenceError(RuntimeError):
    """An operation needs the text of evidence that the PUBLIC export withholds (rights-reserved third-party text)."""


def claim_examples(split: str | None = None, *, available_only: bool = False) -> list[dict]:
    """Examples with their evidence resolved (passage text, source URL, retrieval date) for running or review.

    In the public export some passages are withheld: their `passage_text` and `quote` are None and the example has `evidence_withheld`.
    By default such examples are still returned (their ids, claims, labels and hashes exist); `available_only=True` returns only examples whose evidence is
    published, which is the explicit available-evidence subset. Use `require_evidence` before anything that needs the text."""
    data = load_claim_dataset()
    out = []
    for e in data["examples"]:
        if split and e["split"] != split:
            continue
        if available_only and e.get("evidence_withheld"):
            continue
        ev = []
        for item in e["evidence"]:
            p = data["passages"][item["passage"]]
            s = data["sources"][p["source"]]
            ev.append({**item, "passage_text": p["text"], "ordinal": p["ordinal"], "source_key": p["source"], "url": s["url"], "title": s["title"],
                       "retrieved_at": s["retrieved_at"], "content_hash": s["content_hash"], "withheld": bool(p.get("withheld"))})
        out.append({**e, "evidence": ev})
    return out


def withheld_ids(examples: list[dict]) -> list[str]:
    return [e["id"] for e in examples if e.get("evidence_withheld")]


def unavailable_reasons(examples: list[dict]) -> dict[str, list[str]]:
    """{example id: why it is unavailable in the public export} (empty for examples whose claim and evidence are published)."""
    return {e["id"]: (["evidence text withheld"] if e.get("evidence_withheld") else []) + (["claim text withheld"] if e.get("claim_withheld") else [])
            for e in examples if e.get("evidence_withheld") or e.get("claim_withheld")}


def hash_report(examples: list[dict] | None = None) -> dict:
    """What can and cannot be verified from THIS copy of the data.

    `recomputable`: the example's claim and evidence text are published, so its claim hash is recomputed from them here (and is what labels and the lock use).
    `retained_only`: the content is withheld; the hash recorded on the original is used as is and CANNOT be verified from this export.
    The split lock compares one aggregate hash over the held-out examples; it can only be recomputed in full when no held-out example is withheld."""
    examples = claim_examples() if examples is None else examples
    lock = _read(LOCK)["lock"]["claim_examples"]
    held = set(lock["heldout_ids"])
    labels = authoritative_labels(examples)
    recomputable = sorted(e["id"] for e in examples if not (e.get("evidence_withheld") or e.get("claim_withheld")))
    retained = sorted(unavailable_reasons(examples))
    held_retained = sorted(held & set(retained))
    data = load_claim_dataset()
    passages = data["passages"].values()
    return {
        "examples": {"total": len(examples), "recomputable": recomputable, "retained_only": retained,
                     "reasons": unavailable_reasons(examples)},
        "passages": {"published_text": sum(1 for p in passages if not p.get("withheld")), "withheld_hash_retained": sum(1 for p in passages if p.get("withheld"))},
        "labels": {"in_force": len(labels), "verifiable": sorted(i for i in labels if i in set(recomputable)),
                   "retained_hash_only": sorted(i for i in labels if i in set(retained))},
        "split_lock": {"heldout_total": len(held), "heldout_recomputable": sorted(held & set(recomputable)), "heldout_retained_only": held_retained,
                       "aggregate_matches": not verify_split_lock(),
                       "fully_verifiable_here": not held_retained,
                       "statement": ("the aggregate held-out hash matches when recomputed from the published examples together with the retained hashes of "
                                     f"{len(held_retained)} withheld held-out example(s); those {len(held_retained)} cannot be verified from this export" if held_retained
                                     else "recomputed entirely from published content")},
    }


def require_evidence(examples: list[dict], purpose: str) -> None:
    """Raise a clear error unless every example's evidence text is available (it is not for some examples of the public export)."""
    missing = withheld_ids(examples)
    if missing:
        claims = [e["id"] for e in examples if e.get("claim_withheld")]
        raise WithheldEvidenceError(
            f"{purpose} needs the evidence text of {missing}, which the public export withholds (rights-reserved third-party text; see PUBLIC_RELEASE.md)"
            + (f"; the claim text of {claims} is withheld as well" if claims else "") + ". "
            "Select the available-evidence subset explicitly (--available-evidence-only) and report the smaller denominator, or use the full private dataset.")


def claim_hash(example: dict) -> str:
    """Hash of what a label refers to: the claim text and each (passage text, quote). A label for an example that was later edited is stale.
    For an example whose evidence the public export withholds, the hash recorded on the full evidence (`claim_sha256_full_evidence`) is returned."""
    if example.get("evidence_withheld"):
        return example["claim_sha256_full_evidence"]
    ev = [(i.get("passage_text"), i["quote"]) for i in example["evidence"]]
    return _sha({"claim": example["claim"], "evidence": ev})


# --- validation -------------------------------------------------------------------------------------------------------------------

ORIGINAL_REQUIREMENT_SET = "requirements-v1"


def requirement_sets(question: dict) -> dict[str, list[str]]:
    """{set name: requirement ids}. A question without versions has exactly the original set (all its requirements)."""
    return question.get("requirement_sets") or {ORIGINAL_REQUIREMENT_SET: [r["id"] for r in question["answer_requirements"]]}


def validate_questions(questions: list[dict]) -> list[str]:
    problems = []
    ids = [q["id"] for q in questions]
    if len(ids) != len(set(ids)):
        problems.append("duplicate question ids")
    for q in questions:
        if q["split"] not in ("dev", "heldout"):
            problems.append(f"{q['id']}: bad split")
        if q["expected_outcome"] not in EXPECTED_OUTCOMES:
            problems.append(f"{q['id']}: bad expected_outcome")
        if not q["answer_requirements"]:
            problems.append(f"{q['id']}: no answer requirements")
        rids = [r["id"] for r in q["answer_requirements"]]
        if len(rids) != len(set(rids)):
            problems.append(f"{q['id']}: duplicate requirement ids")
        for key in ("subjects", "dimensions", "exclusions", "as_of"):
            if not q["scope"].get(key):
                problems.append(f"{q['id']}: scope.{key} missing")
        if q["expected_outcome"] in ("abstain_expected", "evidence_seeking") and not q.get("abstention_expectation"):
            problems.append(f"{q['id']}: {q['expected_outcome']} without abstention_expectation")
        if q["expected_outcome"] == "evidence_seeking" and not q.get("revisions") and q["id"] == "Q05":
            problems.append("Q05: the revision from an assumed abstention to evidence-seeking must be recorded")
        sets = q.get("requirement_sets")
        if sets:
            ids_all = [r["id"] for r in q["answer_requirements"]]
            flagged = {r["id"] for r in q["answer_requirements"] if r.get("beyond_explicit_question")}
            if q["split"] == "heldout":
                problems.append(f"{q['id']}: held-out questions must not carry requirement versions (they are not modified)")
            if sets.get(ORIGINAL_REQUIREMENT_SET) != ids_all:
                problems.append(f"{q['id']}: {ORIGINAL_REQUIREMENT_SET} must be the original requirements, unchanged")
            for name, members in sets.items():
                if not set(members) <= set(ids_all):
                    problems.append(f"{q['id']}: {name} names a requirement that does not exist")
                if name != ORIGINAL_REQUIREMENT_SET and set(members) & flagged:
                    problems.append(f"{q['id']}: {name} still contains a requirement annotated beyond_explicit_question")
        if q["split"] == "heldout" and q.get("used_for_tuning"):
            problems.append(f"{q['id']}: a question used for tuning cannot be held out")
    covered = {c for q in questions for c in q["categories"]}
    if REQUIRED_CATEGORIES - covered:
        problems.append(f"categories not covered: {sorted(REQUIRED_CATEGORIES - covered)}")
    if len({q["domain"] for q in questions}) < 6:
        problems.append("fewer than 6 domains")
    return problems


def validate_claim_dataset(data: dict) -> list[str]:
    problems = []
    if data.get("evaluation_scope") != "single_domain_database_evidence" or "NOT a cross-domain" not in data.get("domain_limitation", ""):
        problems.append("the dataset must state that it is single-domain and not a cross-domain evaluation")
    if [r["id"] for r in data.get("label_rules", [])] != [f"R{i}" for i in range(1, 8)]:
        problems.append("label rules R1-R7 are missing")
    versions = [h.get("version") for h in data.get("label_rubric_history", [])]
    if not data.get("label_rubric_version") or data["label_rubric_version"] not in versions:
        problems.append("the dataset must name its label rubric version and keep that version in label_rubric_history")
    held = {e["id"] for e in data["examples"] if e["split"] == "heldout"}
    for fid, flag in data.get("rubric_flags", {}).items():
        if fid in held or fid not in {e["id"] for e in data["examples"]}:
            problems.append(f"rubric flag {fid}: must name an existing development example (held-out examples are never flagged or modified)")
        if flag.get("status") != "needs_human_review":
            problems.append(f"rubric flag {fid}: status must be needs_human_review")
    ids = [e["id"] for e in data["examples"]]
    if len(ids) != len(set(ids)):
        problems.append("duplicate example ids")
    for e in data["examples"]:
        if e["split"] not in ("dev", "heldout"):
            problems.append(f"{e['id']}: bad split")
        if e.get("claim_withheld"):
            if e.get("claim") is not None or not e.get("evidence_withheld"):
                problems.append(f"{e['id']}: a withheld claim must carry no text and must have withheld evidence")
            if len(e.get("claim_sha256") or "") != 64 or not e.get("claim_chars"):
                problems.append(f"{e['id']}: withheld claim lacks its hash or length")
        elif not e.get("claim"):
            problems.append(f"{e['id']}: claim text missing")
        if e["proposed"]["label"] not in VERDICTS or e["proposed"]["status"] != "provisional":
            problems.append(f"{e['id']}: proposed label must be one of the four verdicts and marked provisional")
        if "human" in json.dumps(e["proposed"]).lower().replace("not a human reviewer", ""):
            problems.append(f"{e['id']}: proposed block must not claim human review")
        if not e["evidence"]:
            problems.append(f"{e['id']}: no evidence")
        for item in e["evidence"]:
            p = data["passages"].get(item["passage"])
            if p is None:
                problems.append(f"{e['id']}: unknown passage {item['passage']}")
                continue
            if p.get("withheld"):
                if p.get("text") is not None or item.get("quote") is not None:
                    problems.append(f"{e['id']}: a withheld passage must not carry text or a quote")
                if not (p.get("text_sha256") and item.get("quote_sha256") and e.get("evidence_withheld") and e.get("claim_sha256_full_evidence")):
                    problems.append(f"{e['id']}: withheld evidence lacks its hashes or the recorded claim hash")
                if item.get("quote_chars", 0) < MIN_QUOTE:
                    problems.append(f"{e['id']}: quote shorter than {MIN_QUOTE} characters")
            else:
                if item["quote"] not in p["text"]:
                    problems.append(f"{e['id']}: quote is not verbatim in its passage")
                if len(item["quote"]) < MIN_QUOTE:
                    problems.append(f"{e['id']}: quote shorter than {MIN_QUOTE} characters")
            s = data["sources"][p["source"]]
            if not (s["url"].startswith("http") and s["retrieved_at"] and s["content_hash"]):
                problems.append(f"{e['id']}: source lacks url, retrieval date or content hash")
        if e.get("rule") is not None and e["rule"] not in {r["id"] for r in data.get("label_rules", [])}:
            problems.append(f"{e['id']}: unknown rule {e['rule']}")
        if e["synthetic"] is not None and not {"kind", "detail"} <= set(e["synthetic"]):
            problems.append(f"{e['id']}: synthetic alteration needs kind and detail")
    return problems


# --- split lock -------------------------------------------------------------------------------------------------------------------

def compute_lock() -> dict:
    questions = load_questions()
    data = load_claim_dataset()
    q_held = sorted((q for q in questions if q["split"] == "heldout"), key=lambda q: q["id"])
    q_dev = sorted(q["id"] for q in questions if q["split"] == "dev")
    examples = claim_examples()
    c_held = sorted((e for e in examples if e["split"] == "heldout"), key=lambda e: e["id"])
    return {
        "questions": {"heldout_ids": [q["id"] for q in q_held], "dev_ids": q_dev, "heldout_sha256": _sha(q_held),
                      "all_ids_sha256": _sha(sorted(q["id"] + q["split"] for q in questions))},
        "claim_examples": {"heldout_ids": [e["id"] for e in c_held], "dev_ids": sorted(e["id"] for e in examples if e["split"] == "dev"),
                           "heldout_sha256": _sha([(e["id"], claim_hash(e)) for e in c_held]),
                           "all_ids_sha256": _sha(sorted(e["id"] + e["split"] for e in examples))},
        "dataset_built": data["built"],
    }


def verify_split_lock() -> list[str]:
    """Differences between split.lock.json (recorded before tuning) and the current files. Empty list = the held-out sets are untouched."""
    lock = _read(LOCK)["lock"]
    now = compute_lock()
    diffs = []
    for part in ("questions", "claim_examples"):
        for key in ("heldout_ids", "dev_ids", "heldout_sha256", "all_ids_sha256"):
            if lock[part][key] != now[part][key]:
                diffs.append(f"{part}.{key} changed since the split was recorded")
    return diffs


def tuning_violations() -> list[str]:
    """Held-out ids named in the tuning log (only an entry of kind 'final_heldout_run' may name them)."""
    lock = _read(LOCK)["lock"]
    held = set(lock["questions"]["heldout_ids"]) | set(lock["claim_examples"]["heldout_ids"])
    bad = []
    for entry in _read(TUNING)["entries"]:
        touched = held & set(entry.get("evaluated_on", []))
        if touched and entry.get("kind") != "final_heldout_run":
            bad.append(f"{entry.get('date')}: {entry.get('change')!r} used held-out {sorted(touched)}")
    return bad


# --- human labels -----------------------------------------------------------------------------------------------------------------
# Labels are versioned and append-only. An entry has `version` (absent = 1). A correction is a NEW entry with version n+1, `supersedes` n, a
# `correction_reason`, and its own reviewer and date; the earlier entries stay in the file as the history. The CURRENT label of an example is its
# highest valid version. Every entry records the rubric version it was judged under and its provenance.

LABEL_PROVENANCE = "AI-assisted human review; not independent or blinded."
UNRECORDED_PROVENANCE = "provenance not recorded"


def _resolve(entries: list[dict], key, ok) -> dict:
    """{key: current entry}. Entries are accepted per key in version order: version 1 first, then each next version only with a correction_reason.
    A duplicate or skipped version, an unreasoned correction or an entry failing `ok` ends the chain there, so the last accepted version stays current."""
    grouped: dict = {}
    for entry in entries:
        try:
            k = key(entry)
        except (KeyError, TypeError):
            continue
        v = entry.get("version", 1)
        if isinstance(v, int) and not isinstance(v, bool) and v >= 1:
            grouped.setdefault(k, []).append((v, entry))
    current = {}
    for k, items in grouped.items():
        last = 0
        for v, entry in sorted(items, key=lambda x: x[0]):
            if v != last + 1:
                continue  # a duplicate or skipped version is ignored
            if not _human_entry(entry) or not ok(entry):
                break  # an invalid next version: the chain stops, the last accepted version stays current
            if v > 1 and not str(entry.get("correction_reason") or "").strip():
                break
            current[k] = entry
            last = v
    return current


def _claim_ok(by_id: dict):
    def ok(entry: dict) -> bool:
        e = by_id.get(entry.get("example_id"))
        return e is not None and entry.get("label") in VERDICTS and entry.get("claim_sha256") == claim_hash(e)
    return ok


def current_label_entries(examples: list[dict] | None = None, entries: list[dict] | None = None) -> dict[str, dict]:
    """{example_id: the full current entry} from human_labels.json only (label, version, reviewer, rubric_version, provenance, history fields).
    `entries` lets the importer resolve a store it has not written yet."""
    if entries is None:
        if not HUMAN.exists():
            return {}
        entries = _read(HUMAN).get("claim_labels", [])
    by_id = {e["id"]: e for e in (examples if examples is not None else claim_examples())}
    return _resolve(entries, lambda x: x["example_id"], _claim_ok(by_id))


def authoritative_labels(examples: list[dict] | None = None) -> dict[str, str]:
    """{example_id: label} from human_labels.json only: the current version of each label. An entry counts only if it has a non-empty human reviewer,
    a valid label and a claim hash that still matches the example. Model-proposed labels are never returned by this function."""
    return {i: e["label"] for i, e in current_label_entries(examples).items()}


def label_history(example_id: str) -> list[dict]:
    """Every stored entry for one example, oldest version first, including superseded ones."""
    if not HUMAN.exists():
        return []
    rows = [x for x in _read(HUMAN).get("claim_labels", []) if x.get("example_id") == example_id]
    return sorted(rows, key=lambda x: x.get("version", 1) if isinstance(x.get("version", 1), int) else 0)


def _store_current(collection: str, examples: list[dict] | None) -> dict:
    if collection == "claim_labels":
        return current_label_entries(examples)
    if not HUMAN.exists():
        return {}
    entries = _read(HUMAN).get(collection, [])
    if collection == "requirement_labels":
        return _resolve(entries, lambda x: "/".join((x["run_key"], x["question_id"], x["requirement_id"])), lambda x: x.get("label") in REQUIREMENT_LABELS)
    return _resolve(entries, lambda x: f"{x['run_key']}/{x['claim_id']}", lambda x: x.get("label") in VERDICTS)


def label_summary(ids: list[str] | None = None, examples: list[dict] | None = None, collections: tuple[str, ...] = ("claim_labels",)) -> dict:
    """What a report must say about the labels it used: their provenance, rubric versions and versions. Empty provenance = no labels.
    `ids` limits claim labels to those examples."""
    cur = {}
    for c in collections:
        cur.update({f"{i}" if c == "claim_labels" else f"{c}:{i}": e for i, e in _store_current(c, examples).items()
                    if ids is None or c != "claim_labels" or i in set(ids)})
    return {"labels": len(cur),
            "provenance": sorted({e.get("provenance") or UNRECORDED_PROVENANCE for e in cur.values()}),
            "rubric_versions": sorted({e.get("rubric_version") or "not recorded" for e in cur.values()}),
            "versions": {i: e.get("version", 1) for i, e in sorted(cur.items())},
            "corrected": sorted(i for i, e in cur.items() if e.get("version", 1) > 1)}


def label_problems(examples: list[dict] | None = None) -> list[str]:
    """Stored claim-label entries that are ignored, and why (so a bad correction is visible rather than silently dropped)."""
    if not HUMAN.exists():
        return []
    by_id = {e["id"]: e for e in (examples if examples is not None else claim_examples())}
    ok, problems, seen = _claim_ok(by_id), [], set()
    current = _resolve(_read(HUMAN).get("claim_labels", []), lambda x: x["example_id"], ok)
    for n, x in enumerate(_read(HUMAN).get("claim_labels", [])):
        i, v = x.get("example_id"), x.get("version", 1)
        if (i, v) in seen:
            problems.append(f"{i} v{v}: duplicate version, ignored")
        seen.add((i, v))
        if i not in by_id:
            problems.append(f"entry {n}: unknown example id {i!r}")
        elif not isinstance(v, int) or current.get(i, {}).get("version", 0) < v:
            problems.append(f"{i} v{v}: not in force (invalid reviewer or label, stale claim hash, skipped version, or a correction without a reason)")
    return problems


def authoritative_requirement_labels() -> dict[tuple[str, str, str], str]:
    """{(run_key, question_id, requirement_id): label} from human_labels.json (end-to-end completeness), same reviewer and version rules."""
    if not HUMAN.exists():
        return {}
    cur = _resolve(_read(HUMAN).get("requirement_labels", []), lambda x: (x["run_key"], x["question_id"], x["requirement_id"]),
                   lambda x: x.get("label") in REQUIREMENT_LABELS)
    return {k: e["label"] for k, e in cur.items()}


def authoritative_report_claim_labels() -> dict[tuple[str, int], str]:
    """{(run_key, claim_id): label} for claims that appeared in a report, same reviewer and version rules."""
    if not HUMAN.exists():
        return {}
    cur = _resolve(_read(HUMAN).get("report_claim_labels", []), lambda x: (x["run_key"], x["claim_id"]), lambda x: x.get("label") in VERDICTS)
    return {k: e["label"] for k, e in cur.items()}
