"""One-origin production entry point for the hosted recorded demo:  uvicorn backend.public:app --host 0.0.0.0 --port $PORT --workers 1

  /api/*        the FastAPI application (read-only: this entry point requires PUBLIC_READ_ONLY=1)
  /health       liveness only (no database, no provider): the process is up
  /ready        readiness: the demo database opens read-only and holds runs
  everything else: the production React build (frontend/dist), with index.html as the fallback for deep links

A single worker, because background jobs of the full application run in-process; the demo itself starts none.
"""

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse

from backend import db
from backend.config import Settings, get_settings
from backend.demo_seed import ensure_demo_db
from backend.main import create_app

HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "same-origin",
    "Content-Security-Policy": "default-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'; "
                               "base-uri 'none'; form-action 'self'; frame-ancestors 'none'",
}


def create_public_app(settings: Settings, api: FastAPI | None = None) -> FastAPI:
    dist = Path(settings.frontend_dist)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if not settings.public_read_only:
            raise RuntimeError("backend.public serves the hosted demo and requires PUBLIC_READ_ONLY=1")
        ensure_demo_db(settings.database_path, settings.demo_seed_path)  # creates the database on an empty volume; an existing one is left alone
        if not (dist / "index.html").exists():
            raise RuntimeError(f"production frontend build not found at {dist}; run `npm ci && npm run build` in frontend/")
        yield

    app = FastAPI(title="DeepDive (recorded demo)", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware("http")
    async def security_headers(request, call_next):
        response = await call_next(request)
        for k, v in HEADERS.items():
            response.headers.setdefault(k, v)
        return response

    @app.get("/health", include_in_schema=False)
    def health() -> dict:
        return {"status": "ok"}

    @app.get("/ready", include_in_schema=False)
    def ready():
        try:
            conn = db.connect(settings.database_path, read_only=True)
            try:
                runs = conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0]
            finally:
                conn.close()
        except Exception as exc:  # noqa: BLE001 - any failure means "not ready", reported without internals
            return JSONResponse({"status": "not_ready", "reason": type(exc).__name__}, status_code=503)
        return JSONResponse({"status": "ready", "runs": runs}, status_code=200 if runs else 503)

    app.mount("/api", api or create_app(settings))

    @app.get("/{path:path}", include_in_schema=False)
    def spa(path: str):
        """Static files from the build; any other path serves index.html so a deep link or refresh reaches the client router."""
        if path.startswith("api/"):
            raise HTTPException(404)
        candidate = (dist / path).resolve()
        if path and candidate.is_file() and dist.resolve() in candidate.parents:
            immutable = path.startswith("assets/")  # content-hashed file names
            return FileResponse(candidate, headers={"Cache-Control": "public, max-age=31536000, immutable" if immutable else "no-cache"})
        return FileResponse(dist / "index.html", headers={"Cache-Control": "no-cache"})

    return app


app = create_public_app(get_settings())
