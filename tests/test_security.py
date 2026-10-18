import hashlib
import json
import unittest
from pathlib import Path
from unittest.mock import patch

from public_catalog import PublicNetworkCatalog, _AllowlistedRedirectHandler
from security import (
    RequestBodyLimitMiddleware,
    SecurityHeadersMiddleware,
    SlidingWindowRateLimiter,
)


class SecurityHelperTests(unittest.IsolatedAsyncioTestCase):
    async def test_oversized_declared_body_is_rejected_before_application(self):
        application_called = False

        async def application(scope, receive, send):
            nonlocal application_called
            application_called = True

        messages = []

        async def collect(message):
            messages.append(message)

        scope = {
            "type": "http",
            "path": "/api/plan",
            "headers": [(b"content-length", b"11")],
        }
        await RequestBodyLimitMiddleware(application, max_bytes=10)(
            scope,
            lambda: None,
            collect,
        )

        self.assertFalse(application_called)
        self.assertEqual(413, messages[0]["status"])

    async def test_security_headers_are_added_to_api_responses(self):
        messages = []

        async def collect(message):
            messages.append(message)

        async def application(scope, receive, send):
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": b"{}"})

        scope = {"type": "http", "path": "/api/health", "headers": []}
        await SecurityHeadersMiddleware(application)(scope, lambda: None, collect)
        headers = dict(messages[0]["headers"])

        self.assertEqual(b"no-store", headers[b"cache-control"])
        self.assertEqual(b"nosniff", headers[b"x-content-type-options"])
        policy = headers[b"content-security-policy"]
        self.assertIn(b"default-src 'self'", policy)
        self.assertNotIn(b"cdn.jsdelivr.net", policy)
        self.assertNotIn(b"script-src 'self' 'unsafe-inline'", policy)


class RateLimiterTests(unittest.TestCase):
    def test_limiter_rejects_only_until_the_window_expires(self):
        limiter = SlidingWindowRateLimiter(limit=2, window_seconds=60)
        with patch("security.time.monotonic", side_effect=[0, 1, 2, 61]):
            self.assertTrue(limiter.allow("client"))
            self.assertTrue(limiter.allow("client"))
            self.assertFalse(limiter.allow("client"))
            self.assertTrue(limiter.allow("client"))


class PublicCatalogTests(unittest.TestCase):
    class _Response:
        def __init__(self, url, content):
            self._url = url
            self._content = content
            self.headers = {"Content-Length": str(len(content))}

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def geturl(self):
            return self._url

        def read(self, maximum):
            return self._content[:maximum]

    def test_remote_catalog_rejects_unapproved_and_lookalike_hosts(self):
        sha = "0" * 64
        for url in (
            "http://safe.public.blob.vercel-storage.com/manifest.json",
            "https://evil.example/manifest.json",
            "https://notexample.com/manifest.json",
        ):
            with self.subTest(url=url), self.assertRaises(ValueError):
                PublicNetworkCatalog(
                    Path("unused"),
                    manifest_url=url,
                    manifest_sha256=sha,
                    allowed_hosts=("example.com",),
                )

    def test_remote_catalog_requires_manifest_integrity_hash(self):
        with self.assertRaises(ValueError):
            PublicNetworkCatalog(
                Path("unused"),
                manifest_url="https://data.example.com/manifest.json",
                allowed_hosts=("data.example.com",),
            )

    def test_download_rejects_content_with_wrong_integrity_hash(self):
        url = "https://data.example.com/network.json"
        catalog = PublicNetworkCatalog(Path("unused"), allowed_hosts=("data.example.com",))
        response = self._Response(url, b"tampered")
        with patch("public_catalog.build_opener") as build_opener:
            build_opener.return_value.open.return_value = response
            with self.assertRaisesRegex(ValueError, "Suma kontrolna"):
                catalog._download(url, maximum_bytes=100, expected_sha256="0" * 64)

    def test_redirect_is_validated_before_it_is_followed(self):
        catalog = PublicNetworkCatalog(Path("unused"), allowed_hosts=("data.example.com",))
        handler = _AllowlistedRedirectHandler(catalog._validate_url)

        with self.assertRaisesRegex(ValueError, "dozwolonym hoście"):
            handler.redirect_request(
                None,
                None,
                302,
                "Found",
                {},
                "https://internal.example/network.json",
            )

    def test_remote_editor_uses_small_attraction_document_without_loading_graph(self):
        network_id = "a" * 32
        manifest_url = "https://data.example.com/public-networks.json"
        attractions_url = f"https://data.example.com/{network_id}.attractions.json"
        items = [
            {
                "id": "attraction-1",
                "name": "Atrakcja",
                "duration_seconds": 1800,
                "weight": 5,
                "enabled": True,
                "vertex_id": 7,
            }
        ]
        attractions = json.dumps({"version": 1, "network_id": network_id, "items": items}).encode()
        manifest = json.dumps(
            {
                "networks": [
                    {
                        "id": network_id,
                        "name": "Test",
                        "url": f"https://data.example.com/{network_id}.json",
                        "sha256": "0" * 64,
                        "attractions_url": attractions_url,
                        "attractions_sha256": hashlib.sha256(attractions).hexdigest(),
                    }
                ]
            }
        ).encode()
        catalog = PublicNetworkCatalog(
            Path("unused"),
            manifest_url=manifest_url,
            manifest_sha256=hashlib.sha256(manifest).hexdigest(),
            allowed_hosts=("data.example.com",),
        )

        def response(request, timeout):
            del timeout
            content = manifest if request.full_url == manifest_url else attractions
            return self._Response(request.full_url, content)

        with (
            patch("public_catalog.build_opener") as build_opener,
            patch.object(catalog, "load", side_effect=AssertionError("graph loaded")),
        ):
            build_opener.return_value.open.side_effect = response
            self.assertEqual(items, catalog.list_attractions(network_id))


if __name__ == "__main__":
    unittest.main()
