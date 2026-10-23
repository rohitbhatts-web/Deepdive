"""Build evaluation/data/claim_evidence.json from the stored live databases (read-only) and claim_examples_source.py.

The databases are not committed (they are git-ignored), so the BUILT JSON is the artefact: it embeds every exact passage, its source URL, title,
retrieval date and content hash, and where it came from. Re-running this script needs the databases and refuses to overwrite a file whose
examples already carry human labels in data/human_labels.json (labels are keyed by example id and must keep pointing at the same claim).

    uv run python -m evaluation.build_claim_dataset
"""

import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path

from evaluation.claim_examples_source import (
    EXAMPLES,
    HELD_OUT,
    LABEL_RULES,
    REVIEW_LOG,
    RUBRIC_FLAGS,
    RUBRIC_HISTORY,
    RUBRIC_VERSION,
    SOURCES,
)

DATA = Path(__file__).parent / "data"
DB_DIR = Path(__file__).resolve().parent.parent / "data"
MIN_QUOTE = 15  # same minimum as the pipeline's integrity check


def _open(name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{(DB_DIR / name).as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def _seen_in_development() -> set[tuple[str, int]]:
    """(content_hash, passage ordinal) of every passage a claim of the earlier development runs cited. Claims written for this dataset
    that use the same passages are NOT independent of development: this is recorded, not hidden."""
    seen: set[tuple[str, int]] = set()
    for db_file in sorted(DB_DIR.glob("*.db")):
        conn = sqlite3.connect(f"file:{db_file.as_posix()}?mode=ro", uri=True)
        try:
            rows = conn.execute(
                "SELECT s.content_hash, p.ordinal FROM claim_evidence e JOIN passages p ON p.id = e.passage_id"
                " JOIN sources s ON s.id = p.source_id WHERE e.passage_id IS NOT NULL").fetchall()
        except sqlite3.Error:
            rows = []
        finally:
            conn.close()
        seen |= {(h, o) for h, o in rows}
    return seen


def build() -> dict:
    seen = _seen_in_development()
    dbs: dict[str, sqlite3.Connection] = {}
    sources: dict[str, dict] = {}
    passages: dict[str, dict] = {}
    for key, (db_file, source_id) in SOURCES.items():
        conn = dbs.setdefault(db_file, _open(db_file))
        s = conn.execute("SELECT final_url, requested_url, title, retrieved_at, content_type, content_hash FROM sources WHERE id = ?",
                         (source_id,)).fetchone()
        sources[key] = {"url": s["final_url"], "requested_url": s["requested_url"], "title": s["title"], "retrieved_at": s["retrieved_at"],
                        "content_type": s["content_type"], "content_hash": s["content_hash"],
                        "provenance": {"database": db_file, "source_id": source_id,
                                       "database_sha256": hashlib.sha256((DB_DIR / db_file).read_bytes()).hexdigest()}}
    out = []
    for n, e in enumerate(EXAMPLES, 1):
        ex_id = f"CE{n:02d}"
        evidence = []
        for item in e["evidence"]:
            key = f"{item['source']}:P{item['ordinal']}"
            if key not in passages:
                db_file, source_id = SOURCES[item["source"]]
                row = dbs[db_file].execute("SELECT id, text FROM passages WHERE source_id = ? AND ordinal = ?",
                                           (source_id, item["ordinal"])).fetchone()
                passages[key] = {"source": item["source"], "ordinal": item["ordinal"], "stored_passage_id": row["id"], "text": row["text"],
                                 "cited_by_development_claims": (sources[item["source"]]["content_hash"], item["ordinal"]) in seen}
            if item["quote"] not in passages[key]["text"]:
                raise SystemExit(f"{ex_id}: quote is not verbatim in {key}: {item['quote']!r}")
            if len(item["quote"]) < MIN_QUOTE:
                raise SystemExit(f"{ex_id}: quote shorter than {MIN_QUOTE} characters")
            evidence.append({"passage": key, "quote": item["quote"]})
        out.append({
            "id": ex_id, "split": "heldout" if ex_id in HELD_OUT else "dev", "claim": e["claim"], "subquestion": e["subquestion"],
            "evidence": evidence, "tags": e["tags"], "synthetic": e["synthetic"], "ambiguity": e["ambiguity"], "rule": e["rule"],
            "proposed": {"label": e["proposed_label"], "status": "provisional", "proposed_by": "language model (Claude), not a human reviewer",
                         "rationale": e["rationale"]},
        })
    for key in list(passages):
        passages[key]["cited_by_development_claims"] = bool(passages[key]["cited_by_development_claims"])
    return {"schema": "deepdive-eval-claim-evidence/1", "built": date.today().isoformat(),
            "label_policy": ("`proposed` labels are PROVISIONAL model suggestions. They are never ground truth and the harness never scores "
                             "against them. Authoritative labels live only in human_labels.json."),
            "evaluation_scope": "single_domain_database_evidence",
            "domain_limitation": ("All evidence is reused stored evidence from the SQLite/PostgreSQL development runs. This claim set is a single-domain "
                                  "verification test set: it is NOT a cross-domain evaluation and says nothing about other domains."),
            "label_rules": LABEL_RULES, "label_rubric_version": RUBRIC_VERSION, "label_rubric_history": RUBRIC_HISTORY,
            "rubric_flags": RUBRIC_FLAGS, "label_review_log": REVIEW_LOG,
            "independence_note": ("Passages with cited_by_development_claims=true were cited by claims in the Phase 3/4 development runs. The "
                                  "held-out split is independent in its CLAIMS only, not in its evidence sources."),
            "sources": sources, "passages": passages, "examples": out}


if __name__ == "__main__":
    labels = DATA / "human_labels.json"
    target = DATA / "claim_evidence.json"
    if target.exists() and labels.exists() and json.loads(labels.read_text(encoding="utf-8")).get("claim_labels"):
        sys.exit("human labels exist: refusing to rebuild claim_evidence.json (their example ids must keep meaning the same claim)")
    target.write_text(json.dumps(build(), indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    print("wrote", target, "with", len(EXAMPLES), "examples")
