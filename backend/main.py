from fastapi import FastAPI

from backend.config import get_settings
from backend.mesh_client import mesh_configured

app = FastAPI(title="DeepDive")


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mesh_configured": mesh_configured(),
        "mesh_base_url": get_settings().mesh_base_url,
    }
