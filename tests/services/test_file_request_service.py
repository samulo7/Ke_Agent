from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.file_asset import FileAsset, FileSearchResult
from app.services.file_request import FileApprovalNotifyResult, FileRequestService


class _FakeClock:
    def __init__(self) -> None:
        self.current = datetime(2026, 3, 27, 0, 0, tzinfo=timezone.utc)

    def now(self) -> datetime:
        return self.current

    def advance(self, *, seconds: int) -> None:
        self.current = self.current + timedelta(seconds=seconds)


class _CapturingApprovalNotifier:
    def __init__(self, *, failure_reason: str = "") -> None:
        self.requests = []
        self.cards = []
        self.failure_reason = failure_reason

    def notify(self, *, request, card_payload):  # type: ignore[no-untyped-def]
        self.requests.append(request)
        self.cards.append(card_payload)
        if self.failure_reason:
            return FileApprovalNotifyResult(success=False, reason=self.failure_reason)
        return FileApprovalNotifyResult(success=True, reason="captured")


class _VariantAwareRepository:
    def __init__(self, *, scan_result: FileSearchResult | None, paper_result: FileSearchResult | None = None) -> None:
        self.scan_result = scan_result
        self.paper_result = paper_result if paper_result is not None else scan_result
        self.calls: list[tuple[str, str]] = []

    def search(self, *, query_text: str, variant: str, requester_context=None):  # type: ignore[no-untyped-def]
        self.calls.append((query_text, variant))
        if variant == "scan":
            return self.scan_result or FileSearchResult.no_hit()
        return self.paper_result or FileSearchResult.no_hit()


def make_message(
    *,
    text: str,
    conversation_id: str = "conv-file-1",
    sender_id: str = "user-file-1",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-file-1",
        conversation_id=conversation_id,
        conversation_type="single",
        sender_id=sender_id,
        message_type="text",
        text=text,
    )


def _scan_asset() -> FileAsset:
    return FileAsset(
        file_id="file-scan-1",
        contract_key="dingyingqi",
        title="定影器采购合同-2024版",
        variant="scan",
        file_url="https://example.local/files/dingyingqi-2024-scan",
        tags=("采购", "合同", "定影器"),
        status="active",
        updated_at="2026-03-27",
    )


def _paper_asset() -> FileAsset:
    return FileAsset(
        file_id="file-paper-1",
        contract_key="dingyingqi",
        title="定影器采购合同-2024版",
        variant="paper",
        file_url="https://example.local/files/dingyingqi-2024-paper",
        tags=("采购", "合同", "定影器"),
        status="active",
        updated_at="2026-03-27",
    )


class FileRequestServiceTests(unittest.TestCase):
    def test_first_turn_returns_confirmation_card_before_submitting_approval(self) -> None:
        notifier = _CapturingApprovalNotifier()
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
        )

        result = service.handle(
            message=make_message(text="我想要定影器采购合同"),
            query_text="我想要定影器采购合同",
        )

        self.assertFalse(result.handled)
        self.assertEqual("file_lookup_confirm_required", result.reason)
        self.assertEqual("file_request", result.intent)
        self.assertEqual("interactive_card", result.reply.channel)
        card = result.reply.interactive_card or {}
        self.assertEqual("file_request_confirmation", card.get("card_type"))
        self.assertEqual("已找到《定影器采购合同-2024版》（扫描件）\n确认发起申请吗？", card.get("summary", ""))
        self.assertNotIn("draft_fields", card)
        self.assertNotIn("note", card)
        self.assertNotIn("actions", card)
        self.assertNotIn("btns", card)
        self.assertEqual(0, len(notifier.requests))
        self.assertEqual(0, len(notifier.cards))

    def test_scan_missing_falls_back_to_paper_before_confirmation(self) -> None:
        notifier = _CapturingApprovalNotifier()
        repository = _VariantAwareRepository(
            scan_result=FileSearchResult.no_hit(),
            paper_result=FileSearchResult(matched=True, match_score=0.9, asset=_paper_asset()),
        )
        service = FileRequestService(file_repository=repository, approval_notifier=notifier)

        result = service.handle(
            message=make_message(text="我想要定影器采购合同"),
            query_text="我想要定影器采购合同",
        )

        self.assertEqual(
            [("我想要定影器采购合同", "scan"), ("我想要定影器采购合同", "paper")],
            repository.calls,
        )
        self.assertEqual("file_lookup_confirm_required", result.reason)
        self.assertEqual("interactive_card", result.reply.channel)
        card = result.reply.interactive_card or {}
        self.assertEqual("已找到《定影器采购合同-2024版》（纸质版）\n确认发起申请吗？", card.get("summary", ""))
        self.assertNotIn("draft_fields", card)
        self.assertNotIn("note", card)
        self.assertEqual(0, len(notifier.requests))

    def test_no_hit_returns_executable_fallback(self) -> None:
        service = FileRequestService(
            file_repository=_VariantAwareRepository(scan_result=FileSearchResult.no_hit(), paper_result=FileSearchResult.no_hit())
        )

        result = service.handle(
            message=make_message(text="我想要不存在的合同"),
            query_text="我想要不存在的合同",
        )

        self.assertFalse(result.handled)
        self.assertEqual("file_lookup_no_hit", result.reason)
        self.assertIn("补充关键词", result.reply.text or "")
        self.assertIn("人事行政", result.reply.text or "")

    def test_confirm_then_approval_approve_returns_three_delivery_replies(self) -> None:
        notifier = _CapturingApprovalNotifier()
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
        )
        first = service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-2", sender_id="user-file-2"),
            query_text="我想要定影器采购合同",
        )
        request_id = str((first.reply.interactive_card or {}).get("request_id", ""))
        self.assertTrue(request_id.startswith("file-req-"))

        confirmed = service.handle_approval_action(
            request_id=request_id,
            action="确认申请",
            approver_user_id="user-file-2",
        )
        self.assertTrue(confirmed.handled)
        self.assertEqual("file_lookup_pending_approval", confirmed.reason)
        self.assertEqual("pending", confirmed.status)
        self.assertEqual(1, len(confirmed.replies))
        self.assertIn("申请已提交", confirmed.replies[0].text or "")
        self.assertNotIn("请求编号", confirmed.replies[0].text or "")
        self.assertEqual(1, len(notifier.requests))
        self.assertEqual("人事行政", notifier.requests[0].approver_user_id)
        self.assertEqual("file_access_approval", notifier.cards[0]["card_type"])

        approval = service.handle_approval_action(
            request_id=request_id,
            action="同意",
            approver_user_id="人事行政",
        )

        self.assertTrue(approval.handled)
        self.assertEqual("file_approval_approved", approval.reason)
        self.assertEqual("delivered", approval.status)
        self.assertEqual(3, len(approval.replies))
        self.assertIn("优先为您提供扫描件", approval.replies[0].text or "")
        self.assertIn("定影器采购合同-2024版", approval.replies[1].text or "")
        self.assertIn("点击下载：[下载文件](", approval.replies[1].text or "")
        self.assertIn("复制链接：https://example.local/files/dingyingqi-2024-scan", approval.replies[1].text or "")
        self.assertIn("https://example.local/files/dingyingqi-2024-scan", approval.replies[1].text or "")
        self.assertIn("文件已发送，请查收", approval.replies[2].text or "")

        duplicate = service.handle_approval_action(
            request_id=request_id,
            action="同意",
            approver_user_id="人事行政",
        )
        self.assertFalse(duplicate.handled)
        self.assertEqual("file_approval_already_processed", duplicate.reason)
        self.assertEqual(0, len(duplicate.replies))

    def test_confirm_request_notify_failure_keeps_waiting_confirmation(self) -> None:
        notifier = _CapturingApprovalNotifier(failure_reason="dingtalk_delivery_failed")
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
        )
        first = service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-notify-fail", sender_id="user-file-6"),
            query_text="我想要定影器采购合同",
        )
        request_id = str((first.reply.interactive_card or {}).get("request_id", ""))
        self.assertTrue(request_id.startswith("file-req-"))

        confirmed = service.handle_approval_action(
            request_id=request_id,
            action="确认申请",
            approver_user_id="user-file-6",
        )
        self.assertFalse(confirmed.handled)
        self.assertEqual("file_approval_notify_failed", confirmed.reason)
        self.assertEqual("awaiting_requester_confirmation", confirmed.status)
        self.assertEqual(1, len(confirmed.replies))
        self.assertIn("通知审批人失败", confirmed.replies[0].text or "")

        followup = service.handle(
            message=make_message(text="审批进度", conversation_id="conv-file-notify-fail", sender_id="user-file-6"),
            query_text="审批进度",
        )
        self.assertEqual("file_lookup_confirm_required", followup.reason)
        self.assertIn("尚未提交审批", followup.reply.text or "")

    def test_non_approver_cannot_approve_or_reject(self) -> None:
        notifier = _CapturingApprovalNotifier()
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
        )
        first = service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-authz", sender_id="user-file-7"),
            query_text="我想要定影器采购合同",
        )
        request_id = str((first.reply.interactive_card or {}).get("request_id", ""))
        service.handle_approval_action(
            request_id=request_id,
            action="确认申请",
            approver_user_id="user-file-7",
        )

        unauthorized = service.handle_approval_action(
            request_id=request_id,
            action="同意",
            approver_user_id="sales-user",
        )
        self.assertFalse(unauthorized.handled)
        self.assertEqual("file_approval_forbidden", unauthorized.reason)
        self.assertEqual("pending", unauthorized.status)
        self.assertEqual(1, len(unauthorized.replies))
        self.assertIn("仅审批人可执行", unauthorized.replies[0].text or "")

        authorized = service.handle_approval_action(
            request_id=request_id,
            action="同意",
            approver_user_id="人事行政",
        )
        self.assertTrue(authorized.handled)
        self.assertEqual("file_approval_approved", authorized.reason)

    def test_confirm_then_approval_reject_returns_lightweight_fallback(self) -> None:
        notifier = _CapturingApprovalNotifier()
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
        )
        first = service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-3", sender_id="user-file-3"),
            query_text="我想要定影器采购合同",
        )
        request_id = str((first.reply.interactive_card or {}).get("request_id", ""))
        service.handle_approval_action(
            request_id=request_id,
            action="确认申请",
            approver_user_id="user-file-3",
        )

        approval = service.handle_approval_action(
            request_id=request_id,
            action="拒绝",
            approver_user_id="人事行政",
        )
        self.assertTrue(approval.handled)
        self.assertEqual("file_approval_rejected", approval.reason)
        self.assertEqual("rejected", approval.status)
        self.assertEqual(1, len(approval.replies))
        self.assertIn("人事行政", approval.replies[0].text or "")

    def test_progress_query_before_confirmation_returns_waiting_confirmation_status(self) -> None:
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            )
        )
        service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-5", sender_id="user-file-5"),
            query_text="我想要定影器采购合同",
        )
        result = service.handle(
            message=make_message(text="审批进度", conversation_id="conv-file-5", sender_id="user-file-5"),
            query_text="审批进度",
        )
        self.assertEqual("file_lookup_confirm_required", result.reason)
        self.assertIn("尚未提交审批", result.reply.text or "")
        self.assertIn("确认申请", result.reply.text or "")

    def test_pending_progress_after_timeout_returns_reminder(self) -> None:
        clock = _FakeClock()
        notifier = _CapturingApprovalNotifier()
        service = FileRequestService(
            file_repository=_VariantAwareRepository(
                scan_result=FileSearchResult(matched=True, match_score=0.98, asset=_scan_asset())
            ),
            approval_notifier=notifier,
            now_provider=clock.now,
        )
        service.handle(
            message=make_message(text="我想要定影器采购合同", conversation_id="conv-file-4", sender_id="user-file-4"),
            query_text="我想要定影器采购合同",
        )
        request_id = notifier.requests[0].request_id if notifier.requests else ""
        if not request_id:
            first = service.handle(
                message=make_message(text="确认申请", conversation_id="conv-file-4", sender_id="user-file-4"),
                query_text="确认申请",
            )
            self.assertEqual("file_lookup_pending_approval", first.reason)
        else:
            service.handle_approval_action(
                request_id=request_id,
                action="确认申请",
                approver_user_id="user-file-4",
            )
        clock.advance(seconds=301)
        result = service.handle(
            message=make_message(text="审批进度", conversation_id="conv-file-4", sender_id="user-file-4"),
            query_text="审批进度",
        )
        self.assertEqual("file_lookup_pending_approval", result.reason)
        self.assertIn("当前审批状态：待审批", result.reply.text or "")
        self.assertNotIn("请求编号", result.reply.text or "")


if __name__ == "__main__":
    unittest.main()
