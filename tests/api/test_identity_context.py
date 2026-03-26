from __future__ import annotations

import json
import unittest
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.schemas.user_context import UserContext


def _load_logs(log_stream: StringIO) -> list[dict[str, object]]:
    rows = [line.strip() for line in log_stream.getvalue().splitlines() if line.strip()]
    return [json.loads(row) for row in rows]


class _StubResolver:
    def __init__(self) -> None:
        self._contexts = {
            "staff-a": UserContext(
                user_id="staff-a",
                user_name="Alice",
                dept_id="dept-finance",
                dept_name="Finance",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-26T00:00:00+00:00",
            ),
            "staff-b": UserContext(
                user_id="staff-b",
                user_name="Bob",
                dept_id="dept-hr",
                dept_name="HR",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-26T00:00:01+00:00",
            ),
        }

    def resolve(self, message):  # type: ignore[no-untyped-def]
        key = message.sender_staff_id or message.sender_id
        return self._contexts.get(
            key,
            UserContext(
                user_id=key or "unknown",
                user_name=message.sender_nick or "unknown",
                dept_id="unknown",
                dept_name="unknown",
                identity_source="event_fallback",
                is_degraded=True,
                resolved_at="2026-03-26T00:00:59+00:00",
            ),
        )


def _payload(*, sender_staff_id: str, text: str) -> dict[str, object]:
    return {
        "event_id": "evt-a06-001",
        "conversation_id": "conv-a06-001",
        "conversation_type": "single",
        "senderStaffId": sender_staff_id,
        "senderNick": "tester",
        "message_type": "text",
        "text": text,
    }


class DingTalkIdentityContextApiTests(unittest.TestCase):
    def test_response_contains_user_context_and_log_matches(self) -> None:
        trace_id = "trace-a06-identity"
        log_stream = StringIO()
        app = create_app(log_stream=log_stream, user_context_resolver=_StubResolver())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": trace_id},
            json=_payload(sender_staff_id="staff-a", text="你好"),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("other", body["intent"])
        self.assertEqual("staff-a", body["user_context"]["user_id"])
        self.assertEqual("dept-finance", body["user_context"]["dept_id"])
        self.assertEqual("openapi", body["user_context"]["identity_source"])

        request_logs = [
            item
            for item in _load_logs(log_stream)
            if item.get("event") == "request_completed" and item.get("path") == "/dingtalk/stream/events"
        ]
        self.assertGreaterEqual(len(request_logs), 1)
        log = request_logs[-1]
        self.assertEqual(trace_id, log["trace_id"])
        self.assertEqual(body["user_context"]["user_id"], log["user_id"])
        self.assertEqual(body["user_context"]["dept_id"], log["dept_id"])
        self.assertEqual(body["intent"], log["intent"])
        self.assertEqual(body["user_context"]["identity_source"], log["identity_source"])
        self.assertEqual(body["user_context"]["is_degraded"], log["is_degraded"])
        self.assertEqual(body["source_ids"], log["source_ids"])
        self.assertEqual(body["permission_decision"], log["permission_decision"])
        self.assertEqual(body["knowledge_version"], log["knowledge_version"])
        self.assertEqual(body["answered_at"], log["answered_at"])

    def test_same_question_cross_department_has_no_context_mismatch(self) -> None:
        log_stream = StringIO()
        app = create_app(log_stream=log_stream, user_context_resolver=_StubResolver())
        client = TestClient(app)

        response_a = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": "trace-a06-a"},
            json=_payload(sender_staff_id="staff-a", text="报销流程是什么"),
        )
        response_b = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": "trace-a06-b"},
            json=_payload(sender_staff_id="staff-b", text="报销流程是什么"),
        )

        self.assertEqual(200, response_a.status_code)
        self.assertEqual(200, response_b.status_code)
        body_a = response_a.json()
        body_b = response_b.json()
        self.assertEqual("reimbursement", body_a["intent"])
        self.assertEqual("reimbursement", body_b["intent"])
        self.assertEqual("dept-finance", body_a["user_context"]["dept_id"])
        self.assertEqual("dept-hr", body_b["user_context"]["dept_id"])
        self.assertNotEqual(body_a["user_context"]["dept_id"], body_b["user_context"]["dept_id"])


if __name__ == "__main__":
    unittest.main()
