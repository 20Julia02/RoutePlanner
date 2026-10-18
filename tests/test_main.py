import unittest
from unittest.mock import patch

from fastapi import HTTPException
from starlette.requests import Request

import main


def request_from(host: str, *, method: str = "GET") -> Request:
    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": method,
            "scheme": "http",
            "path": "/",
            "raw_path": b"/",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
            "client": (host, 12345),
            "server": ("localhost", 8000),
        }
    )


class LocalAdministrationTests(unittest.TestCase):
    def test_admin_feature_is_exposed_only_to_loopback_clients(self):
        with patch.object(main, "ALLOW_LOCAL_ADMIN", True):
            self.assertTrue(main.frontend_config(request_from("127.0.0.1"))["local_admin"])
            self.assertTrue(main.frontend_config(request_from("::1"))["local_admin"])
            self.assertFalse(main.frontend_config(request_from("203.0.113.10"))["local_admin"])

    def test_public_client_cannot_prepare_a_network(self):
        with (
            patch.object(main, "ALLOW_LOCAL_ADMIN", True),
            self.assertRaises(HTTPException) as context,
        ):
            main.prepare_admin_network(
                {"name": "Niedozwolony"},
                request_from("203.0.113.10", method="POST"),
            )
        self.assertEqual(404, context.exception.status_code)

    def test_public_plan_endpoint_rejects_raw_network_data(self):
        with (
            patch.object(main, "ALLOW_LOCAL_ADMIN", True),
            self.assertRaises(HTTPException) as context,
        ):
            main.create_plan(
                {"edges": {}, "attractions": {}},
                request_from("203.0.113.10", method="POST"),
            )
        self.assertEqual(422, context.exception.status_code)

    def test_loopback_admin_can_prepare_and_save_a_network(self):
        prepared = object()
        metadata = {"id": "a" * 32, "name": "Test"}
        with (
            patch.object(main, "ALLOW_LOCAL_ADMIN", True),
            patch.object(main, "prepare_network", return_value=prepared) as prepare,
            patch.object(main.admin_store, "save", return_value=metadata) as save,
        ):
            result = main.prepare_admin_network(
                {"name": "Test", "edges": {}, "attractions": {}},
                request_from("127.0.0.1", method="POST"),
            )

        prepare.assert_called_once()
        self.assertTrue(prepare.call_args.kwargs["calculate_cost_matrix"])
        save.assert_called_once_with("Test", prepared)
        self.assertEqual(metadata, result["metadata"])

    def test_missing_public_network_attractions_return_not_found(self):
        with (
            patch.object(main.network_store, "list_attractions", side_effect=FileNotFoundError),
            self.assertRaises(HTTPException) as context,
        ):
            main.list_public_network_attractions("missing")

        self.assertEqual(404, context.exception.status_code)


class DocumentationTests(unittest.TestCase):
    def test_documentation_routes_follow_configuration(self):
        paths = {route.path for route in main.app.routes}
        self.assertEqual(main.ENABLE_DOCS, "/docs" in paths)
        self.assertEqual(main.ENABLE_DOCS, "/redoc" in paths)
        self.assertEqual(main.ENABLE_DOCS, "/openapi.json" in paths)


if __name__ == "__main__":
    unittest.main()
