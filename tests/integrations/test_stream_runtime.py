from __future__ import annotations

import unittest
from typing import Any, Mapping

from app.integrations.dingtalk.stream_runtime import (
    DEFAULT_STREAM_ENDPOINT,
    StreamRuntimeError,
    handle_single_chat_payload,
    load_stream_credentials,
)
from app.schemas.user_context import UserContext
from app.services.single_chat import SingleChatService


class _FakeSender:
    def __init__(self) -> None:
        self.text_messages: list[str] = []
        self.card_payloads: list[dict[str, Any]] = []

    def send_text(self, text: str) -> None:
        self.text_messages.append(text)

    def send_interactive_card(self, card_payload: Mapping[str, Any]) -> None:
        self.card_payloads.append(dict(card_payload))


class _FakeResolver:
    def __init__(self, context: UserContext) -> None:
        self._context = context

    def resolve(self, message: Any) -> UserContext:
        return self._context


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


def _make_payload(*, text: str, conversation_type: str = "single", message_type: str = "text") -> dict[str, Any]:
    return {
        "event_id": "evt-a05-001",
        "conversation_id": "conv-a05-001",
        "conversation_type": conversation_type,
        "sender_id": "user-a05-001",
        "message_type": message_type,
        "text": text,
    }


class StreamRuntimeTests(unittest.TestCase):
    def _build_resolver(self) -> _FakeResolver:
        return _FakeResolver(
            UserContext(
                user_id="user-a05-001",
                user_name="Alice",
                dept_id="dept-finance",
                dept_name="Finance",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-26T00:00:00+00:00",
            )
        )

    def test_load_stream_credentials_uses_default_endpoint(self) -> None:
        credentials = load_stream_credentials(
            {
                "DINGTALK_CLIENT_ID": "client-id",
                "DINGTALK_CLIENT_SECRET": "client-secret",
                "DINGTALK_AGENT_ID": "agent-id",
            }
        )
        self.assertEqual(DEFAULT_STREAM_ENDPOINT, credentials.stream_endpoint)

    def test_load_stream_credentials_rejects_missing_required_keys(self) -> None:
        with self.assertRaises(StreamRuntimeError) as context:
            load_stream_credentials({"DINGTALK_CLIENT_ID": "client-id"})

        self.assertIn("DINGTALK_CLIENT_SECRET", str(context.exception))
        self.assertIn("DINGTALK_AGENT_ID", str(context.exception))

    def test_handle_single_chat_payload_sends_text_for_general_question(self) -> None:
        sender = _FakeSender()
        outcome = handle_single_chat_payload(
            _make_payload(text="你好"),
            service=SingleChatService(),
            sender=sender,
            user_context_resolver=self._build_resolver(),
        )

        self.assertEqual("text", outcome["channel"])
        self.assertEqual("other", outcome["intent"])
        self.assertFalse(outcome["handled"])
        self.assertEqual("knowledge_no_hit", outcome["reason"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertEqual(0, len(sender.card_payloads))
        self.assertEqual("user-a05-001", outcome["user_context"]["user_id"])
        self.assertEqual([], outcome["source_ids"])
        self.assertEqual("allow", outcome["permission_decision"])
        self.assertTrue(outcome["knowledge_version"])
        self.assertTrue(outcome["answered_at"])

    def test_handle_single_chat_payload_returns_traceable_knowledge_fields(self) -> None:
        sender = _FakeSender()
        outcome = handle_single_chat_payload(
            _make_payload(text="宴请标准是什么"),
            service=SingleChatService(),
            sender=sender,
            user_context_resolver=self._build_resolver(),
        )

        self.assertTrue(outcome["handled"])
        self.assertEqual("knowledge_answer", outcome["reason"])
        self.assertEqual("policy_process", outcome["intent"])
        self.assertEqual("text", outcome["channel"])
        self.assertIn("doc-policy-banquet-2026-01", outcome["source_ids"])
        self.assertEqual("allow", outcome["permission_decision"])
        self.assertTrue(outcome["knowledge_version"])
        self.assertTrue(outcome["answered_at"])
        self.assertGreaterEqual(len(outcome["citations"]), 1)

    def test_handle_single_chat_payload_returns_system_fallback_on_service_error(self) -> None:
        sender = _FakeSender()
        service = SingleChatService(knowledge_answer_service=_RaisingKnowledgeAnswerService())
        outcome = handle_single_chat_payload(
            _make_payload(text="宴请标准是什么"),
            service=service,
            sender=sender,
            user_context_resolver=self._build_resolver(),
        )

        self.assertFalse(outcome["handled"])
        self.assertEqual("system_fallback", outcome["reason"])
        self.assertEqual("text", outcome["channel"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertEqual(0, len(sender.card_payloads))
        self.assertEqual([], outcome["source_ids"])

    def test_handle_single_chat_payload_sends_card_for_application_question(self) -> None:
        sender = _FakeSender()
        outcome = handle_single_chat_payload(
            _make_payload(text="我要申请采购制度文件"),
            service=SingleChatService(),
            sender=sender,
            user_context_resolver=self._build_resolver(),
        )

        self.assertEqual("interactive_card", outcome["channel"])
        self.assertEqual("document_request", outcome["intent"])
        self.assertEqual(0, len(sender.text_messages))
        self.assertEqual(1, len(sender.card_payloads))
        self.assertEqual("application_draft", sender.card_payloads[0]["card_type"])

    def test_handle_single_chat_payload_raises_on_invalid_input(self) -> None:
        sender = _FakeSender()
        with self.assertRaises(ValueError):
            handle_single_chat_payload(
                {"conversation_type": "single"},
                service=SingleChatService(),
                sender=sender,
                user_context_resolver=self._build_resolver(),
            )


if __name__ == "__main__":
    unittest.main()
