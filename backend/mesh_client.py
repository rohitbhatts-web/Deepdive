"""Shared OpenAI SDK client, pointed exclusively at Mesh API."""

import openai
from openai import AsyncOpenAI, DefaultAsyncHttpxClient, OpenAI

from backend import transport_trace
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


def traced_async_client(api_key: str, base_url: str, timeout: float) -> AsyncOpenAI:
    """An async client exactly like the SDK's own default (same httpx defaults, max_retries=0, the given timeout) plus two httpx hooks that let `LLM` observe the
    request's transport phases and the response status and safe headers (see transport_trace). The hooks do nothing unless the LLM has set a trace for the request;
    they change no request, header, timeout or retry."""
    http = DefaultAsyncHttpxClient(event_hooks={"request": [transport_trace.on_request], "response": [transport_trace.on_response]})
    return AsyncOpenAI(api_key=api_key, base_url=base_url, max_retries=0, timeout=timeout, http_client=http)


def get_mesh_async_client() -> AsyncOpenAI:
    """A fresh async client for ONE request (same Mesh endpoint, key, max_retries=0). `LLM` creates one per call, bounds the call with
    `asyncio.timeout` and closes the client in a `finally`, so cancelling the task closes the underlying connection."""
    settings = get_settings()
    if settings.public_read_only:
        raise ReadOnlyError("public read-only deployment: no provider client is ever built")
    if not settings.mesh_api_key:
        raise MeshNotConfiguredError("MESH_API_KEY is not set. Copy .env.example to .env and fill it in.")
    return traced_async_client(settings.mesh_api_key, settings.mesh_base_url, settings.mesh_timeout)


STATUS_CLASS = {400: "bad_request", 401: "authentication", 402: "payment_required", 403: "forbidden", 404: "not_found", 408: "request_timeout",
                422: "unprocessable", 429: "rate_limited_or_quota"}


def exception_chain(exc: BaseException) -> list[str]:
    """Class names only (never messages) of an exception and the causes it was raised from, nearest first."""
    out, seen = [], set()
    while exc is not None and id(exc) not in seen and len(out) < 6:
        seen.add(id(exc))
        out.append(type(exc).__name__)
        exc = exc.__cause__ or exc.__context__
    return out


def classify_failure(exc: BaseException) -> dict:
    """Where a failed request failed, from what the exception IS (never from its message text): our outer deadline, an HTTP status the provider answered
    with, or the transport (connection, SDK timeout, anything else)."""
    out: dict = {"chain": exception_chain(exc), "http_status": None, "error_class": None, "provider_code": None}
    if getattr(exc, "deepdive_deadline", False):
        return {**out, "failure_stage": "outer_deadline", "error_class": "deadline"}
    if isinstance(exc, openai.APIStatusError):
        status = exc.status_code
        code = getattr(exc, "code", None)
        return {**out, "failure_stage": "http_status", "http_status": status, "provider_code": str(code)[:80] if code else None,
                "error_class": STATUS_CLASS.get(status, "upstream_server_error" if status >= 500 else f"http_{status}")}
    if isinstance(exc, openai.APITimeoutError):
        return {**out, "failure_stage": "transport", "error_class": "sdk_timeout"}
    if isinstance(exc, openai.APIConnectionError):
        return {**out, "failure_stage": "transport", "error_class": "connection"}
    return {**out, "failure_stage": "unclassified", "error_class": type(exc).__name__}


def mesh_configured() -> bool:
    settings = get_settings()
    return not settings.public_read_only and settings.mesh_api_key not in ('', 'your-mesh-api-key-here')
