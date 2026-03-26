from __future__ import annotations

import unittest
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app


def make_stream_payload(
    *,
    text: str = "hello",
    conversation_type: str = "single",
    sender_id: str = "user-001",
    message_type: str = "text",
) -> dict[str, object]:
    return {
        "event_id": "evt-001",
        "conversation_id": "conv-001",
        "conversation_type": conversation_type,
        "sender_id": sender_id,
        "message_type": message_type,
        "text": text,
    }


class DingTalkSingleChatApiTests(unittest.TestCase):
    def test_greeting_returns_text_channel(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            headers={"X-Trace-Id": "trace-a05-greet"},
            json=make_stream_payload(text="你好"),
        )
        self.assertEqual(200, response.status_code)
        self.assertEqual("trace-a05-greet", response.headers["X-Trace-Id"])

        body = response.json()
        self.assertTrue(body["handled"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])

    def test_flow_query_returns_interactive_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="出差报销怎么弄"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("flow_guidance_card", body["reason"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("flow_guidance", body["reply"]["interactive_card"]["card_type"])
        self.assertEqual("interactive_card", body["dingtalk_payload"]["msgtype"])

    def test_document_request_returns_application_draft_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="我要申请采购制度文件"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("application_draft_card", body["reason"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("application_draft", body["reply"]["interactive_card"]["card_type"])

    def test_empty_input_returns_text_fallback(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="   "))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("empty_input", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])

    def test_group_chat_returns_non_single_notice(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(conversation_type="group", text="hello group"),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("non_single_chat", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])

    def test_invalid_payload_returns_400(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json={"conversation_type": "single"})
        self.assertEqual(400, response.status_code)
        self.assertIn("sender_id is required", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
