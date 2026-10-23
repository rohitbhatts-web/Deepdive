"""Human review sheets and label import.

The sheet shows each claim and EXACTLY the evidence text the frozen verifier receives for it (not a shorter or longer excerpt), the written label
rules, and the PROVISIONAL proposed label with a rationale and the rule that decides it. The human label and reviewer columns start empty.

Only `import_labels` can create an authoritative label, and only in human_labels.json: it needs a valid label, a reviewer name that is not an AI
or model name, and an explicit confirmation from the person importing that a human made the judgement. Nothing an AI suggests is imported.
"""

import csv
import json
from datetime import date
from pathlib import Path

from evaluation import verifier_view
from evaluation.datasets import (
    HUMAN,
    LABEL_PROVENANCE,
    claim_examples,
    claim_hash,
    current_label_entries,
    load_claim_dataset,
    require_evidence,
    valid_reviewer,
)
from evaluation.metrics import VERDICTS

BATCH1 = ["CE01", "CE08", "CE11", "CE12", "CE16", "CE21", "CE23", "CE30", "CE31", "CE36", "CE37", "CE40"]  # dev examples only, mixed labels and tags
COLUMNS = ["id", "claim", "quote", "source_url", "retrieved_at", "proposed_label_PROVISIONAL", "deciding_rule", "rationale", "synthetic",
           "human_label", "reviewer", "reviewed_at", "note", "correction_reason"]
LABELS = """Labels (judge the claim ONLY against the evidence text shown below each claim, which is exactly what the verifier receives):
- supported: every assertion, including numbers, dates, qualifiers and attribution, is established by the evidence.
- partially_supported: some assertions are established and at least one is missing, stronger, broader or more certain, WITHOUT explicit contradiction.
- unsupported: the evidence does not establish the claim's central fact.
- contradicted: the evidence explicitly states something incompatible with any assertion of the claim. An explicit contradiction takes precedence over
  partial support, even when another part of the claim is established.
"""


def select(ids: list[str]) -> list[dict]:
    by_id = {e["id"]: e for e in claim_examples()}
    return [by_id[i] for i in ids]


def _rules_block() -> list[str]:
    return [f"- **{r['id']} {r['title']}.** {r['text']}" for r in load_claim_dataset()["label_rules"]]


def render_markdown(examples: list[dict], title: str) -> str:
    require_evidence(examples, "rendering review sheets")
    data = load_claim_dataset()
    flags = data.get("rubric_flags", {})
    out = [f"# {title}", "", f"Label rubric version: **{data.get('label_rubric_version')}**. Label provenance when imported: {LABEL_PROVENANCE}", "",
           LABELS, "## Rules that decide ambiguous cases", "", *_rules_block(), "",
           ("Fill the fields under each example. `Proposed` is a PROVISIONAL suggestion from a language model: it is not the answer, it may be wrong, "
            "and nothing in it is a label until you write one. Leave `human label` empty to skip. Frozen examples show no neighbouring passages; the "
            "production verifier would also receive neighbours."), "", "---", ""]
    for e in examples:
        view = verifier_view.view(e)
        out += [f"## {e['id']}  ({e['split']})", "", f"**Claim:** {e['claim']}", ""]
        for ev, shown in zip(e["evidence"], view["cited"], strict=True):
            visible = "" if shown["quote_visible"] else "  (WARNING: the quote lies outside the text the verifier receives)"
            cut = "  (cut at the verifier's limit)" if shown["truncated"] else "  (complete: nothing is cut)"
            out += [f"**Source** - {ev['title'] or 'untitled'} - <{ev['url']}> - retrieved {ev['retrieved_at'][:10]}", "",
                    f"> Cited quote: \"{ev['quote']}\"", "", f"Evidence text the verifier receives{cut}{visible}:", "", "```text", shown["shown_text"], "```", ""]
        alt = f" SYNTHETIC alteration ({e['synthetic']['kind']}): {e['synthetic']['detail']}" if e["synthetic"] else ""
        rule = f" Deciding rule: {e['rule']}." if e.get("rule") else ""
        out += [f"**Proposed (PROVISIONAL, not a label):** `{e['proposed']['label']}` - {e['proposed']['rationale']}{alt}{rule}"]
        if e["id"] in flags:
            fl = flags[e["id"]]
            out += ["", f"**FLAGGED FOR REVIEW under {fl['rubric']}:** the proposed label was made under the superseded rule; rubric v2 would give `{fl['v2_would_give']}`. {fl['why']}"]
        if e["ambiguity"]:
            out += ["", f"**Why a reviewer might hesitate:** {e['ambiguity']}"]
        out += ["", "human label: ______________   reviewer: ______________   note: ______________", "", "---", ""]
    return "\n".join(out)


def write_csv(examples: list[dict], path: Path) -> None:
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for e in examples:
            ev = e["evidence"]
            w.writerow({"id": e["id"], "claim": e["claim"], "quote": " || ".join(i["quote"] for i in ev), "source_url": " || ".join(i["url"] for i in ev),
                        "retrieved_at": " || ".join(i["retrieved_at"][:10] for i in ev), "proposed_label_PROVISIONAL": e["proposed"]["label"],
                        "deciding_rule": e.get("rule") or "", "rationale": e["proposed"]["rationale"],
                        "synthetic": json.dumps(e["synthetic"]) if e["synthetic"] else "", "human_label": "", "reviewer": "", "reviewed_at": "", "note": "", "correction_reason": ""})


def import_labels(csv_path: Path, human_path: Path = HUMAN, examples: list[dict] | None = None, *, confirm_human: bool = False,
                  correct: bool = False, provenance: str = LABEL_PROVENANCE) -> dict:
    """Add rows that have a valid human_label AND a human reviewer name to human_labels.json. `confirm_human` must be True: it records that the
    person running the import made these judgements themselves. Skipped rows are listed with the reason.

    Entries are versioned and never overwritten. A new label is version 1. An already-labelled id is skipped unless `correct` is True; with it, a row
    that has a `correction_reason` and a DIFFERENT label is appended as the next version (supersedes the previous one, which stays in the file with
    its reviewer and date). Each entry records the rubric version and the provenance."""
    if not confirm_human:
        raise SystemExit("refusing to import: labels become authoritative only when a human confirms they are their own judgements "
                         "(pass --i-am-the-human-reviewer)")
    by_id = {e["id"]: e for e in (examples if examples is not None else claim_examples())}
    rubric = load_claim_dataset().get("label_rubric_version")
    store = json.loads(human_path.read_text(encoding="utf-8")) if human_path.exists() else {}
    store.setdefault("claim_labels", [])
    added, corrected, skipped = [], [], []
    with csv_path.open(newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            label, reviewer = row["human_label"].strip(), row["reviewer"].strip()
            reason = (row.get("correction_reason") or "").strip()
            if not label and not reviewer:
                continue
            stored = [x for x in store["claim_labels"] if x.get("example_id") == row["id"]]
            current = current_label_entries(list(by_id.values()), store["claim_labels"]).get(row["id"]) if stored else None
            if row["id"] not in by_id:
                skipped.append((row["id"], "unknown example id"))
            elif label not in VERDICTS:
                skipped.append((row["id"], f"invalid label {label!r}"))
            elif not reviewer:
                skipped.append((row["id"], "reviewer is empty"))
            elif not valid_reviewer(reviewer):
                skipped.append((row["id"], f"reviewer {reviewer!r} looks like an AI or model name, not a person"))
            elif stored and current is None:
                skipped.append((row["id"], "a stored label exists but is not in force (stale claim hash or invalid entry); resolve it in human_labels.json"))
            elif stored and not correct:
                skipped.append((row["id"], f"already labelled (version {current.get('version', 1)}); to change it import with --correct and a correction_reason column"))
            elif stored and not reason:
                skipped.append((row["id"], "a correction needs a correction_reason"))
            elif stored and label == current["label"]:
                skipped.append((row["id"], f"unchanged: version {current.get('version', 1)} already says {label}"))
            else:
                entry = {"example_id": row["id"], "label": label, "reviewer": reviewer, "source": "human", "claim_sha256": claim_hash(by_id[row["id"]]),
                         "reviewed_at": row["reviewed_at"].strip() or date.today().isoformat(), "note": row["note"].strip(),
                         "rubric_version": rubric, "provenance": provenance, "version": 1, "supersedes": None}
                if stored:
                    entry.update(version=current.get("version", 1) + 1, supersedes=current.get("version", 1), correction_reason=reason,
                                 previous_label=current["label"], previous_reviewer=current["reviewer"])
                store["claim_labels"].append(entry)
                (corrected if stored else added).append(row["id"])
    human_path.write_text(json.dumps(store, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    return {"added": added, "corrected": corrected, "skipped": skipped}
