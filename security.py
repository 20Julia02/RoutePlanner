"""Small, dependency-free HTTP hardening helpers for the public API."""

from __future__ import annotations

import threading
import time
from collections import OrderedDict, deque
from typing import Deque

class PayloadTooLarge(Exception):
    """Raised internally when a request exceeds the configured body limit."""


class RequestBodyLimitMiddleware:
    """Reject oversized HTTP bodies, including requests without Content-Length."""

    def __init__(self, app, max_bytes: int):
        self.app = app
        self.max_bytes = max(1, int(max_bytes))

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        raw_content_length = next(
            (
                value
                for key, value in scope.get("headers", [])
                if key.lower() == b"content-length"
            ),
            None,
        )
        if raw_content_length:
            try:
                content_length = raw_content_length.decode("ascii", errors="strict")
                declared_bytes = int(content_length)
                if declared_bytes < 0:
                    raise ValueError
                if declared_bytes > self.max_bytes:
                    await self._reject(send)
                    return
            except (UnicodeDecodeError, ValueError):
                await self._reject(send, 400, "Nieprawidłowy nagłówek Content-Length.")
                return

        received = 0

        async def limited_receive():
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise PayloadTooLarge
            return message

        try:
            await self.app(scope, limited_receive, send)
        except PayloadTooLarge:
            await self._reject(send)

    @staticmethod
    async def _reject(send, status: int = 413, detail: str = "Żądanie jest zbyt duże."):
        body = ("{\"detail\":\"" + detail + "\"}").encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": status,
                "headers": [
                    (b"content-type", b"application/json; charset=utf-8"),
                    (b"content-length", str(len(body)).encode("ascii")),
                    (b"cache-control", b"no-store"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})


class SecurityHeadersMiddleware:
    """Attach browser security headers without weakening API cache semantics."""

    CONTENT_SECURITY_POLICY = "; ".join(
        (
            "default-src 'self'",
            "base-uri 'self'",
            "object-src 'none'",
            "frame-ancestors 'none'",
            "form-action 'self'",
            "script-src 'self' "
            "'sha384-cxOPjt7s7Iz04uaHJceBmS+qpjv2JkIHNVcuOrM+YHwZOmJGBXI00mdUXEq65HTH' "
            "'sha384-5+cfbwT0iiub6VsQAdn6yz16nr6sDiQoHx6tm4O8OVYXHYOxcffFmCJBL0dgdvGp' "
            "'sha384-tXYNKOHx4T02jMP7YYCtBxPIv1B5gaA5mcVPBzqMp6d7VzWzxJgI2aWF/nJLrQdS'",
            "style-src 'self' 'unsafe-inline' https://unpkg.com",
            "style-src-elem 'self' https://unpkg.com",
            "style-src-attr 'unsafe-inline'",
            "img-src 'self' data: blob: https:",
            "connect-src 'self' https://tiles.openfreemap.org https://*.openfreemap.org",
            "font-src 'self' data: https://tiles.openfreemap.org https://*.openfreemap.org",
            "worker-src 'self' blob:",
            "manifest-src 'self'",
            "upgrade-insecure-requests",
        )
    )

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = list(message.get("headers", []))
                existing = {key.lower() for key, _ in headers}

                def add_default(name: str, value: str) -> None:
                    encoded_name = name.lower().encode("ascii")
                    if encoded_name not in existing:
                        headers.append((encoded_name, value.encode("ascii")))
                        existing.add(encoded_name)

                add_default("Content-Security-Policy", self.CONTENT_SECURITY_POLICY)
                add_default("X-Content-Type-Options", "nosniff")
                add_default("X-Frame-Options", "DENY")
                add_default("Referrer-Policy", "no-referrer")
                add_default(
                    "Permissions-Policy",
                    "camera=(), microphone=(), geolocation=(self), payment=(), usb=()",
                )
                add_default("Cross-Origin-Opener-Policy", "same-origin")
                add_default("Cross-Origin-Resource-Policy", "same-origin")
                add_default("X-Permitted-Cross-Domain-Policies", "none")
                add_default(
                    "Strict-Transport-Security",
                    "max-age=63072000; includeSubDomains; preload",
                )
                if scope.get("path", "").startswith("/api/"):
                    add_default("Cache-Control", "no-store")
                message["headers"] = headers
            await send(message)

        await self.app(scope, receive, send_with_headers)


class SlidingWindowRateLimiter:
    """A bounded per-process limiter; platform-level rate limiting remains advised."""

    def __init__(self, limit: int, window_seconds: int, max_clients: int = 10_000):
        self.limit = max(1, int(limit))
        self.window_seconds = max(1, int(window_seconds))
        self.max_clients = max(100, int(max_clients))
        self._events: OrderedDict[str, Deque[float]] = OrderedDict()
        self._lock = threading.Lock()

    def allow(self, client_key: str) -> bool:
        now = time.monotonic()
        cutoff = now - self.window_seconds
        key = client_key or "unknown"
        with self._lock:
            events = self._events.pop(key, deque())
            while events and events[0] <= cutoff:
                events.popleft()
            allowed = len(events) < self.limit
            if allowed:
                events.append(now)
            self._events[key] = events
            while len(self._events) > self.max_clients:
                self._events.popitem(last=False)
            return allowed
