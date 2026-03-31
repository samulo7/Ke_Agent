from __future__ import annotations

import json
import logging
import unittest
from io import StringIO
from uuid import UUID

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.core.structured_logging import configure_structured_logging
from app.core.trace_context import get_trace_id
from app.services.health import HealthProbe, HealthService


def _load_logs(log_stream: StringIO) -> list[dict[str, object]]:
    rows = [line.strip() for line in log_stream.getvalue().splitlines() if line.strip()]
    return [json.loads(row) for row in rows]


class HealthObservabilityTests(unittest.TestCase):
    def test_healthz_returns_200_and_reuses_trace_header(self) -> None:
        trace_id = "trace-a04-success"
        log_stream = StringIO()
        app = create_app(log_stream=log_stream)
        client = TestClient(app)

        response = client.get("/healthz", headers={"X-Trace-Id": trace_id})
        self.assertEqual(200, response.status_code)
        self.assertEqual(trace_id, response.headers["X-Trace-Id"])
        self.assertEqual(trace_id, response.json()["trace_id"])
        self.assertEqual("ok", response.json()["status"])

        logs = _load_logs(log_stream)
        self.assertGreaterEqual(len(logs), 1)
        request_log = logs[-1]
        self.assertEqual("request_completed", request_log["event"])
        self.assertEqual("api.middleware", request_log["module"])
        self.assertEqual(trace_id, request_log["trace_id"])
        self.assertEqual(200, request_log["status_code"])
        self.assertIsNone(request_log["error_category"])
        self.assertIsInstance(request_log["duration_ms"], (int, float))

    def test_healthz_generates_trace_when_missing(self) -> None:
        log_stream = StringIO()
        app = create_app(log_stream=log_stream)
        client = TestClient(app)

        response = client.get("/healthz")
        self.assertEqual(200, response.status_code)
        generated = response.headers["X-Trace-Id"]
        self.assertEqual(generated, response.json()["trace_id"])
        UUID(generated)

    def test_trace_context_is_accessible_inside_probe(self) -> None:
        trace_id = "trace-context-check"

        def context_probe() -> str:
            return "ok" if get_trace_id() == trace_id else "down"

        service = HealthService(probes=[HealthProbe(name="trace_context", check=context_probe)])
        app = create_app(health_service=service, log_stream=StringIO())
        client = TestClient(app)

        response = client.get("/healthz", headers={"X-Trace-Id": trace_id})
        self.assertEqual(200, response.status_code)
        checks = response.json()["checks"]
        self.assertEqual("ok", checks[0]["status"])

    def test_healthz_probe_failure_returns_503_and_logs_dependency_error(self) -> None:
        def failing_probe() -> str:
            raise RuntimeError("probe failed")

        log_stream = StringIO()
        service = HealthService(probes=[HealthProbe(name="failing_probe", check=failing_probe)])
        app = create_app(health_service=service, log_stream=log_stream)
        client = TestClient(app)

        response = client.get("/healthz")
        self.assertEqual(503, response.status_code)
        response_body = response.json()
        self.assertEqual("down", response_body["status"])
        self.assertEqual(response.headers["X-Trace-Id"], response_body["trace_id"])
        self.assertEqual("failing_probe", response_body["checks"][0]["name"])
        self.assertEqual("down", response_body["checks"][0]["status"])
        self.assertIsNotNone(response_body["checks"][0]["error"])

        logs = _load_logs(log_stream)
        self.assertGreaterEqual(len(logs), 1)
        request_log = logs[-1]
        self.assertEqual("request_completed", request_log["event"])
        self.assertEqual("api.middleware", request_log["module"])
        self.assertEqual(503, request_log["status_code"])
        self.assertEqual("dependency_error", request_log["error_category"])
        self.assertEqual(response_body["trace_id"], request_log["trace_id"])

    def test_structured_logging_preserves_extra_obs_fields(self) -> None:
        log_stream = StringIO()
        logger = configure_structured_logging(stream=log_stream)

        logger.warning(
            "leave.approval.api_error",
            extra={
                "obs": {
                    "module": "integrations.dingtalk.leave_approval",
                    "event": "leave_approval_api_error",
                    "process_code": "PROC-LEAVE",
                    "errcode": 40001,
                    "errmsg": "invalid form value",
                }
            },
        )

        logs = _load_logs(log_stream)
        self.assertEqual(1, len(logs))
        event_log = logs[0]
        self.assertEqual("leave_approval_api_error", event_log["event"])
        self.assertEqual("integrations.dingtalk.leave_approval", event_log["module"])
        self.assertEqual("PROC-LEAVE", event_log["process_code"])
        self.assertEqual(40001, event_log["errcode"])
        self.assertEqual("invalid form value", event_log["errmsg"])


if __name__ == "__main__":
    unittest.main()
