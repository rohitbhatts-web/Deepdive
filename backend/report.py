"""Report assembly and retrieval. Reports are immutable snapshots built from EXACT stored claim text.

The only model input is an ordering plan (claim IDs). Everything a reader sees comes from rows: claim text copied
verbatim from `claims`, citations copied from the passages the claim's semantic verdict actually relied on, section
labels that are fixed neutral strings, and the planner's own subquestion text. Eligibility is re-checked inside the same
transaction that writes the report, and the database triggers re-check it again on every insert.
"""

import json
import sqlite3

from backend import claims, correction, db
from backend.agents import consistency
from backend.agents.schemas import SynthesisPlan
from backend.config import Settings


class ReportError(RuntimeError):
    """A report could not be built without breaking an invariant."""


def build_report(
    conn: sqlite3.Connection,
    run_id: int,
    *,
    plan: SynthesisPlan | None,
    ordering_reason: str | None,
    model: str | None,
    model_call_id: int | None,
    stop_reason: str | None,
    corrective_rounds: int,
    max_claims: int | None = None,
    settings: Settings | None = None,
) -> dict:
    """Persist one report snapshot and return {"report_id", "outcome", "rejected"}. `plan=None` means a deterministic
    ordering (claim ID order within each subquestion), used when no model call was available or needed.

    With `settings` the report also snapshots the cross-claim consistency state: the stored assessment of EXACTLY these claims and this configuration, or an explicit
    "not checked" (no model call is made here, and an assessment of different input is never attached). Without `settings` (older callers) nothing is recorded and the
    report reads exactly as before. The consistency state never changes which claims are eligible or selected, except that a claim named in a flagged conflict is not
    dropped by the synthesizer as a "duplicate"."""
    rejected: list[dict] = []
    with db.tx(conn):
        subqs = db.list_subquestions(conn, run_id)
        everything = claims.list_claims(conn, run_id)  # read inside the transaction: nothing can change underneath
        eligible = {c["id"]: c for c in everything if c["eligible_for_report"]}
        selection: dict[int, list[int]] = {s["id"]: [] for s in subqs}
        omitted: dict[int, str] = {}
        cstate = consistency.report_state(conn, run_id, sorted(eligible.values(), key=lambda c: c["id"]), settings) if settings is not None else None
        conflicted = consistency.conflict_claim_ids(cstate) if cstate else set()

        if plan is None:
            for c in sorted(eligible.values(), key=lambda c: c["id"]):
                selection[c["subquestion_id"]].append(c["id"])
        else:
            seen: set[int] = set()
            for section in plan.sections:
                if section.subquestion_id not in selection:
                    rejected.append({"why": "unknown subquestion", "subquestion_id": section.subquestion_id})
                    continue
                for claim_id in section.claim_ids:
                    claim = eligible.get(claim_id)
                    if claim is None:
                        rejected.append({"why": "not an eligible claim of this run (fabricated, stale, excluded, test input or not supported)", "claim_id": claim_id})
                    elif claim["subquestion_id"] != section.subquestion_id:
                        rejected.append({"why": "claim belongs to a different subquestion", "claim_id": claim_id})
                    elif claim_id in seen:
                        rejected.append({"why": "duplicate", "claim_id": claim_id})
                    else:
                        seen.add(claim_id)
                        selection[section.subquestion_id].append(claim_id)
            for item in plan.omitted:
                if item.claim_id in eligible and item.claim_id not in seen:
                    omitted[item.claim_id] = item.reason
            for claim_id in eligible:
                if claim_id not in seen:
                    omitted.setdefault(claim_id, "not selected by the synthesizer")
            for claim_id in sorted(conflicted & set(eligible) - seen):  # two claims that may contradict each other are not duplicates: both stay, in their own section
                selection[eligible[claim_id]["subquestion_id"]].append(claim_id)
                omitted.pop(claim_id, None)

        # ANSWER RELEVANCE (from the latest coverage round; support was decided by the verifier and is not re-judged here). Only a direct_answer claim
        # answers a section; relevant_context is shown apart and never stands in for an answer; a claim that answers none of its subquestion's requirements
        # is omitted with that reason. Without any relevance judgment (coverage never ran) the legacy selection is kept and the report says it was not judged.
        round_now = correction.latest_round(conn, run_id)
        role_rows = correction.claim_roles(conn, run_id, round_now) if round_now is not None else []
        judged = bool(role_rows)
        rank = {"direct_answer": 2, "relevant_context": 1, "does_not_answer": 0}
        roles: dict[tuple[int, int], dict] = {}
        for rr in role_rows:
            key = (rr["subquestion_id"], rr["claim_id"])
            cur = roles.get(key)
            if cur is None or rank[rr["role"]] > rank[cur["role"]]:
                roles[key] = {"role": rr["role"], "keys": [rr["requirement_key"]], "reason": rr["reason"]}
            elif rank[rr["role"]] == rank[cur["role"]] and rr["requirement_key"] not in cur["keys"]:
                cur["keys"].append(rr["requirement_key"])
        if judged:
            for sq_id, ids in selection.items():
                for claim_id in list(ids):
                    info = roles.get((sq_id, claim_id))
                    if info is None or info["role"] == "does_not_answer":
                        ids.remove(claim_id)
                        omitted[claim_id] = (f"judged not to answer any requirement of its subquestion (coverage round {round_now})" if info
                                             else f"no answer-relevance judgment for this claim in coverage round {round_now}")
                ids.sort(key=lambda cid: -rank[roles[(sq_id, cid)]["role"]])  # direct answers first, then context; stable otherwise

        if max_claims is not None:  # the report's own size limit, independent of how many candidates were drafted
            kept = 0
            for sq in subqs:
                for claim_id in list(selection[sq["id"]]):
                    if kept >= max_claims:
                        selection[sq["id"]].remove(claim_id)
                        omitted[claim_id] = f"report claim limit ({max_claims}) reached"
                    else:
                        kept += 1
        rows = correction.assessments(conn, run_id)
        chosen = sum(1 for sq_id, v in selection.items() for cid in v if not judged or roles[(sq_id, cid)]["role"] == "direct_answer")
        if chosen == 0:
            outcome = "insufficient_evidence"
        elif rows and correction.coverage_summary(rows)["fully_addressed"]:
            outcome = "claims_all_requirements_addressed"
        else:
            outcome = "claims_with_gaps"

        report_id = conn.execute(
            "INSERT INTO reports (run_id, outcome, coverage_round, corrective_rounds, stop_reason, ordering, ordering_reason,"
            " model, model_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, outcome, correction.latest_round(conn, run_id), corrective_rounds, stop_reason,
             "model" if plan is not None else "deterministic", ordering_reason, model, model_call_id, db.utcnow()),
        ).lastrowid

        conn.execute("INSERT INTO report_relevance (report_id, judged, coverage_round) VALUES (?, ?, ?)", (report_id, 1 if judged else 0, round_now if judged else None))
        if cstate is not None:
            conn.execute(
                "INSERT INTO report_consistency (report_id, check_id, status, cause, reason, fingerprint, shown_claim_ids, unchecked_claim_ids, claim_states, dropped_findings)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (report_id, cstate["check_id"], cstate["status"], cstate["cause"], cstate["reason"][:500], cstate["fingerprint"], json.dumps(cstate["shown_claim_ids"]),
                 json.dumps(cstate["unchecked_claim_ids"]), json.dumps(cstate["claim_states"]), json.dumps(cstate["dropped_findings"])))
            for f in cstate["findings"]:
                conn.execute("INSERT INTO report_consistency_findings (report_id, kind, explanation, claim_ids, evidence, carried_from_check) VALUES (?, ?, ?, ?, ?, ?)",
                             (report_id, f["kind"], f["explanation"], json.dumps(f["claim_ids"]), json.dumps(f["evidence"]), f.get("carried_from_check")))
        section_ids: dict[int, int] = {}
        report_claim_ids: dict[int, int] = {}
        for position, sq in enumerate(subqs, 1):
            section_id = section_ids[sq["id"]] = conn.execute(
                "INSERT INTO report_sections (report_id, position, label, subquestion_id, subquestion_text)"
                " VALUES (?, ?, ?, ?, ?)", (report_id, position, f"Section {position}", sq["id"], sq["question"])).lastrowid
            for claim_position, claim_id in enumerate(selection[sq["id"]], 1):
                claim = eligible[claim_id]
                report_claim_id = conn.execute(
                    "INSERT INTO report_claims (report_id, section_id, position, claim_id, claim_version, claim_text,"
                    " semantic_result_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (report_id, section_id, claim_position, claim_id, claim["version"], claim["text"],
                     claim["semantic"]["id"])).lastrowid
                report_claim_ids[claim_id] = report_claim_id
                if judged:
                    info = roles[(sq["id"], claim_id)]
                    conn.execute("INSERT INTO report_claim_roles (report_claim_id, role, requirement_keys, reason) VALUES (?, ?, ?, ?)",
                                 (report_claim_id, info["role"], json.dumps(info["keys"]), info["reason"]))
                if not claim["citations"]:
                    raise ReportError(f"claim {claim_id} has no resolvable citations")
                for cit in claim["citations"]:
                    conn.execute(
                        "INSERT INTO report_citations (report_claim_id, passage_id, role, quotes, source_id, url, title)"
                        " VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (report_claim_id, cit["passage_id"], cit["role"], json.dumps(cit["quotes"]), cit["source_id"],
                         cit["url"], cit["title"]))

        # A claim stays in its own section; when coverage recorded it as a DIRECT ANSWER to a requirement of another subquestion, that section references the same report
        # claim (no copy: same text, same citations). Only the stored direct-answer judgment of this run and round counts; context never fills an answer.
        answer_refs = []
        if judged:
            for (sq_id, claim_id), info in sorted(roles.items(), key=lambda kv: (kv[0][0], kv[0][1])):
                owner = eligible[claim_id]["subquestion_id"] if claim_id in eligible else None
                if info["role"] == "direct_answer" and claim_id in report_claim_ids and owner != sq_id and sq_id in section_ids:
                    conn.execute("INSERT INTO report_answer_refs (report_id, section_id, report_claim_id, requirement_keys, reason) VALUES (?, ?, ?, ?, ?)",
                                 (report_id, section_ids[sq_id], report_claim_ids[claim_id], json.dumps(info["keys"]), info["reason"]))
                    answer_refs.append({"claim_id": claim_id, "subquestion_id": sq_id, "requirement_keys": info["keys"]})
        by_key = {r["requirement_key"]: r for r in rows}
        for req in correction.requirements(conn, run_id):
            a = by_key.get(req["key"])
            conn.execute(
                "INSERT INTO report_gaps (report_id, requirement_key, subquestion_id, requirement_text, status, gap, missing_kind)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (report_id, req["key"], req["subquestion_id"], req["text"], a["status"] if a else "not_assessed",
                 a["gap"] if a else "", a["missing_kind"] if a else "none"))

        for claim_id, reason in omitted.items():
            conn.execute("INSERT INTO report_omissions (report_id, claim_id, reason) VALUES (?, ?, ?)", (report_id, claim_id, reason))
        for claim in everything:  # active claims that did not qualify are listed, not hidden
            if claims.is_active(claim) and not claim["eligible_for_report"]:
                made = ((claim.get("semantic") or {}).get("details") or {}).get("rubric") or "no rubric recorded"
                why = (f"its supported verdict was made under verifier rubric {made}, not {claims.CURRENT_RUBRIC}, and has not been re-verified"
                       if claim["needs_rejudge"] else claim["status"])
                conn.execute("INSERT OR IGNORE INTO report_omissions (report_id, claim_id, reason) VALUES (?, ?, ?)",
                             (report_id, claim["id"], f"not eligible: {why}"))
        db.add_event(conn, run_id, "report_created", "report", {
            "report_id": report_id, "outcome": outcome, "claims": chosen, "ordering": "model" if plan is not None else "deterministic",
            "rejected_ids": rejected, "answer_refs": answer_refs})
    return {"report_id": report_id, "outcome": outcome, "rejected": rejected}


# --- reading -----------------------------------------------------------------------------------------------------


def _consistency_view(conn: sqlite3.Connection, report_id: int) -> dict:
    """The stored consistency snapshot of one report. Completeness (`status`) and the findings are separate fields and both are always returned: a partial or failed
    assessment still lists the conflicts it recorded. A report made before this existed (or without settings) has status `not_recorded`."""
    try:
        row = conn.execute("SELECT * FROM report_consistency WHERE report_id = ?", (report_id,)).fetchone()
        found = conn.execute("SELECT id, kind, explanation, claim_ids, evidence, carried_from_check FROM report_consistency_findings WHERE report_id = ? ORDER BY id", (report_id,)).fetchall()
    except sqlite3.OperationalError:  # a database from before the tables existed
        row, found = None, []
    if row is None:
        return {"status": "not_recorded", "findings": []}
    findings = [{"id": f["id"], "kind": f["kind"], "explanation": f["explanation"], "claim_ids": json.loads(f["claim_ids"]), "evidence": json.loads(f["evidence"]),
                 "carried_from_check": f["carried_from_check"]} for f in found]
    states = json.loads(row["claim_states"])
    return {"status": row["status"], "cause": row["cause"], "reason": row["reason"], "check_id": row["check_id"], "fingerprint": row["fingerprint"],
            "shown_claim_ids": json.loads(row["shown_claim_ids"]), "unchecked_claim_ids": json.loads(row["unchecked_claim_ids"]),
            "claim_states": states, "limited_evidence": {k: v["state"] for k, v in states.items() if v["state"] not in ("full",)},
            "dropped_findings": json.loads(row["dropped_findings"]), "has_conflicts": any(f["kind"] == "conflict" for f in findings), "findings": findings}


def _legacy_row(conn: sqlite3.Connection, sql: str, args: tuple):
    """A row from a table that databases from before answer relevance do not have (opened read-only, e.g. by the evaluation tools): None, not an error."""
    try:
        return conn.execute(sql, args).fetchone()
    except sqlite3.OperationalError:
        return None


def get_report(conn: sqlite3.Connection, run_id: int, report_id: int | None = None) -> dict | None:
    sql, args = "SELECT * FROM reports WHERE run_id = ?", [run_id]
    if report_id is not None:
        sql += " AND id = ?"
        args.append(report_id)
    head = conn.execute(sql + " ORDER BY id DESC LIMIT 1", args).fetchone()
    if head is None:
        return None
    rid = head["id"]
    sections = []
    rel = _legacy_row(conn, "SELECT judged, coverage_round FROM report_relevance WHERE report_id = ?", (rid,))
    judged_row = bool(rel and rel["judged"])
    by_rc: dict[int, dict] = {}  # report claim id -> its item, so a section can reference another section's claim
    owner_of: dict[int, dict] = {}
    for s in conn.execute("SELECT * FROM report_sections WHERE report_id = ? ORDER BY position", (rid,)).fetchall():
        items = []
        for rc in conn.execute("SELECT * FROM report_claims WHERE section_id = ? ORDER BY position", (s["id"],)).fetchall():
            cites = [{**dict(c), "quotes": json.loads(c["quotes"])} for c in conn.execute(
                "SELECT passage_id, role, quotes, source_id, url, title FROM report_citations WHERE report_claim_id = ? ORDER BY id", (rc["id"],))]
            made = conn.execute("SELECT details FROM verification_results WHERE id = ?", (rc["semantic_result_id"],)).fetchone()
            rubric = (json.loads(made[0]) if made and made[0] else {}).get("rubric")  # the rubric the verdict BEHIND this report claim was made under
            role = _legacy_row(conn, "SELECT role, requirement_keys, reason FROM report_claim_roles WHERE report_claim_id = ?", (rc["id"],))
            items.append({"claim_id": rc["claim_id"], "claim_version": rc["claim_version"], "text": rc["claim_text"],
                          "semantic_result_id": rc["semantic_result_id"], "verifier_rubric": rubric,
                          "role": role["role"] if role else None, "requirement_keys": json.loads(role["requirement_keys"]) if role else [],
                          "role_reason": role["reason"] if role else None, "citations": cites})
            by_rc[rc["id"]] = items[-1]
            owner_of[rc["id"]] = {"label": s["label"], "subquestion_id": s["subquestion_id"], "position": s["position"]}
        direct = [i for i in items if i["role"] in (None, "direct_answer")]  # a report without judgments (older, or coverage never ran) keeps all its claims
        sections.append({"position": s["position"], "label": s["label"], "subquestion_id": s["subquestion_id"], "section_id": s["id"],
                         "subquestion_text": s["subquestion_text"], "claims": direct, "context": [i for i in items if i["role"] == "relevant_context"],
                         "answered": bool(direct) if any(i["role"] for i in items) or judged_row else None})
    try:  # databases from before answer references have no such table; their reports have no references
        refs = conn.execute("SELECT section_id, report_claim_id, requirement_keys, reason FROM report_answer_refs WHERE report_id = ? ORDER BY id", (rid,)).fetchall()
    except sqlite3.OperationalError:
        refs = []
    for ref in refs:
        sec = next(x for x in sections if x["section_id"] == ref["section_id"])
        base = by_rc[ref["report_claim_id"]]
        sec["claims"].append({**base, "role": "direct_answer", "requirement_keys": json.loads(ref["requirement_keys"]), "role_reason": ref["reason"],
                              "cross_reference": {"owner_label": owner_of[ref["report_claim_id"]]["label"], "owner_subquestion_id": owner_of[ref["report_claim_id"]]["subquestion_id"]}})
        sec["answered"] = True
    for sec in sections:
        sec.pop("section_id")
    consistency_view = _consistency_view(conn, rid)
    if consistency_view["findings"]:  # a claim carries a label for every flagged conflict it is part of, wherever it appears (answers, related context, cross-references)
        conflicts = [f for f in consistency_view["findings"] if f["kind"] == "conflict"]
        for sec in sections:
            for item in sec["claims"] + sec["context"]:
                mine = [{"finding_id": f["id"], "with_claim_ids": [i for i in f["claim_ids"] if i != item["claim_id"]]} for f in conflicts if item["claim_id"] in f["claim_ids"]]
                if mine:
                    item["conflicts"] = mine
    requirements = [dict(r) for r in conn.execute("SELECT requirement_key, subquestion_id, requirement_text, status, gap, missing_kind FROM report_gaps WHERE report_id = ? ORDER BY id", (rid,))]
    omissions = [dict(r) for r in conn.execute("SELECT claim_id, reason FROM report_omissions WHERE report_id = ? ORDER BY claim_id", (rid,))]
    # Three different things, never merged: assessed as answered, assessed as NOT (fully) answered, and NOT ASSESSED at all (no coverage assessment covers the
    # requirement, e.g. the job ran out of budget first). An absent assessment is not evidence that a requirement is unanswered.
    unresolved = [r for r in requirements if r["status"] not in ("addressed", "not_assessed")]
    unassessed = [r for r in requirements if r["status"] == "not_assessed"]
    # Per section: which of ITS requirements no claim directly answers, and per claim: which requirements it directly answers (judged reports only).
    by_key = {r["requirement_key"]: r for r in requirements}
    for sec in sections:
        sec["open_requirements"] = ([{"requirement_key": r["requirement_key"], "requirement_text": r["requirement_text"], "status": r["status"], "gap": r["gap"]}
                                     for r in requirements if r["subquestion_id"] == sec["subquestion_id"] and r["status"] not in ("addressed", "not_assessed")]
                                    if judged_row else [])
        for item in sec["claims"] + sec["context"]:
            item["requirement_texts"] = [by_key[k]["requirement_text"] for k in item["requirement_keys"] if k in by_key]
    rubrics = sorted({i["verifier_rubric"] or "no rubric recorded" for s in sections for i in s["claims"]})
    report = {**dict(head), "verifier_rubrics": rubrics, "relevance_judged": judged_row, "sections": sections, "requirements": requirements, "unresolved_requirements": unresolved,
              "fully_addressed": bool(requirements) and not unresolved and not unassessed, "unassessed_requirements": unassessed,
              "coverage_assessed": any(r["status"] != "not_assessed" for r in requirements) if requirements else None,
              "omissions": omissions, "consistency": consistency_view}
    report["markdown"] = render_markdown(report, conn.execute("SELECT question FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
    return report


_STATUS_TEXT = {
    "reviewed": "The model listed every shown claim as reviewed. That is the model's own assertion, not proof that every pair of claims was compared.",
    "partial": "Only partly checked.", "failed": "The check failed; no usable assessment exists.", "not_checked": "Not checked.",
}
_LIMITED = {"shortened": "quotes were shortened", "absent": "no quote text is stored", "id_only": "only the passage ID was shown (no text)", "none": "no evidence was shown",
            "not_shown": "the claim was not shown"}


def _consistency_markdown(c: dict) -> list[str]:
    """Plain rendering of the stored snapshot. Nothing is said for a report made without one, or with fewer than two claims. 'No conflicts flagged' is never worded as
    'consistent'."""
    if c.get("status") in (None, "not_recorded", "not_applicable"):
        return []
    # the stored reason of a `reviewed` check only repeats the fixed status text; for every other status it says WHY the check is incomplete
    why = "" if c["status"] == "reviewed" or not c.get("reason") else f" Reason: {c['reason'].rstrip('.')}."
    lines = ["## Cross-claim consistency", "", f"Status: {c['status']}. {_STATUS_TEXT.get(c['status'], '')}{why}".strip(), ""]
    conflicts = [f for f in c.get("findings", []) if f["kind"] == "conflict"]
    others = [f for f in c.get("findings", []) if f["kind"] != "conflict"]
    if conflicts:
        lines.append("Possible conflicts flagged by the model (each claim is still supported by its own citations; this does not say which claim, if either, is wrong):")
        for f in conflicts:
            lines.append(f"- Claims {', '.join(str(i) for i in f['claim_ids'])}: {f['explanation']}{_evidence_text(f)}")
    elif c["status"] == "reviewed":  # never for a partial, failed or unchecked assessment
        lines.append("The model flagged no conflicts among the claims it examined. This does not show that the claims are consistent.")
    if others:
        lines += ["", "Differences the model judged not to be conflicts (scope, conditions, time or terminology):"]
        lines += [f"- Claims {', '.join(str(i) for i in f['claim_ids'])} ({f['kind']}): {f['explanation']}{_evidence_text(f)}" for f in others]
    if c.get("unchecked_claim_ids"):
        lines += ["", f"Not compared (not shown, or not listed as reviewed): claims {', '.join(str(i) for i in c['unchecked_claim_ids'])}."]
    limited = {k: v for k, v in (c.get("limited_evidence") or {}).items() if v != "not_shown"}
    if limited:
        lines += ["", "Evidence limits: " + "; ".join(f"claim {k}: {_LIMITED.get(v, v)}" for k, v in sorted(limited.items(), key=lambda kv: int(kv[0])))]
    if c.get("dropped_findings"):
        lines += ["", f"{len(c['dropped_findings'])} item(s) of the model's reply were dropped because they referred to claims or passages that were not shown."]
    return lines + [""]


def _evidence_text(f: dict) -> str:
    note = " (from an earlier attempt on the same input)" if f.get("carried_from_check") else ""
    if not f.get("evidence"):
        return note
    return note + " Evidence: " + "; ".join(f"claim {e['claim_id']} passage {e['passage_id']} ({'quote shown' if e.get('text_shown') else 'passage ID only, its text was not shown to the model'})"
                                     for e in f["evidence"])


def render_markdown(report: dict, question: str) -> str:
    """Plain rendering of stored rows. No sentence in it is generated: claim text is verbatim, headings are fixed."""
    lines = ["# Research report", "", f"Research question: {question}", ""]
    note = {"insufficient_evidence": ("Result: insufficient evidence. No claim directly answers a requirement of the research question."
                                      if report.get("relevance_judged") else "Result: insufficient evidence. No claim qualified for this report."),
            "claims_with_gaps": "Result: verified claims are listed below; some evidence requirements are not fully addressed.",
            "claims_all_requirements_addressed": "Result: every evidence requirement was assessed as addressed (a model assessment, not a guarantee)."}
    result = note[report["outcome"]]
    if report.get("coverage_assessed") is False and report["outcome"] == "claims_with_gaps":
        result = "Result: verified claims are listed below; no coverage assessment was made, so the evidence requirements were NOT ASSESSED (not shown to be unanswered)."
    lines += [result, f"Stopped because: {report['stop_reason'] or 'the loop finished on its own criteria'}", ""]
    refs: dict[str, int] = {}
    lines += _consistency_markdown(report.get("consistency") or {})
    for sec in report["sections"]:
        lines += [f"## {sec['label']}", f"Subquestion: {sec['subquestion_text']}", ""]
        if sec.get("answered") is False:
            lines += ["No direct answer to this subquestion was found.", ""]
        elif not sec["claims"]:
            lines += ["No verified claim is available for this subquestion.", ""]

        def bullet(item, answers=True):
            marks = []
            for c in item["citations"]:
                refs.setdefault(c["passage_id"], len(refs) + 1)
                marks.append(f"[{refs[c['passage_id']]}]")
            tail = f"; directly answers: {'; '.join(item['requirement_texts'])}" if answers and item.get("requirement_texts") else ""
            if item.get("cross_reference"):
                tail += f"; the same verified claim as in {item['cross_reference']['owner_label']}, where it was drafted"
            for c in item.get("conflicts", []):
                tail += "; POSSIBLE CONFLICT with claim " + ", ".join(str(i) for i in c["with_claim_ids"]) + " (see Cross-claim consistency)"
            return f"- {item['text']} {' '.join(marks)} (claim {item['claim_id']}, version {item['claim_version']}{tail})"

        lines += [bullet(item) for item in sec["claims"]]
        if sec.get("context"):
            lines += ["", "Related context (verified, but it does not answer this subquestion):"] + [bullet(item, answers=False) for item in sec["context"]]
        if sec.get("open_requirements"):
            lines += ["", "Not directly answered in this section:"] + [f"- {r['requirement_text']} ({r['status']}: {r['gap'] or 'none given'})" for r in sec["open_requirements"]]
        lines.append("")
    if report["unresolved_requirements"]:
        lines += ["## Evidence requirements not fully addressed", ""]
        for r in report["unresolved_requirements"]:
            lines.append(f"- {r['requirement_text']} ({r['status']}; coverage note, model assessment: {r['gap'] or 'none given'})")
        lines.append("")
    if report.get("unassessed_requirements"):
        lines += ["## Evidence requirements not assessed", "", "No coverage assessment covers these requirements, so whether the claims above answer them is unknown.", ""]
        lines += [f"- {r['requirement_text']}" for r in report["unassessed_requirements"]]
        lines.append("")
    if refs:
        lines += ["## Citations", ""]
        seen: dict[str, dict] = {}
        for sec in report["sections"]:
            for item in sec["claims"] + sec.get("context", []):
                for c in item["citations"]:
                    seen.setdefault(c["passage_id"], c)
        for passage_id, number in sorted(refs.items(), key=lambda kv: kv[1]):
            c = seen[passage_id]
            lines.append(f"[{number}] passage {passage_id}, {c['title'] or c['url']} - {c['url']}")
    return "\n".join(lines)
