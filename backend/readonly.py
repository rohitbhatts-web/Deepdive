"""Public read-only mode: the hosted demo shows RECORDED runs and starts nothing.

Enforced in three independent places, so that one mistake does not open a write path:
  1. `ReadOnlyMiddleware` answers every request whose method is not GET/HEAD/OPTIONS with 403, before routing;
  2. the database is opened with SQLite `mode=ro` (a write raises even if a handler tried one);
  3. no provider client can be built (`get_mesh_client`, `build_services` refuse), and the deployment holds no provider keys anyway.
"""

import json

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MESSAGE = ("This is a read-only demonstration of recorded runs. Starting research, retrying, verifying, completing or adding sources is disabled; "
           "nothing here calls a model, search or page fetch.")


class ReadOnlyError(RuntimeError):
    """Raised where a provider client or a write would be needed in public read-only mode."""


class ReadOnlyMiddleware:
    """Pure ASGI middleware (no body is read): refuse every mutating HTTP method."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            body = json.dumps({"detail": {"error": "read_only", "message": MESSAGE}}).encode()
            await send({"type": "http.response.start", "status": 403,
                        "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()), (b"allow", b"GET, HEAD, OPTIONS")]})
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)
