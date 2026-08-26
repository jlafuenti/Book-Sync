"""
Pre-parse request guards (issue #157).

FastAPI resolves a route's body — including `await request.form()` for
multipart — BEFORE it resolves the route's dependencies, auth included. That
means an unauthenticated caller can drive the multipart parser with an
arbitrarily large body on any upload route. This middleware bounds that cost:
a `multipart/form-data` request whose declared Content-Length exceeds
`settings.upload_max_bytes` is refused with 413 before any parsing happens.

Pure ASGI (no Starlette request/response objects) so nothing here touches the
body. The cap is read from settings at request time so it can be tuned via the
UPLOAD_MAX_BYTES env var and monkeypatched in tests. Requests without a valid
Content-Length pass through — bounding chunked bodies needs streaming
byte-counting and belongs in the reverse proxy (see docs/operations.md).
"""

import json

from config import settings


class MultipartBodyLimitMiddleware:
    """Reject oversized multipart/form-data requests with 413 pre-parse."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and self._is_oversized_multipart(scope):
            body = json.dumps(
                {
                    "detail": (
                        "Request body exceeds the configured upload limit "
                        f"({settings.upload_max_bytes} bytes)"
                    )
                }
            ).encode("utf-8")
            await send(
                {
                    "type": "http.response.start",
                    "status": 413,
                    "headers": [
                        (b"content-type", b"application/json"),
                        (b"content-length", str(len(body)).encode("ascii")),
                    ],
                }
            )
            await send({"type": "http.response.body", "body": body})
            return
        await self.app(scope, receive, send)

    @staticmethod
    def _is_oversized_multipart(scope) -> bool:
        content_type = b""
        content_length = b""
        for name, value in scope.get("headers", []):
            lowered = name.lower()
            if lowered == b"content-type":
                content_type = value
            elif lowered == b"content-length":
                content_length = value
        if not content_type.strip().lower().startswith(b"multipart/form-data"):
            return False
        if not content_length.strip().isdigit():
            return False
        return int(content_length) > settings.upload_max_bytes
