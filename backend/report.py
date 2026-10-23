"""Report assembly and retrieval. Reports are immutable snapshots built from EXACT stored claim text.

The only model input is an ordering plan (claim IDs). Everything a reader sees comes from rows: claim text copied
verbatim from `claims`, citations copied from the passages the claim's semantic verdict actually relied on, section
labels that are fixed neutral strings, and the planner's own subquestion text. Eligibility is re-checked inside the same
transaction that writes the report, and the database triggers re-check it again on every insert.
"""

import json
import sqlite3

from backend import claims, correction, db
from backend.agents.schemas import SynthesisPlan


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
) -> dict:
    """Persist one report snapshot and return {"report_id", "outcome", "rejected"}. `plan=None` means a deterministic
    ordering (claim ID order within each subquestion), used when no model call was available or needed."""
    rejected: list[dict] = []
    with db.tx(conn):
        subqs = db.list_subquestions(conn, run_id)
        everything = claims.list_claims(conn, run_id)  # read inside the transaction: nothing can change underneath
        eligible = {c["id"]: c for c in everything if c["eligible_for_report"]}
        selection: dict[int, list[int]] = {s["id"]: [] for s in subqs}
        omitted: dict[int, str] = {}

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
        for position, sq in enumerate(subqs, 1):
            section_id = conn.execute(
                "INSERT INTO report_sections (report_id, position, label, subquestion_id, subquestion_text)"
                " VALUES (?, ?, ?, ?, ?)", (report_id, position, f"Section {position}", sq["id"], sq["question"])).lastrowid
            for claim_position, claim_id in enumerate(selection[sq["id"]], 1):
                claim = eligible[claim_id]
                report_claim_id = conn.execute(
                    "INSERT INTO report_claims (report_id, section_id, position, claim_id, claim_version, claim_text,"
                    " semantic_result_id) VALUES (?, ?, ?, ?, ?, ?, ?)",
                    (report_id, section_id, claim_position, claim_id, claim["version"], claim["text"],
                     claim["semantic"]["id"])).lastrowid
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
            "rejected_ids": rejected})
    return {"report_id": report_id, "outcome": outcome, "rejected": rejected}


# --- reading -----------------------------------------------------------------------------------------------------


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
        direct = [i for i in items if i["role"] in (None, "direct_answer")]  # a report without judgments (older, or coverage never ran) keeps all its claims
        sections.append({"position": s["position"], "label": s["label"], "subquestion_id": s["subquestion_id"],
                         "subquestion_text": s["subquestion_text"], "claims": direct, "context": [i for i in items if i["role"] == "relevant_context"],
                         "answered": bool(direct) if any(i["role"] for i in items) or judged_row else None})
    requirements = [dict(r) for r in conn.execute("SELECT requirement_key, subquestion_id, requirement_text, status, gap, missing_kind FROM report_gaps WHERE report_id = ? ORDER BY id", (rid,))]
    omissions = [dict(r) for r in conn.execute("SELECT claim_id, reason FROM report_omissions WHERE report_id = ? ORDER BY claim_id", (rid,))]
    unresolved = [r for r in requirements if r["status"] != "addressed"]
    # Per section: which of ITS requirements no claim directly answers, and per claim: which requirements it directly answers (judged reports only).
    by_key = {r["requirement_key"]: r for r in requirements}
    for sec in sections:
        sec["open_requirements"] = ([{"requirement_key": r["requirement_key"], "requirement_text": r["requirement_text"], "status": r["status"], "gap": r["gap"]}
                                     for r in requirements if r["subquestion_id"] == sec["subquestion_id"] and r["status"] != "addressed"] if judged_row else [])
        for item in sec["claims"] + sec["context"]:
            item["requirement_texts"] = [by_key[k]["requirement_text"] for k in item["requirement_keys"] if k in by_key]
    rubrics = sorted({i["verifier_rubric"] or "no rubric recorded" for s in sections for i in s["claims"]})
    report = {**dict(head), "verifier_rubrics": rubrics, "relevance_judged": judged_row, "sections": sections, "requirements": requirements, "unresolved_requirements": unresolved,
              "fully_addressed": bool(requirements) and not unresolved, "omissions": omissions}
    report["markdown"] = render_markdown(report, conn.execute("SELECT question FROM runs WHERE id = ?", (run_id,)).fetchone()[0])
    return report


def render_markdown(report: dict, question: str) -> str:
    """Plain rendering of stored rows. No sentence in it is generated: claim text is verbatim, headings are fixed."""
    lines = ["# Research report", "", f"Research question: {question}", ""]
    note = {"insufficient_evidence": ("Result: insufficient evidence. No claim directly answers a requirement of the research question."
                                      if report.get("relevance_judged") else "Result: insufficient evidence. No claim qualified for this report."),
            "claims_with_gaps": "Result: verified claims are listed below; some evidence requirements are not fully addressed.",
            "claims_all_requirements_addressed": "Result: every evidence requirement was assessed as addressed (a model assessment, not a guarantee)."}
    lines += [note[report["outcome"]], f"Stopped because: {report['stop_reason'] or 'the loop finished on its own criteria'}", ""]
    refs: dict[str, int] = {}
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
