"""What the HTTP transport can actually observe about ONE model request, for the diagnostics stored beside each model call.

httpcore reports a request's phases through a `trace` callback (connection.connect_tcp, connection.start_tls, http11.send_request_headers, send_request_body,
receive_response_headers, receive_response_body, response_closed), and httpx's response hook gives the status and headers. Everything here uses a monotonic clock and
records only what those two sources reported: phase start and end offsets, the number of connection attempts and of HTTP requests actually written, the connect timeout
httpcore applied, the status and an allow-list of non-secret response headers. It does NOT see DNS separately (name resolution happens inside connect_tcp), cannot
see what the provider is doing between the request and its first byte, and never records a request or response body, a header such as Authorization, or a key.
"""

import math
import re
import time
from contextvars import ContextVar

# The ONLY response headers whose values are stored, by exact name: when to retry, how much allowance is left, the id to quote to support, and the standard cache
# indicators. Any other header (cookies, tokens, internal or arbitrary headers) is never stored; a name that is not here is ignored, not matched by prefix or substring.
SAFE_HEADERS = frozenset({
    "retry-after", "retry-after-ms", "x-request-id", "content-type",
    "x-ratelimit-limit-requests", "x-ratelimit-remaining-requests", "x-ratelimit-reset-requests",
    "x-ratelimit-limit-tokens", "x-ratelimit-remaining-tokens", "x-ratelimit-reset-tokens",
    "ratelimit-limit", "ratelimit-remaining", "ratelimit-reset", "ratelimit-policy",
    "age", "x-cache", "x-cache-status", "cf-cache-status",
})
MAX_HEADER_VALUE = 100
MAX_HEADER_NAMES = 40
_NAME = re.compile(r"[a-z0-9-]{1,64}")
_PRINTABLE = re.compile(r"[\x20-\x7e]*")

# Mesh's routing headers, parsed to VALIDATED values (never stored raw). Documented on https://developers.meshapi.ai/docs/platform/retry-and-fallback.md (read 27 Oct 2026):
#   X-Mesh-Routing-Attempts: "Total attempts made (the initial try plus any retries and fallback hops)"; the worked example shows `3`; no type is stated.
#   X-Mesh-Routing-Fallback: "`true` if the request was ultimately served by a fallback rather than the primary provider"; only `true` is documented, and the docs do not say
#     whether the header is absent or `false` otherwise. (Not returned on streamed responses; this client does not stream.)
#   X-BYOK-Fallback-Triggered: the meaning is documented (a bring-your-own provider key failed, so shared platform credentials were used); the value format is not.
#   X-Provider-Latency-Ms: not documented anywhere I could find.
# So the accepted forms below are the narrowest that are plausible: a small non-negative integer; the words true/false; a non-negative decimal number of milliseconds. Anything else,
# and a missing header, is unknown (None), never 0 and never false. This DeepDive key is not BYOK (the usage export says so), so a value there is not expected.
MESH_HEADERS = ("x-mesh-routing-attempts", "x-mesh-routing-fallback", "x-provider-latency-ms", "x-byok-fallback-triggered")
MAX_ATTEMPTS = 20
MAX_PROVIDER_LATENCY_MS = 3_600_000.0
_INT = re.compile(r"[0-9]{1,3}")
_DECIMAL = re.compile(r"[0-9]{1,9}(\.[0-9]{1,6})?")


def clean_header_value(value) -> str | None:
    """A header value fit to store: a string of printable ASCII, at most MAX_HEADER_VALUE characters, else None (dropped, not truncated or repaired)."""
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value if len(value) <= MAX_HEADER_VALUE and _PRINTABLE.fullmatch(value) else None


def _parse_int(value: str | None) -> int | None:
    if value is None or not _INT.fullmatch(value):
        return None
    n = int(value)
    return n if 0 <= n <= MAX_ATTEMPTS else None


def _parse_bool(value: str | None) -> bool | None:
    return {"true": True, "false": False}.get(value.lower()) if value is not None else None


def _parse_ms(value: str | None) -> int | float | None:
    if value is None or not _DECIMAL.fullmatch(value):
        return None
    n = float(value)
    if not (math.isfinite(n) and 0 <= n <= MAX_PROVIDER_LATENCY_MS):
        return None
    return int(n) if "." not in value else n


def parse_mesh_headers(headers) -> dict:
    """Validated values of Mesh's four routing headers. `present` lists the ones the response carried; `unrecognized` those whose value did not match an accepted form
    (their value is not stored). A header that is missing or unrecognized is None: unknown."""
    lower = {str(k).lower(): v for k, v in headers.items()}
    out: dict = {"routing_attempts": None, "routing_fallback": None, "provider_latency_ms": None, "byok_fallback_triggered": None, "present": [], "unrecognized": []}
    parsers = {"x-mesh-routing-attempts": ("routing_attempts", _parse_int), "x-mesh-routing-fallback": ("routing_fallback", _parse_bool),
               "x-provider-latency-ms": ("provider_latency_ms", _parse_ms), "x-byok-fallback-triggered": ("byok_fallback_triggered", _parse_bool)}
    for name, (key, parse) in parsers.items():
        if name not in lower:
            continue
        out["present"].append(name)
        value = parse(clean_header_value(lower[name]))
        if value is None:
            out["unrecognized"].append(name)
        else:
            out[key] = value
    return out

# The trace of the request currently being made on this task (the LLM sets it around each request; the httpx hooks below read it).
CURRENT_TRACE: ContextVar["TransportTrace | None"] = ContextVar("deepdive_current_trace", default=None)


def safe_headers(headers) -> dict[str, str]:
    out = {}
    for name, value in headers.items():
        key = str(name).lower()
        cleaned = clean_header_value(value)
        if key in SAFE_HEADERS and cleaned is not None:
            out[key] = cleaned
    return out


def header_names(headers) -> list[str]:
    """NAMES only (never values) of the response headers, bounded in number and length and limited to ordinary header-name characters."""
    return sorted({str(n).lower() for n in headers if _NAME.fullmatch(str(n).lower())})[:MAX_HEADER_NAMES]


class TransportTrace:
    def __init__(self, clock=time.monotonic):
        self.clock = clock
        self.t0 = clock()
        self.phases: dict[str, dict] = {}  # phase -> {"start_ms", "end_ms" (None while open), "failed"}; a phase seen twice keeps its first start and last end
        self.connect_attempts = 0  # connection.connect_tcp.started events
        self.http_requests_sent = 0  # send_request_headers.started events: HTTP requests actually written to a connection
        self.connect_timeout_s: float | None = None  # the connect timeout httpcore reported using
        self.status_code: int | None = None
        self.headers: dict[str, str] = {}
        self.header_names: list[str] = []  # NAMES only of the response headers (never values), to learn what a provider reports about caching, attempts or routing
        self.mesh: dict = parse_mesh_headers({})  # validated values of Mesh's routing headers; all unknown until a response arrives
        self.sdk_max_retries: int | None = None  # the client's own retry setting, set by the LLM
        self.order: list[str] = []  # phases in the order they started

    def _ms(self) -> int:
        return round((self.clock() - self.t0) * 1000)

    async def callback(self, event_name: str, info: dict) -> None:  # httpcore's trace signature
        _protocol, _, rest = event_name.partition(".")
        phase, _, kind = rest.rpartition(".")
        if not phase or kind not in ("started", "complete", "failed"):
            return
        now = self._ms()
        p = self.phases.get(phase)
        if kind == "started":
            if p is None:
                self.phases[phase] = {"start_ms": now, "end_ms": None, "failed": False}
                self.order.append(phase)
            else:
                p["end_ms"] = None  # started again (a second request on the connection)
            if phase == "connect_tcp":
                self.connect_attempts += 1
                timeout = info.get("timeout") if isinstance(info, dict) else None
                self.connect_timeout_s = float(timeout) if isinstance(timeout, (int, float)) else self.connect_timeout_s
            elif phase == "send_request_headers":
                self.http_requests_sent += 1
        elif p is not None:
            p["end_ms"] = now
            p["failed"] = kind == "failed"

    def on_response(self, response) -> None:  # the httpx response hook
        self.status_code = response.status_code
        self.headers = safe_headers(response.headers)
        self.header_names = header_names(response.headers)
        self.mesh = parse_mesh_headers(response.headers)

    def open_phase(self) -> str | None:
        """The phase that had started and not finished: where the request was when it stopped. None when every observed phase finished (or none was observed)."""
        for phase in reversed(self.order):
            if self.phases[phase]["end_ms"] is None:
                return phase
        return None

    def failed_phase(self) -> str | None:
        return next((ph for ph in self.order if self.phases[ph]["failed"]), None)

    def summary(self) -> dict:
        return {
            "phases_ms": {ph: [self.phases[ph]["start_ms"], self.phases[ph]["end_ms"]] for ph in self.order},
            "open_phase": self.open_phase(), "failed_phase": self.failed_phase(),
            "connect_attempts": self.connect_attempts, "http_requests_sent": self.http_requests_sent,
            "connect_timeout_s": self.connect_timeout_s, "status_code": self.status_code, "response_headers": self.headers, "response_header_names": self.header_names, "mesh": self.mesh,
            "not_observable": "DNS (it is inside connect_tcp); and everything after the request is written: network transit, gateway queueing, upstream generation, upstream retries or fallback",
        }


async def on_request(request) -> None:  # httpx request hook: ask httpcore to report this request's phases to the current trace
    trace = CURRENT_TRACE.get()
    if trace is not None:
        request.extensions["trace"] = trace.callback


async def on_response(response) -> None:  # httpx response hook
    trace = CURRENT_TRACE.get()
    if trace is not None:
        trace.on_response(response)
