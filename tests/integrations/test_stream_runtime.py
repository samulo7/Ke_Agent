from __future__ import annotations

import asyncio
import io
import json
import logging
import re
import requests
import sqlite3
import unittest
from contextlib import redirect_stderr
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Any, Mapping
from unittest.mock import patch

from app.integrations.dingtalk.leave_approval import (
    DingTalkLeaveApprovalCreator,
    LeaveApprovalSettings,
    build_default_leave_approval_creator,
    load_leave_approval_settings,
)
from app.integrations.dingtalk.stream_runtime import (
    DEFAULT_STREAM_ENDPOINT,
    DingTalkStreamCredentials,
    HRApprovalCardSettings,
    StreamingCardSettings,
    StreamRuntimeError,
    _SdkLoggerAdapter,
    _SdkReplySender,
    _StreamHRApprovalNotifier,
    build_stream_client,
    _extract_card_text_lines,
    _split_text_chunks,
    handle_single_chat_payload,
    load_hr_approval_card_settings,
    load_streaming_card_settings,
    load_stream_credentials,
)
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.user_context import UserContext
from app.schemas.file_asset import FileAsset, FileSearchCandidate, FileSearchResult
from app.schemas.reimbursement import ReimbursementApprovalResult, ReimbursementAttachmentProcessResult, TravelApplication
from app.services.file_request import FileApprovalRequest, FileRequestService
from app.services.leave_request import LeaveApprovalResult, LeaveRequestOrchestrator
from app.services.reimbursement_request import ReimbursementRequestOrchestrator
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


class _FakeCardModule:
    @staticmethod
    def generate_multi_text_line_card_data(*, title: str, logo: str, texts: list[str]) -> dict[str, Any]:
        return {"title": title, "logo": logo, "texts": list(texts)}


class _FakeDingTalkClient:
    def __init__(self) -> None:
        self.credential = SimpleNamespace(client_id="client-id")

    def get_access_token(self) -> str:
        return "fake-access-token"


class _FakeSdkHandler:
    def __init__(self, *, reply_card_return_id: str = "") -> None:
        self.dingtalk_client = _FakeDingTalkClient()
        self.text_messages: list[str] = []
        self.markdown_messages: list[dict[str, str]] = []
        self.card_payloads: list[dict[str, Any]] = []
        self.reply_card_call_kwargs: list[dict[str, Any]] = []
        self.reply_card_return_id = reply_card_return_id

    def reply_text(self, text: str, incoming_message: Any) -> None:
        self.text_messages.append(text)

    def reply_card(self, card_data: dict[str, Any], incoming_message: Any, **kwargs: Any) -> str:
        self.card_payloads.append(card_data)
        self.reply_card_call_kwargs.append(dict(kwargs))
        return self.reply_card_return_id

    def reply_markdown(self, title: str, text: str, incoming_message: Any) -> None:
        self.markdown_messages.append({"title": title, "text": text})
        self.text_messages.append(text)


class _FakeAsyncCardReplier:
    latest: _FakeAsyncCardReplier | None = None
    raise_on_processing_stream: bool = False

    def __init__(self, dingtalk_client: Any, incoming_message: Any) -> None:
        self.create_calls: list[dict[str, Any]] = []
        self.streaming_calls: list[dict[str, Any]] = []
        _FakeAsyncCardReplier.latest = self

    async def async_create_and_deliver_card(self, card_template_id: str, card_data: dict[str, Any]) -> str:
        self.create_calls.append({"card_template_id": card_template_id, "card_data": dict(card_data)})
        return "card-instance-1"

    async def async_streaming(
        self,
        card_instance_id: str,
        *,
        content_key: str,
        content_value: str,
        append: bool,
        finished: bool,
        failed: bool,
    ) -> None:
        if _FakeAsyncCardReplier.raise_on_processing_stream and not finished and not failed:
            raise RuntimeError("simulated streaming failure")
        self.streaming_calls.append(
            {
                "card_instance_id": card_instance_id,
                "content_key": content_key,
                "content_value": content_value,
                "append": append,
                "finished": finished,
                "failed": failed,
            }
        )


async def _noop_sleep(_: float) -> None:
    return None


def _make_payload(
    *,
    text: str,
    conversation_type: str = "single",
    conversation_id: str = "conv-a05-001",
    sender_id: str = "user-a05-001",
    message_type: str = "text",
    file_name: str = "",
    file_download_url: str = "",
    file_media_id: str = "",
    file_content_base64: str = "",
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "event_id": "evt-a05-001",
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


def _make_leave_callback_payload(
    *,
    action_id: str,
    sender_id: str,
    conversation_id: str,
) -> dict[str, Any]:
    return {
        "data": {
            "type": "actionCallback",
            "userId": sender_id,
            "extension": json.dumps({"openConversationId": conversation_id}, ensure_ascii=False),
            "content": json.dumps({"componentType": "button", "componentId": action_id}, ensure_ascii=False),
        }
    }


def _make_reimbursement_callback_payload(
    *,
    action_id: str,
    conversation_id: str,
    sender_id: str,
) -> dict[str, Any]:
    return {
        "data": {
            "type": "actionCallback",
            "userId": sender_id,
            "extension": json.dumps({"openConversationId": conversation_id}, ensure_ascii=False),
            "content": json.dumps({"componentType": "button", "componentId": action_id}, ensure_ascii=False),
            "outTrackId": "reimbursement-confirm-test-1",
        }
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

    def test_load_streaming_card_settings_reads_defaults_and_overrides(self) -> None:
        defaults = load_streaming_card_settings({})
        self.assertFalse(defaults.enabled)
        self.assertEqual("content", defaults.content_key)
        self.assertEqual(20, defaults.chunk_chars)
        self.assertEqual(0.12, defaults.interval_seconds)

        configured = load_streaming_card_settings(
            {
                "DINGTALK_AI_CARD_STREAMING_ENABLED": "true",
                "DINGTALK_AI_CARD_TEMPLATE_ID": "tpl-1.schema",
                "DINGTALK_AI_CARD_CONTENT_KEY": "md",
                "DINGTALK_AI_CARD_TITLE_KEY": "title",
                "DINGTALK_AI_CARD_TITLE": "AI 回答",
                "DINGTALK_AI_CARD_CHUNK_CHARS": "8",
                "DINGTALK_AI_CARD_INTERVAL_MS": "60",
                "DINGTALK_AI_CARD_MIN_CHARS": "40",
            }
        )
        self.assertTrue(configured.enabled)
        self.assertEqual("tpl-1.schema", configured.template_id)
        self.assertEqual("md", configured.content_key)
        self.assertEqual("title", configured.title_key)
        self.assertEqual("AI 回答", configured.title)
        self.assertEqual(8, configured.chunk_chars)
        self.assertEqual(0.06, configured.interval_seconds)
        self.assertEqual(40, configured.min_chars)

    def test_load_hr_approval_card_settings_reads_defaults_and_enablement(self) -> None:
        defaults = load_hr_approval_card_settings({})
        self.assertFalse(defaults.enabled)
        self.assertEqual("", defaults.approver_user_id)
        self.assertEqual("", defaults.template_id)
        self.assertEqual("https://api.dingtalk.com", defaults.openapi_endpoint)

        configured = load_hr_approval_card_settings(
            {
                "DINGTALK_HR_APPROVER_USER_ID": "hr-user-1",
                "DINGTALK_HR_CARD_TEMPLATE_ID": "tpl-hr.schema",
                "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com/",
            }
        )
        self.assertTrue(configured.enabled)
        self.assertEqual("hr-user-1", configured.approver_user_id)
        self.assertEqual("tpl-hr.schema", configured.template_id)
        self.assertEqual("https://api.dingtalk.com", configured.openapi_endpoint)

    def test_load_leave_approval_settings_reads_defaults_and_enablement(self) -> None:
        defaults = load_leave_approval_settings({})
        self.assertFalse(defaults.enabled)
        self.assertEqual("", defaults.process_code)
        self.assertEqual("https://api.dingtalk.com", defaults.openapi_endpoint)
        self.assertEqual("https://oapi.dingtalk.com", defaults.legacy_openapi_endpoint)

        configured = load_leave_approval_settings(
            {
                "DINGTALK_LEAVE_APPROVAL_ENABLED": "true",
                "DINGTALK_LEAVE_APPROVAL_PROCESS_CODE": "PROC-LEAVE",
                "DINGTALK_LEAVE_APPROVAL_TYPE_FIELD": "请假类型",
                "DINGTALK_LEAVE_APPROVAL_START_TIME_FIELD": "开始时间",
                "DINGTALK_LEAVE_APPROVAL_END_TIME_FIELD": "结束时间",
                "DINGTALK_LEAVE_APPROVAL_REASON_FIELD": "请假事由",
                "DINGTALK_LEAVE_APPROVAL_APPLICANT_FIELD": "申请人",
                "DINGTALK_LEAVE_APPROVAL_DEPARTMENT_FIELD": "部门",
                "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com/",
                "DINGTALK_LEGACY_OPENAPI_ENDPOINT": "https://oapi.dingtalk.com/",
            }
        )
        self.assertTrue(configured.enabled)
        self.assertEqual("PROC-LEAVE", configured.process_code)
        self.assertEqual("请假类型", configured.leave_type_field)
        self.assertEqual("开始时间", configured.leave_start_time_field)
        self.assertEqual("结束时间", configured.leave_end_time_field)
        self.assertEqual("https://api.dingtalk.com", configured.openapi_endpoint)
        self.assertEqual("https://oapi.dingtalk.com", configured.legacy_openapi_endpoint)

    def test_build_default_leave_approval_creator_disables_incomplete_config(self) -> None:
        creator = build_default_leave_approval_creator(
            {
                "DINGTALK_LEAVE_APPROVAL_ENABLED": "true",
                "DINGTALK_CLIENT_ID": "cid",
                "DINGTALK_CLIENT_SECRET": "secret",
                "DINGTALK_LEAVE_APPROVAL_PROCESS_CODE": "PROC-LEAVE",
                "DINGTALK_LEAVE_APPROVAL_TYPE_FIELD": "请假类型",
            }
        )
        self.assertIsNone(creator)

    @patch("app.integrations.dingtalk.leave_approval.requests.post")
    def test_leave_approval_creator_submits_process_instance(self, mock_post) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise requests.HTTPError(f"http {self.status_code}")

        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=200, payload={"errcode": 0, "process_instance_id": "proc-1"}),
        ]
        creator = DingTalkLeaveApprovalCreator(
            client_id="cid",
            client_secret="secret",
            settings=LeaveApprovalSettings(
                enabled=True,
                process_code="PROC-LEAVE",
                leave_type_field="请假类型",
                leave_start_time_field="开始时间",
                leave_end_time_field="结束时间",
                leave_reason_field="请假事由",
                applicant_field="申请人",
                department_field="部门",
                openapi_endpoint="https://api.dingtalk.com",
                legacy_openapi_endpoint="https://oapi.dingtalk.com",
            ),
        )

        result = creator.submit(
            submission=type("_Submission", (), {
                "originator_user_id": "user-1",
                "applicant_name": "Alice",
                "department": "财务部",
                "department_id": "1001",
                "leave_type": "年假",
                "leave_time": "明天到后天",
                "leave_start_time": "明天",
                "leave_end_time": "后天",
                "leave_reason": "回家",
            })()
        )

        self.assertTrue(result.success)
        self.assertEqual("submitted", result.reason)
        self.assertEqual("proc-1", result.process_instance_id)
        self.assertEqual(2, mock_post.call_count)
        _, create_kwargs = mock_post.call_args_list[1]
        self.assertEqual({"access_token": "token-1"}, create_kwargs["params"])
        self.assertEqual("PROC-LEAVE", create_kwargs["json"]["process_code"])
        self.assertEqual("user-1", create_kwargs["json"]["originator_user_id"])
        self.assertEqual(1001, create_kwargs["json"]["dept_id"])
        self.assertEqual(
            [
                {"name": "请假类型", "value": "年假"},
                {"name": "开始时间", "value": "明天"},
                {"name": "结束时间", "value": "后天"},
                {"name": "请假事由", "value": "回家"},
                {"name": "申请人", "value": "Alice"},
                {"name": "部门", "value": "财务部"},
            ],
            create_kwargs["json"]["form_component_values"],
        )

    @patch("app.integrations.dingtalk.leave_approval.requests.post")
    def test_leave_approval_creator_returns_fallback_reason_when_dept_id_missing(self, mock_post) -> None:  # type: ignore[no-untyped-def]
        creator = DingTalkLeaveApprovalCreator(
            client_id="cid",
            client_secret="secret",
            settings=LeaveApprovalSettings(
                enabled=True,
                process_code="PROC-LEAVE",
                leave_type_field="请假类型",
                leave_start_time_field="开始时间",
                leave_end_time_field="结束时间",
                leave_reason_field="请假事由",
                applicant_field="申请人",
                department_field="部门",
                openapi_endpoint="https://api.dingtalk.com",
                legacy_openapi_endpoint="https://oapi.dingtalk.com",
            ),
        )

        result = creator.submit(
            submission=type("_Submission", (), {
                "originator_user_id": "user-1",
                "applicant_name": "Alice",
                "department": "财务部",
                "department_id": "",
                "leave_type": "年假",
                "leave_time": "明天到后天",
                "leave_start_time": "明天",
                "leave_end_time": "后天",
                "leave_reason": "回家",
            })()
        )

        self.assertFalse(result.success)
        self.assertEqual("missing_dept_id", result.reason)
        self.assertEqual(0, mock_post.call_count)

    @patch("app.integrations.dingtalk.leave_approval.requests.post")
    def test_leave_approval_creator_returns_fallback_reason_on_transport_error(self, mock_post) -> None:  # type: ignore[no-untyped-def]
        mock_post.side_effect = requests.RequestException("boom")
        creator = DingTalkLeaveApprovalCreator(
            client_id="cid",
            client_secret="secret",
            settings=LeaveApprovalSettings(
                enabled=True,
                process_code="PROC-LEAVE",
                leave_type_field="请假类型",
                leave_start_time_field="开始时间",
                leave_end_time_field="结束时间",
                leave_reason_field="",
                applicant_field="",
                department_field="",
                openapi_endpoint="https://api.dingtalk.com",
                legacy_openapi_endpoint="https://oapi.dingtalk.com",
            ),
        )

        result = creator.submit(
            submission=type("_Submission", (), {
                "originator_user_id": "user-1",
                "applicant_name": "Alice",
                "department": "Finance",
                "department_id": "1001",
                "leave_type": "年假",
                "leave_time": "明天到后天",
                "leave_start_time": "明天",
                "leave_end_time": "后天",
                "leave_reason": "",
            })()
        )

        self.assertFalse(result.success)
        self.assertEqual("transport_error", result.reason)

    @patch("app.integrations.dingtalk.leave_approval.requests.post")
    def test_leave_approval_creator_fills_default_reason_when_reason_field_configured_but_missing(
        self, mock_post
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise requests.HTTPError(f"http {self.status_code}")

        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=200, payload={"errcode": 0, "process_instance_id": "proc-reason-default-1"}),
        ]
        creator = DingTalkLeaveApprovalCreator(
            client_id="cid",
            client_secret="secret",
            settings=LeaveApprovalSettings(
                enabled=True,
                process_code="PROC-LEAVE",
                leave_type_field="请假类型",
                leave_start_time_field="开始时间",
                leave_end_time_field="结束时间",
                leave_reason_field="请假事由",
                applicant_field="",
                department_field="",
                openapi_endpoint="https://api.dingtalk.com",
                legacy_openapi_endpoint="https://oapi.dingtalk.com",
            ),
        )

        result = creator.submit(
            submission=type("_Submission", (), {
                "originator_user_id": "user-1",
                "applicant_name": "Alice",
                "department": "Finance",
                "department_id": "1001",
                "leave_type": "年假",
                "leave_time": "明天到后天",
                "leave_start_time": "明天",
                "leave_end_time": "后天",
                "leave_reason": "",
            })()
        )

        self.assertTrue(result.success)
        _, create_kwargs = mock_post.call_args_list[1]
        self.assertIn(
            {"name": "请假事由", "value": "未填写"},
            create_kwargs["json"]["form_component_values"],
        )

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    def test_stream_hr_approval_notifier_sends_create_and_deliver_payload(self, mock_post) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload, ensure_ascii=False)

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=200, payload={"success": True, "result": [{"success": True}]}),
        ]
        notifier = _StreamHRApprovalNotifier(
            client_id="cid",
            client_secret="secret",
            settings=HRApprovalCardSettings(
                enabled=True,
                approver_user_id="hr-user-1",
                template_id="tpl-hr.schema",
                openapi_endpoint="https://api.dingtalk.com",
            ),
        )
        request = FileApprovalRequest(
            request_id="file-req-abc123",
            requester_sender_id="user-a",
            requester_conversation_id="conv-a",
            requester_display_name="Alice",
            query_text="我要采购合同",
            variant="scan",
            asset=FileAsset(
                file_id="file-1",
                contract_key="dingyingqi",
                title="定影器采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/dingyingqi-2024-scan",
                tags=("采购", "合同"),
                status="active",
                updated_at="2026-03-29",
            ),
            fallback_from_scan_to_paper=False,
            approver_user_id="hr-user-1",
            created_at=datetime(2026, 3, 29, 0, 0, tzinfo=timezone.utc),
        )

        result = notifier.notify(
            request=request,
            card_payload={"card_type": "file_access_approval", "title": "文件发放审批", "summary": "Alice 申请查阅文件，请审批。"},
        )

        self.assertTrue(result.success)
        self.assertEqual("delivered", result.reason)
        self.assertEqual(2, mock_post.call_count)
        _, delivery_kwargs = mock_post.call_args_list[1]
        body = delivery_kwargs["json"]
        self.assertEqual("hr-user-1", body["userId"])
        self.assertEqual("tpl-hr.schema", body["cardTemplateId"])
        self.assertEqual("STREAM", body["callbackType"])
        self.assertEqual("file-req-abc123", body["cardData"]["cardParamMap"]["request_id"])
        self.assertEqual("Alice", body["cardData"]["cardParamMap"]["requester_name"])

    def test_split_text_chunks_splits_by_configured_size(self) -> None:
        self.assertEqual(["ab", "cd", "ef"], _split_text_chunks("abcdef", 2))
        self.assertEqual(["abcdef"], _split_text_chunks("abcdef", 99))

    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_build_stream_client_registers_chat_and_card_callback_topics(self, mock_load_sdk) -> None:  # type: ignore[no-untyped-def]
        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeChatbotHandler:
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)

        fake_sdk = SimpleNamespace(
            Credential=_FakeCredential,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)

        client = build_stream_client(
            DingTalkStreamCredentials(
                client_id="client-id",
                client_secret="client-secret",
                agent_id="agent-id",
            ),
            single_chat_service=SingleChatService(),
            user_context_resolver=self._build_resolver(),  # type: ignore[arg-type]
        )

        self.assertIn("/v1.0/im/bot/messages/get", client.registered_topics)
        self.assertIn("/v1.0/card/instances/callback", client.registered_topics)

    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_returns_official_response_payload(self, mock_load_sdk) -> None:  # type: ignore[no-untyped-def]
        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)

        service = SingleChatService()
        resolver = self._build_resolver()
        sender = _FakeSender()
        handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-card-callback-1",
                sender_id="user-card-callback-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        client = build_stream_client(
            DingTalkStreamCredentials(
                client_id="client-id",
                client_secret="client-secret",
                agent_id="agent-id",
            ),
            single_chat_service=service,
            user_context_resolver=resolver,  # type: ignore[arg-type]
        )
        card_handler = client.handlers["/v1.0/card/instances/callback"]

        callback_message = SimpleNamespace(
            headers=SimpleNamespace(message_id="trace-card-1", topic="/v1.0/card/instances/callback"),
            data={
                "data": {
                    "type": "actionCallback",
                    "userId": "user-card-callback-1",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{}}}",
                }
            },
            extensions={},
        )
        code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertIn("cardUpdateOptions", response)
        self.assertTrue(response["cardUpdateOptions"]["updateCardDataByKey"])
        self.assertIn("userPrivateData", response)
        self.assertEqual("pending", response["userPrivateData"]["cardParamMap"]["approval_status"])
        self.assertIn("summary", response["userPrivateData"]["cardParamMap"])
        self.assertIn("申请已提交", response["userPrivateData"]["cardParamMap"]["summary"])
        self.assertNotIn("请求编号", response["userPrivateData"]["cardParamMap"]["summary"])

    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_leave_confirm_updates_card_as_submitted(self, mock_load_sdk) -> None:  # type: ignore[no-untyped-def]
        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)

        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-card-1")
                )
            )
        )
        resolver = self._build_resolver()
        sender = _FakeSender()
        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-card-1",
                sender_id="user-leave-card-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="明天年假",
                conversation_id="conv-leave-card-1",
                sender_id="user-leave-card-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        client = build_stream_client(
            DingTalkStreamCredentials(
                client_id="client-id",
                client_secret="client-secret",
                agent_id="agent-id",
            ),
            single_chat_service=service,
            user_context_resolver=resolver,  # type: ignore[arg-type]
        )
        card_handler = client.handlers["/v1.0/card/instances/callback"]

        callback_message = SimpleNamespace(
            headers=SimpleNamespace(message_id="trace-leave-card-1", topic="/v1.0/card/instances/callback"),
            data=_make_leave_callback_payload(
                action_id="leave_confirm_submit",
                sender_id="user-leave-card-1",
                conversation_id="conv-leave-card-1",
            ),
            extensions={},
        )
        code, response = asyncio.run(card_handler.process(callback_message))
        self.assertEqual(200, code)
        self.assertEqual("submitted", response["userPrivateData"]["cardParamMap"]["leave_status"])
        self.assertEqual("true", response["userPrivateData"]["cardParamMap"]["actions_locked"])
        self.assertIn("已提交", response["userPrivateData"]["cardParamMap"]["summary"])

    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_leave_session_not_found_returns_restart_hint(self, mock_load_sdk) -> None:  # type: ignore[no-untyped-def]
        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)

        service = SingleChatService()
        resolver = self._build_resolver()
        client = build_stream_client(
            DingTalkStreamCredentials(
                client_id="client-id",
                client_secret="client-secret",
                agent_id="agent-id",
            ),
            single_chat_service=service,
            user_context_resolver=resolver,  # type: ignore[arg-type]
        )
        card_handler = client.handlers["/v1.0/card/instances/callback"]
        callback_message = SimpleNamespace(
            headers=SimpleNamespace(message_id="trace-leave-card-miss-1", topic="/v1.0/card/instances/callback"),
            data=_make_leave_callback_payload(
                action_id="leave_confirm_submit",
                sender_id="user-leave-miss-1",
                conversation_id="conv-leave-miss-1",
            ),
            extensions={},
        )
        code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertEqual("not_found", response["userPrivateData"]["cardParamMap"]["leave_status"])
        self.assertEqual("true", response["userPrivateData"]["cardParamMap"]["actions_locked"])
        self.assertIn("未找到待确认请假", response["userPrivateData"]["cardParamMap"]["summary"])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_approve_pushes_result_card_to_requester(
        self,
        mock_load_sdk,
        mock_post,
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload, ensure_ascii=False)

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)
        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=200, payload={"success": True, "result": [{"success": True}]}),
        ]

        with patch.dict(
            "os.environ",
            {"DINGTALK_CARD_TEMPLATE_ID": "tpl-requester-result.schema", "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com"},
            clear=False,
        ):
            service = SingleChatService()
            resolver = self._build_resolver()
            sender = _FakeSender()
            handle_single_chat_payload(
                _make_payload(
                    text="我要定影器采购合同文件",
                    conversation_id="conv-card-approve-1",
                    sender_id="user-card-approve-1",
                ),
                service=service,
                sender=sender,
                user_context_resolver=resolver,
            )
            request_id = str(sender.card_payloads[0]["request_id"])
            confirm_result = service.handle_file_approval_action(
                request_id=request_id,
                action="confirm_request",
                approver_user_id="user-card-approve-1",
            )
            self.assertTrue(confirm_result.handled)
            self.assertEqual("pending", confirm_result.status)

            client = build_stream_client(
                DingTalkStreamCredentials(
                    client_id="client-id",
                    client_secret="client-secret",
                    agent_id="agent-id",
                ),
                single_chat_service=service,
                user_context_resolver=resolver,  # type: ignore[arg-type]
            )
            card_handler = client.handlers["/v1.0/card/instances/callback"]
            callback_message = SimpleNamespace(
                headers=SimpleNamespace(message_id="trace-card-approve-1", topic="/v1.0/card/instances/callback"),
                data={
                    "data": {
                        "type": "actionCallback",
                        "userId": "人事行政",
                        "outTrackId": f"hr-approval-{request_id}",
                        "spaceId": "cid-hr-1",
                        "content": json.dumps(
                            {
                                "cardPrivateData": {
                                    "actionIds": ["approve"],
                                    "params": {"request_id": request_id},
                                }
                            },
                            ensure_ascii=False,
                        ),
                    }
                },
                extensions={},
            )
            code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertEqual("delivered", response["userPrivateData"]["cardParamMap"]["approval_status"])
        self.assertEqual(2, mock_post.call_count)
        _, delivery_kwargs = mock_post.call_args_list[1]
        delivery_body = delivery_kwargs["json"]
        self.assertEqual("user-card-approve-1", delivery_body["userId"])
        self.assertEqual("tpl-requester-result.schema", delivery_body["cardTemplateId"])
        self.assertEqual("STREAM", delivery_body["callbackType"])
        self.assertEqual(request_id, delivery_body["cardData"]["cardParamMap"]["request_id"])
        self.assertEqual("delivered", delivery_body["cardData"]["cardParamMap"]["approval_status"])
        self.assertEqual("true", delivery_body["cardData"]["cardParamMap"]["actions_locked"])
        self.assertEqual("true", delivery_body["cardData"]["cardParamMap"]["submitted"])
        self.assertEqual("定影器采购合同-2024版", delivery_body["cardData"]["cardParamMap"]["file_title"])
        self.assertEqual("true", delivery_body["cardData"]["cardParamMap"]["show_download_button"])
        self.assertIn("https://example.local/files/", delivery_body["cardData"]["cardParamMap"]["download_url"])
        self.assertIn("已审批通过", delivery_body["cardData"]["cardParamMap"]["summary"])
        self.assertNotIn("优先为您提供", delivery_body["cardData"]["cardParamMap"]["summary"])
        self.assertNotIn("正在发送", delivery_body["cardData"]["cardParamMap"]["summary"])
        self.assertNotIn("复制链接", delivery_body["cardData"]["cardParamMap"]["summary"])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_reject_pushes_result_card_to_requester(
        self,
        mock_load_sdk,
        mock_post,
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload, ensure_ascii=False)

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)
        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=200, payload={"success": True, "result": [{"success": True}]}),
        ]

        with patch.dict(
            "os.environ",
            {"DINGTALK_CARD_TEMPLATE_ID": "tpl-requester-result.schema", "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com"},
            clear=False,
        ):
            service = SingleChatService()
            resolver = self._build_resolver()
            sender = _FakeSender()
            handle_single_chat_payload(
                _make_payload(
                    text="我要定影器采购合同文件",
                    conversation_id="conv-card-reject-1",
                    sender_id="user-card-reject-1",
                ),
                service=service,
                sender=sender,
                user_context_resolver=resolver,
            )
            request_id = str(sender.card_payloads[0]["request_id"])
            confirm_result = service.handle_file_approval_action(
                request_id=request_id,
                action="confirm_request",
                approver_user_id="user-card-reject-1",
            )
            self.assertTrue(confirm_result.handled)
            self.assertEqual("pending", confirm_result.status)

            client = build_stream_client(
                DingTalkStreamCredentials(
                    client_id="client-id",
                    client_secret="client-secret",
                    agent_id="agent-id",
                ),
                single_chat_service=service,
                user_context_resolver=resolver,  # type: ignore[arg-type]
            )
            card_handler = client.handlers["/v1.0/card/instances/callback"]
            callback_message = SimpleNamespace(
                headers=SimpleNamespace(message_id="trace-card-reject-1", topic="/v1.0/card/instances/callback"),
                data={
                    "data": {
                        "type": "actionCallback",
                        "userId": "人事行政",
                        "outTrackId": f"hr-approval-{request_id}",
                        "spaceId": "cid-hr-1",
                        "content": json.dumps(
                            {
                                "cardPrivateData": {
                                    "actionIds": ["reject"],
                                    "params": {"request_id": request_id},
                                }
                            },
                            ensure_ascii=False,
                        ),
                    }
                },
                extensions={},
            )
            code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertEqual("rejected", response["userPrivateData"]["cardParamMap"]["approval_status"])
        self.assertEqual(2, mock_post.call_count)
        _, delivery_kwargs = mock_post.call_args_list[1]
        delivery_body = delivery_kwargs["json"]
        self.assertEqual("user-card-reject-1", delivery_body["userId"])
        self.assertEqual("tpl-requester-result.schema", delivery_body["cardTemplateId"])
        self.assertEqual(request_id, delivery_body["cardData"]["cardParamMap"]["request_id"])
        self.assertEqual("rejected", delivery_body["cardData"]["cardParamMap"]["approval_status"])
        self.assertEqual("定影器采购合同-2024版", delivery_body["cardData"]["cardParamMap"]["file_title"])
        self.assertEqual("", delivery_body["cardData"]["cardParamMap"]["download_url"])
        self.assertEqual("false", delivery_body["cardData"]["cardParamMap"]["show_download_button"])
        self.assertIn("审批未通过", delivery_body["cardData"]["cardParamMap"]["summary"])
        self.assertNotIn("优先为您提供", delivery_body["cardData"]["cardParamMap"]["summary"])
        self.assertNotIn("复制链接", delivery_body["cardData"]["cardParamMap"]["summary"])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_result_push_failure_keeps_ack_ok(
        self,
        mock_load_sdk,
        mock_post,
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            def __init__(self, *, status_code: int, payload: dict[str, Any]) -> None:
                self.status_code = status_code
                self._payload = payload
                self.text = json.dumps(payload, ensure_ascii=False)

            def json(self) -> dict[str, Any]:
                return dict(self._payload)

            def raise_for_status(self) -> None:
                if self.status_code >= 400:
                    raise RuntimeError(f"http {self.status_code}")

        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)
        mock_post.side_effect = [
            _FakeResponse(status_code=200, payload={"accessToken": "token-1", "expireIn": 7200}),
            _FakeResponse(status_code=500, payload={"code": "err", "message": "failed"}),
        ]

        with patch.dict(
            "os.environ",
            {"DINGTALK_CARD_TEMPLATE_ID": "tpl-requester-result.schema", "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com"},
            clear=False,
        ):
            service = SingleChatService()
            resolver = self._build_resolver()
            sender = _FakeSender()
            handle_single_chat_payload(
                _make_payload(
                    text="我要定影器采购合同文件",
                    conversation_id="conv-card-fail-1",
                    sender_id="user-card-fail-1",
                ),
                service=service,
                sender=sender,
                user_context_resolver=resolver,
            )
            request_id = str(sender.card_payloads[0]["request_id"])
            confirm_result = service.handle_file_approval_action(
                request_id=request_id,
                action="confirm_request",
                approver_user_id="user-card-fail-1",
            )
            self.assertTrue(confirm_result.handled)
            self.assertEqual("pending", confirm_result.status)

            client = build_stream_client(
                DingTalkStreamCredentials(
                    client_id="client-id",
                    client_secret="client-secret",
                    agent_id="agent-id",
                ),
                single_chat_service=service,
                user_context_resolver=resolver,  # type: ignore[arg-type]
            )
            card_handler = client.handlers["/v1.0/card/instances/callback"]
            callback_message = SimpleNamespace(
                headers=SimpleNamespace(message_id="trace-card-fail-1", topic="/v1.0/card/instances/callback"),
                data={
                    "data": {
                        "type": "actionCallback",
                        "userId": "人事行政",
                        "content": json.dumps(
                            {
                                "cardPrivateData": {
                                    "actionIds": ["approve"],
                                    "params": {"request_id": request_id},
                                }
                            },
                            ensure_ascii=False,
                        ),
                    }
                },
                extensions={},
            )
            code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertEqual("delivered", response["userPrivateData"]["cardParamMap"]["approval_status"])
        self.assertEqual(2, mock_post.call_count)

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    @patch("app.integrations.dingtalk.stream_runtime._load_dingtalk_sdk")
    def test_card_callback_handler_forbidden_does_not_push_result_card(
        self,
        mock_load_sdk,
        mock_post,
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeAckMessage:
            STATUS_OK = 200
            STATUS_BAD_REQUEST = 400
            STATUS_SYSTEM_EXCEPTION = 500

        class _FakeCredential:
            def __init__(self, client_id: str, client_secret: str) -> None:
                self.client_id = client_id
                self.client_secret = client_secret

        class _FakeCallbackHandler:
            TOPIC_CARD_CALLBACK = "/v1.0/card/instances/callback"

            def __init__(self) -> None:
                self.dingtalk_client = _FakeDingTalkClient()

        class _FakeChatbotHandler(_FakeCallbackHandler):
            pass

        class _FakeChatbotMessage:
            TOPIC = "/v1.0/im/bot/messages/get"

            @staticmethod
            def from_dict(payload: Mapping[str, Any]) -> Any:
                return SimpleNamespace(
                    sender_staff_id=str(payload.get("senderStaffId") or payload.get("sender_id") or ""),
                    conversation_type=str(payload.get("conversationType") or payload.get("conversation_type") or "1"),
                    session_webhook=str(payload.get("sessionWebhook") or ""),
                )

        class _FakeDingTalkStreamClient:
            OPEN_CONNECTION_API = ""

            def __init__(self, credential: _FakeCredential, logger: Any = None) -> None:
                self.credential = credential
                self.logger = logger
                self.registered_topics: list[str] = []
                self.handlers: dict[str, Any] = {}

            def register_callback_handler(self, topic: str, handler: Any) -> None:
                self.registered_topics.append(topic)
                self.handlers[topic] = handler

        fake_sdk = SimpleNamespace(
            AckMessage=_FakeAckMessage,
            Credential=_FakeCredential,
            CallbackHandler=_FakeCallbackHandler,
            ChatbotHandler=_FakeChatbotHandler,
            ChatbotMessage=_FakeChatbotMessage,
            DingTalkStreamClient=_FakeDingTalkStreamClient,
        )
        fake_stream_module = SimpleNamespace(DingTalkStreamClient=_FakeDingTalkStreamClient)
        fake_card_module = _FakeCardModule()
        fake_card_replier_module = SimpleNamespace(AICardReplier=None)
        mock_load_sdk.return_value = (fake_sdk, fake_stream_module, fake_card_module, fake_card_replier_module)

        with patch.dict(
            "os.environ",
            {"DINGTALK_CARD_TEMPLATE_ID": "tpl-requester-result.schema", "DINGTALK_OPENAPI_ENDPOINT": "https://api.dingtalk.com"},
            clear=False,
        ):
            service = SingleChatService()
            resolver = self._build_resolver()
            sender = _FakeSender()
            handle_single_chat_payload(
                _make_payload(
                    text="我要定影器采购合同文件",
                    conversation_id="conv-card-forbidden-1",
                    sender_id="user-card-forbidden-1",
                ),
                service=service,
                sender=sender,
                user_context_resolver=resolver,
            )
            request_id = str(sender.card_payloads[0]["request_id"])
            confirm_result = service.handle_file_approval_action(
                request_id=request_id,
                action="confirm_request",
                approver_user_id="user-card-forbidden-1",
            )
            self.assertTrue(confirm_result.handled)
            self.assertEqual("pending", confirm_result.status)

            client = build_stream_client(
                DingTalkStreamCredentials(
                    client_id="client-id",
                    client_secret="client-secret",
                    agent_id="agent-id",
                ),
                single_chat_service=service,
                user_context_resolver=resolver,  # type: ignore[arg-type]
            )
            card_handler = client.handlers["/v1.0/card/instances/callback"]
            callback_message = SimpleNamespace(
                headers=SimpleNamespace(message_id="trace-card-forbidden-1", topic="/v1.0/card/instances/callback"),
                data={
                    "data": {
                        "type": "actionCallback",
                        "userId": "sales-user",
                        "content": json.dumps(
                            {
                                "cardPrivateData": {
                                    "actionIds": ["approve"],
                                    "params": {"request_id": request_id},
                                }
                            },
                            ensure_ascii=False,
                        ),
                    }
                },
                extensions={},
            )
            code, response = asyncio.run(card_handler.process(callback_message))

        self.assertEqual(200, code)
        self.assertEqual("pending", response["userPrivateData"]["cardParamMap"]["approval_status"])
        self.assertEqual(0, mock_post.call_count)

    def test_sdk_logger_adapter_formats_standard_placeholder(self) -> None:
        stream = io.StringIO()
        logger = logging.getLogger("tests.stream_runtime.sdk_logger.standard")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        adapter = _SdkLoggerAdapter(logger)
        adapter.info("endpoint is %s", "wss://example")

        self.assertIn("endpoint is wss://example", stream.getvalue())

    def test_sdk_logger_adapter_handles_malformed_exception_args(self) -> None:
        stream = io.StringIO()
        stderr_stream = io.StringIO()
        logger = logging.getLogger("tests.stream_runtime.sdk_logger.exception")
        logger.handlers.clear()
        logger.propagate = False
        logger.setLevel(logging.INFO)
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)

        adapter = _SdkLoggerAdapter(logger)
        with redirect_stderr(stderr_stream):
            try:
                raise ConnectionResetError("boom")
            except ConnectionResetError as exc:
                adapter.exception("unknown exception", exc)

        self.assertIn("unknown exception", stream.getvalue())
        self.assertIn("ConnectionResetError", stream.getvalue())
        self.assertNotIn("Logging error", stderr_stream.getvalue())

    def test_extract_card_lines_uses_chinese_labels_for_draft_fields(self) -> None:
        title, lines = _extract_card_text_lines(
            {
                "title": "文档申请草稿",
                "draft_fields": {
                    "applicant_name": "Alice",
                    "department": "Finance",
                    "requested_item": "采购制度文件",
                    "leave_type": "年假",
                    "leave_time": "明天后天请两天",
                },
            }
        )

        self.assertEqual("文档申请草稿", title)
        self.assertIn("申请人姓名: Alice", lines)
        self.assertIn("所属部门: Finance", lines)
        self.assertIn("申请资料名称: 采购制度文件", lines)
        self.assertIn("请假类型: 年假", lines)
        self.assertIn("请假时间: 明天后天请两天", lines)

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
                "next_action": "请补充申请用途后再提交。",
            }
        )

        self.assertIn("申请人姓名: Alice", lines)
        self.assertIn("【需细化】申请用途: 采购", lines)
        self.assertIn("【待补充】期望使用时间: ____", lines)
        self.assertIn("可操作：确认提交 / 取消", lines)
        self.assertIn("下一步：请补充申请用途后再提交。", lines)

    def test_extract_card_lines_prefers_action_oriented_flow_guidance(self) -> None:
        title, lines = _extract_card_text_lines(
            {
                "card_type": "flow_guidance",
                "title": "请假申请指引",
                "summary": "我可以先帮你判断入口；如果你是要实际办理，也可以直接说“我要请假”。",
                "primary_action": "先到控制台/工作台进入 OA审批，选择“请假”后发起提交。",
                "context": "我要请假",
                "entry_point": "控制台/工作台 > OA审批 > 请假",
                "required_materials": "提前确认假期类型、请假时间，并按模板补充必要说明。",
                "common_errors": ["未提前申请（需提前1天）", "假种选择错误"],
                "process_path": ["控制台/工作台", "OA审批", "请假", "选择提交", "领导审批", "通过"],
                "next_action": "如果你已经确定请假类型和时间，也可以直接告诉我，我先帮你整理提交草稿。",
            }
        )

        self.assertEqual("请假申请指引", title)
        self.assertEqual("我可以先帮你判断入口；如果你是要实际办理，也可以直接说“我要请假”。", lines[0])
        self.assertEqual("建议动作：先到控制台/工作台进入 OA审批，选择“请假”后发起提交。", lines[1])
        self.assertNotIn("你的问题：我要请假", lines)
        self.assertIn("办理入口：控制台/工作台 > OA审批 > 请假", lines)
        self.assertIn("准备材料：提前确认假期类型、请假时间，并按模板补充必要说明。", lines)
        self.assertIn("常见错误：未提前申请（需提前1天）；假种选择错误", lines)
        self.assertIn("流程路径：控制台/工作台 > OA审批 > 请假 > 选择提交 > 领导审批 > 通过", lines)
        self.assertIn("下一步：如果你已经确定请假类型和时间，也可以直接告诉我，我先帮你整理提交草稿。", lines)

    def test_sdk_reply_sender_interactive_card_without_template_falls_back_to_text_confirmation(self) -> None:
        handler = _FakeSdkHandler(reply_card_return_id="card-biz-1")
        sender = _SdkReplySender(
            handler=handler,
            incoming_message=object(),
            card_module=_FakeCardModule(),
            action_card_template_id="",
        )
        sender.send_interactive_card(
            {
                "card_type": "file_request_confirmation",
                "title": "确认文件申请",
                "summary": "已找到《采购合同》，确认发起申请吗？",
            }
        )

        self.assertEqual(0, len(handler.card_payloads))
        self.assertEqual(0, len(handler.reply_card_call_kwargs))
        self.assertEqual([], handler.markdown_messages)
        self.assertEqual(1, len(handler.text_messages))
        self.assertIn("已找到《采购合同》，确认发起申请吗？", handler.text_messages[0])
        self.assertIn("请回复“确认申请”或“取消”。", handler.text_messages[0])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    def test_sdk_reply_sender_interactive_card_prefers_create_and_deliver_when_template_present(
        self, mock_post
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            status_code = 200
            text = "{\"success\":true}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {"success": True, "result": [{"success": True}]}

        incoming_message = SimpleNamespace(sender_staff_id="2024493135-1049003835")
        handler = _FakeSdkHandler(reply_card_return_id="card-biz-legacy")
        sender = _SdkReplySender(
            handler=handler,
            incoming_message=incoming_message,
            card_module=_FakeCardModule(),
            action_card_template_id="tpl-action.schema",
            openapi_endpoint="https://api.dingtalk.com",
        )
        mock_post.return_value = _FakeResponse()

        sender.send_interactive_card(
            {
                "card_type": "file_request_confirmation",
                "request_id": "file-req-abc123",
                "title": "确认文件申请",
                "summary": "已找到《采购合同》，确认发起申请吗？",
            }
        )

        self.assertEqual(0, len(handler.card_payloads))
        self.assertEqual(0, len(handler.reply_card_call_kwargs))
        self.assertEqual(1, mock_post.call_count)
        args, kwargs = mock_post.call_args
        self.assertIn("/v1.0/card/instances/createAndDeliver", str(args[0]))
        self.assertEqual("STREAM", kwargs["json"]["callbackType"])
        self.assertEqual("tpl-action.schema", kwargs["json"]["cardTemplateId"])
        self.assertEqual(
            {
                "title": "确认文件申请",
                "summary": "已找到《采购合同》，确认发起申请吗？",
                "workflow_type": "file_request",
                "actions_locked": "false",
                "approval_status": "awaiting_requester_confirmation",
                "submitted": "false",
            },
            kwargs["json"]["cardData"]["cardParamMap"],
        )
        self.assertNotIn("robotCode", kwargs["json"]["imRobotOpenDeliverModel"])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    def test_sdk_reply_sender_leave_confirmation_card_uses_template_delivery_when_template_present(
        self, mock_post
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            status_code = 200
            text = "{\"success\":true}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {"success": True, "result": [{"success": True}]}

        incoming_message = SimpleNamespace(sender_staff_id="2024493135-1049003835")
        handler = _FakeSdkHandler(reply_card_return_id="card-biz-legacy")
        sender = _SdkReplySender(
            handler=handler,
            incoming_message=incoming_message,
            card_module=_FakeCardModule(),
            action_card_template_id="tpl-action.schema",
            openapi_endpoint="https://api.dingtalk.com",
        )
        mock_post.return_value = _FakeResponse()

        sender.send_interactive_card(
            {
                "card_type": "leave_request_ready",
                "title": "请假确认",
                "summary": "我将按年假、2026-04-01 09:00 到 2026-04-02 18:00 发起请假审批。",
            }
        )

        self.assertEqual(0, len(handler.card_payloads))
        self.assertEqual(0, len(handler.reply_card_call_kwargs))
        self.assertEqual(1, mock_post.call_count)
        args, kwargs = mock_post.call_args
        self.assertIn("/v1.0/card/instances/createAndDeliver", str(args[0]))
        self.assertTrue(str(kwargs["json"]["outTrackId"]).startswith("leave-confirm-"))
        self.assertEqual(
            {
                "title": "请假确认",
                "summary": "我将按年假、2026-04-01 09:00 到 2026-04-02 18:00 发起请假审批。",
                "workflow_type": "leave",
                "actions_locked": "false",
                "approval_status": "awaiting_requester_confirmation",
                "submitted": "false",
            },
            kwargs["json"]["cardData"]["cardParamMap"],
        )
        self.assertNotIn("robotCode", kwargs["json"]["imRobotOpenDeliverModel"])

    @patch("app.integrations.dingtalk.stream_runtime.requests.post")
    def test_sdk_reply_sender_leave_confirmation_card_prefers_leave_specific_template_id(
        self, mock_post
    ) -> None:  # type: ignore[no-untyped-def]
        class _FakeResponse:
            status_code = 200
            text = "{\"success\":true}"

            @staticmethod
            def json() -> dict[str, Any]:
                return {"success": True, "result": [{"success": True}]}

        incoming_message = SimpleNamespace(sender_staff_id="2024493135-1049003835")
        handler = _FakeSdkHandler(reply_card_return_id="card-biz-legacy")
        sender = _SdkReplySender(
            handler=handler,
            incoming_message=incoming_message,
            card_module=_FakeCardModule(),
            action_card_template_id="tpl-file-action.schema",
            leave_action_card_template_id="tpl-leave-action.schema",
            openapi_endpoint="https://api.dingtalk.com",
        )
        mock_post.return_value = _FakeResponse()

        sender.send_interactive_card(
            {
                "card_type": "leave_request_ready",
                "title": "请假确认",
                "summary": "我将按年假、2026-04-01 09:00 到 2026-04-02 18:00 发起请假审批。",
            }
        )

        self.assertEqual(1, mock_post.call_count)
        _, kwargs = mock_post.call_args
        self.assertEqual("tpl-leave-action.schema", kwargs["json"]["cardTemplateId"])

    def test_sdk_reply_sender_streaming_card_pushes_incremental_updates(self) -> None:
        async def _run_case() -> None:
            _FakeAsyncCardReplier.latest = None
            _FakeAsyncCardReplier.raise_on_processing_stream = False
            handler = _FakeSdkHandler()
            sender = _SdkReplySender(
                handler=handler,
                incoming_message=object(),
                card_module=_FakeCardModule(),
                ai_card_replier_cls=_FakeAsyncCardReplier,
                streaming_card_settings=StreamingCardSettings(
                    enabled=True,
                    template_id="tpl-typewriter.schema",
                    content_key="content",
                    title_key="title",
                    title="企业 Agent",
                    chunk_chars=2,
                    interval_seconds=0.0,
                    min_chars=1,
                ),
                async_sleep_fn=_noop_sleep,
            )
            sender.send_text("abcdef")
            await asyncio.sleep(0)

            self.assertEqual([], handler.text_messages)
            instance = _FakeAsyncCardReplier.latest
            self.assertIsNotNone(instance)
            assert instance is not None
            self.assertEqual("tpl-typewriter.schema", instance.create_calls[0]["card_template_id"])
            self.assertEqual(
                [{"content_value": "ab"}, {"content_value": "abcd"}, {"content_value": "abcdef"}],
                [{"content_value": item["content_value"]} for item in instance.streaming_calls[:-1]],
            )
            self.assertTrue(instance.streaming_calls[-1]["finished"])
            self.assertFalse(instance.streaming_calls[-1]["failed"])

        asyncio.run(_run_case())

    def test_sdk_reply_sender_streaming_failure_marks_failed_and_falls_back_to_text(self) -> None:
        async def _run_case() -> None:
            _FakeAsyncCardReplier.latest = None
            _FakeAsyncCardReplier.raise_on_processing_stream = True
            handler = _FakeSdkHandler()
            sender = _SdkReplySender(
                handler=handler,
                incoming_message=object(),
                card_module=_FakeCardModule(),
                ai_card_replier_cls=_FakeAsyncCardReplier,
                streaming_card_settings=StreamingCardSettings(
                    enabled=True,
                    template_id="tpl-typewriter.schema",
                    content_key="content",
                    title_key="",
                    title="",
                    chunk_chars=2,
                    interval_seconds=0.0,
                    min_chars=1,
                ),
                async_sleep_fn=_noop_sleep,
            )
            sender.send_text("abcdef")
            await asyncio.sleep(0)

            self.assertEqual(["abcdef"], handler.text_messages)
            instance = _FakeAsyncCardReplier.latest
            self.assertIsNotNone(instance)
            assert instance is not None
            self.assertEqual(1, len(instance.streaming_calls))
            self.assertTrue(instance.streaming_calls[0]["failed"])
            self.assertFalse(instance.streaming_calls[0]["finished"])

        asyncio.run(_run_case())

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
        self.assertIn("intent", outcome["llm_trace"])
        self.assertIn("content", outcome["llm_trace"])
        self.assertIn("orchestrator_shadow", outcome["llm_trace"])

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
            _make_payload(text="我要申请采购制度文件权限"),
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
                text="我要申请采购制度文件权限",
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
                text="我要申请采购制度文件权限",
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
        self.assertEqual("file_lookup_confirm_required", first["reason"])
        self.assertEqual("file_request", first["intent"])
        self.assertEqual(0, len(sender.text_messages))
        self.assertEqual(1, len(sender.card_payloads))
        self.assertEqual("file_request_confirmation", sender.card_payloads[0]["card_type"])
        request_id = str(sender.card_payloads[0]["request_id"])
        self.assertTrue(request_id.startswith("file-req-"))

        second = handle_single_chat_payload(
            {
                "request_id": request_id,
                "approval_action": "确认申请",
                "approver_user_id": "user-file-stream-1",
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("file_request", second["intent"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertIn("申请已提交", sender.text_messages[0])
        self.assertNotIn("请求编号", sender.text_messages[0])

        third = handle_single_chat_payload(
            {
                "request_id": request_id,
                "approval_action": "同意",
                "approver_user_id": "人事行政",
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(third["handled"])
        self.assertEqual("file_approval_approved", third["reason"])
        self.assertEqual("file_request", third["intent"])
        self.assertEqual(4, len(sender.text_messages))
        self.assertIn("优先为您提供扫描件", sender.text_messages[1])
        self.assertIn("点击下载：[下载文件](", sender.text_messages[2])
        self.assertIn("复制链接：https://example.local/files/dingyingqi-contract-2024-scan", sender.text_messages[2])
        self.assertIn("文件已发送，请查收", sender.text_messages[3])
        self.assertEqual(1, len(sender.card_payloads))

    def test_handle_single_chat_payload_file_request_multi_match_returns_text_selection(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_MultiHitFileRepository()))

        first = handle_single_chat_payload(
            _make_payload(
                text="我要采购合同",
                conversation_id="conv-file-stream-multi-1",
                sender_id="user-file-stream-multi-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_multiple_matches", first["reason"])
        self.assertEqual("file_request", first["intent"])
        self.assertEqual("text", first["channel"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertIn("找到多个匹配文件", sender.text_messages[0])
        self.assertEqual(0, len(sender.card_payloads))

    def test_handle_single_chat_payload_multi_match_timeout_then_leave_routes_normally(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        clock = _FakeClock()
        service = SingleChatService(
            file_request_service=FileRequestService(
                file_repository=_MultiHitFileRepository(),
                now_provider=clock.now,
            )
        )

        first = handle_single_chat_payload(
            _make_payload(
                text="我要采购合同",
                conversation_id="conv-file-stream-multi-2",
                sender_id="user-file-stream-multi-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_multiple_matches", first["reason"])

        clock.advance(seconds=601)
        second = handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-file-stream-multi-2",
                sender_id="user-file-stream-multi-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("leave", second["intent"])
        self.assertEqual("leave_workflow_collecting", second["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("开始和结束时间", sender.text_messages[-1])
        self.assertEqual(0, len(sender.card_payloads))

    def test_handle_single_chat_payload_leave_workflow_requires_button_confirmation(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-1",
                sender_id="user-leave-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("leave_workflow_collecting", first["reason"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertIn("开始和结束时间", sender.text_messages[-1])
        self.assertEqual(0, len(sender.card_payloads))

        second = handle_single_chat_payload(
            _make_payload(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-stream-1",
                sender_id="user-leave-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("leave_workflow_ready", second["reason"])
        self.assertEqual("leave_request_ready", sender.card_payloads[-1]["card_type"])
        self.assertNotIn("draft_fields", sender.card_payloads[-1])

        third = handle_single_chat_payload(
            _make_payload(
                text="确认",
                conversation_id="conv-leave-stream-1",
                sender_id="user-leave-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("leave_workflow_waiting_button_action", third["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("点击卡片按钮", sender.text_messages[-1])

    def test_handle_single_chat_payload_reimbursement_workflow_with_callback_submit(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        creator = _StubReimbursementApprovalCreator(
            ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id="proc-rmb-stream-1")
        )
        service = SingleChatService(
            reimbursement_request_orchestrator=ReimbursementRequestOrchestrator(
                travel_application_provider=_StubTravelApplicationProvider(),
                attachment_processor=_StubReimbursementAttachmentProcessor(),
                approval_creator=creator,
            )
        )

        first = handle_single_chat_payload(
            _make_payload(
                text="我要报销差旅费",
                conversation_id="conv-rmb-stream-1",
                sender_id="user-rmb-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("reimbursement_travel_collecting_trip", first["reason"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertIn("你最近的出差申请", sender.text_messages[-1])

        second = handle_single_chat_payload(
            _make_payload(
                text="1",
                conversation_id="conv-rmb-stream-1",
                sender_id="user-rmb-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("reimbursement_travel_collecting_attachment", second["reason"])
        self.assertIn("差旅费报销单（Excel）", sender.text_messages[-1])

        third = handle_single_chat_payload(
            _make_payload(
                text="",
                message_type="file",
                conversation_id="conv-rmb-stream-1",
                sender_id="user-rmb-stream-1",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("reimbursement_travel_collecting_company", third["reason"])
        self.assertIn("部门：总经办", sender.text_messages[-1])

        fourth = handle_single_chat_payload(
            _make_payload(
                text="SY",
                conversation_id="conv-rmb-stream-1",
                sender_id="user-rmb-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("reimbursement_travel_ready", fourth["reason"])
        self.assertEqual("reimbursement_request_ready", sender.card_payloads[-1]["card_type"])

        fifth = handle_single_chat_payload(
            _make_reimbursement_callback_payload(
                action_id="reimbursement_confirm_submit",
                conversation_id="conv-rmb-stream-1",
                sender_id="user-rmb-stream-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("reimbursement_travel_submitted", fifth["reason"])
        self.assertEqual("reimbursement", fifth["intent"])
        self.assertEqual("reimbursement_confirm_submit", fifth["reimbursement_action"])
        self.assertEqual("submitted", fifth["reimbursement_status"])
        self.assertIn("已提交，审批中", sender.text_messages[-1])
        self.assertEqual("trip-1", creator.submission.travel_process_instance_id)
        self.assertEqual("YXQY", creator.submission.fixed_company)
        self.assertEqual("SY", creator.submission.cost_company)

    def test_handle_single_chat_payload_leave_confirm_button_submits_text(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")
                )
            )
        )

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-submit-1",
                sender_id="user-leave-stream-submit-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-stream-submit-1",
                sender_id="user-leave-stream-submit-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        third = handle_single_chat_payload(
            _make_leave_callback_payload(
                action_id="leave_confirm_submit",
                conversation_id="conv-leave-stream-submit-1",
                sender_id="user-leave-stream-submit-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        self.assertEqual("leave_workflow_submitted", third["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("已帮你发起", sender.text_messages[-1])

    def test_handle_single_chat_payload_leave_confirm_button_returns_fallback_text(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=False, reason="api_error")
                )
            )
        )

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-submit-2",
                sender_id="user-leave-stream-submit-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-stream-submit-2",
                sender_id="user-leave-stream-submit-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        third = handle_single_chat_payload(
            _make_leave_callback_payload(
                action_id="leave_confirm_submit",
                conversation_id="conv-leave-stream-submit-2",
                sender_id="user-leave-stream-submit-2",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        self.assertEqual("leave_workflow_handoff_fallback", third["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("暂时没能直接帮你发起钉钉审批", sender.text_messages[-1])
        self.assertIn("OA审批", sender.text_messages[-1])

    def test_handle_single_chat_payload_leave_callback_alias_confirm_request_with_leave_outtrackid(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-alias-1")
                )
            )
        )

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-alias-1",
                sender_id="user-leave-stream-alias-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-stream-alias-1",
                sender_id="user-leave-stream-alias-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        third = handle_single_chat_payload(
            {
                "data": {
                    "type": "actionCallback",
                    "userId": "user-leave-stream-alias-1",
                    "extension": "{\"openConversationId\":\"conv-leave-stream-alias-1\"}",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{\"local_input\":\"submit\"}}}",
                    "outTrackId": "leave-confirm-alias-1",
                }
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        self.assertTrue(third["handled"])
        self.assertEqual("leave_workflow_submitted", third["reason"])
        self.assertEqual("leave", third["intent"])
        self.assertEqual("leave_confirm_submit", third["leave_action"])

    def test_handle_single_chat_payload_leave_callback_uses_space_id_when_open_conversation_id_missing(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-space-id-1")
                )
            )
        )

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-spaceid-1",
                sender_id="user-leave-stream-spaceid-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="明天后天年假",
                conversation_id="conv-leave-stream-spaceid-1",
                sender_id="user-leave-stream-spaceid-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        third = handle_single_chat_payload(
            {
                "data": {
                    "type": "actionCallback",
                    "userId": "user-leave-stream-spaceid-1",
                    "extension": "{}",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{}}}",
                    "spaceId": "conv-leave-stream-spaceid-1",
                    "outTrackId": "leave-confirm-spaceid-1",
                    "value": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{}}}",
                }
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        self.assertTrue(third["handled"])
        self.assertEqual("leave_workflow_submitted", third["reason"])
        self.assertEqual("leave_confirm_submit", third["leave_action"])

    def test_handle_single_chat_payload_leave_cancel_button_returns_cancelled_text(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-cancel-1",
                sender_id="user-leave-stream-cancel-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="明天年假",
                conversation_id="conv-leave-stream-cancel-1",
                sender_id="user-leave-stream-cancel-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        third = handle_single_chat_payload(
            _make_leave_callback_payload(
                action_id="leave_cancel_submit",
                conversation_id="conv-leave-stream-cancel-1",
                sender_id="user-leave-stream-cancel-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )

        self.assertEqual("leave_workflow_cancelled", third["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("已取消", sender.text_messages[-1])

    def test_handle_single_chat_payload_leave_confirm_button_expired_requires_restart(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        clock = _FakeClock()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(
                approval_creator=_StubLeaveApprovalCreator(
                    LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-expired-1")
                ),
                now_provider=clock.now,
            )
        )

        handle_single_chat_payload(
            _make_payload(
                text="我要请假",
                conversation_id="conv-leave-stream-expired-1",
                sender_id="user-leave-stream-expired-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        handle_single_chat_payload(
            _make_payload(
                text="明天年假",
                conversation_id="conv-leave-stream-expired-1",
                sender_id="user-leave-stream-expired-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        clock.advance(seconds=301)
        third = handle_single_chat_payload(
            _make_leave_callback_payload(
                action_id="leave_confirm_submit",
                conversation_id="conv-leave-stream-expired-1",
                sender_id="user-leave-stream-expired-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("leave_workflow_confirmation_expired", third["reason"])
        self.assertEqual(2, len(sender.text_messages))
        self.assertIn("重新发送“我要请假”", sender.text_messages[-1])

    def test_handle_single_chat_payload_accepts_button_id_callback_for_confirm(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-file-stream-button-1",
                sender_id="user-file-stream-button-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_confirm_required", first["reason"])
        request_id = str(sender.card_payloads[0]["request_id"])

        second = handle_single_chat_payload(
            {
                "buttonId": f"confirm_request::{request_id}",
                "sender_id": "user-file-stream-button-1",
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("pending", second["approval_status"])

    def test_handle_single_chat_payload_accepts_action_only_callback_for_confirm_by_session(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-file-stream-action-only-1",
                sender_id="user-file-stream-action-only-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_confirm_required", first["reason"])

        second = handle_single_chat_payload(
            {
                "approval_action": "确认申请",
                "conversation_type": "single",
                "conversation_id": "conv-file-stream-action-only-1",
                "sender_id": "user-file-stream-action-only-1",
                "message_type": "interactive_card_callback",
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("pending", second["approval_status"])

    def test_handle_single_chat_payload_accepts_plain_text_confirm_by_session(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-file-stream-text-confirm-1",
                sender_id="user-file-stream-text-confirm-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_confirm_required", first["reason"])

        second = handle_single_chat_payload(
            _make_payload(
                text="确认申请",
                conversation_id="conv-file-stream-text-confirm-1",
                sender_id="user-file-stream-text-confirm-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("pending", second["approval_status"])

    def test_handle_single_chat_payload_plain_text_cancel_without_pending_is_not_callback(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        outcome = handle_single_chat_payload(
            _make_payload(
                text="取消",
                conversation_id="conv-file-stream-cancel-no-pending-1",
                sender_id="user-file-stream-cancel-no-pending-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertNotEqual("file_approval_not_found", outcome["reason"])
        self.assertGreaterEqual(len(sender.text_messages), 1)
        self.assertNotIn("已收到按钮点击", sender.text_messages[0])

    def test_handle_single_chat_payload_plain_text_cancel_with_msgtype_without_pending_is_not_callback(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        outcome = handle_single_chat_payload(
            {
                "event_id": "evt-file-stream-cancel-msgtype-1",
                "conversation_id": "conv-file-stream-cancel-msgtype-1",
                "conversation_type": "single",
                "sender_id": "user-file-stream-cancel-msgtype-1",
                "msgtype": "text",
                "text": "取消",
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertNotEqual("file_approval_not_found", outcome["reason"])
        self.assertGreaterEqual(len(sender.text_messages), 1)
        self.assertNotIn("已收到按钮点击", sender.text_messages[0])

    def test_handle_single_chat_payload_accepts_card_callback_shape_with_component_id(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-file-stream-card-1",
                sender_id="user-file-stream-card-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_confirm_required", first["reason"])

        second = handle_single_chat_payload(
            {
                "data": {
                    "type": "actionCallback",
                    "userId": "user-file-stream-card-1",
                    "extension": "{\"openConversationId\":\"conv-file-stream-card-1\"}",
                    "content": "{\"componentType\":\"button\",\"componentId\":\"confirm_request\"}",
                }
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("pending", second["approval_status"])

    def test_handle_single_chat_payload_accepts_official_action_ids_callback_shape(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        first = handle_single_chat_payload(
            _make_payload(
                text="我要定影器采购合同文件",
                conversation_id="conv-file-stream-official-1",
                sender_id="user-file-stream-official-1",
            ),
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertEqual("file_lookup_confirm_required", first["reason"])

        second = handle_single_chat_payload(
            {
                "data": {
                    "corpId": "ding-corp",
                    "type": "actionCallback",
                    "userId": "user-file-stream-official-1",
                    "content": "{\"cardPrivateData\":{\"actionIds\":[\"confirm_request\"],\"params\":{\"local_input\":\"submit\"}}}",
                    "outTrackId": "track-official-1",
                }
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertTrue(second["handled"])
        self.assertEqual("file_lookup_pending_approval", second["reason"])
        self.assertEqual("pending", second["approval_status"])

    def test_handle_single_chat_payload_not_found_callback_returns_user_facing_hint(self) -> None:
        sender = _FakeSender()
        resolver = self._build_resolver()
        service = SingleChatService()

        outcome = handle_single_chat_payload(
            {
                "data": {
                    "type": "actionCallback",
                    "userId": "unknown-user",
                    "extension": "{\"openConversationId\":\"unknown-conv\"}",
                    "content": "{\"componentType\":\"button\",\"componentId\":\"confirm_request\"}",
                }
            },
            service=service,
            sender=sender,
            user_context_resolver=resolver,
        )
        self.assertFalse(outcome["handled"])
        self.assertEqual("file_approval_not_found", outcome["reason"])
        self.assertEqual("text", outcome["channel"])
        self.assertEqual(1, len(sender.text_messages))
        self.assertIn("未定位到待处理申请", sender.text_messages[0])

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
