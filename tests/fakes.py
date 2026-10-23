"""FIXTURES ONLY: scripted stand-ins for Mesh and the search provider. Nothing here is used outside tests."""

import json
import re
from types import SimpleNamespace
from typing import ClassVar

from backend.search import SearchHit, SearchResponse


class FakeMesh:
    """FIXTURE Mesh client. Replies are scripted per agent step; token usage numbers are fixed test values."""

    STEPS: ClassVar[dict[str, str]] = {"You are the Planner": "planner", "Write targeted web search queries": "queries",
             "choose the pages": "select", "running a relevance check": "relevance",
        "You are the Drafter": "draft", "You are the Verifier": "verify",
        "You are the Coverage Assessor": "coverage", "You are the Reviser": "revise",
        "You are the Obligation Analyst": "obligations",
        "You are the Synthesizer": "synth"}

    def __init__(self, *, report_usage=True, **replies):
        self.report_usage = report_usage
        self.replies = {k: list(v) if isinstance(v, list) else [v] for k, v in replies.items()}
        self.calls: list[dict] = []
        self.request_counter = 0
        self.chat = SimpleNamespace(completions=SimpleNamespace(
            with_raw_response=SimpleNamespace(create=self._create_raw)))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        system = kwargs["messages"][0]["content"]
        step = next(name for marker, name in self.STEPS.items() if marker in system)
        queue = self.replies.get(step) or ([{"assessments": []}] if step == "relevance" else None)
        reply = queue.pop(0) if len(queue) > 1 else queue[0]
        if isinstance(reply, Exception):
            raise reply
        text = reply if isinstance(reply, str) else json.dumps(reply)
        if "{QUESTION}" in text:
            found = re.search(r"<question>\n(.*?)\n</question>", kwargs["messages"][-1]["content"], re.DOTALL) or re.search(r"<question>\n(.*?)\n</question>", kwargs["messages"][1]["content"], re.DOTALL)
            text = text.replace("{QUESTION}", json.dumps(found.group(1))[1:-1] if found else "")
        return SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=text), finish_reason="stop")],
            usage=SimpleNamespace(prompt_tokens=100, completion_tokens=20, total_tokens=120) if self.report_usage else None,
        )

    def _create_raw(self, **kwargs):
        """FIXTURE of `with_raw_response.create`: the parsed completion plus response headers incl. X-Request-Id."""
        completion = self._create(**kwargs)
        self.request_counter += 1
        return SimpleNamespace(parse=lambda: completion, headers={"x-request-id": f"req_fixture_{self.request_counter}"})

    def steps(self) -> list[str]:
        return [next(n for m, n in self.STEPS.items() if m in c["messages"][0]["content"]) for c in self.calls]


class FakeSearch:
    """FIXTURE search provider: query -> list of (url, title, snippet), or an exception to raise."""

    name = "fixture-search"

    def __init__(self, results: dict):
        self.results = results
        self.queries: list[str] = []

    def search(self, query: str, max_results: int) -> SearchResponse:
        self.queries.append(query)
        outcome = self.results.get(query, [])
        if isinstance(outcome, Exception):
            raise outcome
        hits = [SearchHit(url=u, title=t, snippet=s) for u, t, s in outcome][:max_results]
        return SearchResponse(hits=hits, usage={"credits": 1}, request_id="fixture-req")


def plan(*subquestions):
    """FIXTURE plan: one ask quoting the whole question ({QUESTION} is replaced by FakeMesh with the run's real question) covered by every subquestion."""
    return {"asks": [{"quote": "{QUESTION}", "label": "the question"}], "subquestions": [
        {"question": q, "evidence_requirements": reqs, "rationale": "fixture rationale", "covers": [0]} for q, reqs in subquestions
    ]}


def queries(*qs):
    return {"queries": list(qs), "rationale": "fixture rationale"}


def select(*urls):
    return {"selections": [{"url": u, "reason": "fixture reason"} for u in urls]}


def draft(*claims):
    """FIXTURE drafter reply."""
    return {"claims": list(claims)}


def claim(subquestion_id, text, *evidence):
    return {"subquestion_id": subquestion_id, "text": text,
            "evidence": [{"passage_id": pid, "quote": quote} for pid, quote in evidence]}


OK_ASPECTS = {"date": "ok", "quantity": "ok", "entity": "ok", "attribution": "ok", "scope": "ok", "causality": "not_applicable",
              "forecast_vs_observation": "ok", "qualifications": "ok"}


def verdict(value, explanation="fixture explanation", used=("S1-P0",), unsupported=(), conflicts=(), **aspects):
    """FIXTURE verifier reply. These are scripted answers: they test how the pipeline handles a verdict, not whether a
    real model would reach it."""
    return {"verdict": value, "explanation": explanation, "unsupported_details": list(unsupported),
            "conflicts": list(conflicts), "evidence_used": list(used), "aspects": {**OK_ASPECTS, **aspects}}


def coverage(*rows):
    """FIXTURE coverage reply. Each row: (key, status, supporting_claim_ids, gap, missing_kind). A requirement key such as
    Q1.R1 means its single whole-requirement obligation (Q1.R1.O1); an obligation key such as Q1.R1.O2 is used as given."""
    return {"obligations": [{"obligation_key": k if ".O" in k else f"{k}.O1", "status": st,
                             "claim_roles": [{"claim_id": i, "role": "direct_answer", "reason": "fixture: answers it"} for i in ids],
                             "gap": gap, "missing_kind": kind} for k, st, ids, gap, kind in rows]}


def obligations(**by_requirement):
    """FIXTURE obligation-analyst reply: obligations(Q1_R1=[("subject", "dimension"), ...]) (underscores for dots)."""
    return {"requirements": [{"requirement_key": k.replace("_", "."), "obligations": [{"subject": s, "dimension": d} for s, d in v]}
                             for k, v in by_requirement.items()]}


def revise(text, *evidence, reason="fixture: reworded to what the passage says"):
    return {"action": "revise", "text": text, "reason": reason, "parts": [],
            "evidence": [{"passage_id": pid, "quote": quote} for pid, quote in evidence]}


def split(*parts, reason="fixture: one compound claim split into atomic claims"):
    """FIXTURE reviser reply splitting a claim. Each part: (text, [(passage_id, quote), ...])."""
    return {"action": "split", "text": "", "evidence": [], "reason": reason,
            "parts": [{"text": text, "evidence": [{"passage_id": pid, "quote": quote} for pid, quote in ev]} for text, ev in parts]}


def abandon(reason="fixture: the stored passages do not permit an accurate claim"):
    return {"action": "abandon", "text": "", "evidence": [], "parts": [], "reason": reason}


def synth(*sections, omitted=()):
    """FIXTURE synthesizer reply. Each section: (subquestion_id, [claim ids in order])."""
    return {"sections": [{"subquestion_id": sq, "claim_ids": list(ids)} for sq, ids in sections],
            "omitted": [{"claim_id": cid, "reason": why} for cid, why in omitted]}
