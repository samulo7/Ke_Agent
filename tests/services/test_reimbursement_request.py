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
    def __init__(self, amount: str) -> None:
        self._amount = amount

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
        )


class _StubApprovalCreator:
    def __init__(self) -> None:
        self.submissions = []

    def submit(self, submission):  # type: ignore[no-untyped-def]
        self.submissions.append(submission)
        return ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id="proc-1")


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


if __name__ == "__main__":
    unittest.main()
