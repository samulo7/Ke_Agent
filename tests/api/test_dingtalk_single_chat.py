from __future__ import annotations

import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
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


class _FakeClock:
    def __init__(self) -> None:
        self._current = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self._current

    def advance(self, *, seconds: int) -> None:
        self._current = self._current + timedelta(seconds=seconds)


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
    conversation_id: str = "conv-001",
    message_type: str = "text",
) -> dict[str, object]:
    return {
        "event_id": "evt-001",
        "conversation_id": conversation_id,
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
        self.assertIn("intent", body["llm_trace"])
        self.assertIn("content", body["llm_trace"])
        self.assertIn("orchestrator_shadow", body["llm_trace"])

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
        self.assertEqual("application_draft_collecting", body["reason"])
        self.assertEqual("document_request", body["intent"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("application_draft_collecting", body["reply"]["interactive_card"]["card_type"])
        self.assertEqual([], body["source_ids"])
        self.assertEqual("allow", body["permission_decision"])

    def test_document_request_can_reach_ready_card_with_followup(self) -> None:
        app = create_app(log_stream=StringIO(), user_context_resolver=_PermissionResolver())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要申请采购制度文件",
                sender_id="finance-user",
                conversation_id="conv-b14-api-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        body_first = first.json()
        self.assertEqual("application_draft_collecting", body_first["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="用途: 月度预算复盘；使用时间: 下周一",
                sender_id="finance-user",
                conversation_id="conv-b14-api-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()

        self.assertTrue(body_second["handled"])
        self.assertEqual("application_draft_ready", body_second["reason"])
        self.assertEqual("interactive_card", body_second["reply"]["channel"])
        self.assertEqual("application_draft_ready", body_second["reply"]["interactive_card"]["card_type"])
        self.assertEqual("document_request", body_second["intent"])
        self.assertEqual("人事行政", body_second["reply"]["interactive_card"]["draft_fields"]["suggested_approver"])
        self.assertIn("人事行政", body_second["reply"]["interactive_card"]["next_action"])

    def test_document_request_ready_state_is_final_without_confirmation(self) -> None:
        app = create_app(log_stream=StringIO(), user_context_resolver=_PermissionResolver())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要申请采购制度文件",
                sender_id="finance-user",
                conversation_id="conv-b14-api-confirm-1",
            ),
        )
        self.assertEqual(200, first.status_code)

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="用途: 用于内部采购流程参考；使用时间: 下周一",
                sender_id="finance-user",
                conversation_id="conv-b14-api-confirm-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        self.assertEqual("application_draft_ready", second.json()["reason"])
        self.assertEqual("interactive_card", second.json()["reply"]["channel"])

        third = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="hello there",
                sender_id="finance-user",
                conversation_id="conv-b14-api-confirm-1",
            ),
        )
        self.assertEqual(200, third.status_code)
        body_third = third.json()
        self.assertFalse(body_third["handled"])
        self.assertEqual("knowledge_no_hit", body_third["reason"])
        self.assertEqual("text", body_third["reply"]["channel"])

    def test_file_request_sent_response_contains_reply_sequences(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="帮我找一下定影器的采购合同",
                sender_id="user-file-api-1",
                conversation_id="conv-file-api-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        body_first = first.json()
        self.assertEqual("file_lookup_confirm_required", body_first["reason"])
        self.assertEqual("file_request", body_first["intent"])
        self.assertFalse(body_first["handled"])
        self.assertEqual(1, len(body_first["replies"]))
        self.assertEqual(body_first["reply"], body_first["replies"][0])
        self.assertEqual(1, len(body_first["dingtalk_payloads"]))
        self.assertEqual(body_first["dingtalk_payload"], body_first["dingtalk_payloads"][0])
        self.assertEqual("interactive_card", body_first["replies"][0]["channel"])
        card = body_first["replies"][0]["interactive_card"]
        self.assertEqual("file_request_confirmation", card["card_type"])
        request_id = card["request_id"]
        self.assertTrue(str(request_id).startswith("file-req-"))

        second = client.post(
            "/dingtalk/stream/events",
            json={
                "request_id": request_id,
                "approval_action": "确认申请",
                "approver_user_id": "user-file-api-1",
            },
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])
        self.assertEqual(1, len(body_second["replies"]))
        self.assertIn("申请已提交", body_second["replies"][0]["text"])
        self.assertNotIn("请求编号", body_second["replies"][0]["text"])

        third = client.post(
            "/dingtalk/stream/events",
            json={
                "request_id": request_id,
                "approval_action": "同意",
                "approver_user_id": "人事行政",
            },
        )
        self.assertEqual(200, third.status_code)
        body_third = third.json()
        self.assertTrue(body_third["handled"])
        self.assertEqual("file_approval_approved", body_third["reason"])
        self.assertEqual("file_request", body_third["intent"])
        self.assertEqual(3, len(body_third["replies"]))
        self.assertEqual(body_third["reply"], body_third["replies"][0])
        self.assertEqual(3, len(body_third["dingtalk_payloads"]))
        self.assertEqual(body_third["dingtalk_payload"], body_third["dingtalk_payloads"][0])
        self.assertIn("优先为您提供扫描件", body_third["replies"][0]["text"])
        self.assertIn("点击下载：[下载文件](", body_third["replies"][1]["text"])
        self.assertIn("复制链接：https://example.local/files/dingyingqi-contract-2024-scan", body_third["replies"][1]["text"])
        self.assertIn("文件已发送，请查收", body_third["replies"][2]["text"])

    def test_file_request_progress_query_returns_structured_status(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-status-1",
                conversation_id="conv-file-api-status-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        request_id = first.json()["reply"]["interactive_card"]["request_id"]

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="审批进度",
                sender_id="user-file-api-status-1",
                conversation_id="conv-file-api-status-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertEqual("file_lookup_confirm_required", body_second["reason"])
        self.assertIn("尚未提交审批", body_second["reply"]["text"])
        self.assertNotIn(request_id, body_second["reply"]["text"])
        self.assertNotIn("请求编号", body_second["reply"]["text"])

        client.post(
            "/dingtalk/stream/events",
            json={
                "request_id": request_id,
                "approval_action": "确认申请",
                "approver_user_id": "user-file-api-status-1",
            },
        )
        third = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="通过了吗",
                sender_id="user-file-api-status-1",
                conversation_id="conv-file-api-status-1",
            ),
        )
        self.assertEqual(200, third.status_code)
        body_third = third.json()
        self.assertEqual("file_lookup_pending_approval", body_third["reason"])
        self.assertIn("当前审批状态：待审批", body_third["reply"]["text"])
        self.assertNotIn(request_id, body_third["reply"]["text"])
        self.assertNotIn("请求编号", body_third["reply"]["text"])

    def test_file_request_button_id_callback_can_confirm_request(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-button-1",
                conversation_id="conv-file-api-button-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        request_id = first.json()["reply"]["interactive_card"]["request_id"]

        second = client.post(
            "/dingtalk/stream/events",
            json={
                "buttonId": f"confirm_request::{request_id}",
                "sender_id": "user-file-api-button-1",
            },
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])

    def test_file_request_action_only_callback_can_confirm_request_by_session(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-action-only-1",
                conversation_id="conv-file-api-action-only-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("file_lookup_confirm_required", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json={
                "approval_action": "确认申请",
                "sender_id": "user-file-api-action-only-1",
                "conversation_id": "conv-file-api-action-only-1",
                "conversation_type": "single",
            },
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])

    def test_file_request_plain_text_confirm_can_confirm_request_by_session(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-text-confirm-1",
                conversation_id="conv-file-api-text-confirm-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("file_lookup_confirm_required", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="确认申请",
                sender_id="user-file-api-text-confirm-1",
                conversation_id="conv-file-api-text-confirm-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])

    def test_file_request_card_callback_shape_can_confirm_request_by_session(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-card-1",
                conversation_id="conv-file-api-card-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("file_lookup_confirm_required", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json={
                "data": {
                    "type": "actionCallback",
                    "userId": "user-file-api-card-1",
                    "extension": "{\"openConversationId\":\"conv-file-api-card-1\"}",
                    "content": "{\"componentType\":\"button\",\"componentId\":\"confirm_request\"}",
                }
            },
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])

    def test_file_request_official_action_ids_callback_shape_can_confirm_request(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同文件",
                sender_id="user-file-api-official-1",
                conversation_id="conv-file-api-official-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("file_lookup_confirm_required", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json={
                "data": {
                    "corpId": "ding-corp",
                    "type": "actionCallback",
                    "userId": "user-file-api-official-1",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{\"local_input\":\"submit\"}}}",
                    "outTrackId": "track-official-1",
                }
            },
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertTrue(body_second["handled"])
        self.assertEqual("file_lookup_pending_approval", body_second["reason"])
        self.assertEqual("pending", body_second["approval_status"])

    def test_file_request_not_found_callback_returns_user_facing_hint(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json={
                "data": {
                    "type": "actionCallback",
                    "userId": "unknown-user",
                    "extension": "{\"openConversationId\":\"unknown-conv\"}",
                    "content": "{\"componentType\":\"button\",\"componentId\":\"confirm_request\"}",
                }
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["handled"])
        self.assertEqual("file_approval_not_found", body["reason"])
        self.assertIn("未定位到待处理申请", body["reply"]["text"])

    def test_document_request_timeout_returns_fallback_reason(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(document_request_orchestrator=DocumentRequestDraftOrchestrator(now_provider=clock.now))
        app = create_app(log_stream=StringIO(), single_chat_service=service)
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要申请采购制度文件",
                conversation_id="conv-b14-api-2",
                sender_id="user-b14-api-2",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("application_draft_collecting", first.json()["reason"])

        clock.advance(seconds=301)
        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="用途: 项目预算",
                conversation_id="conv-b14-api-2",
                sender_id="user-b14-api-2",
            ),
        )
        self.assertEqual(200, second.status_code)
        body = second.json()
        self.assertFalse(body["handled"])
        self.assertEqual("application_draft_timeout", body["reason"])

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
