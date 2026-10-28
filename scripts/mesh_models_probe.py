"""ONE read-only request to the configured Mesh endpoint's /models: is the endpoint reachable, what HTTP status came back, how long did it take (one client-observed wall time: DNS, connect, TLS, request and response together; the parts are not measured), and are the
configured models listed. This is endpoint reachability, not an inference health test: it makes no model call and says nothing about whether a model generates.

Uses the shared Mesh client (max_retries=0, no hidden retries). Mesh's /models returns a bare JSON list, not OpenAI's {"data": [...]}, so the body is read raw and
both shapes are accepted. Nothing secret is printed: only the host, the status, the timing, the request id header and model names.

    uv run python scripts/mesh_models_probe.py
"""

import sys
import time
from urllib.parse import urlparse

import httpx

SAFE_FIELDS = ("model_type", "supports_structured_output", "supports_system_prompt", "supports_completions_api")


def catalog_rows(body) -> list:
    """The model entries of a /models body: a bare list or {"data": [...]}. Anything else is an error, never an empty catalog."""
    rows = body.get("data") if isinstance(body, dict) else body
    if not isinstance(rows, list):
        raise ValueError(f"unexpected /models body: {type(body).__name__} without a list of models")  # noqa: TRY004 - the probe reports a ValueError for any unusable body
    return rows


def summarize_catalog(body, wanted: set[str]) -> dict:
    rows = catalog_rows(body)
    usable = [r for r in rows if isinstance(r, dict) and isinstance(r.get("id"), str)]
    ids = {r["id"] for r in usable}
    return {
        "models_listed": len(ids),
        "entries_without_id": len(rows) - len(usable),  # entries that are not objects, or have no string id: counted, never silently dropped
        "configured": {m: m in ids for m in sorted(wanted)},
        "configured_flags": {r["id"]: {k: r[k] for k in SAFE_FIELDS if k in r} for r in usable if r["id"] in wanted},
    }


def probe(client, wanted: set[str]) -> dict:
    """Exactly one GET /models. Never raises for a failed request: the failure is the result."""
    out: dict = {"status": None, "wall_ms": None, "request_id": None, "error": None}
    started = time.perf_counter()
    try:
        response = client.get("/models", cast_to=httpx.Response)
        response.read()  # the body is part of the measured time (the SDK has normally read it already)
        out["wall_ms"] = round((time.perf_counter() - started) * 1000)
        out["status"] = response.status_code
        out["request_id"] = response.headers.get("x-request-id")
        out.update(summarize_catalog(response.json(), wanted))
    except Exception as exc:  # noqa: BLE001 - reachability tool: report whatever happened
        out["wall_ms"] = out["wall_ms"] if out["wall_ms"] is not None else round((time.perf_counter() - started) * 1000)
        out["error"] = f"{type(exc).__name__}"
        out["status"] = out["status"] if out["status"] is not None else getattr(exc, "status_code", None)
        out["request_id"] = out["request_id"] or getattr(exc, "request_id", None)  # an SDK status error carries the response's id
    return out


def render(host: str, result: dict) -> str:
    lines = [f"endpoint host: {host}", "one GET /models (reachability only; no model call, nothing inferred about generation)"]
    for key in ("status", "wall_ms", "request_id", "error", "models_listed", "entries_without_id", "configured", "configured_flags"):
        if key in result:
            lines.append(f"{key}: {result[key]}")
    return "\n".join(lines)


def main() -> int:
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # run as a script from anywhere
    from backend.config import get_settings
    from backend.mesh_client import get_mesh_client

    s = get_settings()
    wanted = {s.planner_model, s.researcher_model, s.drafter_model, s.verifier_model, s.effective_coverage_model, s.effective_synthesizer_model} - {""}
    print(render(urlparse(s.mesh_base_url).netloc, probe(get_mesh_client(), wanted)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
