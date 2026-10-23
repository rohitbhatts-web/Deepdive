"""Read endpoints added for the UI (run list, configured limits). Fixture runs only."""

from backend import db


def test_run_list_is_paginated_newest_first(client):
    conn = db.connect(client.db_path)
    ids = [db.create_run(conn, f"question number {i}")["id"] for i in range(5)]
    conn.close()
    page = client.get("/runs", params={"limit": 2}).json()
    assert page["total"] == 5 and page["limit"] == 2 and [r["id"] for r in page["items"]] == [ids[4], ids[3]]
    assert page["items"][0]["report_outcome"] is None and page["items"][0]["complete_status"] == "not_started"
    assert [r["id"] for r in client.get("/runs", params={"limit": 2, "offset": 4}).json()["items"]] == [ids[0]]
    assert client.get("/runs", params={"limit": 0}).status_code == 422
    assert client.get("/runs", params={"limit": 101}).status_code == 422


def test_run_list_empty(client):
    assert client.get("/runs").json() == {"items": [], "total": 0, "limit": 30, "offset": 0}


def test_limits_expose_budget_bounds_without_secrets(client):
    body = client.get("/limits").json()
    assert body["limits"]["model_requests"] > 0 and body["question_min_chars"] == 3
    assert "key" not in str(body).lower()


def test_request_connection_survives_being_used_in_another_thread(real_get_db):
    """Found by the UI's parallel polling: FastAPI opens a sync dependency and runs the endpoint in different worker threads."""
    from concurrent.futures import ThreadPoolExecutor

    def open_conn():
        gen = real_get_db()
        return gen, next(gen)

    with ThreadPoolExecutor(1) as a, ThreadPoolExecutor(1) as b:
        gen, conn = a.submit(open_conn).result()
        assert b.submit(lambda: conn.execute("SELECT 1").fetchone()[0]).result() == 1
        b.submit(lambda: list(gen)).result()
