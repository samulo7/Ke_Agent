from __future__ import annotations

import sqlite3
import unittest
from datetime import datetime, timedelta, timezone

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.schemas.file_asset import FileAsset, FileSearchCandidate, FileSearchResult
from app.schemas.llm import IntentInferenceResult, OrchestratorShadowResult
from app.schemas.user_context import UserContext
from app.services.document_request_draft import DocumentRequestDraftOrchestrator
from app.services.file_request import FileRequestService
from app.services.intent_classifier import IntentClassification
from app.services.leave_request import LeaveApprovalResult, LeaveRequestOrchestrator
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.tone_resolver import ToneResolver


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


class _CountingKnowledgeAnswerService:
    def __init__(self) -> None:
        self.calls = 0

    def answer(self, *, question: str, intent: str, access_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        raise RuntimeError("should not be called in file_request path")


class _StubIntentClassifier:
    def __init__(self, *, intent: str, confidence: float = 0.99) -> None:
        self._intent = intent
        self._confidence = confidence

    def classify(self, text: str) -> IntentClassification:
        return IntentClassification(intent=self._intent, confidence=self._confidence)  # type: ignore[arg-type]


class _StubFileRequestService:
    def __init__(self) -> None:
        self.calls = 0

    def handle(self, *, message, query_text, user_context=None):  # type: ignore[no-untyped-def]
        self.calls += 1
        return ChatHandleResult(
            handled=False,
            reason="file_lookup_collecting",
            intent="file_request",
            reply=AgentReply(channel="text", text="请问您需要纸质版还是扫描版？"),
        )


class _StubLeaveApprovalCreator:
    def __init__(self, result: LeaveApprovalResult) -> None:
        self._result = result

    def submit(self, submission):  # type: ignore[no-untyped-def]
        self.submission = submission
        return self._result


class _AlwaysHitFileRepository:
    def __init__(self) -> None:
        self._scan_asset = FileAsset(
            file_id="file-dingyingqi-contract-2024-scan",
            contract_key="dingyingqi_contract",
            title="定影器采购合同-2024版",
            variant="scan",
            file_url="https://example.local/files/dingyingqi-contract-2024-scan",
            tags=("采购", "合同", "定影器", "2024"),
            status="active",
            updated_at="2026-03-27",
        )
        self._paper_asset = FileAsset(
            file_id="file-dingyingqi-contract-2024-paper",
            contract_key="dingyingqi_contract",
            title="定影器采购合同-2024版",
            variant="paper",
            file_url="https://example.local/files/dingyingqi-contract-2024-paper",
            tags=("采购", "合同", "定影器", "2024"),
            status="active",
            updated_at="2026-03-27",
        )

    def search(self, *, query_text: str, variant: str, requester_context=None):  # type: ignore[no-untyped-def]
        asset = self._scan_asset if variant == "scan" else self._paper_asset
        return FileSearchResult(matched=True, match_score=0.99, asset=asset)


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
                updated_at="2026-03-27",
            ),
            FileAsset(
                file_id="file-2",
                contract_key="printer_contract",
                title="打印机采购合同-2023版",
                variant="scan",
                file_url="https://example.local/files/printer-contract-2023-scan",
                tags=("采购", "合同", "打印机", "2023"),
                status="active",
                updated_at="2026-03-27",
            ),
            FileAsset(
                file_id="file-3",
                contract_key="copier_contract",
                title="复印机采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/copier-contract-2024-scan",
                tags=("采购", "合同", "复印机", "2024"),
                status="active",
                updated_at="2026-03-27",
            ),
        )

    def search(self, *, query_text: str, variant: str, requester_context=None):  # type: ignore[no-untyped-def]
        del query_text, requester_context
        if variant != "scan":
            return FileSearchResult.no_hit()
        return FileSearchResult(
            matched=True,
            match_score=0.91,
            asset=self._assets[0],
            candidates=tuple(FileSearchCandidate(asset=asset, match_score=0.9 - index * 0.01) for index, asset in enumerate(self._assets)),
        )


class _StubLLMIntentService:
    def __init__(self, *, intent: str, confidence: float = 0.95, reason: str = "stub") -> None:
        self._intent = intent
        self._confidence = confidence
        self._reason = reason

    def infer(self, *, text: str, conversation_id: str, sender_id: str) -> IntentInferenceResult:
        return IntentInferenceResult(
            intent=self._intent,  # type: ignore[arg-type]
            confidence=self._confidence,
            reason=self._reason,
            model="qwen-plus",
            fallback_used=False,
            validation_passed=True,
        )


class _StubShadowService:
    def suggest(self, *, question: str, intent: str, rule_action: str, conversation_id: str, sender_id: str):  # type: ignore[no-untyped-def]
        return OrchestratorShadowResult(
            suggested_action=rule_action,  # type: ignore[arg-type]
            rule_action=rule_action,  # type: ignore[arg-type]
            reason="shadow-ok",
            model="qwen-plus",
            fallback_used=False,
            validation_passed=True,
        )


def make_message(
    *,
    conversation_type: str = "single",
    message_type: str = "text",
    text: str = "hello",
    conversation_id: str = "conv-1",
    sender_id: str = "user-1",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-1",
        conversation_id=conversation_id,
        conversation_type=conversation_type,  # type: ignore[arg-type]
        sender_id=sender_id,
        message_type=message_type,
        text=text,
    )


def make_user_context(*, user_id: str, dept_id: str, dept_name: str | None = None) -> UserContext:
    return UserContext(
        user_id=user_id,
        user_name=user_id,
        dept_id=dept_id,
        dept_name=dept_name or dept_id,
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
    def test_file_request_routes_to_file_service_without_calling_knowledge_service(self) -> None:
        knowledge_service = _CountingKnowledgeAnswerService()
        file_service = _StubFileRequestService()
        service = SingleChatService(
            intent_classifier=_StubIntentClassifier(intent="file_request"),
            knowledge_answer_service=knowledge_service,
            file_request_service=file_service,  # type: ignore[arg-type]
        )

        result = service.handle(make_message(text="帮我找一下定影器的采购合同"))

        self.assertEqual("file_lookup_collecting", result.reason)
        self.assertEqual("file_request", result.intent)
        self.assertEqual(1, file_service.calls)
        self.assertEqual(0, knowledge_service.calls)

    def test_pending_file_request_does_not_hijack_unrelated_policy_question(self) -> None:
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_AlwaysHitFileRepository()))
        first = service.handle(
            make_message(
                text="我想要定影器采购合同",
                conversation_id="conv-pending-route-1",
                sender_id="user-pending-route-1",
            )
        )
        self.assertEqual("file_lookup_confirm_required", first.reason)

        second = service.handle(
            make_message(
                text="采购合同流程是什么",
                conversation_id="conv-pending-route-1",
                sender_id="user-pending-route-1",
            )
        )
        self.assertEqual("policy_process", second.intent)
        self.assertEqual("knowledge_answer", second.reason)

    def test_pending_file_request_keeps_progress_followup_in_file_flow(self) -> None:
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_AlwaysHitFileRepository()))
        first = service.handle(
            make_message(
                text="我想要定影器采购合同",
                conversation_id="conv-pending-route-2",
                sender_id="user-pending-route-2",
            )
        )
        self.assertEqual("file_lookup_confirm_required", first.reason)

        second = service.handle(
            make_message(
                text="审批进度",
                conversation_id="conv-pending-route-2",
                sender_id="user-pending-route-2",
            )
        )
        self.assertEqual("file_request", second.intent)
        self.assertEqual("file_lookup_confirm_required", second.reason)

    def test_pending_file_request_followup_file_query_still_returns_pending_status(self) -> None:
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_AlwaysHitFileRepository()))
        first = service.handle(
            make_message(
                text="我想要定影器采购合同",
                conversation_id="conv-pending-route-3",
                sender_id="user-pending-route-3",
            )
        )
        self.assertEqual("file_lookup_confirm_required", first.reason)

        second = service.handle(
            make_message(
                text="定影器采购合同在哪里下载",
                conversation_id="conv-pending-route-3",
                sender_id="user-pending-route-3",
            )
        )
        self.assertEqual("file_request", second.intent)
        self.assertEqual("file_lookup_confirm_required", second.reason)

    def test_file_request_multi_match_returns_selection_text_without_confirmation_card(self) -> None:
        service = SingleChatService(file_request_service=FileRequestService(file_repository=_MultiHitFileRepository()))
        result = service.handle(
            make_message(
                text="我要采购合同",
                conversation_id="conv-multi-route-1",
                sender_id="user-multi-route-1",
            )
        )
        self.assertEqual("file_request", result.intent)
        self.assertEqual("file_lookup_multiple_matches", result.reason)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("找到多个匹配文件", result.reply.text or "")

    def test_pending_multi_match_selection_does_not_hijack_leave_intent(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(
            file_request_service=FileRequestService(
                file_repository=_MultiHitFileRepository(),
                now_provider=clock.now,
            )
        )
        first = service.handle(
            make_message(
                text="我要采购合同",
                conversation_id="conv-multi-route-2",
                sender_id="user-multi-route-2",
            )
        )
        self.assertEqual("file_lookup_multiple_matches", first.reason)

        clock.advance(seconds=601)
        second = service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-multi-route-2",
                sender_id="user-multi-route-2",
            )
        )
        self.assertEqual("leave", second.intent)
        self.assertEqual("leave_workflow_collecting", second.reason)
        self.assertEqual("text", second.reply.channel)
        self.assertIn("开始和结束时间", second.reply.text)

    def test_leave_workflow_collects_then_requires_button_confirmation(self) -> None:
        service = SingleChatService()
        start = service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-1",
                sender_id="user-leave-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("leave_workflow_collecting", start.reason)
        self.assertEqual("text", start.reply.channel)
        self.assertIn("开始和结束时间", start.reply.text)

        ready = service.handle(
            make_message(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-1",
                sender_id="user-leave-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("leave_workflow_ready", ready.reason)
        self.assertEqual("leave_request_ready", ready.reply.interactive_card["card_type"])
        self.assertNotIn("draft_fields", ready.reply.interactive_card)
        self.assertIn("我将按年假", ready.reply.interactive_card["summary"])
        self.assertEqual("leave_confirm_submit", ready.reply.interactive_card["actions"][0]["action"])
        self.assertEqual("leave_cancel_submit", ready.reply.interactive_card["actions"][1]["action"])

        text_confirm = service.handle(
            make_message(
                text="可以",
                conversation_id="conv-leave-1",
                sender_id="user-leave-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("leave_workflow_waiting_button_action", text_confirm.reason)
        self.assertEqual("text", text_confirm.reply.channel)
        self.assertIn("点击卡片按钮", text_confirm.reply.text or "")

    def test_leave_workflow_defaults_to_work_hours_when_time_missing(self) -> None:
        service = SingleChatService()
        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-default-hours-1",
                sender_id="user-leave-default-hours-1",
            )
        )
        ready = service.handle(
            make_message(
                text="明天年假",
                conversation_id="conv-leave-default-hours-1",
                sender_id="user-leave-default-hours-1",
            )
        )
        self.assertEqual("leave_workflow_ready", ready.reason)
        summary = ready.reply.interactive_card["summary"]
        self.assertIn("09:00", summary)
        self.assertIn("18:00", summary)

    def test_leave_workflow_parses_minghoutian_as_two_day_range(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(now_provider=clock.now),
        )
        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-minghoutian-1",
                sender_id="user-leave-minghoutian-1",
            )
        )
        ready = service.handle(
            make_message(
                text="明后天年假",
                conversation_id="conv-leave-minghoutian-1",
                sender_id="user-leave-minghoutian-1",
            )
        )
        self.assertEqual("leave_workflow_ready", ready.reason)
        summary = ready.reply.interactive_card["summary"]
        self.assertIn("2026-03-28 09:00", summary)
        self.assertIn("2026-03-29 18:00", summary)

    def test_leave_workflow_submits_approval_when_creator_succeeds(self) -> None:
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )

        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-submit-1",
                sender_id="user-leave-submit-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="1001", dept_name="财务部"),
        )
        service.handle(
            make_message(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-submit-1",
                sender_id="user-leave-submit-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="1001", dept_name="财务部"),
        )

        submitted = service.handle_leave_confirmation_action_by_session(
            action="leave_confirm_submit",
            conversation_id="conv-leave-submit-1",
            sender_id="user-leave-submit-1",
        )
        self.assertEqual("leave_workflow_submitted", submitted.reason)
        self.assertEqual("text", submitted.reply.channel)
        self.assertIn("已帮你发起", submitted.reply.text)
        self.assertEqual("Alice", creator.submission.originator_user_id)
        self.assertEqual("财务部", creator.submission.department)
        self.assertEqual("1001", creator.submission.department_id)
        self.assertEqual("年假", creator.submission.leave_type)
        self.assertEqual("2026-04-01 09:00", creator.submission.leave_start_time)
        self.assertEqual("2026-04-02 18:00", creator.submission.leave_end_time)

    def test_leave_workflow_continues_collecting_when_time_is_ambiguous(self) -> None:
        creator = _StubLeaveApprovalCreator(
            LeaveApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")
        )
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )

        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-submit-ambiguous-1",
                sender_id="user-leave-submit-ambiguous-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        collecting = service.handle(
            make_message(
                text="请两天年假",
                conversation_id="conv-leave-submit-ambiguous-1",
                sender_id="user-leave-submit-ambiguous-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )

        self.assertEqual("leave_workflow_collecting", collecting.reason)
        self.assertEqual("text", collecting.reply.channel)
        self.assertIn("请假时间", collecting.reply.text or "")
        self.assertFalse(hasattr(creator, "submission"))

    def test_leave_workflow_falls_back_when_creator_fails(self) -> None:
        creator = _StubLeaveApprovalCreator(LeaveApprovalResult(success=False, reason="api_error"))
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(approval_creator=creator),
        )

        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-submit-2",
                sender_id="user-leave-submit-2",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        service.handle(
            make_message(
                text="年假 2026-04-01 到 2026-04-02",
                conversation_id="conv-leave-submit-2",
                sender_id="user-leave-submit-2",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )

        fallback = service.handle_leave_confirmation_action_by_session(
            action="leave_confirm_submit",
            conversation_id="conv-leave-submit-2",
            sender_id="user-leave-submit-2",
        )
        self.assertEqual("leave_workflow_handoff_fallback", fallback.reason)
        self.assertEqual("text", fallback.reply.channel)
        self.assertIn("暂时没能直接帮你发起钉钉审批", fallback.reply.text)
        self.assertIn("OA审批", fallback.reply.text)

    def test_leave_workflow_can_start_from_low_confidence_leave_text(self) -> None:
        service = SingleChatService(
            llm_intent_service=_StubLLMIntentService(intent="other", confidence=0.2),  # type: ignore[arg-type]
            orchestrator_shadow_service=_StubShadowService(),  # type: ignore[arg-type]
        )
        result = service.handle(make_message(text="明天后天请两天年假"))
        self.assertTrue(result.handled)
        self.assertEqual("leave", result.intent)
        self.assertEqual("leave_workflow_ready", result.reason)
        self.assertEqual("leave_request_ready", result.reply.interactive_card["card_type"])
        self.assertNotIn("draft_fields", result.reply.interactive_card)

    def test_leave_workflow_times_out_before_confirm_button_click(self) -> None:
        clock = _FakeClock()
        service = SingleChatService(
            leave_request_orchestrator=LeaveRequestOrchestrator(now_provider=clock.now),
        )
        start = service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-timeout-1",
                sender_id="user-leave-timeout-1",
            )
        )
        self.assertEqual("leave_workflow_collecting", start.reason)

        service.handle(
            make_message(
                text="明天年假",
                conversation_id="conv-leave-timeout-1",
                sender_id="user-leave-timeout-1",
            )
        )

        clock.advance(seconds=301)
        timed_out = service.handle_leave_confirmation_action_by_session(
            action="leave_confirm_submit",
            conversation_id="conv-leave-timeout-1",
            sender_id="user-leave-timeout-1",
        )
        self.assertEqual("leave_workflow_confirmation_expired", timed_out.reason)
        self.assertEqual("text", timed_out.reply.channel)
        self.assertIn("重新发送“我要请假”", timed_out.reply.text or "")

    def test_leave_workflow_cancelled_by_button_action(self) -> None:
        service = SingleChatService()
        service.handle(
            make_message(
                text="我要请假",
                conversation_id="conv-leave-cancel-1",
                sender_id="user-leave-cancel-1",
            )
        )
        service.handle(
            make_message(
                text="明天年假",
                conversation_id="conv-leave-cancel-1",
                sender_id="user-leave-cancel-1",
            )
        )

        cancelled = service.handle_leave_confirmation_action_by_session(
            action="leave_cancel_submit",
            conversation_id="conv-leave-cancel-1",
            sender_id="user-leave-cancel-1",
        )
        self.assertEqual("leave_workflow_cancelled", cancelled.reason)
        self.assertEqual("text", cancelled.reply.channel)
        self.assertIn("已取消", cancelled.reply.text or "")

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

    def test_result_contains_llm_trace_contract_fields(self) -> None:
        service = SingleChatService(
            llm_intent_service=_StubLLMIntentService(intent="policy_process"),  # type: ignore[arg-type]
            orchestrator_shadow_service=_StubShadowService(),  # type: ignore[arg-type]
        )
        result = service.handle(make_message(text="宴请标准是什么"))
        self.assertIn("intent", result.llm_trace)
        self.assertIn("content", result.llm_trace)
        self.assertIn("orchestrator_shadow", result.llm_trace)
        self.assertIn("fallback_used", result.llm_trace["intent"])
        self.assertIn("validation_passed", result.llm_trace["content"])
        self.assertIn("suggested_action", result.llm_trace["orchestrator_shadow"])

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

    def test_returns_flow_guidance_text_for_leave(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="请假流程入口在哪"))
        self.assertTrue(result.handled)
        self.assertEqual("flow_guidance_text", result.reason)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("OA审批", result.reply.text or "")
        self.assertIn("我要请假", result.reply.text or "")
        self.assertEqual("leave", result.intent)

    def test_plain_leave_word_starts_leave_workflow(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="请假"))
        self.assertEqual("leave_workflow_collecting", result.reason)
        self.assertEqual("leave", result.intent)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("开始和结束时间", result.reply.text or "")

    def test_natural_leave_phrase_with_duration_starts_leave_workflow(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我要请一天的假，4月7号"))
        self.assertEqual("leave_workflow_collecting", result.reason)
        self.assertEqual("leave", result.intent)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("请假类型", result.reply.text or "")

    def test_returns_flow_guidance_card_for_reimbursement(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="出差报销怎么弄"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])
        self.assertEqual("报销办理指引", result.reply.interactive_card["title"])
        self.assertIn("报销模板", result.reply.interactive_card["primary_action"])
        self.assertEqual("钉钉 > 工作台 > 审批 > 报销", result.reply.interactive_card["entry_point"])
        self.assertEqual("reimbursement", result.intent)

    def test_returns_application_draft_card(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我要申请项目资料权限"))
        self.assertTrue(result.handled)
        self.assertEqual("application_draft_collecting", result.reason)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("application_draft_collecting", result.reply.interactive_card["card_type"])
        self.assertEqual("document_request", result.intent)

    def test_document_request_completes_with_one_followup(self) -> None:
        service = SingleChatService()
        start = service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-1",
                sender_id="user-b14-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("application_draft_collecting", start.reason)
        self.assertEqual("application_draft_collecting", start.reply.interactive_card["card_type"])

        ready = service.handle(
            make_message(
                text="用途: 月度预算复盘；使用时间: 下周一",
                conversation_id="conv-b14-1",
                sender_id="user-b14-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertTrue(ready.handled)
        self.assertEqual("application_draft_ready", ready.reason)
        self.assertEqual("interactive_card", ready.reply.channel)
        self.assertEqual("application_draft_ready", ready.reply.interactive_card["card_type"])
        draft_fields = ready.reply.interactive_card["draft_fields"]
        for key in (
            "applicant_name",
            "department",
            "requested_item",
            "request_purpose",
            "suggested_approver",
        ):
            self.assertTrue(str(draft_fields.get(key, "")).strip(), f"missing field: {key}")
        self.assertEqual("人事行政", str(draft_fields.get("suggested_approver", "")))
        self.assertGreaterEqual(len(ready.reply.interactive_card.get("process_path", [])), 3)

    def test_document_request_natural_purpose_phrase_moves_to_next_missing_field(self) -> None:
        service = SingleChatService()
        start = service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-purpose-1",
                sender_id="user-b14-purpose-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("application_draft_collecting", start.reason)
        self.assertEqual("申请用途", start.reply.interactive_card.get("missing_field"))

        ready = service.handle(
            make_message(
                text="用作采购",
                conversation_id="conv-b14-purpose-1",
                sender_id="user-b14-purpose-1",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertEqual("application_draft_ready", ready.reason)
        self.assertEqual("interactive_card", ready.reply.channel)
        self.assertEqual("人事行政", ready.reply.interactive_card["draft_fields"]["suggested_approver"])

    def test_document_request_exceeds_followup_limit_returns_incomplete(self) -> None:
        service = SingleChatService()
        service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-2",
                sender_id="user-b14-2",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        incomplete = service.handle(
            make_message(
                text="我先看看",
                conversation_id="conv-b14-2",
                sender_id="user-b14-2",
            ),
            user_context=make_user_context(user_id="Alice", dept_id="Finance"),
        )
        self.assertFalse(incomplete.handled)
        self.assertEqual("application_draft_incomplete", incomplete.reason)
        self.assertEqual("text", incomplete.reply.channel)
        self.assertIn("人事行政", incomplete.reply.text or "")

    def test_document_request_times_out_lazily_and_clears_session(self) -> None:
        clock = _FakeClock()
        orchestrator = DocumentRequestDraftOrchestrator(now_provider=clock.now)
        service = SingleChatService(document_request_orchestrator=orchestrator)

        service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-3",
                sender_id="user-b14-3",
            )
        )
        clock.advance(seconds=301)
        timed_out = service.handle(
            make_message(
                text="用途: 项目复盘",
                conversation_id="conv-b14-3",
                sender_id="user-b14-3",
            )
        )
        self.assertFalse(timed_out.handled)
        self.assertEqual("application_draft_timeout", timed_out.reason)

        restarted = service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-3",
                sender_id="user-b14-3",
            )
        )
        self.assertEqual("application_draft_collecting", restarted.reason)

    def test_document_request_can_be_cancelled_explicitly(self) -> None:
        service = SingleChatService()
        service.handle(
            make_message(
                text="我要申请采购制度文件权限",
                conversation_id="conv-b14-4",
                sender_id="user-b14-4",
            )
        )
        cancelled = service.handle(
            make_message(
                text="取消",
                conversation_id="conv-b14-4",
                sender_id="user-b14-4",
            )
        )
        self.assertFalse(cancelled.handled)
        self.assertEqual("application_draft_cancelled", cancelled.reason)

        post_cancel = service.handle(
            make_message(
                text="hello there",
                conversation_id="conv-b14-4",
                sender_id="user-b14-4",
            )
        )
        self.assertEqual("knowledge_no_hit", post_cancel.reason)

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
