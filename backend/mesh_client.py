"""Shared OpenAI SDK client, pointed exclusively at Mesh API."""

from openai import AsyncOpenAI, OpenAI

from backend.config import get_settings
from backend.readonly import ReadOnlyError


class MeshNotConfiguredError(RuntimeError):
    pass


def get_mesh_client() -> OpenAI:
    """Build an OpenAI client for Mesh API. Raises if MESH_API_KEY is unset."""
    settings = get_settings()
    if settings.public_read_only:
        raise ReadOnlyError("public read-only deployment: no provider client is ever built")
    if not settings.mesh_api_key:
        raise MeshNotConfiguredError("MESH_API_KEY is not set. Copy .env.example to .env and fill it in.")
    # max_retries=0: the SDK would otherwise retry 429/5xx/timeouts invisibly, outside the run's request budget
    # and the model_calls table. The timeout stops one request from outlasting the run's time budget.
    return OpenAI(
        api_key=settings.mesh_api_key, base_url=settings.mesh_base_url,
        max_retries=0, timeout=settings.mesh_timeout,
    )


def get_mesh_async_client() -> AsyncOpenAI:
    """A fresh async client for ONE request (same Mesh endpoint, key, max_retries=0). `LLM` creates one per call, bounds the call with
    `asyncio.timeout` and closes the client in a `finally`, so cancelling the task closes the underlying connection."""
    settings = get_settings()
    if settings.public_read_only:
        raise ReadOnlyError("public read-only deployment: no provider client is ever built")
    if not settings.mesh_api_key:
        raise MeshNotConfiguredError("MESH_API_KEY is not set. Copy .env.example to .env and fill it in.")
    return AsyncOpenAI(api_key=settings.mesh_api_key, base_url=settings.mesh_base_url, max_retries=0, timeout=settings.mesh_timeout)


def mesh_configured() -> bool:
    settings = get_settings()
    return not settings.public_read_only and settings.mesh_api_key not in ('', 'your-mesh-api-key-here')
