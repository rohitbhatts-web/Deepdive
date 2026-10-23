import json
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from backend import db
from backend.api.claims import router as claims_router
from backend.api.completion import router as completion_router
from backend.api.research import router as research_router
from backend.api.runs import router as runs_router
from backend.config import Settings, get_settings
from backend.mesh_client import mesh_configured
from backend.readonly import ReadOnlyMiddleware


def create_app(settings: Settings | None = None) -> FastAPI:
    """The API. With `public_read_only` it refuses every mutation, serves no interactive docs and never opens the database for writing.
    Without explicit settings the process settings are read when needed (so a test or restart that changes the environment is honoured)."""

    def cfg() -> Settings:
        return settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if not cfg().public_read_only:  # the read-only demo writes nothing, not even the interrupted-run recovery
            conn = db.connect(cfg().database_path)
            try:
                db.mark_interrupted_runs(conn)
            finally:
                conn.close()
        yield

    # In public read-only mode the interactive API docs are not served either (their "try it out" would only meet the 403).
    docs = {"docs_url": None, "redoc_url": None, "openapi_url": None} if cfg().public_read_only else {}
    app = FastAPI(title="DeepDive", lifespan=lifespan, **docs)
    if cfg().public_read_only:
        app.add_middleware(ReadOnlyMiddleware)
    app.include_router(runs_router)
    app.include_router(research_router)
    app.include_router(claims_router)
    app.include_router(completion_router)

    @app.get("/health")
    def health() -> dict:
        return {
            "status": "ok",
            "read_only": cfg().public_read_only,
            "mesh_configured": mesh_configured(),
            "mesh_base_url": cfg().mesh_base_url,
        }

    @app.get("/demo")
    def demo() -> dict:
        """Whether this deployment is the read-only recorded demo, and how it labels itself. Reads a static file; nothing else."""
        if not cfg().public_read_only:
            return {"read_only": False}
        path = Path(cfg().demo_info_path)
        info = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        conn = db.connect(cfg().database_path, read_only=True)
        try:
            present = {r[0] for r in conn.execute("SELECT id FROM runs")}
        finally:
            conn.close()
        # only runs that exist in this database are listed (an update that was refused must not leave a dead link)
        return {**info, "read_only": True, "runs": [r for r in info.get("runs", []) if r["run_id"] in present]}

    return app


app = create_app()
