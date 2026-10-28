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
from backend.mesh_client import classify_failure
from backend.redact import redact
from backend.transport_trace import CURRENT_TRACE, TransportTrace

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


def validation_errors(exc: Exception) -> tuple[str, list[dict]]:
    """(one-line summary, structured errors) for a rejected reply, WITHOUT the input values: pydantic's own message quotes the model's text, and the summary is
    stored in model_calls.error, which the ordinary API returns. Model output must not travel that way; the text itself goes to model_call_rejections."""
    if isinstance(exc, ValidationError):
        errs = [{"loc": ".".join(str(p) for p in e["loc"]), "type": e["type"], "msg": e["msg"]}
                for e in exc.errors(include_url=False, include_input=False, include_context=False)]
        n = len(errs)
        return (f"{n} validation error{'s' if n != 1 else ''} for {exc.title}: " + "; ".join(f"{e['loc'] or '(root)'} ({e['type']}: {e['msg']})" for e in errs), errs)
    return f"unparseable reply: {exc}", [{"loc": "", "type": "parse", "msg": str(exc)}]  # parse_json_object's and json's messages carry no reply text


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


def exception_request_id(exc: BaseException) -> str | None:
    """The provider's request id as the failed call's exception carries it in a structured field, or None. Never read out of message text and never made up:
    the OpenAI SDK sets `request_id` from the response's x-request-id header, and a provider may instead put `request_id` at the top level of its JSON error
    body (reachable through the exception's response). A call that timed out or never connected has none."""
    value = getattr(exc, "request_id", None)
    if not (isinstance(value, str) and value.strip()):
        value = None
        response = getattr(exc, "response", None)
        try:
            body = response.json() if response is not None else None
        except Exception:  # noqa: BLE001 - a body that is not JSON just means there is no structured id
            body = None
        if isinstance(body, dict) and isinstance(body.get("request_id"), str) and body["request_id"].strip():
            value = body["request_id"]
    return value.strip()[:200] if value else None


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
        self.last_reply_text: str | None = None  # raw text of the most recent reply (None when no reply arrived); kept only to store a rejection
        self._trace: TransportTrace | None = None  # what the transport observed about the request just made (None for a fixture client)

    def _send(self, *, model: str, messages: list[dict], options: dict, timeout: float, max_tokens: int | None = None) -> tuple[Any, str | None]:
        """ONE request, no retry. Returns (parsed response, request id). With an async factory the request is bounded by a wall-clock deadline of
        `timeout` seconds that cancels it: the connection is closed and nothing keeps running in the background. Cancelling the client says nothing
        about whether the provider stopped generating or billing; the caller keeps usage and charge unknown. Must be called from a thread that has no
        running event loop (every job thread qualifies)."""
        self._trace = None
        if self.async_client_factory is None:
            raw = self.client.chat.completions.with_raw_response.create(
                model=model, messages=messages, max_tokens=max_tokens or self.settings.max_output_tokens, timeout=timeout, **options)
            return raw.parse(), raw.headers.get("x-request-id")
        return asyncio.run(self._asend(model, messages, options, timeout, max_tokens))

    async def _asend(self, model: str, messages: list[dict], options: dict, timeout: float, max_tokens: int | None = None) -> tuple[Any, str | None]:
        client = self.async_client_factory()
        trace = TransportTrace()
        trace.sdk_max_retries = getattr(client, "max_retries", None)
        self._trace = trace
        token = CURRENT_TRACE.set(trace)
        try:
            try:
                async with asyncio.timeout(timeout):
                    raw = await client.chat.completions.with_raw_response.create(
                        model=model, messages=messages, max_tokens=max_tokens or self.settings.max_output_tokens, timeout=timeout, **options)
                    parsed = raw.parse()  # sync in the current SDK (the body is already read); tolerate an awaitable one
                    return (await parsed if inspect.isawaitable(parsed) else parsed), raw.headers.get("x-request-id")
            except TimeoutError:
                err = TimeoutError(f"wall-clock deadline of {timeout:g}s reached; the request was cancelled and its connection closed")
                err.deepdive_deadline = True  # type: ignore[attr-defined]  # our own deadline, as opposed to a TimeoutError raised inside the transport
                raise err from None
        finally:
            CURRENT_TRACE.reset(token)
            await client.close()  # also reached on KeyboardInterrupt/cancellation: the connection pool never outlives the call

    def _diagnostics(self, *, timeout: float, started: float, attempt: int, outcome: str, failure: dict | None = None, stage: str | None = None) -> dict:
        """The redacted observation stored beside a model call. Only what was measured or configured: monotonic elapsed time, the deadline, the transport's phases and
        counts, the status and safe headers, the failure class. Nothing the transport cannot see is claimed (see transport_trace)."""
        trace = self._trace
        d: dict = {
            "outcome": outcome, "failure_stage": (failure or {}).get("failure_stage") or stage,
            "elapsed_ms": int((time.monotonic() - started) * 1000), "deadline_s": round(timeout, 3), "mesh_timeout_s": self.settings.mesh_timeout,
            "timeout_setting": "one float: the SDK applies it to connect, read, write and pool alike; the outer wall-clock deadline is the same value",
            "retries": {"sdk": trace.sdk_max_retries if trace else None, "validation_retry_number": attempt - 1},
            "transport": trace.summary() if trace else None,
        }
        if trace is None:
            d["transport_note"] = "not traced: a fixture or synchronous client was used"
        if failure:
            d["error"] = {k: failure[k] for k in ("http_status", "error_class", "provider_code", "chain")}
            if failure["failure_stage"] == "outer_deadline" and trace is not None:
                # cancelling at the deadline makes httpcore report the phase it interrupted as failed, so the phase it was in is the open one or the failed one
                d["error"]["stopped_in_phase"] = trace.open_phase() or trace.failed_phase() or ("before any transport event" if not trace.order else "after the last observed phase")
        return d

    def record_rejection(self, conn: sqlite3.Connection, model_call_id: int, stage: str, errors: list[dict]) -> None:
        """Keep the last reply (bounded, configured secrets redacted) beside its model call for local diagnosis. Nothing is stored when no reply arrived."""
        if self.last_reply_text is not None:
            db.record_rejected_reply(conn, model_call_id, stage, redact(self.last_reply_text, self.settings), errors)

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
        attempts: int = 2,
        output_cap: int | None = None,
        validate_with: type[BaseModel] | None = None,
    ) -> T:
        """`attempts` is how many requests an invalid reply may cost (2 = one corrective retry, the default; 1 = none). `output_cap` can only LOWER the per-call
        output-token maximum (it is both what is requested and what a failed or usage-less call is charged); it never raises it. `validate_with` validates the
        reply against a looser model than the `schema` the model is shown (the caller then validates the parts itself); it is returned instead of `schema`.
        A rejected reply is stored, bounded and redacted, in model_call_rejections."""
        self.last_reply_text = None
        if output_cap is not None and output_cap <= 0:
            raise ValueError("output_cap must be a positive number of tokens")
        cap = min(output_cap, self.settings.max_output_tokens) if output_cap is not None else self.settings.max_output_tokens
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
        for attempt in range(1, attempts + 1):
            timeout = budget.call_timeout()  # raises BudgetExceeded BEFORE anything is counted or sent when the deadline is too close
            budget.charge_model_request()
            started = time.monotonic()
            try:
                resp, request_id = self._send(model=model, messages=messages, options=options, timeout=timeout, max_tokens=cap)
            except Exception as exc:
                waited_ms = int((time.monotonic() - started) * 1000)  # how long this attempt actually waited: measured, known even when nothing came back
                detail = redact(f"{type(exc).__name__}: {exc} (request timeout {timeout:.0f}s)", self.settings)
                # The request was sent and counted. What it used is UNKNOWN (usage stays NULL in the record, nothing is invented), but the output-token
                # cap must not be defeated by failures that may have generated tokens: count the per-call maximum, as for a reply that omits usage.
                budget.add_output_tokens(cap)
                self.last_call_id = db.record_model_call(
                    conn, run_id, agent=agent, model=model, attempt=attempt, ok=False, latency_ms=waited_ms, error=detail[:500],
                    request_id=exception_request_id(exc),  # only when the exception carries one in a structured field; otherwise NULL
                    structured_output=structured)  # token counts and finish reason stay NULL: the provider returned none
                db.record_model_call_diagnostics(conn, self.last_call_id, self._diagnostics(timeout=timeout, started=started, attempt=attempt, outcome="failed", failure=classify_failure(exc)))
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
            budget.add_output_tokens(reported if reported is not None else cap)
            choice = resp.choices[0]
            text = choice.message.content or ""
            self.last_reply_text = text
            try:
                result = (validate_with or schema).model_validate(parse_json_object(text))
            except (ValueError, ValidationError) as exc:
                summary, errs = validation_errors(exc)
                error = f"invalid output: {summary}"[:500]
                self.last_call_id = db.record_model_call(
                    conn, run_id, agent=agent, model=model, attempt=attempt, ok=False, latency_ms=latency_ms,
                    finish_reason=choice.finish_reason, error=error, request_id=request_id,
                    structured_output=structured, **tokens)
                db.record_model_call_diagnostics(conn, self.last_call_id, self._diagnostics(
                    timeout=timeout, started=started, attempt=attempt, outcome="invalid_output", stage="validation" if isinstance(exc, ValidationError) else "parsing"))
                self.record_rejection(conn, self.last_call_id, "envelope", errs)
                messages += [
                    {"role": "assistant", "content": text[:2000]},
                    {"role": "user", "content": f"That reply was invalid ({error}). Return only the corrected JSON."},
                ]
                continue
            self.last_call_id = db.record_model_call(
                conn, run_id, agent=agent, model=model, attempt=attempt, ok=True, latency_ms=latency_ms,
                finish_reason=choice.finish_reason, request_id=request_id, structured_output=structured, **tokens)
            db.record_model_call_diagnostics(conn, self.last_call_id, self._diagnostics(timeout=timeout, started=started, attempt=attempt, outcome="ok"))
            return result
        raise AgentOutputError(f"{agent}: model {model!r} did not return valid output after {attempts} attempt{'s' if attempts != 1 else ''}")
