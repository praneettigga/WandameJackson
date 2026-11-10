"""ASGI guard for a local-only API: host/origin checks, request size limits and a JSON 500 envelope.

It sits inside the CORS middleware, so its own error responses (and any unexpected 500) still carry
CORS headers and the browser can show the real message instead of a generic network failure.
"""
from __future__ import annotations

import json
import logging

from .config import MB, Settings
from .errors import error_body

log = logging.getLogger("roomshift")
UNSAFE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# Multipart framing and form fields on top of the file payload itself.
MULTIPART_OVERHEAD = 1 * MB


class BodyTooLarge(Exception):
    pass


def _host_name(host: str) -> str:
    """Host header without the port; IPv6 literals keep their brackets ("[::1]")."""
    if host.startswith("["):
        return host[: host.find("]") + 1] if "]" in host else host
    return host.rsplit(":", 1)[0] if host.count(":") == 1 else host


class LocalGuard:
    def __init__(self, app, settings: Settings):
        self.app = app
        self.settings = settings

    def body_limit(self, method: str, path: str) -> int:
        s = self.settings
        if method == "POST" and path == "/api/captures":
            return s.max_capture_bytes + MULTIPART_OVERHEAD
        if method == "POST" and path == "/api/projects":
            return s.max_upload_bytes + MULTIPART_OVERHEAD
        return s.max_json_bytes

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        headers = {k.decode("latin-1"): v.decode("latin-1") for k, v in scope["headers"]}
        method, path = scope["method"], scope["path"]

        host = _host_name(headers.get("host", "").lower())
        if "*" not in self.settings.allowed_hosts and host not in self.settings.allowed_hosts:
            return await _json(send, 400, "INVALID_HOST", "This API only accepts requests addressed to the local host.")
        origin = headers.get("origin")
        # Browsers send Origin on cross-site writes; refuse writes from pages we do not serve.
        if method in UNSAFE_METHODS and origin and origin not in self.settings.cors_origins:
            return await _json(send, 403, "FORBIDDEN_ORIGIN", f"Requests from {origin} are not allowed.")

        limit = self.body_limit(method, path)
        too_large = f"Request body exceeds {limit // MB} MB."
        try:
            if int(headers.get("content-length") or 0) > limit:
                return await _json(send, 413, "UPLOAD_TOO_LARGE", too_large)
        except ValueError:
            return await _json(send, 400, "VALIDATION_ERROR", "Invalid Content-Length header.")

        received = 0
        started = overflow = False

        async def counted_receive():
            nonlocal received, overflow
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > limit:
                    overflow = True
                    raise BodyTooLarge()
            return message

        async def tracked_send(message):
            nonlocal started
            if overflow:
                # The framework may turn the aborted read into its own 400; answer 413 instead.
                if message["type"] == "http.response.start" and not started:
                    started = True
                    await _json(send, 413, "UPLOAD_TOO_LARGE", too_large)
                return
            if message["type"] == "http.response.start":
                started = True
            await send(message)

        try:
            await self.app(scope, counted_receive, tracked_send)
        except BodyTooLarge:
            if not started:
                started = True
                await _json(send, 413, "UPLOAD_TOO_LARGE", too_large)
        except Exception:
            log.exception("unhandled error on %s %s", method, path)
            if not started:
                await _json(send, 500, "INTERNAL_ERROR", "Unexpected server error.")


async def _json(send, status: int, code: str, message: str) -> None:
    body = json.dumps(error_body(code, message)).encode("utf-8")
    await send({"type": "http.response.start", "status": status,
                "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
    await send({"type": "http.response.body", "body": body})
