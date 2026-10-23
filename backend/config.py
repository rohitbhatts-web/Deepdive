"""Application settings, loaded from environment variables (and a local .env file)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

MESH_BASE_URL = "https://api.meshapi.ai/v1"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    @property
    def effective_coverage_model(self) -> str:
        return self.coverage_model or self.verifier_model

    @property
    def effective_synthesizer_model(self) -> str:
        return self.synthesizer_model or self.drafter_model

    mesh_api_key: str = ""
    mesh_base_url: str = MESH_BASE_URL
    mesh_timeout: float = 60.0  # seconds per model request; the SDK default of 600s would outlast any run budget
    min_call_seconds: float = 10.0  # a model request is not started with less job time than this left (it could not finish before the deadline)
    database_path: str = "data/deepdive.db"
    # Hosted recorded demo (see docs/HOSTING.md). When true the backend refuses every mutation, opens SQLite read-only and builds no provider client.
    public_read_only: bool = False
    frontend_dist: str = "frontend/dist"  # the production React build served by backend.public
    demo_info_path: str = "demo/demo_info.json"
    demo_seed_path: str = "demo/seed.sql.gz"
    demo_updates_dir: str = "demo/updates"  # versioned update packages applied to the persistent demo database at start-up (backend/demo_update.py)
    api_url: str = "http://127.0.0.1:8000"

    fetch_connect_timeout: float = 5.0
    fetch_read_timeout: float = 10.0
    fetch_total_timeout: float = 30.0
    fetch_dns_timeout: float = 5.0
    fetch_max_bytes: int = 2_000_000
    fetch_max_redirects: int = 5
    fetch_user_agent: str = "DeepDiveBot/0.1 (+https://github.com/rohitbhatts-web/Deepdive)"

    planner_model: str = ""  # no default: set to a model ID that exists on Mesh (checked before each run)
    researcher_model: str = ""
    drafter_model: str = ""  # Phase 3; no default. Prefer a different model from the verifier.
    verifier_model: str = ""
    coverage_model: str = ""  # Phase 4; empty = use verifier_model
    synthesizer_model: str = ""  # Phase 4; empty = use drafter_model (the Reviser also uses drafter_model)
    tavily_api_key: str = ""
    tavily_base_url: str = "https://api.tavily.com"

    # Research limits. Any one being reached stops the run, and the stop reason is stored.
    max_model_requests: int = 20
    max_search_queries: int = 8
    corrective_search_reserve: int = 2  # searches held back INSIDE max_search_queries for corrective research; initial collection cannot use them (total unchanged)
    max_page_fetches: int = 12
    max_output_tokens: int = 1500  # per model call
    max_total_output_tokens: int = 15000  # across the run, from reported usage
    max_elapsed_seconds: float = 300.0
    max_subquestions: int = 4
    queries_per_subquestion: int = 2
    results_per_query: int = 5
    urls_per_subquestion: int = 3
    association_passages_per_pair: int = 2  # candidate passages shown to the relevance check per source/subquestion
    association_min_overlap: int = 2  # distinct content words a passage must share with a subquestion to be a candidate
    association_max_pairs: int = 8

    # Phase 3 limits
    max_claims: int = 8  # claims kept from one drafting call
    draft_input_chars: int = 9000  # total passage text shown to the drafter
    draft_passages_per_subquestion: int = 4
    draft_context_passages: int = 1  # extra neighbouring passages (per subquestion) kept when a selected one is a fragment
    verifier_context_chars: int = 1500  # per cited passage (neighbours get half)
    verifier_neighbor_passages: int = 1
    min_quote_chars: int = 15  # a shorter quote proves nothing even if it matches

    # Phase 4 correction loop (limits come on top of the shared Budget limits above)
    max_correction_rounds: int = 2  # corrective rounds after the initial pass
    max_candidate_claims: int = 4  # HARD cumulative cap on new candidate claims drafted from corrective evidence per job
    max_report_claims: int = 12  # most claims a report will contain; a separate cap, applied only when assembling it
    max_obligations_per_requirement: int = 4
    max_repairs_per_claim: int = 1  # a repaired version that fails again is excluded
    max_tasks_per_round: int = 4
    queries_per_task: int = 1
    coverage_input_chars: int = 8000
    reviser_input_chars: int = 5000

    passage_target_chars: int = 1000
    passage_max_chars: int = 1500


@lru_cache
def get_settings() -> Settings:
    return Settings()
