"""Dashboard credential and unexpected-error boundary regression tests."""

from __future__ import annotations

import importlib.util
import unittest
from unittest import mock


@unittest.skipUnless(
    importlib.util.find_spec("fastapi") and importlib.util.find_spec("httpx"),
    "Dashboard API contract dependencies are not installed",
)
class DashboardSecurityBoundaryTests(unittest.TestCase):
    def test_query_token_is_never_accepted_as_dashboard_credential(self) -> None:
        from starlette.requests import Request

        from cluster.dashboard.dependencies import supplied_dashboard_token

        query_only = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/events",
                "headers": [],
                "query_string": b"token=query-secret",
            }
        )
        header = Request(
            {
                "type": "http",
                "method": "GET",
                "path": "/api/events",
                "headers": [(b"x-cluster-token", b"header-secret")],
                "query_string": b"token=query-secret",
            }
        )

        self.assertEqual(supplied_dashboard_token(query_only), "")
        self.assertEqual(supplied_dashboard_token(header), "header-secret")

    def test_unexpected_error_response_never_exposes_exception_text(self) -> None:
        from fastapi.testclient import TestClient

        from cluster.dashboard import app as dashboard_app

        with mock.patch.object(dashboard_app.services, "ensure_runtime"):
            application = dashboard_app.create_app()

        async def fail_with_secret() -> None:
            raise RuntimeError("credential=must-not-leak")

        application.add_api_route("/phase14-error-boundary", fail_with_secret)
        with self.assertLogs("cluster.dashboard.app", level="ERROR") as captured:
            response = TestClient(
                application, raise_server_exceptions=False
            ).get("/phase14-error-boundary")

        self.assertEqual(response.status_code, 500)
        payload = response.json()
        self.assertEqual(payload["detail"], "Internal server error")
        self.assertEqual(payload["request_id"], response.headers["X-Request-ID"])
        self.assertNotIn("must-not-leak", response.text)
        self.assertNotIn("must-not-leak", "\n".join(captured.output))
        self.assertIn("dashboard_unexpected_error", captured.output[0])
        self.assertIn("RuntimeError", captured.output[0])


if __name__ == "__main__":
    unittest.main()
