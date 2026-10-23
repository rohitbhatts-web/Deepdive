"""Test fixtures. Everything here is a deterministic stand-in (temp DB, mock HTTP, fake DNS): no network."""

import httpx
import pytest
from fastapi.testclient import TestClient

from backend import db
from backend.api.completion import get_complete_services, get_retry_services
from backend.api.research import get_services, get_verify_services
from backend.api.runs import get_db, get_fetcher
from backend.config import Settings
from backend.llm import LLM
from backend.main import app
from backend.services import ResearchServices
from backend.web import Fetcher

# FIXTURE DNS: example hosts -> a public documentation-range address; others are deliberately non-public.
FAKE_DNS = {
    "example.test": ["93.184.216.34"],
    "other.test": ["93.184.216.35"],
    "internal.test": ["10.0.0.5"],
    "loopback.test": ["127.0.0.1"],
    "mixed.test": ["93.184.216.34", "192.168.1.10"],
}


def fake_resolver(host: str, port: int) -> list[str]:
    return FAKE_DNS[host]


@pytest.fixture
def settings() -> Settings:
    return Settings(
        mesh_api_key="", passage_target_chars=300, passage_max_chars=500, fetch_max_bytes=50_000,
        max_elapsed_seconds=1_000_000.0,  # tests must not depend on the wall clock (a suspended machine once tripped the 300 s job deadline); tests of the deadline set it explicitly
        planner_model="fixture-planner", researcher_model="fixture-researcher",
        drafter_model="fixture-drafter", verifier_model="fixture-verifier",
    )


@pytest.fixture
def conn(tmp_path):
    c = db.connect(tmp_path / "test.db")
    yield c
    c.close()


class FakeWeb:
    """FIXTURE HTTP server: maps URL -> (status, headers, body) or an exception to raise."""

    def __init__(self):
        self.pages: dict[str, object] = {}
        self.requests: list[str] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        url = str(request.url)
        self.requests.append(url)
        page = self.pages.get(url)
        if page is None:
            return httpx.Response(404, text="not found")
        if isinstance(page, Exception):
            raise page
        status, headers, body = page
        return httpx.Response(status, headers=headers, content=body)

    def html(self, url: str, body: str, status: int = 200):
        self.pages[url] = (status, {"content-type": "text/html; charset=utf-8"}, body.encode())


@pytest.fixture
def web() -> FakeWeb:
    return FakeWeb()


@pytest.fixture
def fetcher(web, settings) -> Fetcher:
    return Fetcher(settings, transport=httpx.MockTransport(web.handler), resolver=fake_resolver)


@pytest.fixture
def client(tmp_path, fetcher):
    path = tmp_path / "api.db"

    def _db():
        c = db.connect(path)
        try:
            yield c
        finally:
            c.close()

    app.dependency_overrides[get_db] = _db
    app.dependency_overrides[get_fetcher] = lambda: fetcher
    test_client = TestClient(app)
    test_client.db_path = path  # type: ignore[attr-defined]
    yield test_client
    app.dependency_overrides.clear()


A, B, C = "https://example.test/a", "https://example.test/b", "https://other.test/c"
def page(topic):
    return f"<html><head><title>{topic}</title></head><body>" + "".join(
        f"<p>{topic} paragraph {i} contains a distinct statement about {topic}.</p>" for i in range(8)) + "</body></html>"


@pytest.fixture
def research(client, web, fetcher, settings):
    """Install FIXTURE services into the app; returns a function to (re)configure them."""
    web.html(A, page("alpha")); web.html(B, page("beta")); web.html(C, page("gamma"))

    def install(mesh, search, structured=frozenset(), **overrides):
        s = settings.model_copy(update=overrides)
        svc = ResearchServices(s, LLM(mesh, s, structured), search, fetcher, lambda: db.connect(client.db_path))
        app.dependency_overrides[get_services] = lambda: svc
        return svc

    return install


@pytest.fixture
def verify_env(client, settings):
    """Install FIXTURE services for verification of an already collected run (no search, no fetcher)."""

    def install(mesh, structured=frozenset(), **overrides):
        s = settings.model_copy(update=overrides)
        svc = ResearchServices(s, LLM(mesh, s, structured), None, None, lambda: db.connect(client.db_path))
        app.dependency_overrides[get_verify_services] = lambda: svc
        return svc

    return install


@pytest.fixture
def complete_env(client, fetcher, settings):
    """Install FIXTURE services for completing an existing run (search, fetch and every model role)."""

    def install(mesh, search, structured=frozenset(), **overrides):
        s = settings.model_copy(update=overrides)
        svc = ResearchServices(s, LLM(mesh, s, structured), search, fetcher, lambda: db.connect(client.db_path))
        app.dependency_overrides[get_complete_services] = lambda: svc
        app.dependency_overrides[get_retry_services] = lambda: svc  # the retry endpoint must not build real services from a local .env
        app.dependency_overrides[get_services] = lambda: svc  # for the end-to-end option (?complete=true)
        return svc

    return install


@pytest.fixture
def real_get_db(client, monkeypatch):
    """The real `get_db` dependency (the API overrides it in `client`), pointed at the test database, for tests that call it directly."""
    from backend.api import runs as runs_api

    monkeypatch.setattr(runs_api, "get_settings", lambda: Settings(database_path=str(client.db_path)))
    return runs_api.get_db


@pytest.fixture
def empty_label_store(tmp_path, monkeypatch):
    """The evaluation tests that mean 'no human label exists' must not depend on the shipped human_labels.json, which now holds the reviewed batch."""
    from evaluation import datasets
    monkeypatch.setattr(datasets, "HUMAN", tmp_path / "no_labels.json")


# The PUBLIC export withholds the text of some evidence passages; tests that need that text run in the private repository and are skipped here, visibly.
import pytest

from evaluation import datasets as _datasets

requires_full_evidence = pytest.mark.skipif(
    bool(_datasets.withheld_ids(_datasets.claim_examples())),
    reason="needs the full private evidence; the public export (public-export-v1) withholds some passages, see PUBLIC_RELEASE.md")
