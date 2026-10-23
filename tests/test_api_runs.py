import httpx

from backend import db

ARTICLE = (
    "<html><head><title>Fixture Article</title></head><body><h1>Fixture Article</h1>"
    + "".join(f"<p>Paragraph {i}: the fixture states fact number {i} plainly.</p>" for i in range(30))
    + "</body></html>"
)
URL = "https://example.test/article"


def _run(client, q="What does the fixture say?"):
    return client.post("/runs", json={"question": q}).json()["id"]


def test_create_and_get_run(client):
    resp = client.post("/runs", json={"question": "  What is X?  "})
    assert resp.status_code == 201
    run = resp.json()
    assert run["question"] == "What is X?" and run["status"] == "created"
    assert client.get(f"/runs/{run['id']}").json() == run
    assert client.get("/runs/999").status_code == 404
    assert client.post("/runs", json={"question": "x"}).status_code == 422


def test_ingest_stores_source_passages_and_events(client, web):
    web.html(URL, ARTICLE)
    run_id = _run(client)
    resp = client.post(f"/runs/{run_id}/sources", json={"url": URL})
    assert resp.status_code == 201
    body = resp.json()
    assert body["status"] == "ingested" and body["source"]["title"] == "Fixture Article"

    sources = client.get(f"/runs/{run_id}/sources").json()
    passages = client.get(f"/runs/{run_id}/passages").json()
    assert len(sources) == 1 and sources[0]["passage_count"] == len(passages) > 1
    assert [p["ordinal"] for p in passages] == list(range(len(passages)))
    assert passages[0]["id"] == f"S{sources[0]['id']}-P0"
    events = [e["event_type"] for e in client.get(f"/runs/{run_id}/events").json()]
    assert events == ["run_created", "source_ingested"]


def test_passage_text_equals_snapshot_slice(client, web):
    web.html(URL, ARTICLE)
    run_id = _run(client)
    client.post(f"/runs/{run_id}/sources", json={"url": URL})
    conn = db.connect(client.db_path)
    snapshot = conn.execute("SELECT snapshot FROM sources").fetchone()[0]
    passages = client.get(f"/runs/{run_id}/passages").json()
    assert passages and all(snapshot[p["start_offset"]:p["end_offset"]] == p["text"] for p in passages)
    conn.close()


def test_repeated_ingestion_does_not_duplicate(client, web):
    web.html(URL, ARTICLE)
    web.html("https://example.test/mirror", ARTICLE)  # different URL, identical extracted text
    run_id = _run(client)
    first = client.post(f"/runs/{run_id}/sources", json={"url": URL})
    again = client.post(f"/runs/{run_id}/sources", json={"url": URL})
    mirror = client.post(f"/runs/{run_id}/sources", json={"url": "https://example.test/mirror"})
    assert first.status_code == 201
    assert (again.status_code, again.json()["outcome"]) == (200, "duplicate_url")
    assert (mirror.status_code, mirror.json()["outcome"]) == (200, "duplicate_content")
    assert {again.json()["source"]["id"], mirror.json()["source"]["id"]} == {first.json()["source"]["id"]}
    assert len(client.get(f"/runs/{run_id}/sources").json()) == 1
    assert web.requests.count(URL) == 1  # known URL is not refetched


def test_same_url_in_different_runs_is_stored_per_run(client, web):
    web.html(URL, ARTICLE)
    a, b = _run(client), _run(client)
    client.post(f"/runs/{a}/sources", json={"url": URL})
    assert client.get(f"/runs/{b}/sources").json() == []
    assert client.post(f"/runs/{b}/sources", json={"url": URL}).status_code == 201
    assert client.get(f"/runs/{b}/passages").json() != []
    assert {p["id"] for p in client.get(f"/runs/{a}/passages").json()}.isdisjoint(
        {p["id"] for p in client.get(f"/runs/{b}/passages").json()})


def test_failures_are_recorded_with_clear_status(client, web):
    web.html("https://example.test/gone", "x", status=404)
    web.pages["https://example.test/slow"] = httpx.ReadTimeout("slow")
    web.pages["https://example.test/a.pdf"] = (200, {"content-type": "application/pdf"}, b"%PDF")
    web.html("https://example.test/empty", "<html><body><script>1</script></body></html>")
    run_id = _run(client)
    cases = {
        "http://localhost/": (422, "blocked"),
        "https://internal.test/": (422, "blocked"),
        "https://example.test/gone": (502, "http_error"),
        "https://example.test/slow": (504, "timeout"),
        "https://example.test/a.pdf": (415, "unsupported_content_type"),
        "https://example.test/empty": (422, "no_extractable_text"),
    }
    for url, (status, outcome) in cases.items():
        resp = client.post(f"/runs/{run_id}/sources", json={"url": url})
        assert resp.status_code == status, url
        assert resp.json()["detail"]["outcome"] == outcome
    events = client.get(f"/runs/{run_id}/events").json()
    failed = [e for e in events if e["event_type"] == "fetch_failed"]
    assert [e["payload"]["outcome"] for e in failed] == [o for _, o in cases.values()]
    assert client.get(f"/runs/{run_id}/sources").json() == []


def test_unknown_run_is_404(client):
    assert client.post("/runs/999/sources", json={"url": URL}).status_code == 404
    for path in ("sources", "passages", "events"):
        assert client.get(f"/runs/999/{path}").status_code == 404
