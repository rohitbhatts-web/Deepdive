"""LangGraph flow.

Collection:   planner -> researcher (once per subquestion) -> associate -> finish
Verification: draft -> integrity -> verify -> finish_verify
Completion:   rejudge -> coverage -> [correct -> reverify -> coverage]* -> report -> finish_complete
              The bracketed loop runs at most `max_correction_rounds` times after the initial assessment.
The stages chain (`verify=True`, `complete=True`) or start midway on an already collected run (`mode="verify"` /
`mode="complete"`). ONE Budget object is created per job and passed to every node, so limits are never reset when a node
or a round starts: coverage, repairs, targeted research, re-verification, relevance checks, synthesis, retries and failed
requests all draw on the same counters. A conditional edge after each node ends a stage early on a budget stop, a node
failure, or (completion loop) the loop's own stopping criteria, and the reason is stored.

Statuses describe *collection* (evidence_collected = a page is linked), *verification* (verdicts per claim) and
*completion*; a finished pipeline never implies coverage, and only claims with a current 'supported' verdict are
report-eligible.
"""

import sqlite3
from typing import Any, TypedDict

from langgraph.graph import END, START, StateGraph

from backend import claims, correction, db
from backend.agents.coverage import assess as assess_coverage
from backend.agents.drafter import run_drafter
from backend.agents.obligations import ensure_obligations
from backend.agents.planner import run_planner
from backend.agents.relevance import associate_sources
from backend.agents.researcher import research_subquestion
from backend.agents.reviser import revise_claim
from backend.agents.synthesizer import plan_synthesis
from backend.agents.verifier import verify_claim, verify_pending
from backend.budget import Budget, BudgetExceeded
from backend.llm import AgentOutputError, ModelCallError
from backend.redact import redact
from backend.report import ReportError, build_report
from backend.search import SearchFatalError
from backend.services import ResearchServices

RESERVE = 2  # model requests never spent on corrections: one final coverage assessment and one synthesis


class ResearchState(TypedDict, total=False):
    run_id: int
    mode: str  # "collect" (default), "verify" (start at draft) or "complete" (start after verification)
    verify: bool  # collect mode only: continue into verification when collection ends cleanly
    complete: bool  # continue into the correction loop and report after verification
    verify_first: bool  # complete mode only: the run has no claims yet, so draft and verify them first
    queue: list[int]  # subquestion IDs still to research
    stop_reason: str | None  # collection: budget limit reached
    error: str | None  # collection: a node failed
    claim_ids: list[int]
    v_stop: str | None  # verification: budget limit reached
    v_error: str | None  # verification: a node failed
    round: int  # corrective rounds completed
    loop_end: str | None  # why the correction loop ended on its own criteria
    c_stop: str | None  # completion: a budget limit was reached
    c_error: str | None  # completion: a node failed
    last_sig: Any  # coverage signature of the previous assessment (no-progress detection)
    new_claim_ids: list[int]
    report_id: int | None


KNOWN_ERRORS = (ModelCallError, AgentOutputError, SearchFatalError)


def build_graph(svc: ResearchServices, conn: sqlite3.Connection, budget: Budget):
    s = svc.settings

    def model_left() -> int:
        return s.max_model_requests - budget.model_requests

    # ---- collection ----------------------------------------------------------------------------------------------

    def planner(state: ResearchState) -> ResearchState:
        run = db.get_run(conn, state["run_id"])
        try:
            return {"queue": run_planner(conn, svc.llm, budget, s, run)}
        except BudgetExceeded as exc:
            return {"stop_reason": exc.reason}
        except KNOWN_ERRORS as exc:
            return {"error": f"planner failed: {exc}"}

    def researcher(state: ResearchState) -> ResearchState:
        run_id, (current, *rest) = state["run_id"], state["queue"]
        subquestion = db.get_subquestion(conn, current)
        try:
            status, reason = research_subquestion(
                conn, svc.llm, svc.search, svc.fetcher, budget, s, run_id, subquestion, queue_left=len(state["queue"]))
        except BudgetExceeded as exc:
            return {"stop_reason": exc.reason}  # `current` stays queued so finish marks it collection_incomplete
        except KNOWN_ERRORS as exc:
            return {"error": f"researcher failed on subquestion {current}: {exc}"}
        db.add_event(conn, run_id, "subquestion_done", "researcher",
                     {"subquestion_id": current, "status": status, "reason": reason})
        return {"queue": rest}

    def associate(state: ResearchState) -> ResearchState:
        try:
            associate_sources(conn, svc.llm, budget, s, state["run_id"])
        except BudgetExceeded as exc:
            db.add_event(conn, state["run_id"], "association_skipped", "relevance", {"reason": exc.reason})
            return {"stop_reason": exc.reason}
        except (ModelCallError, AgentOutputError) as exc:
            # Cross-subquestion association is OPTIONAL: every source already has its direct subquestion link from collection. A model/transport failure
            # here is recorded as a warning and the run goes on, but only while the shared deadline and budget still permit more work. Database,
            # integrity and programming errors are not caught: they propagate to the job's failure handling.
            db.add_event(conn, state["run_id"], "association_failed", "relevance", {
                "warning": "optional cross-subquestion association failed; direct source-subquestion links were kept",
                "error_type": type(exc).__name__, "error": redact(str(exc), s)[:300]})
            try:
                budget.call_timeout()
            except BudgetExceeded as stop:
                return {"stop_reason": stop.reason}
            return {}
        except SearchFatalError as exc:
            return {"error": f"relevance check failed: {exc}"}
        return {}

    def finish(state: ResearchState) -> ResearchState:
        run_id = state["run_id"]
        unresolved = list(state.get("queue", []))
        with db.tx(conn):
            if state.get("error") and unresolved:
                db.set_subquestion_status(conn, unresolved[0], "failed")  # the subquestion being worked on
                unresolved = unresolved[1:]
            for sq_id in unresolved:
                db.set_subquestion_status(conn, sq_id, "collection_incomplete")
            if state.get("error"):
                status, reason = "failed", state["error"]
            elif state.get("stop_reason"):
                status, reason = "stopped", state["stop_reason"]
            else:
                status, reason = "collection_finished", None
            db.set_run_status(conn, run_id, status, reason)
            db.add_event(conn, run_id, "research_finished", "graph", {
                "status": status, "stop_reason": reason, "unresolved_subquestions": state.get("queue", []),
                "budget_used_of_limit": budget.snapshot(), "search_allocation": budget.search_allocation(),
            })
        return {}

    # ---- verification --------------------------------------------------------------------------------------------

    def draft(state: ResearchState) -> ResearchState:
        try:
            return {"claim_ids": run_drafter(conn, svc.llm, budget, s, state["run_id"])}
        except BudgetExceeded as exc:
            return {"v_stop": exc.reason}
        except KNOWN_ERRORS as exc:
            return {"v_error": f"drafter failed: {exc}"}

    def integrity(state: ResearchState) -> ResearchState:
        """Deterministic evidence check. Claims that fail it are stored as rejected and never reach the verifier."""
        results = [claims.check_integrity(conn, cid, s) for cid in state.get("claim_ids", [])]
        db.add_event(conn, state["run_id"], "integrity_checked", "integrity", {
            "passed": [r["claim_id"] for r in results if r["verdict"] == "passed"],
            "failed": [r["claim_id"] for r in results if r["verdict"] == "failed"]})
        return {}

    def verify(state: ResearchState) -> ResearchState:
        try:
            counts = verify_pending(conn, svc.llm, budget, s, state["run_id"], state.get("claim_ids", []))
        except BudgetExceeded as exc:
            return {"v_stop": exc.reason}  # claims without a semantic result stay 'awaiting_verification'
        db.add_event(conn, state["run_id"], "claims_verified", "verifier", counts)
        return {}

    def finish_verify(state: ResearchState) -> ResearchState:
        run_id = state["run_id"]
        if state.get("v_error"):
            status, reason = "failed", state["v_error"]
        elif state.get("v_stop"):
            status, reason = "stopped", state["v_stop"]
        else:
            status, reason = "completed", None
        with db.tx(conn):
            db.set_verify_status(conn, run_id, status, reason)
            db.add_event(conn, run_id, "verification_finished", "graph", {
                "status": status, "stop_reason": reason, "claims": claims.summary(conn, run_id),
                "budget_used_of_limit": budget.snapshot()})
        return {}

    def skip_verify(state: ResearchState) -> ResearchState:
        """Collection did not end cleanly, so verification was not attempted; leave it startable on its own."""
        with db.tx(conn):
            db.set_verify_status(conn, state["run_id"], "not_started", None)
            if state.get("complete"):
                db.set_complete_status(conn, state["run_id"], "not_started", None)
            db.add_event(conn, state["run_id"], "verification_skipped", "graph",
                         {"reason": "collection did not finish cleanly"})
        return {}

    # ---- completion: correction loop and report -------------------------------------------------------------------

    def rejudge(state: ResearchState) -> ResearchState:
        """Re-judge 'supported' verdicts made under an older verifier rubric: they are not trusted for reports."""
        run_id = state["run_id"]
        todo = [c for c in claims.active_claims(conn, run_id) if c["needs_rejudge"]]
        for c in todo:
            with db.tx(conn):
                tid = correction.add_task(conn, run_id, 0, "rejudge", claim_id=c["id"], subquestion_id=c["subquestion_id"],
                                          spec={"reason": "verdict made under an older verifier rubric",
                                                "previous_rubric": c["semantic"]["details"].get("rubric")})
            try:
                result = verify_claim(conn, svc.llm, budget, s, c["id"], force=True)
            except BudgetExceeded as exc:
                with db.tx(conn):
                    correction.finish_task(conn, tid, "skipped", {"reason": exc.reason})
                return {"c_stop": exc.reason}
            with db.tx(conn):
                correction.finish_task(conn, tid, "done", {"from": "supported", "to": result["verdict"]})
        if todo:
            db.add_event(conn, run_id, "claims_rejudged", "rejudge", {"claim_ids": [c["id"] for c in todo]})
        return {}

    def coverage(state: ResearchState) -> ResearchState:
        run_id, r = state["run_id"], state.get("round", 0)
        try:
            ensure_obligations(conn, svc.llm, budget, s, run_id)  # splits requirements by subject and dimension (once)
            rows = assess_coverage(conn, svc.llm, budget, s, run_id, r)
        except BudgetExceeded as exc:
            return {"c_stop": exc.reason, "loop_end": None}
        except KNOWN_ERRORS as exc:
            return {"c_error": f"coverage assessment failed: {exc}"}
        summary = correction.coverage_summary(rows)
        sig = correction.signature(conn, run_id, rows)
        end, stop = None, None
        if summary["fully_addressed"]:
            end = "all evidence requirements were assessed as addressed"
        elif r >= s.max_correction_rounds:
            end = f"corrective rounds exhausted ({s.max_correction_rounds})"
        elif r > 0 and sig == state.get("last_sig"):
            end = "no progress: the eligible claims and every requirement status are unchanged"
        proposals = correction.propose_tasks(conn, run_id, rows, s, s.max_search_queries - budget.search_queries)
        actionable = [p for p in proposals if p["kind"] in correction.ACTIONABLE and not p.get("skip")]
        search_blocked = [p for p in proposals if p.get("skip") == "no search budget left"]
        if end is None and not actionable:
            if search_blocked:  # the real reason is the search limit, so it is reported (and counted) as a budget stop
                stop = (f"search budget exhausted ({budget.search_queries} of {s.max_search_queries} searches used); "
                        f"{len(search_blocked)} unmet requirement(s) could not be researched")
            else:
                end = "no actionable correction tasks"
        if end is None and stop is None and model_left() < 1 + RESERVE:
            stop = f"insufficient model budget for another round ({model_left()} requests left, {RESERVE} reserved)"
        if end or stop:
            for p in proposals:  # nothing silently disappears: planned work that will not run is recorded as skipped
                if p["kind"] in correction.ACTIONABLE and not p.get("skip"):
                    p["skip"] = end or stop
        correction.persist_tasks(conn, run_id, r, proposals)
        db.add_event(conn, run_id, "coverage_routed", "coverage", {
            "round": r, "summary": summary, "tasks": [{"kind": p["kind"], "claim_id": p.get("claim_id"),
                                                       "requirement_key": p.get("requirement_key"), "skipped": p.get("skip")}
                                                      for p in proposals],
            "loop_end": end, "stop": stop})
        return {"last_sig": sig, "loop_end": end, "c_stop": stop}

    def correct(state: ResearchState) -> ResearchState:
        run_id, planned = state["run_id"], state.get("round", 0)
        k = planned + 1
        tasks = correction.list_tasks(conn, run_id, planned_round=planned, status="planned")
        tasks.sort(key=lambda t: t["kind"] == "missing_evidence")  # repairs and re-checks before searches
        db.add_event(conn, run_id, "correction_round_started", "correct", {"round": k, "tasks": [t["id"] for t in tasks]})
        committed, new_claims, new_sources, touched, stop = 0, [], set(), set(), None  # committed = verifications still owed
        for t in tasks:
            if stop:
                with db.tx(conn):
                    correction.finish_task(conn, t["id"], "skipped", {"reason": stop})
                continue
            # (requests spent now, verifications this task will owe later): a repair is revise + verify, a search is queries + selection
            now, owed = {"claim_repair": (1, 1), "rejudge": (1, 0), "missing_evidence": (2, 0)}[t["kind"]]
            if model_left() - committed < now + owed + RESERVE:
                with db.tx(conn):
                    correction.finish_task(conn, t["id"], "skipped", {"reason": "reserving model requests for coverage and the report"})
                continue
            try:
                if t["kind"] == "claim_repair":
                    out = revise_claim(conn, svc.llm, budget, s, run_id, t["claim_id"], planned_round=planned, task_id=t["id"],
                                       failure=t["spec"].get("failure", {}),
                                       max_parts=max(0, model_left() - 1 - committed - RESERVE))
                    if out["action"] == "revised":
                        new_claims.append(out["claim_id"])
                        committed += 1
                    elif out["action"] == "split":
                        new_claims += out["claim_ids"]
                        committed += len(out["claim_ids"])
                elif t["kind"] == "rejudge":
                    res = verify_claim(conn, svc.llm, budget, s, t["claim_id"], force=True)
                    out = {"verdict": res["verdict"]}
                else:  # missing_evidence: targeted research through the existing Researcher and ingestion service
                    before = {x["id"] for x in db.list_sources(conn, run_id)}
                    sq = db.get_subquestion(conn, t["subquestion_id"])
                    status, reason = research_subquestion(conn, svc.llm, svc.search, svc.fetcher, budget, s, run_id, sq,
                                                          focus=t["spec"])
                    added = sorted({x["id"] for x in db.list_sources(conn, run_id)} - before)
                    new_sources |= set(added)
                    if added:
                        touched.add(t["subquestion_id"])
                    out = {"new_source_ids": added, "status": status, "reason": reason}
                with db.tx(conn):
                    correction.finish_task(conn, t["id"], "skipped" if out.get("action") == "skipped" else "done", out)
            except BudgetExceeded as exc:
                stop = exc.reason
                with db.tx(conn):
                    correction.finish_task(conn, t["id"], "skipped", {"reason": exc.reason})
            except KNOWN_ERRORS as exc:
                with db.tx(conn):
                    correction.finish_task(conn, t["id"], "failed", {"error": redact(str(exc), s)})
        if new_sources and not stop:
            # Corrective evidence may be drafted into bounded CANDIDATE claims. The cap is cumulative for the job and separate
            # from the report's size limit; every candidate still needs integrity and a semantic verdict before it can count.
            allowed = min(budget.candidates_left(), model_left() - committed - 1 - RESERVE)
            if allowed >= 1:
                try:
                    ids = run_drafter(conn, svc.llm, budget, s, run_id, only_sources=new_sources, only_subquestions=touched,
                                      unmet=correction.unmet_obligations(conn, run_id),
                                      max_new=allowed, provenance={"round": k, "origin": "targeted_research", "candidate": True})
                    budget.add_candidates(len(ids))
                    new_claims += ids
                    committed += len(ids)
                    db.add_event(conn, run_id, "candidates_drafted", "correct", {
                        "round": k, "claim_ids": ids, "from_source_ids": sorted(new_sources),
                        "candidate_claims_used_of_limit": [budget.candidate_claims, s.max_candidate_claims]})
                except BudgetExceeded as exc:
                    stop = exc.reason
                except KNOWN_ERRORS as exc:
                    db.add_event(conn, run_id, "drafting_failed", "correct", {"round": k, "error": redact(str(exc), s)})
            else:
                db.add_event(conn, run_id, "drafting_skipped", "correct", {
                    "round": k, "new_source_ids": sorted(new_sources),
                    "candidate_claims_used_of_limit": [budget.candidate_claims, s.max_candidate_claims],
                    "model_requests_left": model_left(), "reserved": RESERVE,
                    "reason": ("candidate-claim budget exhausted" if budget.candidates_left() <= 0 else
                               "not enough model requests to draft and verify candidates while reserving finalization")})
        db.add_event(conn, run_id, "correction_round_finished", "correct", {
            "round": k, "new_claim_ids": new_claims, "new_source_ids": sorted(new_sources), "stop": stop})
        return {"round": k, "new_claim_ids": new_claims, **({"c_stop": stop} if stop else {})}

    def reverify(state: ResearchState) -> ResearchState:
        """New claim versions and newly drafted claims go through integrity and the semantic verifier from scratch."""
        try:
            counts = verify_pending(conn, svc.llm, budget, s, state["run_id"], state.get("new_claim_ids", []))
        except BudgetExceeded as exc:
            return {"c_stop": exc.reason}
        db.add_event(conn, state["run_id"], "claims_reverified", "verifier", counts)
        return {}

    def report(state: ResearchState) -> ResearchState:
        run_id, stop = state["run_id"], state.get("c_stop")
        plan, model, call_id, reason = None, None, None, None
        if not claims.eligible_claims(conn, run_id):
            reason = "no eligible claims: no synthesis call needed"
        elif stop:
            reason = f"synthesizer not called: {stop}"
        else:
            try:
                plan, call_id = plan_synthesis(conn, svc.llm, budget, s, run_id)
                model = s.effective_synthesizer_model
            except BudgetExceeded as exc:
                stop, reason = exc.reason, f"synthesizer not called: {exc.reason}"
            except KNOWN_ERRORS as exc:
                reason = f"synthesizer failed ({redact(str(exc), s)}); deterministic ordering used"
        try:
            info = build_report(conn, run_id, plan=plan, ordering_reason=reason, model=model, model_call_id=call_id,
                                stop_reason=stop or state.get("loop_end"), corrective_rounds=state.get("round", 0),
                                max_claims=s.max_report_claims)
        except (ReportError, sqlite3.IntegrityError) as exc:
            return {"c_error": f"report assembly failed: {exc}", "c_stop": stop}
        return {"report_id": info["report_id"], "c_stop": stop}

    def finish_complete(state: ResearchState) -> ResearchState:
        run_id = state["run_id"]
        error = state.get("c_error") or state.get("v_error")
        if error:
            status, reason = "failed", error
        elif state.get("c_stop"):
            status, reason = "stopped", state["c_stop"]
        else:
            status, reason = "completed", state.get("loop_end")
        with db.tx(conn):
            db.set_complete_status(conn, run_id, status, reason)
            db.add_event(conn, run_id, "completion_finished", "graph", {
                "status": status, "stop_reason": reason, "report_id": state.get("report_id"),
                "corrective_rounds": state.get("round", 0), "budget_used_of_limit": budget.snapshot(),
                "search_allocation": budget.search_allocation()})
        return {}

    # ---- routing -------------------------------------------------------------------------------------------------

    def entry(state: ResearchState) -> str:
        if state.get("mode") == "verify":
            return "draft"
        if state.get("mode") == "complete":
            return "draft" if state.get("verify_first") else "rejudge"
        return "planner"

    def after_planner(state: ResearchState) -> str:
        return "finish" if state.get("error") or state.get("stop_reason") or not state.get("queue") else "researcher"

    def after_research(state: ResearchState) -> str:
        if state.get("error") or state.get("stop_reason"):
            return "finish"
        return "researcher" if state.get("queue") else "associate"

    def after_finish(state: ResearchState) -> str:
        if not state.get("verify"):
            return "end"
        return "skip_verify" if state.get("error") or state.get("stop_reason") else "draft"

    def after_draft(state: ResearchState) -> str:
        return "finish_verify" if state.get("v_error") or state.get("v_stop") or not state.get("claim_ids") else "integrity"

    def after_finish_verify(state: ResearchState) -> str:
        if not state.get("complete") and state.get("mode") != "complete":
            return "end"
        return "finish_complete" if state.get("v_error") else "rejudge"

    def after_rejudge(state: ResearchState) -> str:
        return "report" if state.get("c_stop") else "coverage"

    def after_coverage(state: ResearchState) -> str:
        if state.get("c_error"):
            return "finish_complete"
        return "report" if state.get("c_stop") or state.get("loop_end") else "correct"

    def after_correct(state: ResearchState) -> str:
        return "report" if state.get("c_stop") else "reverify"

    def after_reverify(state: ResearchState) -> str:
        return "report" if state.get("c_stop") else "coverage"

    def after_report(state: ResearchState) -> str:
        return "finish_complete"

    graph = StateGraph(ResearchState)
    for name, fn in (("planner", planner), ("researcher", researcher), ("associate", associate), ("finish", finish),
                     ("draft", draft), ("integrity", integrity), ("verify", verify), ("finish_verify", finish_verify),
                     ("skip_verify", skip_verify), ("rejudge", rejudge), ("coverage", coverage), ("correct", correct),
                     ("reverify", reverify), ("report", report), ("finish_complete", finish_complete)):
        graph.add_node(name, fn)
    graph.add_conditional_edges(START, entry, {"planner": "planner", "draft": "draft", "rejudge": "rejudge"})
    graph.add_conditional_edges("planner", after_planner, {"researcher": "researcher", "finish": "finish"})
    graph.add_conditional_edges(
        "researcher", after_research, {"researcher": "researcher", "associate": "associate", "finish": "finish"})
    graph.add_edge("associate", "finish")
    graph.add_conditional_edges("finish", after_finish, {"end": END, "draft": "draft", "skip_verify": "skip_verify"})
    graph.add_conditional_edges("draft", after_draft, {"integrity": "integrity", "finish_verify": "finish_verify"})
    graph.add_edge("integrity", "verify")
    graph.add_edge("verify", "finish_verify")
    graph.add_conditional_edges("finish_verify", after_finish_verify,
                                {"end": END, "rejudge": "rejudge", "finish_complete": "finish_complete"})
    graph.add_conditional_edges("rejudge", after_rejudge, {"coverage": "coverage", "report": "report"})
    graph.add_conditional_edges("coverage", after_coverage,
                                {"correct": "correct", "report": "report", "finish_complete": "finish_complete"})
    graph.add_conditional_edges("correct", after_correct, {"reverify": "reverify", "report": "report"})
    graph.add_conditional_edges("reverify", after_reverify, {"coverage": "coverage", "report": "report"})
    graph.add_conditional_edges("report", after_report, {"finish_complete": "finish_complete"})
    graph.add_edge("finish_complete", END)
    graph.add_edge("skip_verify", END)
    return graph.compile()


def _run_job(run_id: int, svc: ResearchServices, initial: dict, *, collecting: bool) -> None:
    conn = svc.open_db()
    budget = Budget(svc.settings)  # the ONE budget of this job; every node and round draws on it
    try:
        db.add_event(conn, run_id, "research_started" if collecting else "verification_started", "graph", {
            "limits": budget.snapshot(), "search_allocation": budget.search_allocation(),
            "structured_output_models": sorted(svc.llm.structured_models)})
        build_graph(svc, conn, budget).invoke({"run_id": run_id, **initial})
    except Exception as exc:
        detail = redact(f"{type(exc).__name__}: {exc}", svc.settings)[:500]
        run = db.get_run(conn, run_id)
        if run is None:  # nothing to record on (and no event can reference it): surface the ORIGINAL error instead of failing again here
            raise
        with db.tx(conn):
            if collecting and run["status"] == "researching":
                # Do not leave subquestions claiming to be in progress or untouched after a crash.
                conn.execute("UPDATE subquestions SET status = 'failed' WHERE run_id = ? AND status = 'researching'", (run_id,))
                conn.execute("UPDATE subquestions SET status = 'collection_incomplete' WHERE run_id = ? AND status = 'open'", (run_id,))
                db.set_run_status(conn, run_id, "failed", f"unexpected {detail}")
                run = db.get_run(conn, run_id)
            if run["verify_status"] == "verifying":
                db.set_verify_status(conn, run_id, "failed", f"unexpected {detail}")
            if run["complete_status"] == "completing":
                db.set_complete_status(conn, run_id, "failed", f"unexpected {detail}")
            db.add_event(conn, run_id, "research_finished" if collecting else "verification_finished", "graph",
                         {"status": "failed", "error": detail})
    except BaseException as interrupt:  # KeyboardInterrupt, SystemExit, cancellation: record it, close cleanly, then let it propagate
        db.record_job_interrupted(conn, run_id, type(interrupt).__name__)
        raise
    finally:
        conn.close()


def execute_research(run_id: int, svc: ResearchServices, verify: bool = False, complete: bool = False) -> None:
    """Run collection (and, if `verify`, drafting and verification; if `complete`, also the correction loop and the
    report) for a run already claimed as 'researching'. One shared budget for all of it."""
    _run_job(run_id, svc, {"mode": "collect", "verify": verify or complete, "complete": complete, "queue": [],
                           "stop_reason": None, "error": None}, collecting=True)


def execute_verification(run_id: int, svc: ResearchServices) -> None:
    """Draft and verify claims on an already collected run (verify_status already claimed as 'verifying')."""
    _run_job(run_id, svc, {"mode": "verify"}, collecting=False)


def execute_completion(run_id: int, svc: ResearchServices, verify_first: bool = False) -> None:
    """Complete an already collected run: (draft and verify if there are no claims yet,) re-judge stale verdicts, run the
    correction loop, and persist the report (complete_status already claimed as 'completing')."""
    _run_job(run_id, svc, {"mode": "complete", "complete": True, "verify_first": verify_first, "round": 0},
             collecting=False)
