"""Structured LLM calls through the shared Mesh client. Records every call's actual reported usage."""

import asyncio
import inspect
import json
import re
import sqlite3
import time
from collections.abc import Callable
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from backend import db
from backend.budget import Budget
from backend.config import Settings
from backend.redact import redact

T = TypeVar("T", bound=BaseModel)

# Validation keywords that strict structured-output backends commonly reject; Pydantic still enforces them.
_UNSUPPORTED_KEYWORDS = {"title", "default", "$defs", "minLength", "maxLength", "minItems", "maxItems",
                         "minimum", "maximum", "pattern", "format"}


class ModelCallError(RuntimeError):
    """The Mesh API call itself failed (auth, network, unknown model, ...)."""


class AgentOutputError(RuntimeError):
    """The model replied, but not with output matching the required schema (after one retry)."""


def parse_json_object(text: str) -> dict:
    """Parse the first JSON object in a reply, ignoring code fences and any text before or after it."""
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    start = text.find("{")
    if start == -1:
        raise ValueError("no JSON object found in reply")
    obj, _ = json.JSONDecoder().raw_decode(text, start)
    if not isinstance(obj, dict):
        raise ValueError("reply is not a JSON object")  # noqa: TRY004 - callers catch ValueError
    return obj


def strict_json_schema(model: type[BaseModel]) -> dict:
    """Pydantic's schema, inlined and simplified for `response_format: json_schema`: no $ref/$defs, no validation
    keywords, every object closed (`additionalProperties: false`) with all properties required."""
    raw = model.model_json_schema()
    defs = raw.get("$defs", {})

    def walk(node):
        if isinstance(node, list):
            return [walk(item) for item in node]
        if not isinstance(node, dict):
            return node
        if "$ref" in node:
            return walk(defs[node["$ref"].split("/")[-1]])
        out = {}
        for key, value in node.items():
            if key in _UNSUPPORTED_KEYWORDS:
                continue
            out[key] = {name: walk(sub) for name, sub in value.items()} if key == "properties" else walk(value)
        if out.get("type") == "object":
            out["additionalProperties"] = False
            out["required"] = list(out.get("properties", {}))
        return out

    return walk(raw)


class LLM:
    def __init__(self, client, settings: Settings, structured_models: frozenset[str] = frozenset(), async_client_factory: Callable[[], Any] | None = None):
        self.client = client  # OpenAI-compatible client from get_mesh_client() (used only when there is no async factory: test fixtures)
        # Real services pass a factory for an AsyncOpenAI client: each request then runs under a wall-clock deadline that CANCELS the HTTP operation
        # (the SDK/httpx timeout alone bounds only silence on the socket, so a slowly trickling reply was never cut off).
        self.async_client_factory = async_client_factory
        self.settings = settings
        # Models whose Mesh catalog entry says supports_structured_output: they get `response_format: json_schema`.
        self.structured_models = structured_models
        self.last_call_id: int | None = None  # id of the most recent model_calls row (provenance for callers)

    def _send(self, *, model: str, messages: list[dict], options: dict, timeout: float) -> tuple[Any, str | None]:
        """ONE request, no retry. Returns (parsed response, request id). With an async factory the request is bounded by a wall-clock deadline of
        `timeout` seconds that cancels it: the connection is closed and nothing keeps running in the background. Cancelling the client says nothing
        about whether the provider stopped generating or billing; the caller keeps usage and charge unknown. Must be called from a thread that has no
        running event loop (every job thread qualifies)."""
        if self.async_client_factory is None:
            raw = self.client.chat.completions.with_raw_response.create(
                model=model, messages=messages, max_tokens=self.settings.max_output_tokens, timeout=timeout, **options)
            return raw.parse(), raw.headers.get("x-request-id")
        return asyncio.run(self._asend(model, messages, options, timeout))

    async def _asend(self, model: str, messages: list[dict], options: dict, timeout: float) -> tuple[Any, str | None]:
        client = self.async_client_factory()
        try:
            try:
                async with asyncio.timeout(timeout):
                    raw = await client.chat.completions.with_raw_response.create(
                        model=model, messages=messages, max_tokens=self.settings.max_output_tokens, timeout=timeout, **options)
                    parsed = raw.parse()  # sync in the current SDK (the body is already read); tolerate an awaitable one
                    return (await parsed if inspect.isawaitable(parsed) else parsed), raw.headers.get("x-request-id")
            except TimeoutError:
                raise TimeoutError(f"wall-clock deadline of {timeout:g}s reached; the request was cancelled and its connection closed") from None
        finally:
            await client.close()  # also reached on KeyboardInterrupt/cancellation: the connection pool never outlives the call

    def call(
        self,
        conn: sqlite3.Connection,
        run_id: int,
        budget: Budget,
        *,
        agent: str,
        model: str,
        system: str,
        user: str,
        schema: type[T],
        example: dict | None = None,
    ) -> T:
        system += (
            "\n\nReply with exactly one JSON object and nothing else: no prose, no markdown, no code fence. "
            "Every field in the schema is required."
        )
        if example is not None:
            system += "\nExample of the exact shape (the values are placeholders):\n" + json.dumps(example)
        system += "\nJSON Schema:\n" + json.dumps(strict_json_schema(schema), separators=(",", ":"))
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        options: dict = {}
        structured = model in self.structured_models
        if structured:
            options["response_format"] = {
                "type": "json_schema",
                "json_schema": {"name": schema.__name__, "schema": strict_json_schema(schema)},
            }
        for attempt in (1, 2):
            timeout = budget.call_timeout()  # raises BudgetExceeded BEFORE anything is counted or sent when the deadline is too close
            budget.charge_model_request()
            started = time.monotonic()
            try:
                resp, request_id = self._send(model=model, messages=messages, options=options, timeout=timeout)
            except Exception as exc:
                waited_ms = int((time.monotonic() - started) * 1000)  # how long this attempt actually waited: measured, known even when nothing came back
                detail = redact(f"{type(exc).__name__}: {exc} (request timeout {timeout:.0f}s)", self.settings)
                # The request was sent and counted. What it used is UNKNOWN (usage stays NULL in the record, nothing is invented), but the output-token
                # cap must not be defeated by failures that may have generated tokens: count the per-call maximum, as for a reply that omits usage.
                budget.add_output_tokens(self.settings.max_output_tokens)
                self.last_call_id = db.record_model_call(
                    conn, run_id, agent=agent, model=model, attempt=attempt, ok=False, latency_ms=waited_ms, error=detail[:500],
                    structured_output=structured)  # token counts and finish reason stay NULL: the provider returned none
                raise ModelCallError(f"Mesh call failed for model {model!r}: {detail}") from exc
            latency_ms = int((time.monotonic() - started) * 1000)
            usage = getattr(resp, "usage", None)
            tokens = {
                "prompt_tokens": getattr(usage, "prompt_tokens", None),
                "completion_tokens": getattr(usage, "completion_tokens", None),
                "total_tokens": getattr(usage, "total_tokens", None),
            }
            # The budget must not be defeated by a provider that omits usage: assume the per-call maximum.
            # The stored usage stays NULL (we only record numbers the API reported).
            reported = tokens["completion_tokens"]
            budget.add_output_tokens(reported if reported is not None else self.settings.max_output_tokens)
            choice = resp.choices[0]
            text = choice.message.content or ""
            try:
                result = schema.model_validate(parse_json_object(text))
            except (ValueError, ValidationError) as exc:
                error = f"invalid output: {str(exc)[:300]}"
                self.last_call_id = db.record_model_call(
                    conn, run_id, agent=agent, model=model, attempt=attempt, ok=False, latency_ms=latency_ms,
                    finish_reason=choice.finish_reason, error=error, request_id=request_id,
                    structured_output=structured, **tokens)
                messages += [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content": f"That reply was invalid ({error}). Return only the corrected JSON."},
                ]
                continue
            self.last_call_id = db.record_model_call(
                conn, run_id, agent=agent, model=model, attempt=attempt, ok=True, latency_ms=latency_ms,
                finish_reason=choice.finish_reason, request_id=request_id, structured_output=structured, **tokens)
            return result
        raise AgentOutputError(f"{agent}: model {model!r} did not return valid output after 2 attempts")
