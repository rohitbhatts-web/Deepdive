"""The explicit asks of a research question and which subquestions the planner said cover them (provenance recorded at planning time).

The planner lists `asks` (the distinct things the question asks for, quoted from it) and gives each subquestion `covers` (positions in `asks`). Both are
stored in the plan_created audit event; `covers` has been recorded since answer-completeness routing was added, so an older run has the asks but NOT which
subquestion covers which. For such a run `covers` is None: priority is then unknown, and callers must say so instead of inferring it from keywords.
"""

import sqlite3

from backend import db


def provenance(conn: sqlite3.Connection, run_id: int) -> dict:
    """{"asks": [{"quote", "label"}], "covers": {subquestion_id: [ask positions]} or None, "recorded": bool}."""
    found = {"asks": [], "covers": None, "recorded": False}
    for event in db.list_events(conn, run_id):
        if event["event_type"] == "plan_created":
            p = event["payload"]
            asks = list(p.get("asks") or [])
            sub = p.get("subquestions") or []
            if asks and sub and all("covers" in s for s in sub):
                return {"asks": asks, "covers": {int(s["id"]): [int(c) for c in s["covers"]] for s in sub}, "recorded": True, "source": "planner"}
            found = {"asks": asks, "covers": None, "recorded": False}
        elif event["event_type"] == "ask_provenance_supplied" and found["asks"] and not found["recorded"]:
            # A person supplied the mapping for a run planned before it was recorded. It is labelled as theirs, never inferred from keywords.
            return {**found, "covers": {int(k): [int(c) for c in v] for k, v in event["payload"]["covers"].items()}, "recorded": True, "source": "supplied by the operator"}
    return found


def asks_of_subquestion(prov: dict, subquestion_id: int) -> list[int]:
    return list((prov.get("covers") or {}).get(subquestion_id, []))


def unmet_asks(prov: dict, statuses: dict[int, list[str]]) -> list[int] | None:
    """Positions of asks that no covering subquestion has an 'addressed' requirement for (None when provenance was not recorded).

    `statuses` maps subquestion id -> the statuses of its requirements in the latest coverage round. An ask is met as soon as one subquestion that covers it has
    at least one addressed requirement; this is deliberately coarse (a requirement is not tied to an ask, only its subquestion is), and `provenance` says so."""
    if not prov.get("recorded"):
        return None
    met = set()
    for sq_id, asks in prov["covers"].items():
        if "addressed" in statuses.get(sq_id, []):
            met |= set(asks)
    return [i for i in range(len(prov["asks"])) if i not in met]
