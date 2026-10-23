"""Wiring for a research run: configuration checks and the objects the graph needs."""

import sqlite3
from collections.abc import Callable
from dataclasses import dataclass

from backend import db
from backend.config import Settings, get_settings
from backend.llm import LLM
from backend.mesh_client import get_mesh_async_client, get_mesh_client
from backend.readonly import ReadOnlyError
from backend.search import SearchProvider, TavilySearch
from backend.web import Fetcher

PLACEHOLDERS = {"", "your-mesh-api-key-here", "your-tavily-api-key-here"}


class ConfigurationError(RuntimeError):
    def __init__(self, message: str, missing: list[str] | None = None):
        super().__init__(message)
        self.missing = missing or []


@dataclass
class ResearchServices:
    settings: Settings
    llm: LLM
    search: SearchProvider | None  # None when only verifying: no search is ever possible in that mode
    fetcher: Fetcher | None  # likewise no fetching
    open_db: Callable[[], sqlite3.Connection]


MODES = ("collect", "verify", "all", "retry")


def required_settings(mode: str) -> dict[str, str]:
    """Setting name -> Settings attribute, for what a job of this mode needs."""
    needs = {"MESH_API_KEY": "mesh_api_key"}
    if mode in ("collect", "all"):
        needs |= {"TAVILY_API_KEY": "tavily_api_key", "PLANNER_MODEL": "planner_model",
                  "RESEARCHER_MODEL": "researcher_model"}
    if mode in ("verify", "all", "complete", "retry"):
        needs |= {"DRAFTER_MODEL": "drafter_model", "VERIFIER_MODEL": "verifier_model"}
    if mode == "complete":  # completing an existing run may run targeted searches, so it needs search and the Researcher
        needs |= {"TAVILY_API_KEY": "tavily_api_key", "RESEARCHER_MODEL": "researcher_model"}
    return needs


def missing_configuration(settings: Settings, mode: str = "collect") -> list[str]:
    return [name for name, attr in required_settings(mode).items() if getattr(settings, attr).strip() in PLACEHOLDERS]


def model_catalog(client) -> dict[str, dict]:
    """Mesh's /models returns a bare JSON list (not OpenAI's {"data": [...]}), which the SDK's models.list()
    cannot parse, so read it raw through the same shared client. Also accepts the {"data": [...]} shape."""
    raw = client.get("/models", cast_to=object)
    rows = raw.get("data", []) if isinstance(raw, dict) else raw
    return {m["id"]: m for m in rows}


def check_models(catalog: dict[str, dict], wanted: set[str]) -> None:
    unavailable = sorted(wanted - catalog.keys())
    if unavailable:
        raise ConfigurationError(f"Model(s) not available on Mesh: {', '.join(unavailable)}")
    unusable = sorted(
        m for m in wanted
        if catalog[m].get("model_type", "text") != "text" or catalog[m].get("supports_completions_api") is False
        or catalog[m].get("supports_system_prompt") is False
    )
    if unusable:
        raise ConfigurationError(f"Model(s) cannot serve chat requests with a system prompt: {', '.join(unusable)}")


def build_services(settings: Settings | None = None, mode: str = "collect") -> ResearchServices:
    """Real services. Raises ConfigurationError if anything needed is missing or a model is unavailable."""
    settings = settings or get_settings()
    if settings.public_read_only:
        raise ReadOnlyError("public read-only deployment: research services are never built")
    missing = missing_configuration(settings, mode)
    if missing:
        raise ConfigurationError(f"Missing configuration: {', '.join(missing)}. Set them in .env.", missing)
    client = get_mesh_client()
    try:
        catalog = model_catalog(client)
    except Exception as exc:
        raise ConfigurationError(f"Could not list Mesh models ({type(exc).__name__}): {exc}") from exc
    wanted = {getattr(settings, attr) for name, attr in required_settings(mode).items() if name.endswith("_MODEL")}
    if mode in ("all", "complete", "retry"):  # coverage and synthesis models default to the verifier and drafter models
        wanted |= {settings.effective_coverage_model, settings.effective_synthesizer_model}
    check_models(catalog, wanted)
    structured = frozenset(m for m in wanted if catalog[m].get("supports_structured_output") is True)
    return ResearchServices(
        settings=settings,
        llm=LLM(client, settings, structured, async_client_factory=get_mesh_async_client),
        search=TavilySearch(settings) if mode not in ("verify", "retry") else None,  # a retry never retrieves
        fetcher=Fetcher(settings) if mode not in ("verify", "retry") else None,
        open_db=lambda: db.connect(settings.database_path),
    )
