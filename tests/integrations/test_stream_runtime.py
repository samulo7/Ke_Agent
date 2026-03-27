from __future__ import annotations

import sqlite3
import unittest
from typing import Any, Mapping

from app.integrations.dingtalk.stream_runtime import (
    DEFAULT_STREAM_ENDPOINT,
    StreamRuntimeError,
    handle_single_chat_payload,
    load_stream_credentials,
)
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


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


def _build_permission_service() -> tuple[SingleChatService, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:")
    connection.row_factory = sqlite3.Row
    bootstrap_sqlite_schema(connection)
    connection.executemany(
        """
        INSERT INTO knowledge_docs (
            doc_id,
            source_type,
            title,
            summary,
            applicability,
            next_step,
            source_uri,
            updated_at,
            status,
            owner,
            category,
            version_tag,
            keywords_csv,
            intents_csv,
            permission_scope,
            permitted_depts_csv
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            (
                "doc-public-policy",
                "document",
                "报销流程入口说明",
                "公共流程说明，员工可查看报销入口和步骤。",
                "适用于全员",
                "打开钉钉审批-报销入口",
                "https://example.local/docs/public-policy",
                "2026-03-20",
                "active",
                "hr-team",
                "policy",
                "v1",
                "报销,流程,入口",
                "policy_process",
                "public",
                "",
            ),
            (
                "doc-finance-policy",
                "document",
                "财务报销制度细则",
                "含票据规范与财务专项口径。",
                "适用于财务部门",
                "联系财务专员确认口径",
                "https://example.local/docs/finance-policy",
                "2026-03-20",
                "active",
                "finance-team",
                "policy",
                "v3",
                "财务,发票,口径",
                "policy_process",
                "department",
                "finance",
            ),
            (
                "doc-sensitive-budget",
                "document",
                "高管预算审批规则",
                "高管预算审批阈值与审批链路说明。",
                "适用于财务预算审批岗",
                "通过钉钉“预算审批”流程提交并抄送财务负责人",
                "https://example.local/docs/sensitive-budget",
                "2026-03-21",
                "active",
                "finance-owner",
                "budget",
                "v2",
                "高管,预算,审批,敏感",
                "policy_process",
                "sensitive",
                "finance",
            ),
        ),
    )
    connection.executemany(
        """
        INSERT INTO doc_chunks (
            chunk_id,
            doc_id,
            chunk_index,
            chunk_text,
            chunk_vector
        ) VALUES (?, ?, ?, ?, ?)
        """,
        (
            ("chunk-public-1", "doc-public-policy", 0, "公共报销流程入口说明", "[0.1,0.2]"),
            ("chunk-finance-1", "doc-finance-policy", 0, "财务制度细则中的发票口径", "[0.4,0.6]"),
            ("chunk-sensitive-1", "doc-sensitive-budget", 0, "敏感预算审批流程说明", "[0.8,0.6]"),
        ),
    )
    connection.commit()

    repository = SQLKnowledgeRepository(connection=connection, version="b13-sql-v1")
    answer_service = KnowledgeAnswerService(
        retriever=KnowledgeRetriever(repository=repository, top_k=5),
        repository=repository,
        tone_resolver=ToneResolver(default_tone="neutral"),
    )
    return SingleChatService(knowledge_answer_service=answer_service), connection


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

    def test_handle_single_chat_payload_permission_restricted_summary_only(self) -> None:
        service, connection = _build_permission_service()
        try:
            sender = _FakeSender()
            outcome = handle_single_chat_payload(
                _make_payload(text="财务制度细则规则是什么"),
                service=service,
                sender=sender,
                user_context_resolver=_FakeResolver(
                    UserContext(
                        user_id="sales-user",
                        user_name="Sales",
                        dept_id="sales",
                        dept_name="Sales",
                        identity_source="openapi",
                        is_degraded=False,
                        resolved_at="2026-03-27T00:00:00+00:00",
                    )
                ),
            )
        finally:
            connection.close()

        self.assertFalse(outcome["handled"])
        self.assertEqual("permission_restricted", outcome["reason"])
        self.assertEqual("summary_only", outcome["permission_decision"])
        self.assertEqual("text", outcome["channel"])
        self.assertEqual(1, len(sender.text_messages))

    def test_handle_single_chat_payload_permission_restricted_deny(self) -> None:
        service, connection = _build_permission_service()
        try:
            sender = _FakeSender()
            outcome = handle_single_chat_payload(
                _make_payload(text="高管预算审批规则是什么"),
                service=service,
                sender=sender,
                user_context_resolver=_FakeResolver(
                    UserContext(
                        user_id="sales-user",
                        user_name="Sales",
                        dept_id="sales",
                        dept_name="Sales",
                        identity_source="openapi",
                        is_degraded=False,
                        resolved_at="2026-03-27T00:00:00+00:00",
                    )
                ),
            )
        finally:
            connection.close()

        self.assertFalse(outcome["handled"])
        self.assertEqual("permission_restricted", outcome["reason"])
        self.assertEqual("deny", outcome["permission_decision"])
        self.assertEqual("text", outcome["channel"])
        self.assertEqual(1, len(sender.text_messages))

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
