from fastapi.testclient import TestClient

from backend.main import app


def test_health():
    resp = TestClient(app).get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["mesh_base_url"] == "https://api.meshapi.ai/v1"
