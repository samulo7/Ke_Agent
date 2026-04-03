from __future__ import annotations

import json
import re
import sqlite3
import unittest
from datetime import datetime, timedelta, timezone
from io import StringIO

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.file_asset import FileAsset, FileSearchCandidate, FileSearchResult
from app.schemas.reimbursement import ReimbursementApprovalResult, ReimbursementAttachmentProcessResult, TravelApplication
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
from app.services.file_request import FileRequestService
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.leave_request import LeaveApprovalResult, LeaveRequestOrchestrator
from app.services.reimbursement_request import ReimbursementRequestOrchestrator
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


class _StubLeaveApprovalCreator:
    def __init__(self, result: LeaveApprovalResult) -> None:
        self._result = result

    def submit(self, submission):  # type: ignore[no-untyped-def]
        self.submission = submission
        return self._result


class _StubTravelApplicationProvider:
    def __init__(self, items: list[TravelApplication] | None = None) -> None:
        self._items = items or [
            TravelApplication(process_instance_id="trip-1", start_date="2026-03-15", destination="北京", purpose="云亨售后"),
            TravelApplication(process_instance_id="trip-2", start_date="2026-03-28", destination="上海", purpose=""),
        ]

    def list_recent_approved(self, *, originator_user_id: str, lookback_days: int, now: datetime) -> list[TravelApplication]:
        del originator_user_id, lookback_days, now
        return list(self._items)


class _StubReimbursementAttachmentProcessor:
    def __init__(self, result: ReimbursementAttachmentProcessResult | None = None) -> None:
        self._result = result or ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department="总经办",
            amount="106",
            attachment_media_id="media-pdf-1",
        )

    def process(self, *, message, conversation_id: str, sender_id: str):  # type: ignore[no-untyped-def]
        del message, conversation_id, sender_id
        return self._result


class _StubReimbursementApprovalCreator:
    def __init__(self, result: ReimbursementApprovalResult) -> None:
        self._result = result

    def submit(self, submission):  # type: ignore[no-untyped-def]
        self.submission = submission
        return self._result


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


class _MultiHitFileRepository:
    def __init__(self) -> None:
        self._assets = (
            FileAsset(
                file_id="file-1",
                contract_key="dingyingqi_contract",
                title="定影器采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/dingyingqi-contract-2024-scan",
                tags=("采购", "合同", "定影器", "2024"),
                status="active",
                updated_at="2026-03-30",
            ),
            FileAsset(
                file_id="file-2",
                contract_key="printer_contract",
                title="打印机采购合同-2023版",
                variant="scan",
                file_url="https://example.local/files/printer-contract-2023-scan",
                tags=("采购", "合同", "打印机", "2023"),
                status="active",
                updated_at="2026-03-30",
            ),
            FileAsset(
                file_id="file-3",
                contract_key="copier_contract",
                title="复印机采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/copier-contract-2024-scan",
                tags=("采购", "合同", "复印机", "2024"),
                status="active",
                updated_at="2026-03-30",
            ),
        )

    def search(self, *, query_text: str, variant: str, requester_context=None):  # type: ignore[no-untyped-def]
        del requester_context
        if variant != "scan":
            return FileSearchResult.no_hit()
        if "采购合同" not in query_text:
            return FileSearchResult.no_hit()
        return FileSearchResult(
            matched=True,
            match_score=0.91,
            asset=self._assets[0],
            candidates=tuple(
                FileSearchCandidate(asset=asset, match_score=0.9 - index * 0.01)
                for index, asset in enumerate(self._assets)
            ),
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
    conversation_id: str = "conv-001",
    message_type: str = "text",
    file_name: str = "",
    file_download_url: str = "",
    file_media_id: str = "",
    file_content_base64: str = "",
) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": "evt-001",
        "conversation_id": conversation_id,
        "conversation_type": conversation_type,
        "sender_id": sender_id,
        "message_type": message_type,
        "text": text,
    }
    if message_type == "file":
        payload["content"] = {
            "fileName": file_name,
            "downloadUrl": file_download_url,
            "mediaId": file_media_id,
            "contentBase64": file_content_base64,
        }
    return payload


def make_leave_button_callback_payload(
    *,
    action_id: str,
    sender_id: str,
    conversation_id: str,
) -> dict[str, object]:
    return {
        "data": {
            "type": "actionCallback",
            "userId": sender_id,
            "extension": json.dumps({"openConversationId": conversation_id}, ensure_ascii=False),
            "content": json.dumps(
                {"componentType": "button", "componentId": action_id},
                ensure_ascii=False,
            ),
        }
    }


def make_reimbursement_button_callback_payload(
    *,
    action_id: str,
    sender_id: str,
    conversation_id: str,
) -> dict[str, object]:
    return {
        "data": {
            "type": "actionCallback",
            "userId": sender_id,
            "extension": json.dumps({"openConversationId": conversation_id}, ensure_ascii=False),
            "content": json.dumps(
                {"componentType": "button", "componentId": action_id},
                ensure_ascii=False,
            ),
            "outTrackId": "reimbursement-confirm-test-1",
        }
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

    def test_flow_query_returns_text_guidance(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="出差报销怎么弄"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("flow_guidance_text", body["reason"])
        self.assertEqual("reimbursement", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("发票", body["reply"]["text"])
        self.assertIn("行程单", body["reply"]["text"])
        self.assertIn("30天", body["reply"]["text"])
        self.assertIn("金额", body["reply"]["text"])
        self.assertNotIn("办理入口：", body["reply"]["text"])
        self.assertNotIn("准备材料：", body["reply"]["text"])
        self.assertNotIn("流程路径：", body["reply"]["text"])
        self.assertNotIn("下一步：", body["reply"]["text"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])
        self.assertEqual([], body["source_ids"])

    def test_reimbursement_travel_workflow_happy_path_with_callback_submit(self) -> None:
        clock = _FakeClock()
        approval_creator = _StubReimbursementApprovalCreator(
            ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id="proc-rmb-api-1")
        )
        service = SingleChatService(
            reimbursement_request_orchestrator=ReimbursementRequestOrchestrator(
                travel_application_provider=_StubTravelApplicationProvider(),
                attachment_processor=_StubReimbursementAttachmentProcessor(),
                approval_creator=approval_creator,
                now_provider=clock.now,
            )
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要报销差旅费",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("reimbursement_travel_collecting_trip", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="1",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        self.assertEqual("reimbursement_travel_collecting_attachment", second.json()["reason"])

        third = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="",
                message_type="file",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-1",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
        )
        self.assertEqual(200, third.status_code)
        self.assertEqual("reimbursement_travel_collecting_company", third.json()["reason"])
        self.assertIn("部门：总经办", third.json()["reply"]["text"])

        fourth = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="SY",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-1",
            ),
        )
        self.assertEqual(200, fourth.status_code)
        body_fourth = fourth.json()
        self.assertEqual("reimbursement_travel_ready", body_fourth["reason"])
        self.assertEqual("interactive_card", body_fourth["reply"]["channel"])
        self.assertEqual("reimbursement_request_ready", body_fourth["reply"]["interactive_card"]["card_type"])

        fifth = client.post(
            "/dingtalk/stream/events",
            json=make_reimbursement_button_callback_payload(
                action_id="reimbursement_confirm_submit",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-1",
            ),
        )
        self.assertEqual(200, fifth.status_code)
        body_fifth = fifth.json()
        self.assertEqual("reimbursement_travel_submitted", body_fifth["reason"])
        self.assertEqual("reimbursement", body_fifth["intent"])
        self.assertEqual("submitted", body_fifth["reimbursement_status"])
        self.assertEqual("reimbursement_confirm_submit", body_fifth["reimbursement_action"])
        self.assertEqual("text", body_fifth["dingtalk_payload"]["msgtype"])
        self.assertIn("已提交，审批中", body_fifth["reply"]["text"])
        self.assertEqual("trip-1", approval_creator.submission.travel_process_instance_id)
        self.assertEqual("YXQY", approval_creator.submission.fixed_company)
        self.assertEqual("总经办", approval_creator.submission.department)
        self.assertEqual("SY", approval_creator.submission.cost_company)
        self.assertEqual("106", approval_creator.submission.amount)
        self.assertEqual("否", approval_creator.submission.over_five_thousand)
        self.assertEqual("media-pdf-1", approval_creator.submission.attachment_media_id)
        self.assertEqual("allow", body_fifth["permission_decision"])

    def test_reimbursement_amount_conflict_requires_choice_before_submit(self) -> None:
        clock = _FakeClock()
        approval_creator = _StubReimbursementApprovalCreator(
            ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id="proc-rmb-api-2")
        )
        attachment_result = ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department="总经办",
            amount="106",
            attachment_media_id="media-pdf-1",
            table_amount="106",
            uppercase_amount_raw="壹佰壹拾元整",
            uppercase_amount_numeric="110",
            amount_conflict=True,
            amount_conflict_note="合计金额与大写金额不一致，请确认提交金额来源。",
            amount_source="table_conflict",
            amount_source_note="检测到金额冲突，待人工确认",
        )
        service = SingleChatService(
            reimbursement_request_orchestrator=ReimbursementRequestOrchestrator(
                travel_application_provider=_StubTravelApplicationProvider(),
                attachment_processor=_StubReimbursementAttachmentProcessor(result=attachment_result),
                approval_creator=approval_creator,
                now_provider=clock.now,
            )
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要报销差旅费",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("reimbursement_travel_collecting_trip", first.json()["reason"])

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="1",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
            ),
        )
        self.assertEqual(200, second.status_code)
        self.assertEqual("reimbursement_travel_collecting_attachment", second.json()["reason"])

        third = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="",
                message_type="file",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
        )
        self.assertEqual(200, third.status_code)
        self.assertEqual("reimbursement_travel_collecting_company", third.json()["reason"])

        fourth = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="SY",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
            ),
        )
        self.assertEqual(200, fourth.status_code)
        body_fourth = fourth.json()
        self.assertEqual("reimbursement_travel_amount_conflict_confirmation", body_fourth["reason"])
        self.assertEqual("interactive_card", body_fourth["reply"]["channel"])

        fifth = client.post(
            "/dingtalk/stream/events",
            json=make_reimbursement_button_callback_payload(
                action_id="reimbursement_amount_use_uppercase",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
            ),
        )
        self.assertEqual(200, fifth.status_code)
        body_fifth = fifth.json()
        self.assertEqual("reimbursement_travel_ready", body_fifth["reason"])
        self.assertEqual("reimbursement_amount_use_uppercase", body_fifth["reimbursement_action"])
        self.assertEqual("pending", body_fifth["reimbursement_status"])

        sixth = client.post(
            "/dingtalk/stream/events",
            json=make_reimbursement_button_callback_payload(
                action_id="reimbursement_confirm_submit",
                sender_id="finance-user",
                conversation_id="conv-rmb-api-2",
            ),
        )
        self.assertEqual(200, sixth.status_code)
        body_sixth = sixth.json()
        self.assertEqual("reimbursement_travel_submitted", body_sixth["reason"])
        self.assertEqual("submitted", body_sixth["reimbursement_status"])
        self.assertEqual("110", approval_creator.submission.amount)

    def test_leave_info_query_returns_flow_guidance_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="请假流程入口在哪"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertTrue(body["handled"])
        self.assertEqual("flow_guidance_card", body["reason"])
        self.assertEqual("leave", body["intent"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("flow_guidance", body["reply"]["interactive_card"]["card_type"])
        self.assertIn("请假", body["reply"]["interactive_card"]["title"])
        self.assertIn("OA审批", body["reply"]["interactive_card"]["entry_point"])
        self.assertIn("未提前申请（需提前1天）", body["reply"]["interactive_card"]["common_errors"])
        self.assertIn("假种选择错误", body["reply"]["interactive_card"]["common_errors"])
        self.assertEqual("interactive_card", body["dingtalk_payload"]["msgtype"])

    def test_plain_leave_word_starts_leave_workflow(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="请假"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertEqual("leave", body["intent"])
        self.assertEqual("leave_workflow_collecting", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("开始和结束时间", body["reply"]["text"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])

    def test_natural_leave_phrase_with_duration_starts_leave_workflow(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="我要请一天的假，4月7号"))
        self.assertEqual(200, response.status_code)
        body = response.json()

        self.assertEqual("leave", body["intent"])
        self.assertEqual("leave_workflow_collecting", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("请假类型", body["reply"]["text"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])

    def test_document_request_returns_application_draft_collecting(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post("/dingtalk/stream/events", json=make_stream_payload(text="我要申请采购制度文件权限"))
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
                text="我要申请采购制度文件权限",
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
                text="我要申请采购制度文件权限",
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

    def test_file_request_apply_language_still_hits_confirmation_card(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要申请定影器采购合同",
                sender_id="user-file-api-apply-1",
                conversation_id="conv-file-api-apply-1",
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("file_request", body["intent"])
        self.assertEqual("file_lookup_confirm_required", body["reason"])
        self.assertEqual("interactive_card", body["reply"]["channel"])
        self.assertEqual("file_request_confirmation", body["reply"]["interactive_card"]["card_type"])

    def test_file_request_multi_match_uses_default_repository_data(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同",
                sender_id="user-file-api-default-multi-1",
                conversation_id="conv-file-api-default-multi-1",
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["handled"])
        self.assertEqual("file_lookup_multiple_matches", body["reason"])
        self.assertEqual("file_request", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("找到多个匹配文件", body["reply"]["text"])
        self.assertIn("定影器采购合同-2024版", body["reply"]["text"])
        self.assertIn("打印机采购合同-2023版", body["reply"]["text"])
        self.assertIn("复印机采购合同-2024版", body["reply"]["text"])
        self.assertIsNone(body["reply"]["interactive_card"])

    def test_file_request_multi_match_returns_text_selection_without_confirmation_card(self) -> None:
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_MultiHitFileRepository()))
        app = create_app(log_stream=StringIO(), single_chat_service=service)
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同",
                sender_id="user-file-api-multi-1",
                conversation_id="conv-file-api-multi-1",
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertFalse(body["handled"])
        self.assertEqual("file_lookup_multiple_matches", body["reason"])
        self.assertEqual("file_request", body["intent"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("找到多个匹配文件", body["reply"]["text"])
        self.assertIsNone(body["reply"]["interactive_card"])

    def test_file_request_multi_match_selection_timeout_then_leave_routes_normally(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(
            file_request_service=FileRequestService(
                file_repository=_MultiHitFileRepository(),
                now_provider=clock.now,
            )
        )
        app = create_app(log_stream=StringIO(), single_chat_service=service)
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要采购合同",
                sender_id="user-file-api-multi-2",
                conversation_id="conv-file-api-multi-2",
            ),
        )
        self.assertEqual(200, first.status_code)
        self.assertEqual("file_lookup_multiple_matches", first.json()["reason"])

        clock.advance(seconds=601)
        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="user-file-api-multi-2",
                conversation_id="conv-file-api-multi-2",
            ),
        )
        self.assertEqual(200, second.status_code)
        body = second.json()
        self.assertEqual("leave", body["intent"])
        self.assertEqual("leave_workflow_collecting", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("开始和结束时间", body["reply"]["text"])

    def test_leave_workflow_ready_card_requires_button_confirmation(self) -> None:
        app = create_app(log_stream=StringIO(), user_context_resolver=_PermissionResolver())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-1",
            ),
        )
        self.assertEqual(200, first.status_code)
        body_first = first.json()
        self.assertEqual("leave_workflow_collecting", body_first["reason"])
        self.assertEqual("text", body_first["reply"]["channel"])
        self.assertIn("开始和结束时间", body_first["reply"]["text"])
        self.assertEqual("text", body_first["dingtalk_payload"]["msgtype"])

        second = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="年假 2026-04-01 到 2026-04-02",
                sender_id="finance-user",
                conversation_id="conv-leave-api-1",
            ),
        )
        self.assertEqual(200, second.status_code)
        body_second = second.json()
        self.assertEqual("leave_workflow_ready", body_second["reason"])
        self.assertEqual("leave", body_second["intent"])
        self.assertEqual("leave_request_ready", body_second["reply"]["interactive_card"]["card_type"])
        self.assertNotIn("draft_fields", body_second["reply"]["interactive_card"])
        self.assertEqual("leave_confirm_submit", body_second["reply"]["interactive_card"]["actions"][0]["action"])
        self.assertEqual("leave_cancel_submit", body_second["reply"]["interactive_card"]["actions"][1]["action"])

        third = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="确认",
                sender_id="finance-user",
                conversation_id="conv-leave-api-1",
            ),
        )
        self.assertEqual(200, third.status_code)
        body_third = third.json()
        self.assertEqual("leave_workflow_waiting_button_action", body_third["reason"])
        self.assertEqual("text", body_third["reply"]["channel"])
        self.assertEqual("text", body_third["dingtalk_payload"]["msgtype"])
        self.assertIn("点击卡片按钮", body_third["reply"]["text"])

    def test_leave_workflow_button_callback_submits_when_creator_succeeds(self) -> None:
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-1",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="年假 2026-04-01 到 2026-04-02",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-1",
            ),
        )
        third = client.post(
            "/dingtalk/stream/events",
            json=make_leave_button_callback_payload(
                action_id="leave_confirm_submit",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-1",
            ),
        )

        self.assertEqual(200, third.status_code)
        body = third.json()
        self.assertEqual("leave_workflow_submitted", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])
        self.assertIn("已帮你发起", body["reply"]["text"])
        self.assertEqual("finance-user", creator.submission.originator_user_id)

    def test_leave_workflow_button_callback_accepts_confirm_request_alias_with_leave_outtrackid(self) -> None:
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-alias-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-alias-1",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="年假 2026-04-01 到 2026-04-02",
                sender_id="finance-user",
                conversation_id="conv-leave-api-alias-1",
            ),
        )
        third = client.post(
            "/dingtalk/stream/events",
            json={
                "data": {
                    "type": "actionCallback",
                    "userId": "finance-user",
                    "extension": "{\"openConversationId\":\"conv-leave-api-alias-1\"}",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{\"local_input\":\"submit\"}}}",
                    "outTrackId": "leave-confirm-alias-1",
                }
            },
        )

        self.assertEqual(200, third.status_code)
        body = third.json()
        self.assertEqual("leave_workflow_submitted", body["reason"])
        self.assertEqual("leave_confirm_submit", body["leave_action"])
        self.assertEqual("submitted", body["leave_status"])
        self.assertIn("已帮你发起", body["reply"]["text"])

    def test_leave_workflow_button_callback_uses_space_id_when_open_conversation_id_missing(self) -> None:
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-space-id-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-spaceid-1",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="明天后天年假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-spaceid-1",
            ),
        )
        third = client.post(
            "/dingtalk/stream/events",
            json={
                "data": {
                    "type": "actionCallback",
                    "userId": "finance-user",
                    "extension": "{}",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{}}}",
                    "spaceId": "conv-leave-api-spaceid-1",
                    "outTrackId": "leave-confirm-spaceid-1",
                    "value": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{}}}",
                }
            },
        )

        self.assertEqual(200, third.status_code)
        body = third.json()
        self.assertEqual("leave_workflow_submitted", body["reason"])
        self.assertEqual("leave_confirm_submit", body["leave_action"])
        self.assertEqual("submitted", body["leave_status"])
        self.assertIn("已帮你发起", body["reply"]["text"])

    def test_leave_workflow_button_callback_can_cancel(self) -> None:
        app = create_app(log_stream=StringIO(), user_context_resolver=_PermissionResolver())
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-cancel-1",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="年假 2026-04-01 到 2026-04-02",
                sender_id="finance-user",
                conversation_id="conv-leave-api-cancel-1",
            ),
        )
        response = client.post(
            "/dingtalk/stream/events",
            json=make_leave_button_callback_payload(
                action_id="leave_cancel_submit",
                sender_id="finance-user",
                conversation_id="conv-leave-api-cancel-1",
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertEqual("leave_workflow_cancelled", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("已取消", body["reply"]["text"])

    def test_leave_workflow_button_callback_expired_requires_restart(self) -> None:
        clock = _FakeClock()
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-confirm-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator, now_provider=clock.now),
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-confirm-1",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="明天年假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-confirm-1",
            ),
        )
        clock.advance(seconds=301)
        third = client.post(
            "/dingtalk/stream/events",
            json=make_leave_button_callback_payload(
                action_id="leave_confirm_submit",
                sender_id="finance-user",
                conversation_id="conv-leave-api-confirm-1",
            ),
        )

        self.assertEqual(200, third.status_code)
        body = third.json()
        self.assertEqual("leave_workflow_confirmation_expired", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertEqual("text", body["dingtalk_payload"]["msgtype"])
        self.assertIn("重新发送“我要请假”", body["reply"]["text"])

    def test_leave_workflow_button_callback_returns_fallback_when_creator_fails(self) -> None:
        creator = _StubLeaveApprovalCreator(LeaveApprovalResult(success=False, reason="api_error"))
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )
        app = create_app(
            log_stream=StringIO(),
            single_chat_service=service,
            user_context_resolver=_PermissionResolver(),
        )
        client = TestClient(app)

        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要请假",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-2",
            ),
        )
        client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="年假 2026-04-01 到 2026-04-02",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-2",
            ),
        )
        third = client.post(
            "/dingtalk/stream/events",
            json=make_leave_button_callback_payload(
                action_id="leave_confirm_submit",
                sender_id="finance-user",
                conversation_id="conv-leave-api-submit-2",
            ),
        )

        self.assertEqual(200, third.status_code)
        body = third.json()
        self.assertEqual("leave_workflow_handoff_fallback", body["reason"])
        self.assertEqual("text", body["reply"]["channel"])
        self.assertIn("暂时没能直接帮你发起钉钉审批", body["reply"]["text"])
        self.assertIn("OA审批", body["reply"]["text"])

    def test_file_request_progress_query_returns_structured_status(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要定影器采购合同文件",
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
                text="我要定影器采购合同文件",
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
                text="我要定影器采购合同文件",
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
                text="我要定影器采购合同文件",
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

    def test_plain_text_cancel_without_pending_request_is_not_treated_as_callback(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="取消",
                sender_id="user-file-api-cancel-no-pending-1",
                conversation_id="conv-file-api-cancel-no-pending-1",
            ),
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertNotEqual("file_approval_not_found", body["reason"])
        self.assertNotIn("已收到按钮点击", body["reply"]["text"])

    def test_plain_text_cancel_with_msgtype_without_pending_request_is_not_treated_as_callback(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        response = client.post(
            "/dingtalk/stream/events",
            json={
                "event_id": "evt-file-api-cancel-msgtype-1",
                "conversation_id": "conv-file-api-cancel-msgtype-1",
                "conversation_type": "single",
                "sender_id": "user-file-api-cancel-msgtype-1",
                "msgtype": "text",
                "text": "取消",
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertNotEqual("file_approval_not_found", body["reason"])
        self.assertNotIn("已收到按钮点击", body["reply"]["text"])

    def test_file_request_card_callback_shape_can_confirm_request_by_session(self) -> None:
        app = create_app(log_stream=StringIO())
        client = TestClient(app)

        first = client.post(
            "/dingtalk/stream/events",
            json=make_stream_payload(
                text="我要定影器采购合同文件",
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
                text="我要定影器采购合同文件",
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
                text="我要申请采购制度文件权限",
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
