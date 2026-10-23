"""The pilot: 12 human-reviewed DEVELOPMENT claim examples (frozen verification) and 2 DEVELOPMENT research questions (end to end).

The pilot checks the plumbing and the per-question numbers before anything larger. It never touches the held-out sets: `plan` and `execute`
refuse any held-out id. It also refuses to run until every one of its 12 claim examples has an authoritative human label, so its results can
be scored against human judgement and not against a model's suggestion. `plan` is a dry run; nothing here calls a model, search or fetch.
"""

from backend.config import Settings
from evaluation import datasets, verify_eval
from evaluation.review import BATCH1

PILOT_CLAIMS = BATCH1
PILOT_QUESTIONS = ["Q02", "Q05"]

# Limits per end-to-end question and for the frozen verification step (stated, set by flags, recorded in every result).
E2E_LIMITS = {"max_model_requests": 40, "max_search_queries": 8, "max_page_fetches": 12, "max_total_output_tokens": 30000, "max_elapsed_seconds": 600.0}
VERIFY_LIMITS = {"max_model_requests": 24, "max_total_output_tokens": 36000, "max_elapsed_seconds": 360.0}
DEFAULT_ARM_LIMITS = {"max_model_requests": 20, "max_search_queries": 8, "max_page_fetches": 12, "max_total_output_tokens": 15000, "max_elapsed_seconds": 300.0}

# MEASURED in earlier stored runs (returned token counts x catalog prices, calculated; not provider charges). Verifier: 2,873 prompt / 177 completion
# tokens per call, 4.2 s. The dearest measured role (coverage, claude-haiku-4.5) costs $0.0042598 per call. A typical end-to-end question is a
# GUESS of about 26 requests from two earlier runs, costing about $0.056 (10 verifier + 4 coverage-class calls dominate).
USD_PER_VERIFIER_CALL = 0.0037587
USD_PER_DEAREST_CALL = 0.0042598
TYPICAL_REQUESTS_PER_QUESTION = 26
TYPICAL_USD_PER_QUESTION = 0.056
VERIFIER_SECONDS_PER_CALL = 4.2
TYPICAL_SECONDS_PER_QUESTION = 180


def plan(settings: Settings | None = None) -> dict:
    """The pilot's scope, limits and estimated incremental cost. Refuses held-out ids."""
    lock = datasets._read(datasets.LOCK)["lock"]
    held = set(lock["questions"]["heldout_ids"]) | set(lock["claim_examples"]["heldout_ids"])
    touched = held & (set(PILOT_CLAIMS) | set(PILOT_QUESTIONS))
    if touched:
        raise SystemExit(f"the pilot must not include held-out items: {sorted(touched)}")
    n_claims, n_q = len(PILOT_CLAIMS), len(PILOT_QUESTIONS)
    labels = datasets.authoritative_labels()
    missing = [i for i in PILOT_CLAIMS if i not in labels]
    withheld = datasets.withheld_ids([e for e in datasets.claim_examples() if e["id"] in PILOT_CLAIMS])
    verify = {"examples": PILOT_CLAIMS, "limits": VERIFY_LIMITS,
              "requests": {"typical": n_claims, "cap": VERIFY_LIMITS["max_model_requests"]},
              "output_tokens_cap": VERIFY_LIMITS["max_total_output_tokens"],
              "seconds": {"typical": round(n_claims * VERIFIER_SECONDS_PER_CALL), "cap": VERIFY_LIMITS["max_elapsed_seconds"]},
              "calculated_usd": {"typical": round(n_claims * USD_PER_VERIFIER_CALL, 3),
                                 "cap": round(VERIFY_LIMITS["max_model_requests"] * USD_PER_DEAREST_CALL, 3)}}
    e2e = {"questions": PILOT_QUESTIONS, "per_question_limits": E2E_LIMITS,
           "requests": {"typical": n_q * TYPICAL_REQUESTS_PER_QUESTION, "cap": n_q * E2E_LIMITS["max_model_requests"]},
           "searches_cap": n_q * E2E_LIMITS["max_search_queries"], "fetches_cap": n_q * E2E_LIMITS["max_page_fetches"],
           "output_tokens_cap": n_q * E2E_LIMITS["max_total_output_tokens"],
           "seconds": {"typical": n_q * TYPICAL_SECONDS_PER_QUESTION, "cap": n_q * E2E_LIMITS["max_elapsed_seconds"]},
           "calculated_usd": {"typical": round(n_q * TYPICAL_USD_PER_QUESTION, 3),
                              "cap": round(n_q * E2E_LIMITS["max_model_requests"] * USD_PER_DEAREST_CALL, 3)}}
    total = {"requests": {"typical": verify["requests"]["typical"] + e2e["requests"]["typical"], "cap": verify["requests"]["cap"] + e2e["requests"]["cap"]},
             "searches_cap": e2e["searches_cap"], "fetches_cap": e2e["fetches_cap"],
             "output_tokens_cap": verify["output_tokens_cap"] + e2e["output_tokens_cap"],
             "seconds": {"typical": verify["seconds"]["typical"] + e2e["seconds"]["typical"], "cap": verify["seconds"]["cap"] + e2e["seconds"]["cap"]},
             "calculated_usd": {"typical": round(verify["calculated_usd"]["typical"] + e2e["calculated_usd"]["typical"], 2),
                                "cap": round(verify["calculated_usd"]["cap"] + e2e["calculated_usd"]["cap"], 2)},
             "search_credits_cap": e2e["searches_cap"]}
    default_arm = {
        "what": ("OPTIONAL, separate approval: the same 2 questions again with the PRODUCTION default budget, to measure what the 20-request default "
                 "does to the same pipeline. It is how the difference between 40 and 20 is reported with data rather than argued."),
        "limits": DEFAULT_ARM_LIMITS,
        "requests": {"cap": n_q * DEFAULT_ARM_LIMITS["max_model_requests"]}, "searches_cap": n_q * DEFAULT_ARM_LIMITS["max_search_queries"],
        "fetches_cap": n_q * DEFAULT_ARM_LIMITS["max_page_fetches"], "output_tokens_cap": n_q * DEFAULT_ARM_LIMITS["max_total_output_tokens"],
        "seconds_cap": n_q * DEFAULT_ARM_LIMITS["max_elapsed_seconds"],
        "calculated_usd": {"cap": round(n_q * DEFAULT_ARM_LIMITS["max_model_requests"] * USD_PER_DEAREST_CALL, 2)}}
    return {
        "dry_run": True, "split": "dev only; held-out untouched", "claim_verification": verify, "end_to_end": e2e, "total": total,
        "optional_default_budget_arm": default_arm,
        "ready_to_execute": not missing and not withheld,
        "claim_evidence_withheld": withheld,
        "label_provenance": datasets.label_summary(PILOT_CLAIMS)["provenance"] or ["no labels yet"],
        "blocked_by": ([f"{len(missing)} of {n_claims} pilot claim examples have no authoritative human label yet: {missing}"] if missing else [])
                      + ([(f"the public export withholds the evidence of {withheld}: the claims arm refuses unless --available-evidence-only is given "
                           f"({n_claims - len(withheld)} of {n_claims}, a smaller stated denominator); the end-to-end arm is not affected")] if withheld else []),
        "cost_meaning": ("calculated from measured average tokens x catalog prices; not a provider-reported charge. The 'cap' cost assumes every request "
                         "is the dearest measured role; the request, search, token and time caps are hard limits, the dollar figure is not. Tavily "
                         "search credits are not priced."),
        "budget_note": ("End-to-end limits are 40 requests per question; the product default is 20 (see docs/PHASE6A.md). Results at 40 are not default "
                        "product performance and every result file says so."),
    }


def require_ready() -> None:
    """Raise unless all 12 claim examples carry an authoritative human label (run before any live pilot step)."""
    missing = [i for i in PILOT_CLAIMS if i not in datasets.authoritative_labels()]
    if missing:
        raise SystemExit(f"pilot blocked: no authoritative human label for {missing}. Review evaluation/review/claim_review_batch1.md first.")


def examples(*, available_only: bool = False) -> list[dict]:
    """The 12 pilot examples. In the public export some evidence is withheld: available_only=True returns the ones whose evidence is published (a smaller,
    explicitly selected denominator); otherwise running the claims arm fails clearly."""
    by_id = {e["id"]: e for e in datasets.claim_examples(available_only=available_only)}
    chosen = [by_id[i] for i in PILOT_CLAIMS if i in by_id]
    assert all(e["split"] == "dev" for e in chosen)
    return chosen


def run_verification(llm, settings: Settings, *, available_only: bool = False, **kw):
    """LIVE (spends model credits): frozen verification of the 12 reviewed development examples. Call `require_ready()` first."""
    require_ready()
    return verify_eval.run_frozen(examples(available_only=available_only), llm, settings, **kw)
