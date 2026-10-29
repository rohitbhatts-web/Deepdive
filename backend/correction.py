"""Coverage assessments, correction tasks and the routing rules of the correction loop (no model calls in here).

Routing of a claim that is not report-eligible is deterministic, from its stored state:
  integrity_failed / partially_supported -> repair (a NEW immutable version, verified afresh), at most
                                            `max_repairs_per_claim` times per lineage, then excluded
  unsupported / contradicted             -> excluded (irrelevant or irreparable; history is kept)
  verification_error                     -> verified once more, then excluded
Test inputs, superseded versions and excluded claims are never routed (they are not 'active').
A requirement that is not 'addressed' becomes a targeted missing-evidence search task.
"""

import json
import re
import sqlite3
from collections.abc import Callable

from backend import asks, claims, db, temporal
from backend.config import Settings

TASK_KINDS = ("rejudge", "claim_repair", "missing_evidence", "claim_exclusion")
ACTIONABLE = ("rejudge", "claim_repair", "missing_evidence")


def requirements(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    """The planner's evidence requirements with stable keys: Q<subquestion id>.R<n>."""
    out = []
    for sq in db.list_subquestions(conn, run_id):
        for i, text in enumerate(sq["evidence_requirements"], 1):
            out.append({"key": f"Q{sq['id']}.R{i}", "subquestion_id": sq["id"], "text": text})
    return out


# --- coverage rows -----------------------------------------------------------------------------------------------


def save_assessment(conn: sqlite3.Connection, run_id: int, round_: int, req: dict, status: str, gap: str,
                    missing_kind: str, supporting: list[int], *, model: str | None, model_call_id: int | None,
                    notes: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO coverage_assessments (run_id, round, requirement_key, subquestion_id, requirement_text, status, gap,"
        " missing_kind, model, model_call_id, notes, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, round_, req["key"], req["subquestion_id"], req["text"], status, gap, missing_kind, model,
         model_call_id, json.dumps(notes or {}), db.utcnow()))
    for claim_id in supporting:
        conn.execute("INSERT INTO coverage_supporting_claims (assessment_id, claim_id) VALUES (?, ?)", (cur.lastrowid, claim_id))
    return cur.lastrowid


def assessments(conn: sqlite3.Connection, run_id: int, round_: int | None = None) -> list[dict]:
    """Assessments of one round (default: the latest round), in requirement order."""
    if round_ is None:
        round_ = latest_round(conn, run_id)
        if round_ is None:
            return []
    rows = conn.execute("SELECT * FROM coverage_assessments WHERE run_id = ? AND round = ? ORDER BY id", (run_id, round_)).fetchall()
    out = []
    for r in rows:
        ids = [x[0] for x in conn.execute(
            "SELECT claim_id FROM coverage_supporting_claims WHERE assessment_id = ? ORDER BY claim_id", (r["id"],))]
        out.append({**dict(r), "notes": json.loads(r["notes"]), "supporting_claim_ids": ids,
                    "obligations": obligation_assessments(conn, r["id"])})
    return out


def latest_round(conn: sqlite3.Connection, run_id: int) -> int | None:
    return conn.execute("SELECT max(round) FROM coverage_assessments WHERE run_id = ?", (run_id,)).fetchone()[0]


def coverage_summary(rows: list[dict]) -> dict:
    counts = {"addressed": 0, "partially_addressed": 0, "unresolved": 0}
    for r in rows:
        counts[r["status"]] += 1
    return {**counts, "requirements": len(rows),
            "fully_addressed": bool(rows) and counts["addressed"] == len(rows)}


def signature(conn: sqlite3.Connection, run_id: int, rows: list[dict]) -> tuple:
    """What 'progress' is measured on: which claims are report-eligible and each requirement's status."""
    return (tuple(sorted(c["id"] for c in claims.eligible_claims(conn, run_id))),
            tuple(sorted((r["requirement_key"], r["status"]) for r in rows)))


# --- coverage obligations ------------------------------------------------------------------------------------------------


def obligations(conn: sqlite3.Connection, run_id: int) -> list[dict]:
    return [dict(r) for r in conn.execute(
        "SELECT * FROM coverage_obligations WHERE run_id = ? ORDER BY id", (run_id,))]


def save_obligations(conn: sqlite3.Connection, run_id: int, requirement_key: str, subquestion_id: int,
                     items: list[tuple[str, str]], *, origin: str, model: str | None, model_call_id: int | None) -> None:
    for n, (subject, dimension) in enumerate(items, 1):
        conn.execute(
            "INSERT INTO coverage_obligations (run_id, requirement_key, subquestion_id, obligation_key, subject, dimension,"
            " origin, model, model_call_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_id, requirement_key, subquestion_id, f"{requirement_key}.O{n}", subject, dimension, origin, model,
             model_call_id, db.utcnow()))


def unmet_obligations(conn: sqlite3.Connection, run_id: int, round_: int | None = None) -> list[dict]:
    """Obligations not 'addressed' in an assessment round (default: the latest), in requirement order."""
    if round_ is None:
        round_ = latest_round(conn, run_id)
        if round_ is None:
            return []
    rows = conn.execute(
        "SELECT o.obligation_key, o.requirement_key, o.subquestion_id, o.subject, o.dimension, o.origin, oa.status, oa.gap,"
        " oa.missing_kind FROM obligation_assessments oa JOIN coverage_obligations o ON o.id = oa.obligation_id"
        " JOIN coverage_assessments a ON a.id = oa.assessment_id WHERE a.run_id = ? AND a.round = ? AND oa.status != 'addressed'"
        " ORDER BY oa.id", (run_id, round_))
    return [dict(r) for r in rows]


def ensure_fallback_obligations(conn: sqlite3.Connection, run_id: int) -> int:
    """Give every requirement that has no obligation one obligation covering it as a whole (no model involved). Returns
    how many were added. Such 'fallback' obligations are not subject-checked: nothing names a subject to check."""
    have = {o["requirement_key"] for o in obligations(conn, run_id)}
    added = 0
    with db.tx(conn):
        for req in requirements(conn, run_id):
            if req["key"] not in have:
                save_obligations(conn, run_id, req["key"], req["subquestion_id"],
                                 [("the requirement as a whole", req["text"])], origin="fallback", model=None, model_call_id=None)
                added += 1
    return added


def save_obligation_assessment(conn: sqlite3.Connection, assessment_id: int, obligation_id: int, status: str, gap: str,
                               missing_kind: str, supporting: list[int], notes: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO obligation_assessments (assessment_id, obligation_id, status, gap, missing_kind, notes)"
        " VALUES (?, ?, ?, ?, ?, ?)", (assessment_id, obligation_id, status, gap, missing_kind, json.dumps(notes or {})))
    for claim_id in supporting:
        conn.execute("INSERT INTO obligation_supporting_claims VALUES (?, ?)", (cur.lastrowid, claim_id))
    return cur.lastrowid


ROLES = ("direct_answer", "relevant_context", "does_not_answer")


def save_claim_relevance(conn: sqlite3.Connection, obligation_assessment_id: int, requirement_key: str,
                         roles: dict[int, tuple[str, str, str]]) -> None:
    """Persist {claim_id: (role, reason, origin)} for one obligation assessment. The database re-checks run ownership and the requirement key."""
    for claim_id, (role, reason, origin) in sorted(roles.items()):
        if role not in ROLES:
            raise ValueError(f"unknown relevance role {role!r}")
        conn.execute("INSERT INTO claim_relevance (obligation_assessment_id, requirement_key, claim_id, role, reason, origin) VALUES (?, ?, ?, ?, ?, ?)",
                     (obligation_assessment_id, requirement_key, claim_id, role, reason[:200], origin))


def claim_roles(conn: sqlite3.Connection, run_id: int, round_: int | None = None) -> list[dict]:
    """Every relevance judgment of one coverage round (default: the latest), with the subquestion its requirement belongs to."""
    if round_ is None:
        round_ = latest_round(conn, run_id)
        if round_ is None:
            return []
    rows = conn.execute(
        "SELECT ca.subquestion_id, cr.requirement_key, o.obligation_key, cr.claim_id, cr.role, cr.reason, cr.origin FROM claim_relevance cr"
        " JOIN obligation_assessments oa ON oa.id = cr.obligation_assessment_id JOIN coverage_obligations o ON o.id = oa.obligation_id"
        " JOIN coverage_assessments ca ON ca.id = oa.assessment_id WHERE ca.run_id = ? AND ca.round = ? ORDER BY cr.id", (run_id, round_))
    return [dict(r) for r in rows.fetchall()]


def obligation_assessments(conn: sqlite3.Connection, assessment_id: int) -> list[dict]:
    out = []
    rows = conn.execute(
        "SELECT oa.*, o.obligation_key, o.subject, o.dimension FROM obligation_assessments oa"
        " JOIN coverage_obligations o ON o.id = oa.obligation_id WHERE oa.assessment_id = ? ORDER BY oa.id", (assessment_id,))
    for r in rows.fetchall():
        ids = [x[0] for x in conn.execute(
            "SELECT claim_id FROM obligation_supporting_claims WHERE obligation_assessment_id = ? ORDER BY claim_id", (r["id"],))]
        out.append({**dict(r), "notes": json.loads(r["notes"]), "supporting_claim_ids": ids})
    return out


def aggregate(items: list[dict]) -> dict:
    """A requirement is addressed only if EVERY obligation is; partially if some are; unresolved if none is. Derived in
    code from the per-obligation statuses, so one satisfied subject can never stand in for another."""
    statuses = [i["status"] for i in items]
    if all(s == "addressed" for s in statuses):
        status = "addressed"
    elif all(s == "unresolved" for s in statuses):
        status = "unresolved"
    else:
        status = "partially_addressed"
    unmet = [i for i in items if i["status"] != "addressed"]
    if len(items) == 1:  # a single obligation: keep the assessor's own wording
        gap = unmet[0]["gap"] if unmet else ""
    else:
        gap = "; ".join(f"{i['subject']} / {i['dimension']}: {i['gap'] or 'not established by any verified claim'}" for i in unmet)
    kinds = [i["missing_kind"] for i in unmet if i["missing_kind"] != "none"]
    return {"status": status, "gap": gap[:400], "missing_kind": kinds[0] if kinds else ("none" if not unmet else "other"),
            "supporting": sorted({c for i in items if i["status"] != "unresolved" for c in i["supporting_claim_ids"]}),
            "unmet": [{"obligation_key": i["obligation_key"], "subject": i["subject"], "dimension": i["dimension"],
                       "status": i["status"], "gap": i["gap"]} for i in unmet]}


_INFLECTIONS = ("ing", "ed", "es", "s", "e")


def _stem(word: str) -> str:
    """First 5 letters after dropping one common inflection ('merging', 'merged' and 'merge' share a stem); a word of 4 or fewer letters is left alone."""
    for suffix in _INFLECTIONS:
        if word.endswith(suffix) and len(word) - len(suffix) >= 4:
            word = word[:-len(suffix)]
            break
    return word[:5]


_GLUE = frozenset({"the", "and", "for", "are", "was", "were", "with", "from", "into", "over", "per", "via", "its", "their", "this", "that", "these", "those"})


def shared_subject_stems(subjects: list[str], plan_texts: list[str] | tuple[str, ...] = ()) -> set[str]:
    """Stems of the identifying words that cannot tell one subject of a run from another (a run-wide qualifier such as the product all of them are about).
    A claim that names the subject without repeating such a word is not penalised for the omission. Two signals, either is enough:

    * EVERY distinct subject contains the word; or
    * (when `plan_texts` is given: the run's question followed by its subquestions) the question and EVERY subquestion contain it, and some subject does. The
      subjects are written by a model, one requirement at a time, and need not all repeat the topic word: a stored run's "Python lists" and "Python tuples" did,
      its "list elements", "tuple elements" and "lists and tuples" did not, so the first rule found nothing and a supported claim that said "lists" and "tuples"
      was not credited for lacking "Python". The plan's own questions all say it, so it identifies nothing within this run.

    Empty unless there are at least two distinct subjects (and, for the second signal, at least two subquestions), and empty when the shared words are
    themselves the whole of some subject (then they DO identify it)."""
    distinct = {s.strip().lower() for s in subjects if s.strip()}
    if len(distinct) < 2:
        return set()
    per_subject = [{_stem(w) for w in _subject_words(s)} for s in distinct]
    shared = set.intersection(*per_subject)
    universal = set() if any(words <= shared for words in per_subject) else shared
    if len(plan_texts) < 3:  # the question and at least two subquestions: one subquestion proves nothing about a plan
        return universal
    plan_wide = set.intersection(*({_stem(w) for w in _subject_words(t)} for t in plan_texts)) & set.union(*per_subject)
    merged = universal | plan_wide
    return universal if any(words <= merged for words in per_subject) else merged


def _subject_words(subject: str) -> list[str]:
    tokens = re.findall(r"[a-z0-9]+", subject.lower())
    return [t for t in tokens if not any(ch.isdigit() for ch in t) and len(t) >= 3 and t not in _GLUE and t not in temporal.VERSION_QUALIFIERS]


def mentions(subject: str, text: str, *, ignore_stems: frozenset[str] | set[str] = frozenset()) -> bool:
    """Lexical backstop, not a semantic test: does `text` talk about the named subject at all?

    Every IDENTIFYING word of the subject (3+ letters) must share a 5-letter stem with some word of the text, so 'Postgres' matches 'PostgreSQL'. Two kinds of
    token are handled apart so the check neither rejects a descriptive subject nor confuses two numbered ones: glue words and version/currency qualifiers
    ("the", "current", "revision", "original": which version, not what) are not required to appear; any token containing a digit ("2", "3.45", "9113") is
    an identifier and must appear EXACTLY (a bare number used to be dropped, so 'X 3' and 'X 2' were not told apart).
    `ignore_stems` (see shared_subject_stems) are not required, but only while at least one other identifying word remains to be checked."""
    tokens = re.findall(r"[a-z0-9]+", subject.lower())
    identifiers = [t for t in tokens if any(ch.isdigit() for ch in t)]
    words = _subject_words(subject)
    kept = [w for w in words if _stem(w) not in ignore_stems]
    if kept:  # never leaves the subject with only an identifier (or nothing) to check
        words = kept
    text_tokens = re.findall(r"[a-z0-9]+", text.lower())
    stems = {_stem(w) for w in text_tokens}
    return bool(words or identifiers) and all(_stem(w) in stems for w in words) and all(i in set(text_tokens) for i in identifiers)


_GENERIC_DIMENSION_WORDS = frozenset({"when", "needed", "need", "needs", "scenarios", "scenario", "cases", "case", "using", "used", "uses", "with", "about", "after", "before",
                                      "other", "such", "than", "that", "then", "them", "they", "what", "which", "while", "would", "could", "should"})
MIN_IDENTIFYING_WORDS = 3  # a dimension that names fewer words than this is too abstract for a word check ("mutability", "use cases")


def _identifying_stems(text: str) -> dict[str, str]:
    """{5-letter stem: word} of the words (4+ letters, not glue, version qualifiers or generic) that identify what a dimension asks about."""
    out: dict[str, str] = {}
    for w in re.findall(r"[a-z]+", text.lower()):
        if len(w) >= 4 and w not in _GLUE and w not in temporal.VERSION_QUALIFIERS and w not in _GENERIC_DIMENSION_WORDS:
            out.setdefault(_stem(w), w)
    return out


def claim_carries_dimension(dimension: str, claim_text: str) -> tuple[bool, list[str]]:
    """Narrow, deterministic backstop for a direct-answer credit: the CLAIM'S OWN TEXT must contain at least one of the words that identify the dimension.

    Only applied to a dimension that names MIN_IDENTIFYING_WORDS or more such words, and only when NONE of them appears in the claim (an abstract dimension, or a
    paraphrase such as 'modify' for 'change', cannot be judged by words, so the check stays out of its way). It catches the observed failure: a claim about
    immutability credited with 'use as dictionary keys and for returning multiple values', wording that existed only in the claim's evidence. Returns (ok, the
    dimension words the claim lacks)."""
    wanted = _identifying_stems(dimension)
    if len(wanted) < MIN_IDENTIFYING_WORDS:
        return True, []
    have = {_stem(w) for w in re.findall(r"[a-z]+", claim_text.lower())}
    if set(wanted) & have:
        return True, []
    return False, sorted(wanted.values())


# --- tasks ---------------------------------------------------------------------------------------------------------


def add_task(conn: sqlite3.Connection, run_id: int, planned_round: int, kind: str, *, subquestion_id: int | None = None,
             claim_id: int | None = None, requirement_key: str | None = None, spec: dict | None = None,
             status: str = "planned", outcome: dict | None = None) -> int:
    cur = conn.execute(
        "INSERT INTO correction_tasks (run_id, planned_round, kind, subquestion_id, claim_id, requirement_key, spec, status,"
        " outcome, created_at, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (run_id, planned_round, kind, subquestion_id, claim_id, requirement_key, json.dumps(spec or {}), status,
         json.dumps(outcome or {}), db.utcnow(), None if status == "planned" else db.utcnow()))
    return cur.lastrowid


def finish_task(conn: sqlite3.Connection, task_id: int, status: str, outcome: dict | None = None) -> None:
    conn.execute("UPDATE correction_tasks SET status = ?, outcome = ?, finished_at = ? WHERE id = ?",
                 (status, json.dumps(outcome or {}), db.utcnow(), task_id))


def _task(r: sqlite3.Row) -> dict:
    return {**dict(r), "spec": json.loads(r["spec"]), "outcome": json.loads(r["outcome"])}


def list_tasks(conn: sqlite3.Connection, run_id: int, *, planned_round: int | None = None, status: str | None = None,
               kind: str | None = None) -> list[dict]:
    sql, args = "SELECT * FROM correction_tasks WHERE run_id = ?", [run_id]
    for column, value in (("planned_round", planned_round), ("status", status), ("kind", kind)):
        if value is not None:
            sql += f" AND {column} = ?"
            args.append(value)
    return [_task(r) for r in conn.execute(sql + " ORDER BY id", args)]


# --- routing -------------------------------------------------------------------------------------------------------


def _norm(text: str) -> str:
    return re.sub(r"\W+", " ", text.lower()).strip()


def failure_reasons(claim: dict) -> dict:
    """Why a claim is not eligible, for the Reviser and for the stored exclusion reason."""
    out: dict = {"status": claim["status"]}
    if claim["integrity"] and claim["integrity"]["verdict"] == "failed":
        out["integrity"] = claim["integrity"]["reason"]
    sem = claim["semantic"]
    if sem and sem["verdict"] != "supported":
        d = sem["details"]
        out["semantic"] = {"verdict": sem["verdict"], "reason": sem["reason"],
                           "unsupported_details": d.get("unsupported_details", []), "conflicts": d.get("conflicts", []),
                           "flagged_aspects": {k: v for k, v in d.get("aspects", {}).items() if v in ("mismatch", "missing")}}
    return out


def ask_priority(conn: sqlite3.Connection, run_id: int, rows: list[dict]) -> dict:
    """Which subquestions serve an explicit ask that no covering subquestion has answered yet, from the provenance recorded at planning time.

    Coarse by design: a requirement is not tied to an ask, only its subquestion is, and an ask counts as met as soon as one subquestion that covers it has an addressed
    requirement. A run planned before provenance was recorded has `recorded: False`: priority is then UNKNOWN (never guessed from keywords) and the original
    order is kept. Returns {"recorded", "unmet_asks": [ask labels], "priority": {subquestion_id: 0 (serves an unmet ask) | 1}, "note"}."""
    prov = asks.provenance(conn, run_id)
    if not prov["recorded"]:
        return {"recorded": False, "source": None, "unmet_asks": None, "priority": {}, "note": "this run did not record which subquestion covers which ask, so unmet asks cannot be told from background; the original order is kept"}
    statuses: dict[int, list[str]] = {}
    for r in rows:
        statuses.setdefault(r["subquestion_id"], []).append(r["status"])
    unmet = asks.unmet_asks(prov, statuses) or []
    serves = {sq: set(a) & set(unmet) for sq, a in prov["covers"].items()}
    return {"recorded": True, "source": prov.get("source", "planner"), "unmet_asks": [prov["asks"][i]["label"] for i in unmet],
            "priority": {sq: 0 if s else 1 for sq, s in serves.items()},
            "note": "subquestions that serve an ask nobody has answered come first; a requirement is tied to its subquestion, not to a single ask"}


def propose_tasks(conn: sqlite3.Connection, run_id: int, rows: list[dict], settings: Settings,
                  searches_left: int, stored_probe: Callable[[int], dict] | None = None) -> list[dict]:
    """Pure routing: the tasks implied by the current state. Nothing is written.

    An unmet requirement becomes a `missing_evidence` task with one of two routes, recorded in its spec with the reason: `stored_evidence` (draft from relevant passages
    of already collected sources that were never offered to the drafter or cited, found by `stored_probe`; costs model requests but no search) when such passages exist
    and this exact evidence was not tried before, otherwise `search`. Tasks of subquestions that serve an unmet explicit ask come first (see `ask_priority`)."""
    proposals: list[dict] = []
    for c in claims.active_claims(conn, run_id):
        if claims.is_report_eligible(c) or c["needs_rejudge"]:
            continue
        status, depth = c["status"], c["version"] - 1
        reasons = failure_reasons(c)
        if status in ("integrity_failed", "partially_supported"):
            if depth >= settings.max_repairs_per_claim:
                proposals.append({"kind": "claim_exclusion", "claim_id": c["id"], "subquestion_id": c["subquestion_id"],
                                  "spec": {"reason": f"still {status} after {depth} repair(s)", "failure": reasons}})
            else:
                proposals.append({"kind": "claim_repair", "claim_id": c["id"], "subquestion_id": c["subquestion_id"],
                                  "spec": {"failure": reasons}})
        elif status in ("unsupported", "contradicted"):
            proposals.append({"kind": "claim_exclusion", "claim_id": c["id"], "subquestion_id": c["subquestion_id"],
                              "spec": {"reason": f"{status}: the stored evidence does not allow an accurate version",
                                       "failure": reasons}})
        elif status == "verification_error":
            errors = sum(1 for r in claims.all_results(conn, run_id, c["id"]) if r["stage"] == "semantic" and r["verdict"] == "error")
            kind = "claim_exclusion" if errors >= 2 else "rejudge"
            proposals.append({"kind": kind, "claim_id": c["id"], "subquestion_id": c["subquestion_id"],
                              "spec": {"reason": "verification error" + (" repeated" if errors >= 2 else ""), "failure": reasons}})
    # only attempts that really ran count as "already tried"; a task that was skipped was never tried. Searches and stored-evidence drafting are tracked apart.
    done = [t for t in list_tasks(conn, run_id, kind="missing_evidence") if t["status"] in ("done", "failed")]
    tried = {(t["requirement_key"], _norm(t["spec"].get("gap", ""))) for t in done if t["spec"].get("route", {}).get("chosen") != "stored_evidence"}
    tried_stored = {t["spec"]["route"].get("fingerprint") for t in done if t["spec"].get("route", {}).get("chosen") == "stored_evidence"}
    priority = ask_priority(conn, run_id, rows)
    unmet_rows = [a for a in rows if a["status"] != "addressed"]
    unmet_rows.sort(key=lambda a: priority["priority"].get(a["subquestion_id"], 0))  # stable: unrecorded provenance keeps requirement order
    probes: dict[int, dict] = {}
    stored_for: dict[int, str] = {}  # subquestion id -> the requirement key whose task carries the stored-evidence drafting for it
    for a in unmet_rows:
        spec = {"requirement_text": a["requirement_text"], "gap": a["gap"], "missing_kind": a["missing_kind"],
                "status": a["status"], "priority": {"serves_unmet_ask": priority["priority"].get(a["subquestion_id"]) == 0 if priority["recorded"] else None,
                                                    "ask_provenance_recorded": priority["recorded"], "ask_provenance_source": priority.get("source")}}
        sq_id = a["subquestion_id"]
        probe = probes.setdefault(sq_id, stored_probe(sq_id) if stored_probe else {"passage_ids": [], "fingerprint": None})
        if sq_id in stored_for:
            proposals.append({"kind": "missing_evidence", "requirement_key": a["requirement_key"], "subquestion_id": sq_id, "spec": spec,
                              "skip": f"handled together with {stored_for[sq_id]}: one stored-evidence drafting call serves all unmet requirements of this subquestion"})
            continue
        if probe["passage_ids"] and probe["fingerprint"] not in tried_stored:
            spec["route"] = {"chosen": "stored_evidence", "fingerprint": probe["fingerprint"], "passage_ids": probe["passage_ids"],
                             "why": (f"{len(probe['passage_ids'])} relevant passage(s) of already collected sources were never offered to the drafter or cited by a claim; "
                                     "drafting from them needs no search"),
                             "serves_requirements": [x["requirement_key"] for x in unmet_rows if x["subquestion_id"] == sq_id]}
            stored_for[sq_id] = a["requirement_key"]
            proposals.append({"kind": "missing_evidence", "requirement_key": a["requirement_key"], "subquestion_id": sq_id, "spec": spec, "route": "stored_evidence"})
            continue
        why = ("stored-evidence drafting was already tried on this same evidence" if probe["passage_ids"] and probe["fingerprint"] in tried_stored
               else "no relevant unused passage in the already collected sources")
        spec["route"] = {"chosen": "search", "why": why}
        proposals.append({"kind": "missing_evidence", "requirement_key": a["requirement_key"], "subquestion_id": sq_id, "spec": spec, "route": "search",
                          "repeat": (a["requirement_key"], _norm(a["gap"])) in tried})
    # bounded: repairs and rejudges first, then search tasks while searches remain; exclusions are free
    out, searches = [], searches_left
    actionable = 0
    for p in proposals:
        if p["kind"] == "missing_evidence" and p.get("skip"):
            pass  # already decided above (served by another task of the same subquestion)
        elif p["kind"] == "missing_evidence" and p.get("route") == "stored_evidence":
            if actionable >= settings.max_tasks_per_round:
                p["skip"] = f"more than {settings.max_tasks_per_round} tasks in one round"
            else:
                actionable += 1  # no search is spent: it draws on the shared model budget in the correction round
        elif p["kind"] == "missing_evidence":
            if p.get("repeat"):
                p["skip"] = "same requirement and gap were already searched in an earlier round (no new information)"
            elif searches <= 0:
                p["skip"] = "no search budget left"
            elif actionable >= settings.max_tasks_per_round:
                p["skip"] = f"more than {settings.max_tasks_per_round} tasks in one round"
            else:
                searches -= 1
                actionable += 1
        elif p["kind"] != "claim_exclusion":
            if actionable >= settings.max_tasks_per_round:
                p["skip"] = f"more than {settings.max_tasks_per_round} tasks in one round"
            else:
                actionable += 1
        out.append(p)
    return out


def persist_tasks(conn: sqlite3.Connection, run_id: int, planned_round: int, proposals: list[dict]) -> list[dict]:
    """Write the proposals. Exclusions are applied immediately (the claim keeps all its history); skipped proposals are
    recorded as skipped tasks with the reason, so nothing silently disappears."""
    stored = []
    with db.tx(conn):
        for p in proposals:
            kind, spec = p["kind"], p["spec"]
            common = {"subquestion_id": p.get("subquestion_id"), "claim_id": p.get("claim_id"),
                      "requirement_key": p.get("requirement_key"), "spec": spec}
            if p.get("skip"):
                tid = add_task(conn, run_id, planned_round, kind, status="skipped", outcome={"reason": p["skip"]}, **common)
            elif kind == "claim_exclusion":
                claims.exclude_claim(conn, run_id, p["claim_id"], spec["reason"], planned_round)
                tid = add_task(conn, run_id, planned_round, kind, status="done", outcome={"excluded": True}, **common)
            else:
                tid = add_task(conn, run_id, planned_round, kind, **common)
            stored.append({**p, "task_id": tid})
    return stored
