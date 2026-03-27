from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from typing import Any, Mapping

from app.integrations.dingtalk.stream_runtime import (
    DEFAULT_STREAM_ENDPOINT,
    StreamRuntimeError,
    _extract_card_text_lines,
    handle_single_chat_payload,
    load_stream_credentials,
)
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
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


class _FakeClock:
    def __init__(self) -> None:
        self._current = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, *, seconds: int) -> None:
        self._current = self._current + timedelta(seconds=seconds)


def _make_payload(
    *,
    text: str,
    conversation_type: str = "single",
    conversation_id: str = "conv-a05-001",
    sender_id: str = "user-a05-001",
    message_type: str = "text",
) -> dict[str, Any]:
    return {
        "event_id": "evt-a05-001",
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
        "sender_id": sender_id,
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

    def test_extract_card_lines_uses_chinese_labels_for_draft_fields(self) -> None:
        title, lines = _extract_card_text_lines(
            {
                "title": "文档申请草稿",
                "draft_fields": {
                    "applicant_name": "Alice",
                    "department": "Finance",
                    "requested_item": "采购制度文件",
                },
            }
        )

        self.assertEqual("文档申请草稿", title)
        self.assertIn("申请人姓名: Alice", lines)
        self.assertIn("所属部门: Finance", lines)
        self.assertIn("申请资料名称: 采购制度文件", lines)

    def test_extract_card_lines_marks_missing_and_actions(self) -> None:
        _, lines = _extract_card_text_lines(
            {
                "title": "申请信息收集 · 采购制度文件",
                "draft_fields": {
                    "applicant_name": "Alice",
                    "request_purpose": "采购",
                    "expected_use_time": "",
                },
                "field_status": {
                    "applicant_name": "filled",
                    "request_purpose": "needs_detail",
                    "expected_use_time": "missing",
                },
                "actions": ["确认提交", "取消"],
            }
        )

        self.assertIn("申请人姓名: Alice", lines)
        self.assertIn("【需细化】申请用途: 采购", lines)
        self.assertIn("【待补充】期望使用时间: ____", lines)
        self.assertIn("可操作：确认提交 / 取消", lines)

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
        self.assertEqual("application_draft_collecting", outcome["reason"])
        self.assertEqual(0, len(sender.text_messages))
        self.assertEqual(1, len(sender.card_payloads))
        self.assertEqual("application_draft_collecting", sender.card_payloads[0]["card_type"])

    def test_handle_single_chat_payload_document_request_reaches_ready_state(self) -> None:
        sender = _FakeSender()
        resolver = _FakeResolver(
            UserContext(
                user_id="alice",
                user_name="Alice",
                dept_id="finance",
                dept_name="Finance",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-27T00:00:00+00:00",
            )
        )
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要申请采购制度文件",
                conversation_id="conv-b14-stream-1",
                sender_id="user-b14-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("application_draft_collecting", first["reason"])

        second = handle_single_chat_payload(
            _make_payload(
                text="用途: 月度预算复盘；使用时间: 下周一",
                conversation_id="conv-b14-stream-1",
                sender_id="user-b14-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("application_draft_ready", second["reason"])
        self.assertEqual("interactive_card", second["channel"])
        self.assertEqual("application_draft_ready", sender.card_payloads[-1]["card_type"])
        self.assertEqual("人事行政", sender.card_payloads[-1]["draft_fields"]["suggested_approver"])
        self.assertIn("人事行政", sender.card_payloads[-1].get("next_action", ""))

    def test_handle_single_chat_payload_document_request_timeout(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(document_request_orchestrator=DocumentRequestDraftOrchestrator(now_provider=clock.now))
        sender = _FakeSender()
        resolver = self._build_resolver()

        handle_single_chat_payload(
            _make_payload(
                text="我要申请采购制度文件",
                conversation_id="conv-b14-stream-2",
                sender_id="user-b14-stream-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        clock.advance(seconds=301)
        outcome = handle_single_chat_payload(
            _make_payload(
                text="用途: 项目预算",
                conversation_id="conv-b14-stream-2",
                sender_id="user-b14-stream-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertFalse(outcome["handled"])
        self.assertEqual("application_draft_timeout", outcome["reason"])
        self.assertEqual("text", outcome["channel"])

    def test_handle_single_chat_payload_file_request_sends_sequence_in_order(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="帮我找一下定影器的采购合同",
                conversation_id="conv-file-stream-1",
                sender_id="user-file-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_collecting", first["reason"])
        self.assertEqual("file_request", first["intent"])
        self.assertEqual(1, len(sender.text_messages))

        second = handle_single_chat_payload(
            _make_payload(
                text="扫描版",
                conversation_id="conv-file-stream-1",
                sender_id="user-file-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_sent", second["reason"])
        self.assertEqual("file_request", second["intent"])
        self.assertEqual(4, len(sender.text_messages))
        self.assertIn("优先为您提供扫描版", sender.text_messages[1])
        self.assertIn("已在文件库找到匹配文件", sender.text_messages[2])
        self.assertIn("文件已发送，请查收", sender.text_messages[3])
        self.assertEqual([], sender.card_payloads)

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
