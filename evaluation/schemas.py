"""Result-file schemas and run metadata. `uv run python -m evaluation schema` writes JSON Schemas to evaluation/schemas/."""

import hashlib
import importlib
import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from backend import claims
from backend.config import Settings
from evaluation import datasets

SCHEMA_VERIFY = "deepdive-eval-verification-result/2"
SCHEMA_E2E = "deepdive-eval-e2e-result/2"
DATA = Path(__file__).parent / "data"

AGENT_MODULES = ("planner", "relevance", "drafter", "verifier", "obligations", "coverage", "reviser", "synthesizer")
SECRET_WORDS = ("key", "token", "secret", "password")


class RunMetadata(BaseModel):
    """What a result file needs to be interpreted and reproduced. A result without this is not comparable to another."""

    schema_version: str
    kind: str  # verification | e2e
    created_at: str
    executed: bool = Field(description="False for a dry run or a fixture/harness test; True only when real calls were made")
    fixture: bool = Field(description="True when model replies came from a test fixture: the numbers are harness checks, not performance")
    split: str
    label_basis: str = Field(description="authoritative-human | none (no human labels exist yet)")
    label_statement: str = Field(default="", description="plain-language statement of how the labels behind every correctness number were made; always shown with them")
    label_provenance: list[str] = Field(default=[], description="distinct provenance of the labels used, e.g. AI-assisted human review; not independent or blinded.")
    label_rubric_versions: list[str] = Field(default=[], description="rubric versions the labels used were judged under")
    label_versions: dict[str, int] = Field(default={}, description="version of each label used (1 = never corrected); earlier versions stay in human_labels.json")
    dataset_label_rubric: str = Field(default="", description="label rubric version of the claim-evidence dataset the examples came from")
    dataset_sha256: dict[str, str]
    models: dict[str, str]
    rubric: str
    prompt_sha256: dict[str, str]
    settings: dict[str, Any]
    budget: dict[str, Any]
    budget_class: str = Field(description="production_defaults, or evaluation_budget_differs_from_production when any limit differs")
    budget_differences: dict[str, Any] = Field(description="each limit that differs from the product default: {limit: {evaluation, production_default}}")
    run_ids: list[int]
    notes: list[str] = []


class VerificationRecord(BaseModel):
    id: str
    split: str
    gold: str | None = Field(description="authoritative human label, or None when no human has labelled this example")
    proposed: str = Field(description="PROVISIONAL model-suggested label; never used as correctness")
    outcome: str  # supported | partially_supported | unsupported | contradicted | integrity_failed | model_error | not_judged
    integrity_reason: str | None = None
    semantic_reason: str | None = None
    run_id: int | None = None
    claim_id: int | None = None
    model_calls: int | None = None
    failed_calls: int | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    latency_seconds: float | None = None
    calculated_cost_usd: float | None = None
    provider_reported_cost_usd: float | None = None


class VerificationResult(BaseModel):
    metadata: RunMetadata
    records: list[VerificationRecord]
    metrics: dict[str, Any]
    agreement_with_provisional: dict[str, Any]
    usage: dict[str, Any]
    errors: list[str]


class E2EQuestionRecord(BaseModel):
    question_id: str
    split: str
    run_id: int | None
    status: str
    stop_reason: str | None
    report_outcome: str | None
    report_id: int | None
    report_claims: int
    initial: list[dict[str, Any]]
    final: list[dict[str, Any]]
    citations: list[dict[str, Any]]
    system_coverage: list[dict[str, Any]] = Field(description="the pipeline's OWN requirement statuses; context only, never a correctness label")
    corrective_rounds: int | None
    budget_used_of_limit: dict[str, Any] | None
    usage: dict[str, Any]
    errors: list[str] = []
    failure: dict[str, Any] | None = Field(default=None, description="set when the run failed or produced no report: stage, classification, detail. A failed run is a failure, never an abstention")


class E2EResult(BaseModel):
    metadata: RunMetadata
    questions: list[E2EQuestionRecord]
    metrics: dict[str, Any]
    errors: list[str]


PRODUCTION_LIMITS = ("max_model_requests", "max_search_queries", "max_page_fetches", "max_total_output_tokens", "max_elapsed_seconds",
                     "max_candidate_claims", "max_correction_rounds", "max_report_claims")


def budget_differences(settings: Settings) -> dict[str, Any]:
    """Limits of this run that differ from the product defaults (the defaults of the Settings class, not of any .env file)."""
    out = {}
    for key in PRODUCTION_LIMITS:
        default = Settings.model_fields[key].default
        value = getattr(settings, key)
        if value != default:
            out[key] = {"evaluation": value, "production_default": default}
    return out


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def prompt_hashes() -> dict[str, str]:
    """SHA-256 of each agent's system prompt (and example), so a change of prompt is visible in the result metadata."""
    out = {}
    for name in AGENT_MODULES:
        mod = importlib.import_module(f"backend.agents.{name}")
        parts = [getattr(mod, a) for a in ("SYSTEM", "EXAMPLE", "USER_TEMPLATE") if hasattr(mod, a)]
        out[name] = hashlib.sha256(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()
    return out


def settings_snapshot(settings: Settings) -> dict[str, Any]:
    """Non-secret settings only (anything whose name suggests a credential is dropped; URLs are kept)."""
    return {k: v for k, v in settings.model_dump().items() if not any(w in k.lower() for w in SECRET_WORDS)}


def label_statement(label_basis: str, summary: dict) -> str:
    """The sentence that must accompany any correctness number: who made the labels and how independent they are."""
    if label_basis != "authoritative-human" or not summary["labels"]:
        return "No authoritative human labels were used: correctness metrics are N/A."
    corrected = f"; {len(summary['corrected'])} label(s) have been corrected since first entry (earlier versions kept)" if summary["corrected"] else ""
    return (f"{summary['labels']} human label(s), rubric {', '.join(summary['rubric_versions'])}. Label provenance: {' / '.join(summary['provenance'])}{corrected}. "
            "These are not independent, blinded annotations and support no inter-annotator agreement claim.")


def run_metadata(kind: str, settings: Settings, *, split: str, budget: dict, run_ids: list[int], executed: bool, fixture: bool,
                 label_basis: str, label_ids: list[str] | None = None, notes: list[str] | None = None) -> RunMetadata:
    diff = budget_differences(settings)
    used = label_basis == "authoritative-human"
    collections = ("claim_labels",) if kind == "verification" else ("requirement_labels", "report_claim_labels")
    summary = datasets.label_summary(label_ids, collections=collections) if used else {"labels": 0, "provenance": [], "rubric_versions": [], "versions": {}, "corrected": []}
    statement = label_statement(label_basis, summary)
    notes = [statement, *(notes or [])]
    if diff:
        notes.append("BUDGET DIFFERS FROM THE PRODUCT DEFAULTS (" + ", ".join(f"{k}: {v['evaluation']} vs {v['production_default']}" for k, v in diff.items())
                     + "): these results are not default product performance and must not be reported as such.")
    return RunMetadata(
        budget_class="evaluation_budget_differs_from_production" if diff else "production_defaults", budget_differences=diff,
        schema_version=SCHEMA_VERIFY if kind == "verification" else SCHEMA_E2E, kind=kind,
        created_at=datetime.now(UTC).isoformat(timespec="seconds"), executed=executed, fixture=fixture, split=split, label_basis=label_basis,
        label_statement=statement, label_provenance=summary["provenance"], label_rubric_versions=summary["rubric_versions"],
        label_versions=summary["versions"], dataset_label_rubric=datasets.load_claim_dataset().get("label_rubric_version", ""),
        dataset_sha256={p.name: sha256_file(p) for p in (DATA / "questions.json", DATA / "claim_evidence.json", DATA / "split.lock.json")},
        models={"planner": settings.planner_model, "researcher": settings.researcher_model, "drafter": settings.drafter_model,
                "verifier": settings.verifier_model, "coverage": settings.effective_coverage_model,
                "synthesizer": settings.effective_synthesizer_model},
        rubric=claims.CURRENT_RUBRIC, prompt_sha256=prompt_hashes(), settings=settings_snapshot(settings), budget=budget, run_ids=run_ids,
        notes=notes)


def write_schemas(directory: Path) -> list[Path]:
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    for name, model in (("verification_result", VerificationResult), ("e2e_result", E2EResult)):
        path = directory / f"{name}.schema.json"
        path.write_text(json.dumps(model.model_json_schema(), indent=1) + "\n", encoding="utf-8")
        paths.append(path)
    return paths
