from __future__ import annotations

import sqlite3
import unittest
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


class _PermissionResolver:
    def resolve(self, message):  # type: ignore[no-untyped-def]
        sender = (message.sender_staff_id or message.sender_id or "unknown").strip()
        if sender == "finance-user":
            return UserContext(
                user_id="finance-user",
                user_name="Finance",
                dept_id="finance",
                dept_name="Finance",
                identity_source="openapi",
                is_degraded=False,
                resolved_at="2026-03-27T00:00:00+00:00",
            )
        return UserContext(
            user_id=sender,
            user_name="Sales",
            dept_id="sales",
            dept_name="Sales",
            identity_source="openapi",
            is_degraded=False,
            resolved_at="2026-03-27T00:00:00+00:00",
        )


def build_permission_single_chat_service() -> tuple[SingleChatService, sqlite3.Connection]:
    connection = sqlite3.connect(":memory:", check_same_thread=False)
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

    def test_permission_restricted_summary_only_returns_stable_reason(self) -> None:
        service, connection = build_permission_single_chat_service()
        try:
            app = create_app(
                log_stream=StringIO(),
                single_chat_service=service,
                user_context_resolver=_PermissionResolver(),
            )
            client = TestClient(app)

            response = client.post(
                "/dingtalk/stream/events",
                json=make_stream_payload(text="财务制度细则规则是什么", sender_id="sales-user"),
            )
            self.assertEqual(200, response.status_code)
            body = response.json()

            self.assertFalse(body["handled"])
            self.assertEqual("permission_restricted", body["reason"])
            self.assertEqual("summary_only", body["permission_decision"])
            self.assertIn("不可直接查看", body["reply"]["text"])
            self.assertIn("申请路径", body["reply"]["text"])
        finally:
            connection.close()

    def test_permission_restricted_deny_hides_sensitive_summary(self) -> None:
        service, connection = build_permission_single_chat_service()
        try:
            app = create_app(
                log_stream=StringIO(),
                single_chat_service=service,
                user_context_resolver=_PermissionResolver(),
            )
            client = TestClient(app)

            response = client.post(
                "/dingtalk/stream/events",
                json=make_stream_payload(text="高管预算审批规则是什么", sender_id="sales-user"),
            )
            self.assertEqual(200, response.status_code)
            body = response.json()

            self.assertFalse(body["handled"])
            self.assertEqual("permission_restricted", body["reason"])
            self.assertEqual("deny", body["permission_decision"])
            self.assertIn("申请路径", body["reply"]["text"])
            self.assertNotIn("高管预算审批阈值与审批链路说明", body["reply"]["text"])
        finally:
            connection.close()

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
