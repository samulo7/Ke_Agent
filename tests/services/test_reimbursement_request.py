from __future__ import annotations

import unittest
from datetime import datetime, timezone

from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.reimbursement import ReimbursementApprovalResult, ReimbursementAttachmentProcessResult, TravelApplication
from app.services.reimbursement_request import ReimbursementRequestOrchestrator


class _StubTravelProvider:
    def list_recent_approved(self, *, originator_user_id: str, lookback_days: int, now: datetime) -> list[TravelApplication]:
        del originator_user_id, lookback_days, now
        return [TravelApplication(process_instance_id="trip-1", start_date="2026-03-15", destination="北京", purpose="云亨售后")]


class _StubAttachmentProcessor:
    def __init__(
        self,
        amount: str,
        *,
        table_amount: str | None = None,
        uppercase_amount_raw: str = "",
        uppercase_amount_numeric: str = "",
        amount_conflict: bool = False,
        amount_conflict_note: str = "",
        amount_source: str = "table",
        amount_source_note: str = "表格金额",
    ) -> None:
        self._amount = amount
        self._table_amount = table_amount
        self._uppercase_amount_raw = uppercase_amount_raw
        self._uppercase_amount_numeric = uppercase_amount_numeric
        self._amount_conflict = amount_conflict
        self._amount_conflict_note = amount_conflict_note
        self._amount_source = amount_source
        self._amount_source_note = amount_source_note

    def process(
        self,
        *,
        message: IncomingChatMessage,
        conversation_id: str,
        sender_id: str,
    ) -> ReimbursementAttachmentProcessResult:
        del message, conversation_id, sender_id
        return ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department="总经办",
            amount=self._amount,
            attachment_media_id="media-pdf-1",
            table_amount=(self._table_amount or self._amount),
            uppercase_amount_raw=self._uppercase_amount_raw,
            uppercase_amount_numeric=self._uppercase_amount_numeric,
            amount_conflict=self._amount_conflict,
            amount_conflict_note=self._amount_conflict_note,
            amount_source=self._amount_source,
            amount_source_note=self._amount_source_note,
        )


class _StubApprovalCreator:
    def __init__(self) -> None:
        self.submissions = []

    def submit(self, submission):  # type: ignore[no-untyped-def]
        self.submissions.append(submission)
        return ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")


class _FailingApprovalCreator:
    def submit(self, submission):  # type: ignore[no-untyped-def]
        del submission
        return ReimbursementApprovalResult(
            success=False,
            reason="api_error",
            failure_category="permission_identity",
            raw_errcode=60020,
            raw_errmsg="permission denied for process create",
        )


def _make_message(
    *,
    text: str,
    message_type: str = "text",
    file_name: str = "",
    file_content_base64: str = "",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-rmb-1",
        conversation_id="conv-rmb-1",
        conversation_type="single",
        sender_id="user-rmb-1",
        message_type=message_type,
        text=text,
        file_name=file_name,
        file_content_base64=file_content_base64,
    )


class ReimbursementRequestOrchestratorTests(unittest.TestCase):
    def test_over_five_thousand_boundary(self) -> None:
        creator_5000 = _StubApprovalCreator()
        service_5000 = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor("5000"),
            approval_creator=creator_5000,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        self._run_basic_workflow(service_5000)
        self.assertEqual("否", creator_5000.submissions[-1].over_five_thousand)

        creator_500001 = _StubApprovalCreator()
        service_500001 = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor("5000.01"),
            approval_creator=creator_500001,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        self._run_basic_workflow(service_500001)
        self.assertEqual("是", creator_500001.submissions[-1].over_five_thousand)

    def test_submission_fields_are_built_from_workflow_context(self) -> None:
        creator = _StubApprovalCreator()
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor("106"),
            approval_creator=creator,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        self._run_basic_workflow(orchestrator)
        submission = creator.submissions[-1]
        self.assertEqual("trip-1", submission.travel_process_instance_id)
        self.assertEqual("YXQY", submission.fixed_company)
        self.assertEqual("总经办", submission.department)
        self.assertEqual("SY", submission.cost_company)
        self.assertEqual("2026-04-01", submission.date)
        self.assertEqual("106", submission.amount)
        self.assertEqual("media-pdf-1", submission.attachment_media_id)

    def test_collecting_company_text_includes_amount_source_note(self) -> None:
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor(
                "106",
                amount_source="uppercase_llm",
                amount_source_note="大写金额校验通过",
            ),
            approval_creator=_StubApprovalCreator(),
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        start = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="我要报销差旅费"),
            user_context=None,
            force_start=True,
        )
        assert start is not None
        select = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="1"),
            user_context=None,
            force_start=False,
        )
        assert select is not None
        upload = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(
                text="",
                message_type="file",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            user_context=None,
            force_start=False,
        )
        assert upload is not None
        self.assertEqual("reimbursement_travel_collecting_company", upload.reason)
        self.assertIn("金额来源：大写金额校验通过", upload.reply.text or "")

    def test_submit_failure_returns_actionable_reason_and_suggestion(self) -> None:
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor("106"),
            approval_creator=_FailingApprovalCreator(),
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        start = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="我要报销差旅费"),
            user_context=None,
            force_start=True,
        )
        assert start is not None
        select = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="1"),
            user_context=None,
            force_start=False,
        )
        assert select is not None
        upload = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(
                text="",
                message_type="file",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            user_context=None,
            force_start=False,
        )
        assert upload is not None
        choose = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="SY"),
            user_context=None,
            force_start=False,
        )
        assert choose is not None
        failed = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_confirm_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_handoff_fallback", failed.reason)
        self.assertIn("失败原因", failed.reply.text or "")
        self.assertIn("建议", failed.reply.text or "")

    def test_amount_conflict_blocks_submission_until_choice_is_made(self) -> None:
        creator = _StubApprovalCreator()
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor(
                "106",
                table_amount="106",
                uppercase_amount_raw="壹佰壹拾元整",
                uppercase_amount_numeric="110",
                amount_conflict=True,
                amount_conflict_note="合计金额与大写金额不一致，请确认提交金额来源。",
            ),
            approval_creator=creator,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        start = self._run_until_ready_or_conflict(orchestrator=orchestrator)
        self.assertEqual("reimbursement_travel_amount_conflict_confirmation", start.reason)
        self.assertEqual("interactive_card", start.reply.channel)
        self.assertEqual(0, len(creator.submissions))

        blocked = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_confirm_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_amount_conflict_confirmation", blocked.reason)
        self.assertEqual(0, len(creator.submissions))

        choose_table = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_amount_use_table",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_ready", choose_table.reason)

        submitted = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_confirm_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_submitted", submitted.reason)
        self.assertEqual("106", creator.submissions[-1].amount)

    def test_amount_conflict_supports_using_uppercase_amount(self) -> None:
        creator = _StubApprovalCreator()
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor(
                "106",
                table_amount="106",
                uppercase_amount_raw="壹佰壹拾元整",
                uppercase_amount_numeric="110",
                amount_conflict=True,
                amount_conflict_note="合计金额与大写金额不一致，请确认提交金额来源。",
            ),
            approval_creator=creator,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        start = self._run_until_ready_or_conflict(orchestrator=orchestrator)
        self.assertEqual("reimbursement_travel_amount_conflict_confirmation", start.reason)

        choose_uppercase = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_amount_use_uppercase",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_ready", choose_uppercase.reason)

        submitted = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_confirm_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_submitted", submitted.reason)
        self.assertEqual("110", creator.submissions[-1].amount)

    def test_amount_conflict_can_be_cancelled(self) -> None:
        creator = _StubApprovalCreator()
        orchestrator = ReimbursementRequestOrchestrator(
            travel_application_provider=_StubTravelProvider(),
            attachment_processor=_StubAttachmentProcessor(
                "106",
                table_amount="106",
                uppercase_amount_raw="壹佰壹拾元整",
                uppercase_amount_numeric="110",
                amount_conflict=True,
                amount_conflict_note="合计金额与大写金额不一致，请确认提交金额来源。",
            ),
            approval_creator=creator,
            now_provider=lambda: datetime(2026, 4, 1, 9, 0, tzinfo=timezone.utc),
        )
        start = self._run_until_ready_or_conflict(orchestrator=orchestrator)
        self.assertEqual("reimbursement_travel_amount_conflict_confirmation", start.reason)

        cancelled = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_cancel_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        self.assertEqual("reimbursement_travel_cancelled", cancelled.reason)
        self.assertEqual(0, len(creator.submissions))

    @staticmethod
    def _run_basic_workflow(orchestrator: ReimbursementRequestOrchestrator) -> None:
        start = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="我要报销差旅费"),
            user_context=None,
            force_start=True,
        )
        assert start is not None
        assert start.reason == "reimbursement_travel_collecting_trip"

        select = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="1"),
            user_context=None,
            force_start=False,
        )
        assert select is not None
        assert select.reason == "reimbursement_travel_collecting_attachment"

        upload = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(
                text="",
                message_type="file",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            user_context=None,
            force_start=False,
        )
        assert upload is not None
        assert upload.reason == "reimbursement_travel_collecting_company"

        choose = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="SY"),
            user_context=None,
            force_start=False,
        )
        assert choose is not None
        assert choose.reason == "reimbursement_travel_ready"

        confirmed = orchestrator.handle_confirmation_action_by_session(
            action="reimbursement_confirm_submit",
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
        )
        assert confirmed.reason == "reimbursement_travel_submitted"

    @staticmethod
    def _run_until_ready_or_conflict(*, orchestrator: ReimbursementRequestOrchestrator):
        start = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="我要报销差旅费"),
            user_context=None,
            force_start=True,
        )
        assert start is not None
        assert start.reason == "reimbursement_travel_collecting_trip"

        select = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="1"),
            user_context=None,
            force_start=False,
        )
        assert select is not None
        assert select.reason == "reimbursement_travel_collecting_attachment"

        upload = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(
                text="",
                message_type="file",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            user_context=None,
            force_start=False,
        )
        assert upload is not None
        assert upload.reason == "reimbursement_travel_collecting_company"

        choose = orchestrator.handle(
            conversation_id="conv-rmb-1",
            sender_id="user-rmb-1",
            message=_make_message(text="SY"),
            user_context=None,
            force_start=False,
        )
        assert choose is not None
        return choose


if __name__ == "__main__":
    unittest.main()
