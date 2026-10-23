"""Per-run limits. Each charge happens before the action it pays for; reaching a limit stops the run."""

import time
from collections.abc import Callable

from backend.config import Settings


class BudgetExceeded(Exception):
    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


class Budget:
    def __init__(self, settings: Settings, clock: Callable[[], float] = time.monotonic):
        self.settings = settings
        self.clock = clock
        self.started = clock()
        self.model_requests = 0
        self.search_queries = 0  # initial + corrective, the number the total limit applies to
        self.initial_searches = 0
        self.corrective_searches = 0
        self.page_fetches = 0
        self.output_tokens = 0
        self.candidate_claims = 0  # new claims drafted from corrective evidence (each costs a verification request)

    def _check_common(self) -> None:
        s = self.settings
        if self.elapsed() >= s.max_elapsed_seconds:
            raise BudgetExceeded(f"max_elapsed_seconds reached ({s.max_elapsed_seconds:g}s)")
        if self.output_tokens >= s.max_total_output_tokens:
            raise BudgetExceeded(f"max_total_output_tokens reached ({s.max_total_output_tokens})")

    def charge_model_request(self) -> None:
        self._check_common()
        if self.model_requests >= self.settings.max_model_requests:
            raise BudgetExceeded(f"max_model_requests reached ({self.settings.max_model_requests})")
        self.model_requests += 1

    def corrective_reserve(self) -> int:
        """Searches held back for corrective research, taken from (never added to) max_search_queries; at most half of it."""
        return max(0, min(self.settings.corrective_search_reserve, self.settings.max_search_queries // 2))

    def initial_cap(self) -> int:
        return self.settings.max_search_queries - self.corrective_reserve()

    def initial_searches_left(self) -> int:
        return max(0, self.initial_cap() - self.initial_searches)

    def charge_search(self, purpose: str = "initial") -> None:
        """`initial` (collection) may use only the unreserved part of the total; `corrective` may use whatever of the total is left."""
        self._check_common()
        if self.search_queries >= self.settings.max_search_queries:
            raise BudgetExceeded(f"max_search_queries reached ({self.settings.max_search_queries})")
        if purpose == "initial":
            if self.initial_searches >= self.initial_cap():
                raise BudgetExceeded(f"initial search allocation exhausted ({self.initial_searches} of {self.initial_cap()}); "
                                     f"{self.corrective_reserve()} of {self.settings.max_search_queries} searches are reserved for corrective research")
            self.initial_searches += 1
        else:
            self.corrective_searches += 1
        self.search_queries += 1

    def search_allocation(self) -> dict:
        """Recorded in the run's events: how the unchanged total is split and how much of each part was used."""
        return {"total": [self.search_queries, self.settings.max_search_queries],
                "initial": [self.initial_searches, self.initial_cap()], "corrective": [self.corrective_searches, self.corrective_reserve()],
                "note": "corrective research may also use any initial allocation left unused; initial collection may not use the reserve"}

    def charge_fetch(self) -> None:
        self._check_common()
        if self.page_fetches >= self.settings.max_page_fetches:
            raise BudgetExceeded(f"max_page_fetches reached ({self.settings.max_page_fetches})")
        self.page_fetches += 1

    def remaining_seconds(self) -> float:
        return max(0.0, self.settings.max_elapsed_seconds - self.elapsed())

    def call_timeout(self) -> float:
        """The longest one model request may take: the Mesh timeout, but never past the job deadline. With less than `min_call_seconds` of
        job time left no request is started at all (nothing is counted, nothing is sent): the job stops honestly instead."""
        self._check_common()  # a deadline that has already passed keeps its established message (max_elapsed_seconds reached)
        remaining = self.remaining_seconds()
        if remaining < self.settings.min_call_seconds:
            raise BudgetExceeded(f"insufficient time remaining for a model request ({remaining:.0f}s left of {self.settings.max_elapsed_seconds:g}s;"
                                 f" at least {self.settings.min_call_seconds:g}s are needed)")
        return min(self.settings.mesh_timeout, remaining)

    def candidates_left(self) -> int:
        return max(0, self.settings.max_candidate_claims - self.candidate_claims)

    def add_candidates(self, n: int) -> None:
        self.candidate_claims += n

    def add_output_tokens(self, n: int) -> None:
        self.output_tokens += n

    def elapsed(self) -> float:
        return self.clock() - self.started

    def snapshot(self) -> dict:
        s = self.settings
        return {
            "model_requests": [self.model_requests, s.max_model_requests],
            "search_queries": [self.search_queries, s.max_search_queries],
            "page_fetches": [self.page_fetches, s.max_page_fetches],
            "candidate_claims": [self.candidate_claims, s.max_candidate_claims],
            "output_tokens": [self.output_tokens, s.max_total_output_tokens],
            "elapsed_seconds": [round(self.elapsed(), 1), s.max_elapsed_seconds],
        }
