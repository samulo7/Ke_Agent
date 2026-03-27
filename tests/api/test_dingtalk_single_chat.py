from __future__ import annotations

import unittest
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.services.single_chat import SingleChatService


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


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
    def test_response_content_type_declares_utf8_charset(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="你好"))
        self.assertEqual(200, response.status_code)
        self.assertIn("application/json", response.headers["content-type"].lower())
        self.assertIn("charset=utf-8", response.headers["content-type"].lower())

    def test_greeting_returns_no_hit_text_channel(self) -> None:
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
        self.assertFalse(body["handled"])
        self.assertEqual("knowledge_no_hit", body["reason"])
        self.assertEqual("other", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])
        self.assertEqual([], body["source_ids"])
        self.assertEqual("allow", body["permission_decision"])
        self.assertTrue(body["knowledge_version"])
        self.assertTrue(body["answered_at"])
        self.assertEqual([], body["citations"])

    def test_ambiguous_question_returns_clarification_once(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="这个怎么弄"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("ambiguous_question", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("仅追问一次", body["reply"]["text"])
        self.assertEqual([], body["source_ids"])

    def test_low_confidence_question_returns_handoff_guidance(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(text="我想问个事儿，帮我处理一下"),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("low_confidence_fallback", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("无法准确判断", body["reply"]["text"])
        self.assertEqual([], body["source_ids"])

    def test_policy_query_returns_knowledge_with_source_metadata(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="宴请标准是什么"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("knowledge_answer", body["reason"])
        self.assertEqual("policy_process", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("doc-policy-banquet-2026-01", body["source_ids"])
        self.assertEqual("allow", body["permission_decision"])
        self.assertTrue(body["knowledge_version"])
        self.assertTrue(body["answered_at"])
        self.assertGreaterEqual(len(body["citations"]), 1)

    def test_system_failure_returns_text_fallback_instead_of_500(self) -> None:
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=SingleChatService(knowledge_answer_service=_RaisingKnowledgeAnswerService()),
        )
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="宴请标准是什么"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("system_fallback", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("稍后再试", body["reply"]["text"])

    def test_policy_query_with_mojibake_text_is_repaired(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        original = "宴请标准是什么"
        mojibake = original.encode("utf-8").decode("latin-1")
        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text=mojibake))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("knowledge_answer", body["reason"])
        self.assertEqual("policy_process", body["intent"])
        self.assertIn("doc-policy-banquet-2026-01", body["source_ids"])

    def test_flow_query_returns_interactive_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="出差报销怎么弄"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("flow_guidance_card", body["reason"])
        self.assertEqual("reimbursement", body["intent"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("flow_guidance", body["reply"]["interactive_card"]["card_type"])
        self.assertEqual("interactive_card", body["dingtalk_payload"]["msgtype"])
        self.assertEqual([], body["source_ids"])
        self.assertEqual("allow", body["permission_decision"])

    def test_document_request_returns_application_draft_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="我要申请采购制度文件"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("application_draft_card", body["reason"])
        self.assertEqual("document_request", body["intent"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("application_draft", body["reply"]["interactive_card"]["card_type"])
        self.assertEqual([], body["source_ids"])
        self.assertEqual("allow", body["permission_decision"])

    def test_empty_input_returns_text_fallback(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="   "))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertFalse(body["handled"])
        self.assertEqual("empty_input", body["reason"])
        self.assertEqual("other", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual([], body["source_ids"])

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
        self.assertEqual("other", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual([], body["source_ids"])

    def test_invalid_payload_returns_400(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json={"conversation_type": "single"})
        self.assertEqual(400, response.status_code)
        self.assertIn("sender_id is required", response.json()["error"])


if __name__ == "__main__":
    unittest.main()
