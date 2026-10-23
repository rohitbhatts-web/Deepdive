"""Web search provider. Tavily: https://docs.tavily.com/documentation/api-reference/endpoint/search

Search results are only discovery hints (title, URL, snippet). Verification evidence is the fetched page.
"""

from typing import Protocol

import httpx
from pydantic import BaseModel

from backend.config import Settings
from backend.redact import redact

PROVIDER_NAME = "tavily"


class SearchError(RuntimeError):
    """A search failed; the run can continue with other queries."""


class SearchFatalError(SearchError):
    """Credentials or quota are unusable; further searches would fail the same way."""


class SearchHit(BaseModel):
    title: str = ""
    url: str
    snippet: str = ""
    score: float | None = None


class SearchResponse(BaseModel):
    hits: list[SearchHit]
    usage: dict | None = None
    request_id: str | None = None


class SearchProvider(Protocol):
    name: str

    def search(self, query: str, max_results: int) -> SearchResponse: ...


class TavilySearch:
    name = PROVIDER_NAME

    def __init__(self, settings: Settings, transport: httpx.BaseTransport | None = None):
        if not settings.tavily_api_key:
            raise SearchFatalError("TAVILY_API_KEY is not set")
        self.settings = settings
        self.transport = transport

    def search(self, query: str, max_results: int) -> SearchResponse:
        body = {
            "query": query, "max_results": max_results, "search_depth": "basic",
            "include_answer": False,  # no LLM-generated answer: only discoverable sources, never claims
            "include_raw_content": False, "include_images": False,
            "auto_parameters": False,  # would let the provider raise depth (2 credits) on its own
            "include_usage": True,
        }
        try:
            with httpx.Client(transport=self.transport, timeout=httpx.Timeout(20, connect=5)) as client:
                resp = client.post(
                    f"{self.settings.tavily_base_url}/search",
                    json=body,
                    headers={"Authorization": f"Bearer {self.settings.tavily_api_key}"},
                )
        except httpx.HTTPError as exc:
            raise SearchError(redact(f"{type(exc).__name__}: {exc}", self.settings)) from exc
        if resp.status_code in (401, 403, 432, 433):
            raise SearchFatalError(
                redact(f"Tavily rejected the request (HTTP {resp.status_code}): {resp.text[:200]}", self.settings))
        if resp.status_code != 200:
            raise SearchError(redact(f"Tavily HTTP {resp.status_code}: {resp.text[:200]}", self.settings))
        data = resp.json()
        hits = [
            SearchHit(title=r.get("title") or "", url=r["url"], snippet=r.get("content") or "", score=r.get("score"))
            for r in data.get("results", [])
            if r.get("url")
        ]
        return SearchResponse(hits=hits, usage=data.get("usage"), request_id=data.get("request_id"))
