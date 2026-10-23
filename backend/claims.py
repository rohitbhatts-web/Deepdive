"""Claims: storage helpers, the deterministic evidence-integrity check, and derived claim state.

Integrity is a necessary condition, never a verdict: a quote that matches its passage shows the evidence reference is
real, not that the quote supports the claim. Only the semantic verifier (a separate stage) judges support.

Quote matching is exact first. If that fails, ONE conservative normalization is tried and the match is labelled
'normalized' in the stored details. The normalization only removes differences in how the same characters are laid
out in extracted web text:
  - runs of whitespace (including non-breaking spaces and newlines) become one space, ends stripped;
  - typographic single/double quotes become ASCII quotes;
  - soft hyphens and zero-width characters are deleted.
It never changes case, digits, dashes, words or punctuation, so it cannot turn a different number or date into a match.
"""

import json
import re
import sqlite3

from backend import db
from backend.config import Settings

SEMANTIC_VERDICTS = ("supported", "partially_supported", "unsupported", "contradicted")

# Version of the verifier's rubric (prompt + checklist). Every new semantic result is stamped with it. A 'supported'
# verdict made under an older rubric (for example before the attribution check existed) is NOT trusted for reports: the
# claim is flagged `needs_rejudge` and must be re-judged (a new result is appended; the old one stays in the history).
CURRENT_RUBRIC = "attribution-v5"

# attribution-v2 (2026-10-22) aligned the bundled-claim instruction with the evaluation label rubric claim-labels-v2: an explicit contradiction of any
# asserted part takes precedence over partial support. Earlier verdicts are never rewritten; each keeps the rubric it was stamped with.
# The previous wording and prompt hash are kept here for audit.
RUBRIC_HISTORY = {
    "attribution-v1": {
        "bundled_claim_instruction": ("If a claim bundles independent assertions, judge each one: any that the evidence does not establish makes the claim "
                                      "partially_supported."),
        "verifier_prompt_sha256": "2f8203120c7068c8fac20c59c020f20fb81179e4b4ee0b6ad47df9ee0ea4c215"},
    "attribution-v5": {
        "added_instruction": ("The semantic-verifier request now contains only the claim and its evidence: no research question, no subquestion, no requested answer "
                              "(verifier.USER_TEMPLATE, part of the prompt hash). Relevance to the question, original versus revision, and missing requested "
                              "information are judged by the coverage/relevance stage, which now receives the question and each subquestion."),
        "note": ("A changed input contract: a verdict made with the subquestion in view may differ from one made without it in either direction, so NO older verdict "
                 "is trusted for a new report (SUPPORTED_TRUSTED_RUBRICS). Older verdicts and the reports built from them stay as history; re-verifying is the "
                 "existing visible, budgeted re-judge step, and nothing is re-judged automatically.")},
    "attribution-v4": {
        "verifier_prompt_sha256": "af99eaf93988d80cef74253a3365238b9b93922f84eb0bd022ddf580b58457b2",
        "added_instruction": ("The subquestion is context, not a standard: a claim whose every assertion is established stays supported even if it leaves part of the "
                              "subquestion unanswered (a coverage matter)."),
        "note": ("Can only turn a non-supported verdict into supported; it never invalidates a supported one, so attribution-v3 supported verdicts stay trusted. "
                 "attribution-v1/v2 do not (they predate the revision rule).")},
    "attribution-v3": {
        "added_instruction": ("Original versus later revision: a date presented as when something was first published, released or introduced is a date mismatch "
                              "(not supported) when the evidence shows the item is a later revision or replacement of an earlier one."),
        "note": ("Narrows 'supported'. attribution-v1/v2 supported verdicts were not judged against this rule: they are kept as history but are NOT "
                 "report-eligible for new assembly until re-verified under attribution-v3 (SUPPORTED_TRUSTED_RUBRICS).")},
    "attribution-v2": {
        "bundled_claim_instruction": ("An explicit contradiction of any asserted part takes precedence: the claim is contradicted even when other parts are "
                                      "supported. Otherwise, parts that are merely incomplete or overstated make it partially_supported.")},
}
# Only a 'supported' verdict made under the CURRENT rubric qualifies for a NEW report. attribution-v3 narrowed 'supported' (a date for the original
# publication of something that the evidence shows is a later revision is no longer supported), so v1/v2 verdicts were not judged against the rule
# the new report is held to. They are not deleted or rewritten: the stored verdict keeps its rubric stamp, historical reports still show it, and such a
# claim is listed as ineligible ("needs re-judge") until the completion step re-verifies it under the current rubric (a visible, budgeted request).
# (Between attribution-v1 and v2 supported verdicts stayed valid because that change could not affect them; that is no longer the case.)
SUPPORTED_TRUSTED_RUBRICS = ("attribution-v5",)

_QUOTE_MAP = str.maketrans({"‘": "'", "’": "'", "‚": "'", "‛": "'",
                            "“": '"', "”": '"', "„": '"', "‟": '"'})
_INVISIBLE = dict.fromkeys(map(ord, "­\u200b‌‍⁠﻿"))


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text.translate(_QUOTE_MAP).translate(_INVISIBLE)).strip()


def find_quote(passage_text: str, quote: str) -> dict | None:
    """Locate `quote` in `passage_text`. Returns {mode, start} (start is an offset in the passage text for exact
    matches, in the normalized passage for normalized ones) or None."""
    idx = passage_text.find(quote)
    if idx != -1:
        return {"mode": "exact", "start": idx}
    nq = normalize(quote)
    if nq:
        idx = normalize(passage_text).find(nq)
        if idx != -1:
            return {"mode": "normalized", "start": idx}
    return None


# --- storage -----------------------------------------------------------------------------------------------------


def resolve_passage(conn: sqlite3.Connection, run_id: int, ref: str) -> tuple[str | None, str]:
    """-> (passage_id or None, resolution). Only a passage of THIS run resolves."""
    row = conn.execute(
        "SELECT p.id, s.run_id FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id = ?", (ref,)
    ).fetchone()
    if row is None:
        return None, "unknown_passage"
    if row["run_id"] != run_id:
        return None, "cross_run_passage"
    return row["id"], "resolved"


def add_claim(
    conn: sqlite3.Connection,
    run_id: int,
    subquestion_id: int,
    text: str,
    evidence: list[tuple[str, str]],
    *,
    origin: str,
    drafter_model: str | None = None,
    draft_call_id: int | None = None,
    provenance: dict | None = None,
    parent_claim_id: int | None = None,
    version: int = 1,
) -> int:
    """Insert one claim version with its evidence references. The caller supplies the transaction."""
    cur = conn.execute(
        "INSERT INTO claims (run_id, subquestion_id, parent_claim_id, version, text, origin, drafter_model,"
        " draft_call_id, provenance, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, subquestion_id, parent_claim_id, version, text, origin, drafter_model, draft_call_id,
         json.dumps(provenance or {}), db.utcnow()),
    )
    claim_id = cur.lastrowid
    for position, (ref, quote) in enumerate(evidence):
        passage_id, resolution = resolve_passage(conn, run_id, ref)
        conn.execute(
            "INSERT INTO claim_evidence (claim_id, position, passage_ref, passage_id, resolution, quote)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (claim_id, position, ref, passage_id, resolution, quote),
        )
    return claim_id


def add_challenge_claim(
    conn: sqlite3.Connection,
    run_id: int,
    subquestion_id: int,
    text: str,
    evidence: list[tuple[str, str]],
    *,
    alteration: str,
    derived_from: str,
) -> int:
    """A deliberately altered claim used to test the verifier. Stored with origin 'controlled_challenge' and a
    provenance that says so, so it can never be mistaken for a naturally drafted or naturally rejected claim."""
    provenance = {"test_input": True, "purpose": "controlled challenge of the verifier",
                  "alteration": alteration, "derived_from": derived_from}
    with db.tx(conn):
        return add_claim(conn, run_id, subquestion_id, text, evidence, origin="controlled_challenge",
                         provenance=provenance)


def claim_evidence(conn: sqlite3.Connection, claim_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM claim_evidence WHERE claim_id = ? ORDER BY position", (claim_id,))]


def record_result(
    conn: sqlite3.Connection,
    claim_id: int,
    stage: str,
    verdict: str,
    reason: str,
    *,
    model: str | None = None,
    model_call_id: int | None = None,
    details: dict | None = None,
    evidence_ids: list[int] | None = None,
    passages: list[tuple[str, str]] | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO verification_results (claim_id, stage, verdict, reason, model, model_call_id, details, created_at)"
        " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (claim_id, stage, verdict, reason, model, model_call_id, json.dumps(details or {}), db.utcnow()),
    )
    for evidence_id in evidence_ids or []:
        conn.execute("INSERT OR IGNORE INTO verification_result_evidence (result_id, evidence_id) VALUES (?, ?)",
                     (cur.lastrowid, evidence_id))
    for passage_id, role in passages or []:
        conn.execute("INSERT OR IGNORE INTO verification_result_passages (result_id, passage_id, role) VALUES (?, ?, ?)",
                     (cur.lastrowid, passage_id, role))
    return cur.lastrowid


def _result(conn: sqlite3.Connection, row: sqlite3.Row) -> dict:
    ids = [r[0] for r in conn.execute(
        "SELECT evidence_id FROM verification_result_evidence WHERE result_id = ? ORDER BY evidence_id", (row["id"],))]
    passages = [dict(r) for r in conn.execute(
        "SELECT passage_id, role FROM verification_result_passages WHERE result_id = ? ORDER BY role, passage_id",
        (row["id"],))]
    return {**dict(row), "details": json.loads(row["details"]), "evidence_ids": ids, "passages": passages}


def latest_result(conn: sqlite3.Connection, claim_id: int, stage: str) -> dict | None:
    row = conn.execute(
        "SELECT * FROM verification_results WHERE claim_id = ? AND stage = ? ORDER BY id DESC LIMIT 1",
        (claim_id, stage),
    ).fetchone()
    if row is None:
        return None
    return _result(conn, row)


def claim_state(integrity: dict | None, semantic: dict | None) -> tuple[str, bool]:
    """-> (status, eligible_for_report). Only a claim whose integrity check passed AND whose latest semantic verdict
    is 'supported' passes this stage-level test. Report eligibility has further conditions (not a test input, not
    superseded) and lives in `is_report_eligible`."""
    if integrity is None:
        return "drafted", False
    if integrity["verdict"] == "failed":
        return "integrity_failed", False
    if semantic is None:
        return "awaiting_verification", False
    if semantic["verdict"] == "error":
        return "verification_error", False
    return semantic["verdict"], semantic["verdict"] == "supported"


def is_active(claim: dict) -> bool:
    """A claim still in play: naturally drafted (never a test input), the newest version of its lineage, not excluded."""
    return (claim["origin"] == "drafted" and not claim["provenance"].get("test_input")
            and claim["superseded_by"] is None and claim["excluded"] is None)


def is_report_eligible(claim: dict) -> bool:
    """THE rule for report material; every list, summary and selection goes through this one function.
    All must hold: active (naturally drafted, not a test input, newest version, not excluded), integrity passed, the
    latest semantic verdict is 'supported', and that verdict was made under the CURRENT verifier rubric. An older
    version is stale (its verdict says nothing about the newer text); an excluded claim keeps its history but is out."""
    return (
        is_active(claim)
        and claim["integrity_verdict"] == "passed"
        and claim["semantic_verdict"] == "supported"
        and claim["rubric_trusted"] is True
    )


def citations(conn: sqlite3.Connection, claim: dict) -> list[dict]:
    """What a report may cite for this claim: exactly the passages the latest semantic verdict relied on, each with
    its role and (for the claim's own evidence) the quote(s). Empty until a semantic verdict exists."""
    semantic = claim["semantic"]
    if not semantic:
        return []
    out = []
    for used in semantic["passages"]:
        p = conn.execute(
            "SELECT p.id, p.source_id, s.final_url, s.title FROM passages p JOIN sources s ON s.id = p.source_id"
            " WHERE p.id = ? AND s.run_id = ?", (used["passage_id"], claim["run_id"])).fetchone()
        quotes = [e["quote"] for e in claim["evidence"] if e["passage_id"] == used["passage_id"]]
        out.append({"passage_id": used["passage_id"], "role": used["role"], "quotes": quotes,
                    "source_id": p["source_id"], "url": p["final_url"], "title": p["title"]})
    return out


def get_claim(conn: sqlite3.Connection, run_id: int, claim_id: int) -> dict | None:
    """One claim of THIS run with evidence (including the exact stored passage text) and its results. Integrity and
    semantic outcomes are reported separately; `status` is a convenience that names whichever stage decided."""
    row = conn.execute("SELECT * FROM claims WHERE id = ? AND run_id = ?", (claim_id, run_id)).fetchone()
    if row is None:
        return None
    evidence = []
    for e in claim_evidence(conn, claim_id):
        passage = None
        if e["passage_id"]:
            p = conn.execute(
                "SELECT p.id, p.text, p.start_offset, p.end_offset, s.id AS source_id, s.final_url, s.title"
                " FROM passages p JOIN sources s ON s.id = p.source_id WHERE p.id = ?", (e["passage_id"],)).fetchone()
            passage = dict(p)
        evidence.append({**e, "passage": passage})
    integrity, semantic = latest_result(conn, claim_id, "integrity"), latest_result(conn, claim_id, "semantic")
    status, _ = claim_state(integrity, semantic)
    newer = conn.execute("SELECT max(id) FROM claims WHERE parent_claim_id = ? AND run_id = ?", (claim_id, run_id)).fetchone()[0]
    excluded = conn.execute("SELECT reason, planned_round, created_at FROM claim_dispositions WHERE claim_id = ?",
                            (claim_id,)).fetchone()
    claim = {**dict(row), "provenance": json.loads(row["provenance"]), "evidence": evidence,
             "integrity": integrity, "semantic": semantic, "status": status,
             "integrity_verdict": integrity["verdict"] if integrity else None,
             "semantic_verdict": semantic["verdict"] if semantic else None, "superseded_by": newer,
             "excluded": dict(excluded) if excluded else None,
             "rubric_current": (semantic["details"].get("rubric") == CURRENT_RUBRIC) if semantic else None,
             "rubric_trusted": (semantic["details"].get("rubric") in SUPPORTED_TRUSTED_RUBRICS) if semantic else None}
    claim["needs_rejudge"] = (
        is_active(claim) and claim["integrity_verdict"] == "passed" and claim["semantic_verdict"] == "supported"
        and claim["rubric_trusted"] is False)
    claim["eligible_for_report"] = is_report_eligible(claim)
    claim["citations"] = citations(conn, claim)
    return claim


def list_claims(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    ids = [r[0] for r in conn.execute("SELECT id FROM claims WHERE run_id = ? ORDER BY id", (run_id,))]
    return [get_claim(conn, run_id, i) for i in ids]


def active_claims(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return [c for c in list_claims(conn, run_id) if is_active(c)]


def exclude_claim(conn: sqlite3.Connection, run_id: int, claim_id: int, reason: str, planned_round: int) -> bool:
    """Retire a claim from routing and reports without touching it (its text, verdicts and links stay as they were).
    Returns False if it was already excluded. The caller supplies the transaction."""
    cur = conn.execute(
        "INSERT OR IGNORE INTO claim_dispositions (run_id, claim_id, disposition, reason, planned_round, created_at)"
        " VALUES (?, ?, 'excluded', ?, ?, ?)", (run_id, claim_id, reason, planned_round, db.utcnow()))
    return cur.rowcount == 1


def lineage(conn: sqlite3.Connection, run_id: int, claim_id: int) -> list[dict]:
    """Every version in the claim's family, from the first draft down, oldest first. A claim split into several atomic
    children has all of them listed; each has its own verdicts."""
    root = claim_id
    while True:
        parent = conn.execute("SELECT parent_claim_id FROM claims WHERE id = ? AND run_id = ?", (root, run_id)).fetchone()
        if parent is None or parent[0] is None:
            break
        root = parent[0]
    order, queue = [], [root]
    while queue:
        current = queue.pop(0)
        order.append(current)
        queue += [r[0] for r in conn.execute("SELECT id FROM claims WHERE parent_claim_id = ? AND run_id = ? ORDER BY id", (current, run_id))]
    return [get_claim(conn, run_id, i) for i in order]


def eligible_claims(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """The only supported way to select report material (Phase 4's synthesizer must use this)."""
    return [c for c in list_claims(conn, run_id) if c["eligible_for_report"]]


def all_results(conn: sqlite3.Connection, run_id: int, claim_id: int) -> list[dict]:
    rows = conn.execute(
        "SELECT r.* FROM verification_results r JOIN claims c ON c.id = r.claim_id WHERE c.id = ? AND c.run_id = ?"
        " ORDER BY r.id", (claim_id, run_id)).fetchall()
    return [_result(conn, r) for r in rows]


def summary(conn: sqlite3.Connection, run_id: int) -> dict:
    by_status: dict[str, int] = {}
    by_integrity: dict[str, int] = {}
    by_semantic: dict[str, int] = {}
    rows = list_claims(conn, run_id)
    for claim in rows:
        by_status[claim["status"]] = by_status.get(claim["status"], 0) + 1
        by_integrity[claim["integrity_verdict"] or "unchecked"] = by_integrity.get(claim["integrity_verdict"] or "unchecked", 0) + 1
        by_semantic[claim["semantic_verdict"] or "none"] = by_semantic.get(claim["semantic_verdict"] or "none", 0) + 1
    return {"claims": len(rows), "by_status": by_status, "by_integrity": by_integrity, "by_semantic": by_semantic,
            "eligible_for_report": sum(c["eligible_for_report"] for c in rows)}


# --- deterministic integrity check -------------------------------------------------------------------------------


def check_integrity(conn: sqlite3.Connection, claim_id: int, settings: Settings) -> dict:
    """Validate a claim's evidence without any model. Writes (and returns) an 'integrity' result. Idempotent: a claim
    that already has an integrity result is not checked again."""
    existing = latest_result(conn, claim_id, "integrity")
    if existing:
        return existing
    claim = conn.execute("SELECT * FROM claims WHERE id = ?", (claim_id,)).fetchone()
    refs, failures = [], []
    evidence = claim_evidence(conn, claim_id)
    if not claim["text"].strip():
        failures.append("claim text is empty")
    if not evidence:
        failures.append("claim has no evidence references")
    for e in evidence:
        info = {"evidence_id": e["id"], "passage_ref": e["passage_ref"], "resolution": e["resolution"]}
        problem = None
        if e["resolution"] != "resolved":
            problem = {"unknown_passage": "passage does not exist",
                       "cross_run_passage": "passage belongs to a different run"}.get(
                e["resolution"], "passage does not resolve")
        elif not e["quote"].strip():
            problem = "quote is empty"
        elif len(normalize(e["quote"])) < settings.min_quote_chars:
            problem = f"quote is shorter than {settings.min_quote_chars} characters"
        else:
            text = conn.execute("SELECT text FROM passages WHERE id = ?", (e["passage_id"],)).fetchone()[0]
            match = find_quote(text, e["quote"])
            if match is None:
                problem = "quote does not occur in the cited passage"
            else:
                info["match"] = match
        info["ok"] = problem is None
        if problem:
            info["problem"] = problem
            failures.append(f"{e['passage_ref']}: {problem}")
        refs.append(info)
    passed = not failures
    reason = ("all evidence references resolve and every quote occurs in its passage (integrity only; "
              "this does not show support)") if passed else "; ".join(failures)
    with db.tx(conn):
        rid = record_result(conn, claim_id, "integrity", "passed" if passed else "failed", reason,
                            details={"references": refs})
    return latest_result(conn, claim_id, "integrity") | {"id": rid}
