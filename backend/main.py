from contextlib import asynccontextmanager

from fastapi import FastAPI

from backend import db
from backend.api.claims import router as claims_router
from backend.api.completion import router as completion_router
from backend.api.research import router as research_router
from backend.api.runs import router as runs_router
from backend.config import get_settings
from backend.mesh_client import mesh_configured


@asynccontextmanager
async def lifespan(app: FastAPI):
    conn = db.connect(get_settings().database_path)
    try:
        db.mark_interrupted_runs(conn)
    finally:
        conn.close()
    yield


app = FastAPI(title="DeepDive", lifespan=lifespan)
app.include_router(runs_router)
app.include_router(research_router)
app.include_router(claims_router)
app.include_router(completion_router)


@app.get("/health")
def health() -> dict:
    return {
        "status": "ok",
        "mesh_configured": mesh_configured(),
        "mesh_base_url": get_settings().mesh_base_url,
    }
