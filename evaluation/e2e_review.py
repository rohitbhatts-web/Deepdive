"""Review sheets for a collected end-to-end run: (1) the claims in each final report, (2) the answer requirements of each question.

Both are for a HUMAN. They are read-only views of a finished run's database. They deliberately leave out everything the pipeline concluded about its own
work (verifier verdicts, coverage status, gaps, report outcome): the pipeline's coverage judgements are never ground truth for completeness, and showing
them would anchor the reviewer. What a sheet shows is facts: the question, the requirement, the report's claims verbatim with their cited quotes and
passage text, and (for the search-record requirements) the queries the run actually sent. Labels are keyed `QUESTION@RUN_ID`, the key the result builder
looks up, and are entered in human_labels.json (`report_claim_labels`, `requirement_labels`). Nothing here creates a label.
"""

import csv
import sqlite3
from pathlib import Path

from backend import db
from backend import report as report_db
from evaluation import e2e_eval
from evaluation.datasets import LABEL_PROVENANCE, load_questions

CLAIM_COLUMNS = ["run_key", "question_id", "claim_id", "claim", "cited_quotes", "source_urls", "human_label", "reviewer", "reviewed_at", "note"]
REQ_COLUMNS = ["run_key", "question_id", "requirement_id", "requirement", "kind", "run_status", "human_label", "reviewer", "reviewed_at", "note"]
CLAIM_LABELS = """Report-claim labels (judge the claim ONLY against the cited passage text shown with it):
- supported: every assertion is established by the cited evidence. partially_supported: some are, others missing or overstated, with no explicit contradiction.
- unsupported: the cited evidence does not establish it. contradicted: the cited evidence explicitly states something incompatible with any assertion.
"""
REQ_LABELS = """Requirement labels (judge the FINAL ANSWER below against the requirement; the pipeline's own assessment is deliberately not shown):
- addressed: the answer fully meets the requirement with claims that are in the report.
- partially_addressed: it meets part of it. missing: it does not meet it.
- abstained_appropriately: the evidence for this requirement was not found and the report says so without stating a figure (evidence-seeking questions).
- abstention_failed: the report states an answer the evidence does not support, or fails to say that nothing was found.
"""


def _rows(conn: sqlite3.Connection, run_id: int, question: dict) -> dict:
    rep = report_db.get_report(conn, run_id)
    claims_out, answer = [], []
    for section in (rep["sections"] if rep else []):
        answer.append((section["label"], section["subquestion_text"], []))
        for item in section["claims"]:
            cites = []
            for ct in item["citations"]:
                p = conn.execute("SELECT text FROM passages WHERE id = ?", (ct["passage_id"],)).fetchone()
                cites.append({"url": ct["url"], "title": ct["title"], "quotes": ct["quotes"], "passage_text": p["text"] if p else "(passage not found)"})
            claims_out.append({"claim_id": item["claim_id"], "text": item["text"], "cites": cites})
            answer[-1][2].append(item["text"])
    searches = [s["query"] for s in db.list_searches(conn, run_id)]
    return {"claims": claims_out, "answer": answer, "searches": searches, "has_report": rep is not None}


def build(db_path: Path, outcomes: list[tuple[str, int]], out_stem: Path, annotations: dict | None = None) -> list[Path]:
    """`outcomes` = [(question_id, run_id)] from the collected result. Writes four files next to `out_stem` and returns them. A run that failed shows its
    failure and stage on the requirements sheet (read from the run database; `annotations` {"QUESTION@RUN": {...}} may add a confirmed classification)."""
    questions = {q["id"]: q for q in load_questions()}
    conn = sqlite3.connect(f"file:{db_path.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    claim_md = ["# Final-report claims to label (end-to-end pilot)", "", f"Label provenance when imported: {LABEL_PROVENANCE}", "", CLAIM_LABELS, "---", ""]
    req_md = ["# Answer requirements to label (end-to-end pilot)", "", f"Label provenance when imported: {LABEL_PROVENANCE}", "", REQ_LABELS, "---", ""]
    claim_rows, req_rows = [], []
    try:
        for qid, run_id in outcomes:
            q = questions[qid]
            key = f"{qid}@{run_id}"
            if q["split"] != "dev":
                raise SystemExit(f"{qid} is not a development question; review sheets for held-out questions are made only for the final run")
            data = _rows(conn, run_id, q)
            fail = e2e_eval.failure_info(conn, run_id)
            note = (annotations or {}).get(key, {})
            status = ""
            if fail:
                cls = note.get("classification") or fail["classification"]
                status = f"RUN FAILED at {fail['stage']}; {cls}"
            req_md += [f"## {key}: {q['question']}", ""]
            if fail:
                req_md += [f"> **THIS RUN FAILED and produced no final answer.** Stage: {fail['stage']}. Stopping reason: {fail['reason']}. Classification: {cls}"
                           + (f" (annotation: {note['evidence']})" if note.get("evidence") else f" (automatic reading: {fail['classification']})") + ".",
                           "> A failed run is not an abstention. Label the requirements as the report allows (there is no report), do not treat the absence as an appropriate abstention.", ""]
            if q.get("abstention_expectation"):
                req_md += [f"**What an acceptable answer looks like (written before the run):** {q['abstention_expectation']}", ""]
            req_md += ["**Final answer (the report's claims, verbatim):**", ""]
            if not data["has_report"] or not data["claims"]:
                req_md += ["_The report contains no claims._", ""]
            for label, sub, texts in data["answer"]:
                req_md += [f"- *{label}* ({sub})"] + [f"  - {t}" for t in texts]
            if data["searches"]:
                req_md += ["", "**Searches this run actually sent:** " + "; ".join(f'"{s}"' for s in data["searches"]), ""]
            req_md += ["", "**Requirements to label:**", ""]
            for r in q["answer_requirements"]:
                beyond = ("  *(beyond the explicit question: " + r["annotation"] + ")*") if r.get("beyond_explicit_question") else ""
                req_md += [f"- **{r['id']}** ({r['kind']}): {r['text']}{beyond}", "  human label: ______________   reviewer: ______________   note: ______________"]
                req_rows.append({"run_key": key, "question_id": qid, "requirement_id": r["id"], "requirement": r["text"], "kind": r["kind"],
                                 "run_status": status, "human_label": "", "reviewer": "", "reviewed_at": "", "note": ""})
            req_md += ["", "---", ""]
            claim_md += [f"## {key}: {q['question']}", ""]
            for c in data["claims"]:
                claim_md += [f"### Claim {c['claim_id']}", "", f"**Claim:** {c['text']}", ""]
                for ct in c["cites"]:
                    claim_md += [f"**Source:** {ct['title'] or 'untitled'} - <{ct['url']}>", ""] + [f"> Cited quote: \"{x}\"" for x in ct["quotes"]]
                    claim_md += ["", "Cited passage text:", "", "```text", ct["passage_text"], "```", ""]
                claim_md += ["human label: ______________   reviewer: ______________   note: ______________", "", "---", ""]
                claim_rows.append({"run_key": key, "question_id": qid, "claim_id": c["claim_id"], "claim": c["text"],
                                   "cited_quotes": " || ".join(x for ct in c["cites"] for x in ct["quotes"]),
                                   "source_urls": " || ".join(ct["url"] for ct in c["cites"]), "human_label": "", "reviewer": "", "reviewed_at": "", "note": ""})
    finally:
        conn.close()
    paths = []
    for suffix, md, rows, cols in (("_report_claims", claim_md, claim_rows, CLAIM_COLUMNS), ("_requirements", req_md, req_rows, REQ_COLUMNS)):
        md_path, csv_path = out_stem.with_name(out_stem.name + suffix + ".md"), out_stem.with_name(out_stem.name + suffix + ".csv")
        md_path.write_text("\n".join(md) + "\n", encoding="utf-8")
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)
        paths += [md_path, csv_path]
    return paths

