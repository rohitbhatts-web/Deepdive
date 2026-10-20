# DeepDive — Agent Arena 2026

FastAPI + LangGraph + SQLite backend, Streamlit UI, OpenAI SDK pointed at Mesh API.
Status: foundation only (no agents yet).

## Setup

Requires [uv](https://docs.astral.sh/uv/) and Git.

```bash
uv sync                      # creates .venv with Python 3.12 and installs from uv.lock
cp .env.example .env         # then set MESH_API_KEY
```

## Run

```bash
uv run uvicorn backend.main:app --reload     # API on http://127.0.0.1:8000, try /health
uv run streamlit run ui/app.py               # UI on http://localhost:8501
```

## Checks

```bash
uv run pytest
uv run ruff check .
```

## Layout

- `backend/` — FastAPI app (`main.py`), settings (`config.py`), shared Mesh client (`mesh_client.py`), `agents/`, `api/`
- `ui/` — Streamlit app
- `tests/` — pytest
- `data/` — local SQLite files (git-ignored)

The Mesh client lives in `backend/mesh_client.py`; use `get_mesh_client()` everywhere. Base URL: `https://api.meshapi.ai/v1`, key from `MESH_API_KEY`.
