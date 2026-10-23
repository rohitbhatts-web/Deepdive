"""The exact evidence context the frozen verifier receives for a dataset example.

Label review must use THIS text and not a longer or shorter excerpt: the verifier sees the cited passage cut at `verifier_context_chars` (plus
neighbouring passages of the same source, which a frozen example does not have). A claim whose decisive text lies outside what the verifier sees
cannot be judged by it, so the check below also guards against a label that depends on text the verifier never gets.
"""

import tempfile
from pathlib import Path

from backend import claims, db
from backend.agents.verifier import evidence_block, user_message
from backend.config import Settings
from evaluation import verify_eval


def view(example: dict, settings: Settings | None = None) -> dict:
    """{prompt, evidence_block, cited: [{passage_id, shown_text, truncated, quote_visible}], context_passages}."""
    settings = settings or Settings()
    conn = db.connect(Path(tempfile.mkdtemp(prefix="deepdive-view-")) / "view.db")
    try:
        _run_id, claim_id = verify_eval.prepare(conn, example, settings)
        claim = dict(conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone())
        evidence = [e for e in claims.claim_evidence(conn, claim_id) if e["passage_id"]]
        block, shown = evidence_block(conn, claim, evidence, settings)
        cited = []
        for e in evidence:
            text = conn.execute("SELECT text FROM passages WHERE id = ?", (e["passage_id"],)).fetchone()[0]
            seen = text[: settings.verifier_context_chars]
            cited.append({"passage_id": e["passage_id"], "shown_text": seen, "truncated": len(text) > len(seen),
                          "quote_visible": claims.find_quote(seen, e["quote"]) is not None})
        prompt = user_message(claim["text"], block)
        return {"prompt": prompt, "evidence_block": block, "cited": cited, "context_passages": sorted(shown - {c["passage_id"] for c in cited})}
    finally:
        conn.close()
