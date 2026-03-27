from __future__ import annotations

import sqlite3
import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.user_context import UserContext
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


def make_message(
    *,
    conversation_type: str = "single",
    message_type: str = "text",
    text: str = "hello",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-1",
        conversation_id="conv-1",
        conversation_type=conversation_type,  # type: ignore[arg-type]
        sender_id="user-1",
        message_type=message_type,
        text=text,
    )


def make_user_context(*, user_id: str, dept_id: str) -> UserContext:
    return UserContext(
        user_id=user_id,
        user_name=user_id,
        dept_id=dept_id,
        dept_name=dept_id,
        identity_source="openapi",
        is_degraded=False,
        resolved_at="2026-03-27T00:00:00+00:00",
    )


def _build_permission_aware_service() -> tuple[SingleChatService, sqlite3.Connection]:
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


class SingleChatServiceTests(unittest.TestCase):
    def test_returns_knowledge_text_answer_for_policy_question(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="宴请标准是什么"))
        self.assertTrue(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("policy_process", result.intent)
        self.assertEqual("knowledge_answer", result.reason)
        self.assertIn("doc-policy-banquet-2026-01", result.source_ids)
        self.assertEqual("allow", result.permission_decision)
        self.assertTrue(result.knowledge_version)
        self.assertTrue(result.answered_at)
        self.assertGreaterEqual(len(result.citations), 1)

    def test_returns_no_hit_when_knowledge_is_missing(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="hello there"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("other", result.intent)
        self.assertEqual("knowledge_no_hit", result.reason)
        self.assertEqual(0, len(result.source_ids))

    def test_returns_clarification_for_ambiguous_question(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="这个怎么弄"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("ambiguous_question", result.reason)
        self.assertIn("仅追问一次", result.reply.text or "")
        self.assertIn("请补充", result.reply.text or "")

    def test_returns_low_confidence_fallback_for_unclear_scope(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我想问个事儿，帮我处理一下"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("low_confidence_fallback", result.reason)
        self.assertIn("无法准确判断", result.reply.text or "")

    def test_returns_system_fallback_when_answer_service_raises(self) -> None:
        service = SingleChatService(knowledge_answer_service=_RaisingKnowledgeAnswerService())
        result = service.handle(make_message(text="宴请标准是什么"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("system_fallback", result.reason)
        self.assertIn("稍后再试", result.reply.text or "")
        self.assertIn("联系", result.reply.text or "")

    def test_returns_flow_guidance_card_for_leave(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="请假流程入口在哪"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])
        self.assertEqual("leave", result.intent)

    def test_returns_flow_guidance_card_for_reimbursement(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="出差报销怎么弄"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])
        self.assertEqual("reimbursement", result.intent)

    def test_returns_application_draft_card(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我要申请项目资料"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("application_draft", result.reply.interactive_card["card_type"])
        self.assertEqual("document_request", result.intent)

    def test_rejects_non_single_chat(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(conversation_type="group"))
        self.assertFalse(result.handled)
        self.assertEqual("non_single_chat", result.reason)
        self.assertEqual("other", result.intent)

    def test_permission_restricted_reason_summary_only_when_not_authorized(self) -> None:
        service, connection = _build_permission_aware_service()
        try:
            result = service.handle(
                make_message(text="财务制度细则规则是什么"),
                user_context=make_user_context(user_id="u-sales", dept_id="sales"),
            )
        finally:
            connection.close()

        self.assertFalse(result.handled)
        self.assertEqual("permission_restricted", result.reason)
        self.assertEqual("summary_only", result.permission_decision)
        self.assertIn("不可直接查看", result.reply.text or "")

    def test_permission_restricted_reason_deny_for_sensitive_doc(self) -> None:
        service, connection = _build_permission_aware_service()
        try:
            result = service.handle(
                make_message(text="高管预算审批规则是什么"),
                user_context=make_user_context(user_id="u-sales", dept_id="sales"),
            )
        finally:
            connection.close()

        self.assertFalse(result.handled)
        self.assertEqual("permission_restricted", result.reason)
        self.assertEqual("deny", result.permission_decision)
        self.assertIn("申请路径", result.reply.text or "")


if __name__ == "__main__":
    unittest.main()
