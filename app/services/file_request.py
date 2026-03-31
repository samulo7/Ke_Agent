from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Literal, Protocol
from uuid import uuid4
from zoneinfo import ZoneInfo

from app.repos.file_repository import FileRepository
from app.repos.in_memory_file_repository import InMemoryFileRepository
from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult
from app.schemas.file_asset import FileAsset, FileSearchResult, FileVariant
from app.schemas.user_context import UserContext

ApprovalAction = Literal["confirm_request", "cancel_request", "approve", "reject"]
ApprovalStatus = Literal["awaiting_requester_confirmation", "pending", "delivered", "rejected", "cancelled"]

DEFAULT_APPROVER_USER_ID = "人事行政"
PENDING_REMINDER_SECONDS = 5 * 60
MULTI_MATCH_SELECTION_TIMEOUT_SECONDS = 5 * 60
_SHANGHAI_TIMEZONE = ZoneInfo("Asia/Shanghai")

_SCAN_HINTS = ("扫描", "扫描件", "扫描版", "电子版", "pdf")
_PAPER_HINTS = ("纸质", "纸质版", "纸质件", "原件")
_PROGRESS_HINTS = ("进度", "审批", "通过了吗", "处理好", "还没", "好了没", "什么时候", "状态")
_CONFIRM_HINTS = ("确认申请", "确认", "提交申请", "发起申请", "申请吧")
_CANCEL_HINTS = ("取消", "不用了", "算了", "先不申请", "不申请")


@dataclass(frozen=True)
class FileApprovalRequest:
    request_id: str
    requester_sender_id: str
    requester_conversation_id: str
    requester_display_name: str
    query_text: str
    variant: FileVariant
    asset: FileAsset
    fallback_from_scan_to_paper: bool
    approver_user_id: str
    created_at: datetime
    status: ApprovalStatus = "awaiting_requester_confirmation"
    decision_at: datetime | None = None


@dataclass(frozen=True)
class FileApprovalActionResult:
    handled: bool
    reason: str
    request_id: str
    action: ApprovalAction | None
    status: ApprovalStatus | None
    requester_sender_id: str = ""
    requester_conversation_id: str = ""
    file_title: str = ""
    file_url: str = ""
    replies: tuple[AgentReply, ...] = ()


@dataclass(frozen=True)
class FileApprovalNotifyResult:
    success: bool
    reason: str = ""


class FileApprovalNotifier(Protocol):
    def notify(self, *, request: FileApprovalRequest, card_payload: dict[str, object]) -> FileApprovalNotifyResult: ...


class _NoopApprovalNotifier:
    def __init__(self) -> None:
        self._logger = logging.getLogger("keagent.observability")

    def notify(self, *, request: FileApprovalRequest, card_payload: dict[str, object]) -> FileApprovalNotifyResult:
        self._logger.info(
            "file.approval.notify",
            extra={
                "obs": {
                    "module": "services.file_request",
                    "event": "file_approval_pending",
                    "request_id": request.request_id,
                    "requester_user_id": request.requester_sender_id,
                    "approver_user_id": request.approver_user_id,
                    "file_id": request.asset.file_id,
                    "file_title": request.asset.title,
                    "variant": request.variant,
                    "card_type": card_payload.get("card_type", ""),
                }
            },
        )
        return FileApprovalNotifyResult(success=True, reason="logged_only")


@dataclass
class _MutableFileApprovalRecord:
    request_id: str
    requester_sender_id: str
    requester_conversation_id: str
    requester_display_name: str
    query_text: str
    variant: FileVariant
    asset: FileAsset
    fallback_from_scan_to_paper: bool
    approver_user_id: str
    created_at: datetime
    status: ApprovalStatus = "awaiting_requester_confirmation"
    decision_at: datetime | None = None

    def freeze(self) -> FileApprovalRequest:
        return FileApprovalRequest(
            request_id=self.request_id,
            requester_sender_id=self.requester_sender_id,
            requester_conversation_id=self.requester_conversation_id,
            requester_display_name=self.requester_display_name,
            query_text=self.query_text,
            variant=self.variant,
            asset=self.asset,
            fallback_from_scan_to_paper=self.fallback_from_scan_to_paper,
            approver_user_id=self.approver_user_id,
            created_at=self.created_at,
            status=self.status,
            decision_at=self.decision_at,
        )


@dataclass(frozen=True)
class _MultiMatchSelectionState:
    query_text: str
    variant: FileVariant
    fallback_from_scan_to_paper: bool
    candidates: tuple[FileAsset, ...]
    created_at: datetime


class FileRequestService:
    """File request flow with requester confirmation and approval-before-delivery."""

    def __init__(
        self,
        *,
        file_repository: FileRepository | None = None,
        approver_user_id: str = DEFAULT_APPROVER_USER_ID,
        approval_notifier: FileApprovalNotifier | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._file_repository = file_repository or InMemoryFileRepository()
        self._approver_user_id = (approver_user_id or DEFAULT_APPROVER_USER_ID).strip() or DEFAULT_APPROVER_USER_ID
        self._approval_notifier = approval_notifier or _NoopApprovalNotifier()
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._records_by_request_id: dict[str, _MutableFileApprovalRecord] = {}
        self._request_id_by_session: dict[str, str] = {}
        self._selection_by_session: dict[str, _MultiMatchSelectionState] = {}

    @staticmethod
    def _session_key(*, conversation_id: str, sender_id: str) -> str:
        return f"{conversation_id}::{sender_id}"

    def has_pending_request(self, *, conversation_id: str, sender_id: str) -> bool:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        request_id = self._request_id_by_session.get(key, "")
        record = self._records_by_request_id.get(request_id)
        return record is not None

    def has_pending_selection(self, *, conversation_id: str, sender_id: str) -> bool:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        self._clear_expired_selection_state(key=key, now=self._now())
        return key in self._selection_by_session

    def clear_pending_selection(self, *, conversation_id: str, sender_id: str) -> None:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        self._selection_by_session.pop(key, None)

    def is_selection_reply(self, *, conversation_id: str, sender_id: str, text: str) -> bool:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        self._clear_expired_selection_state(key=key, now=self._now())
        state = self._selection_by_session.get(key)
        if state is None:
            return False
        normalized = self._normalize_text(text)
        if not normalized:
            return False
        if self._is_cancel_query(text):
            return True
        if normalized.isdigit():
            return True
        return self._candidate_title_match_count(state=state, user_text=text) > 0

    def resolve_active_request_id(self, *, conversation_id: str, sender_id: str) -> str:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        request_id = self._request_id_by_session.get(key, "")
        if not request_id:
            return ""
        return request_id if request_id in self._records_by_request_id else ""

    def resolve_active_request_id_by_sender(self, *, sender_id: str) -> str:
        normalized_sender_id = (sender_id or "").strip()
        if not normalized_sender_id:
            return ""

        active_records = [
            record
            for record in self._records_by_request_id.values()
            if record.requester_sender_id == normalized_sender_id
            and record.status in {"awaiting_requester_confirmation", "pending"}
        ]
        if not active_records:
            return ""
        latest = max(active_records, key=lambda item: item.created_at)
        return latest.request_id

    def handle(
        self,
        *,
        message,
        query_text: str,
        user_context: UserContext | None = None,
    ) -> ChatHandleResult:
        key = self._session_key(conversation_id=message.conversation_id, sender_id=message.sender_id)
        now = self._now()
        self._clear_expired_selection_state(key=key, now=now)
        selection_state = self._selection_by_session.get(key)
        if selection_state is not None:
            selection_followup = self._handle_multi_match_selection_followup(
                key=key,
                state=selection_state,
                user_text=query_text,
                sender_id=message.sender_id,
                conversation_id=message.conversation_id,
                user_context=user_context,
                now=now,
            )
            if selection_followup is not None:
                return selection_followup

        request_id = self._request_id_by_session.get(key, "")
        existing = self._records_by_request_id.get(request_id)
        if existing is not None:
            followup = self._handle_existing_request_followup(record=existing, user_text=query_text)
            if followup is not None:
                return followup

        desired_variant = self._detect_variant(query_text)
        search_result = self._file_repository.search(
            query_text=query_text,
            variant=desired_variant,
            requester_context=user_context,
        )
        fallback = False
        selected_variant = desired_variant
        if not search_result.matched and desired_variant == "scan":
            paper_result = self._file_repository.search(
                query_text=query_text,
                variant="paper",
                requester_context=user_context,
            )
            if paper_result.matched:
                search_result = paper_result
                selected_variant = "paper"
                fallback = True

        if not search_result.matched or search_result.asset is None:
            return ChatHandleResult(
                handled=False,
                reason="file_lookup_no_hit",
                intent="file_request",
                reply=AgentReply(
                    channel="text",
                    text="暂未检索到匹配文件。请补充关键词（文件名/年份/版本），或联系人事行政协助。",
                ),
            )

        candidates = self._resolve_candidates_from_search(search_result=search_result)
        if len(candidates) > 1:
            self._selection_by_session[key] = _MultiMatchSelectionState(
                query_text=query_text,
                variant=selected_variant,
                fallback_from_scan_to_paper=fallback,
                candidates=candidates,
                created_at=now,
            )
            return self._build_multi_match_result(state=self._selection_by_session[key], invalid_choice=False)

        record = self._create_request_record(
            key=key,
            sender_id=message.sender_id,
            conversation_id=message.conversation_id,
            query_text=query_text,
            selected_variant=selected_variant,
            asset=search_result.asset,
            fallback_from_scan_to_paper=fallback,
            user_context=user_context,
            created_at=now,
        )

        return ChatHandleResult(
            handled=False,
            reason="file_lookup_confirm_required",
            intent="file_request",
            reply=AgentReply(
                channel="interactive_card",
                interactive_card=self._build_requester_confirmation_card_payload(record=record),
            ),
        )

    def handle_approval_action(
        self,
        *,
        request_id: str,
        action: str,
        approver_user_id: str,
    ) -> FileApprovalActionResult:
        normalized_action = self._normalize_approval_action(action)
        if normalized_action is None:
            return FileApprovalActionResult(
                handled=False,
                reason="file_approval_invalid_action",
                request_id=request_id,
                action=None,
                status=None,
            )

        record = self._records_by_request_id.get(request_id)
        if record is None:
            return FileApprovalActionResult(
                handled=False,
                reason="file_approval_not_found",
                request_id=request_id,
                action=normalized_action,
                status=None,
            )

        if record.status == "awaiting_requester_confirmation":
            if normalized_action == "confirm_request":
                return self._confirm_request(record=record)
            if normalized_action == "cancel_request":
                return self._cancel_request(record=record)
            return FileApprovalActionResult(
                handled=False,
                reason="file_lookup_confirm_required",
                request_id=request_id,
                action=normalized_action,
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
            )

        if record.status != "pending":
            return FileApprovalActionResult(
                handled=False,
                reason="file_approval_already_processed",
                request_id=request_id,
                action=normalized_action,
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
            )

        if normalized_action in {"confirm_request", "cancel_request"}:
            return FileApprovalActionResult(
                handled=False,
                reason="file_lookup_pending_approval",
                request_id=request_id,
                action=normalized_action,
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
            )

        if not self._is_authorized_approver(
            approver_user_id=approver_user_id,
            expected_approver_user_id=record.approver_user_id,
        ):
            return FileApprovalActionResult(
                handled=False,
                reason="file_approval_forbidden",
                request_id=request_id,
                action=normalized_action,
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
                replies=(
                    AgentReply(
                        channel="text",
                        text="仅审批人可执行该操作。请由人事审批后继续。",
                    ),
                ),
            )

        record.decision_at = self._now()
        if normalized_action == "approve":
            record.status = "delivered"
            return FileApprovalActionResult(
                handled=True,
                reason="file_approval_approved",
                request_id=request_id,
                action=normalized_action,
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
                file_title=record.asset.title,
                file_url=record.asset.file_url,
                replies=self._build_delivery_replies(record=record),
            )

        record.status = "rejected"
        reject_text = (
            "本次文件申请暂未获批。"
            "如需继续办理，请补充用途后重新申请，或直接联系人事行政。"
        )
        return FileApprovalActionResult(
            handled=True,
            reason="file_approval_rejected",
            request_id=request_id,
            action=normalized_action,
            status=record.status,
            requester_sender_id=record.requester_sender_id,
            requester_conversation_id=record.requester_conversation_id,
            file_title=record.asset.title,
            file_url="",
            replies=(AgentReply(channel="text", text=reject_text),),
        )

    @staticmethod
    def _normalize_approval_action(action: str) -> ApprovalAction | None:
        normalized = "".join((action or "").strip().lower().split())
        if normalized in {"confirm_request", "confirm", "确认申请", "确认", "提交申请", "发起申请"}:
            return "confirm_request"
        if normalized in {"cancel_request", "cancel", "取消", "不用了", "算了", "先不申请"}:
            return "cancel_request"
        if normalized in {"approve", "approved", "agree", "同意", "通过", "pass"}:
            return "approve"
        if normalized in {"reject", "rejected", "refuse", "拒绝", "驳回"}:
            return "reject"
        return None

    @staticmethod
    def _normalize_actor(value: str) -> str:
        return "".join((value or "").strip().lower().split())

    @staticmethod
    def _is_authorized_approver(*, approver_user_id: str, expected_approver_user_id: str) -> bool:
        normalized_actor = FileRequestService._normalize_actor(approver_user_id)
        normalized_expected = FileRequestService._normalize_actor(expected_approver_user_id)
        if not normalized_actor or not normalized_expected:
            return False
        return normalized_actor == normalized_expected

    @staticmethod
    def _resolve_requester_name(*, user_context: UserContext | None, sender_id: str) -> str:
        if user_context is None:
            return sender_id
        user_name = (user_context.user_name or "").strip()
        if user_name and user_name != "unknown":
            return user_name
        user_id = (user_context.user_id or "").strip()
        return user_id if user_id else sender_id

    @staticmethod
    def _detect_variant(query_text: str) -> FileVariant:
        normalized = FileRequestService._normalize_text(query_text)
        if any(token in normalized for token in _PAPER_HINTS):
            return "paper"
        if any(token in normalized for token in _SCAN_HINTS):
            return "scan"
        return "scan"

    @staticmethod
    def _normalize_text(text: str) -> str:
        return "".join((text or "").strip().lower().split())

    @staticmethod
    def _is_progress_query(text: str) -> bool:
        normalized = FileRequestService._normalize_text(text)
        return any(token in normalized for token in _PROGRESS_HINTS)

    @staticmethod
    def _is_confirm_query(text: str) -> bool:
        normalized = FileRequestService._normalize_text(text)
        return any(token in normalized for token in _CONFIRM_HINTS)

    @staticmethod
    def _is_cancel_query(text: str) -> bool:
        normalized = FileRequestService._normalize_text(text)
        return any(token in normalized for token in _CANCEL_HINTS)

    @staticmethod
    def _resolve_candidates_from_search(*, search_result: FileSearchResult) -> tuple[FileAsset, ...]:
        if search_result.candidates:
            unique: dict[str, FileAsset] = {}
            for candidate in search_result.candidates:
                unique[candidate.asset.file_id] = candidate.asset
            if unique:
                return tuple(unique.values())
        if search_result.asset is not None:
            return (search_result.asset,)
        return ()

    def _clear_expired_selection_state(self, *, key: str, now: datetime) -> None:
        state = self._selection_by_session.get(key)
        if state is None:
            return
        elapsed = int((now - state.created_at).total_seconds())
        if elapsed <= MULTI_MATCH_SELECTION_TIMEOUT_SECONDS:
            return
        self._selection_by_session.pop(key, None)

    def _candidate_title_match_count(self, *, state: _MultiMatchSelectionState, user_text: str) -> int:
        normalized = self._normalize_text(user_text)
        if not normalized:
            return 0
        count = 0
        for asset in state.candidates:
            title = self._normalize_text(asset.title)
            if normalized in title or title in normalized:
                count += 1
        return count

    def _resolve_candidate_by_input(self, *, state: _MultiMatchSelectionState, user_text: str) -> FileAsset | None:
        normalized = self._normalize_text(user_text)
        if not normalized:
            return None
        if normalized.isdigit():
            index = int(normalized)
            if 1 <= index <= len(state.candidates):
                return state.candidates[index - 1]
            return None

        matched: list[FileAsset] = []
        for asset in state.candidates:
            title = self._normalize_text(asset.title)
            if normalized in title or title in normalized:
                matched.append(asset)
        if len(matched) == 1:
            return matched[0]
        return None

    def _looks_like_selection_input(self, *, state: _MultiMatchSelectionState, user_text: str) -> bool:
        normalized = self._normalize_text(user_text)
        if not normalized:
            return False
        if normalized.isdigit():
            return True
        return self._candidate_title_match_count(state=state, user_text=user_text) > 0

    def _build_multi_match_result(
        self,
        *,
        state: _MultiMatchSelectionState,
        invalid_choice: bool,
    ) -> ChatHandleResult:
        lines = ["找到多个匹配文件，请确认："]
        for index, asset in enumerate(state.candidates, start=1):
            lines.append(f"{index}. {asset.title}")
        hint = "请回复序号或完整文件名。"
        if invalid_choice:
            hint = "未识别到有效选择。请回复序号或完整文件名。"
        lines.append(hint)
        return ChatHandleResult(
            handled=False,
            reason="file_lookup_multiple_matches",
            intent="file_request",
            reply=AgentReply(channel="text", text="\n".join(lines)),
        )

    def _create_request_record(
        self,
        *,
        key: str,
        sender_id: str,
        conversation_id: str,
        query_text: str,
        selected_variant: FileVariant,
        asset: FileAsset,
        fallback_from_scan_to_paper: bool,
        user_context: UserContext | None,
        created_at: datetime,
    ) -> _MutableFileApprovalRecord:
        request_id = f"file-req-{uuid4().hex[:12]}"
        requester_name = self._resolve_requester_name(user_context=user_context, sender_id=sender_id)
        record = _MutableFileApprovalRecord(
            request_id=request_id,
            requester_sender_id=sender_id,
            requester_conversation_id=conversation_id,
            requester_display_name=requester_name,
            query_text=query_text,
            variant=selected_variant,
            asset=asset,
            fallback_from_scan_to_paper=fallback_from_scan_to_paper,
            approver_user_id=self._approver_user_id,
            created_at=created_at,
            status="awaiting_requester_confirmation",
        )
        self._records_by_request_id[request_id] = record
        self._request_id_by_session[key] = request_id
        return record

    def _handle_multi_match_selection_followup(
        self,
        *,
        key: str,
        state: _MultiMatchSelectionState,
        user_text: str,
        sender_id: str,
        conversation_id: str,
        user_context: UserContext | None,
        now: datetime,
    ) -> ChatHandleResult | None:
        if self._is_cancel_query(user_text):
            self._selection_by_session.pop(key, None)
            return ChatHandleResult(
                handled=False,
                reason="file_request_cancelled",
                intent="file_request",
                reply=AgentReply(channel="text", text="已取消本次文件选择。需要时可以重新发起。"),
            )

        selected_asset = self._resolve_candidate_by_input(state=state, user_text=user_text)
        if selected_asset is not None:
            self._selection_by_session.pop(key, None)
            record = self._create_request_record(
                key=key,
                sender_id=sender_id,
                conversation_id=conversation_id,
                query_text=state.query_text,
                selected_variant=state.variant,
                asset=selected_asset,
                fallback_from_scan_to_paper=state.fallback_from_scan_to_paper,
                user_context=user_context,
                created_at=now,
            )
            return ChatHandleResult(
                handled=False,
                reason="file_lookup_confirm_required",
                intent="file_request",
                reply=AgentReply(
                    channel="interactive_card",
                    interactive_card=self._build_requester_confirmation_card_payload(record=record),
                ),
            )

        if self._looks_like_selection_input(state=state, user_text=user_text):
            return self._build_multi_match_result(state=state, invalid_choice=True)

        self._selection_by_session.pop(key, None)
        return None

    def _handle_existing_request_followup(
        self,
        *,
        record: _MutableFileApprovalRecord,
        user_text: str,
    ) -> ChatHandleResult | None:
        if record.status == "awaiting_requester_confirmation":
            if self._is_confirm_query(user_text):
                return self._to_chat_result(self._confirm_request(record=record))
            if self._is_cancel_query(user_text):
                return self._to_chat_result(self._cancel_request(record=record))
            return self._build_confirmation_waiting_result(record=record)

        if record.status == "pending":
            return self._build_pending_status_result(record=record, user_text=user_text)

        if record.status in {"delivered", "rejected", "cancelled"} and self._is_progress_query(user_text):
            return self._build_terminal_status_result(record=record)

        return None

    @staticmethod
    def _to_chat_result(action_result: FileApprovalActionResult) -> ChatHandleResult:
        first_reply = action_result.replies[0] if action_result.replies else AgentReply(channel="text", text="")
        return ChatHandleResult(
            handled=action_result.handled,
            reason=action_result.reason,
            intent="file_request",
            reply=first_reply,
            followup_replies=action_result.replies[1:],
        )

    def _confirm_request(self, *, record: _MutableFileApprovalRecord) -> FileApprovalActionResult:
        if record.status != "awaiting_requester_confirmation":
            return FileApprovalActionResult(
                handled=False,
                reason="file_lookup_confirm_required",
                request_id=record.request_id,
                action="confirm_request",
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
            )

        notify_result = self._approval_notifier.notify(
            request=record.freeze(),
            card_payload=self._build_approval_card_payload(record=record),
        )
        if not notify_result.success:
            text = (
                "申请信息已记录，但通知审批人失败。"
                "请稍后回复“确认申请”重试，或回复“取消”结束本次申请。"
            )
            return FileApprovalActionResult(
                handled=False,
                reason="file_approval_notify_failed",
                request_id=record.request_id,
                action="confirm_request",
                status=record.status,
                requester_sender_id=record.requester_sender_id,
                requester_conversation_id=record.requester_conversation_id,
                replies=(AgentReply(channel="text", text=text),),
            )

        record.status = "pending"
        variant_text = "扫描件" if record.variant == "scan" else "纸质版"
        if record.fallback_from_scan_to_paper:
            variant_text = "纸质版（当前未找到扫描件）"
        text = (
            f"申请已提交，当前申请版本：{variant_text}。"
            "审批通过后将自动发送文件。你可以回复“审批进度”查看实时状态。"
        )
        return FileApprovalActionResult(
            handled=True,
            reason="file_lookup_pending_approval",
            request_id=record.request_id,
            action="confirm_request",
            status=record.status,
            requester_sender_id=record.requester_sender_id,
            requester_conversation_id=record.requester_conversation_id,
            replies=(AgentReply(channel="text", text=text),),
        )

    def _cancel_request(self, *, record: _MutableFileApprovalRecord) -> FileApprovalActionResult:
        record.status = "cancelled"
        record.decision_at = self._now()
        text = "已取消本次文件申请，未提交审批。需要时你可以重新发起。"
        return FileApprovalActionResult(
            handled=True,
            reason="file_request_cancelled",
            request_id=record.request_id,
            action="cancel_request",
            status=record.status,
            requester_sender_id=record.requester_sender_id,
            requester_conversation_id=record.requester_conversation_id,
            replies=(AgentReply(channel="text", text=text),),
        )

    def _build_confirmation_waiting_result(self, *, record: _MutableFileApprovalRecord) -> ChatHandleResult:
        text = (
            "当前申请尚未提交审批。"
            "请先确认申请（回复“确认申请”）或取消（回复“取消”）。"
        )
        return ChatHandleResult(
            handled=False,
            reason="file_lookup_confirm_required",
            intent="file_request",
            reply=AgentReply(channel="text", text=text),
        )

    def _build_pending_status_result(self, *, record: _MutableFileApprovalRecord, user_text: str) -> ChatHandleResult:
        elapsed_seconds = int((self._now() - record.created_at).total_seconds())
        minutes = max(1, elapsed_seconds // 60) if elapsed_seconds > 0 else 0
        text = (
            "当前审批状态：待审批。"
            f"已等待约{minutes}分钟。"
            "审批通过后我会立即发送文件。"
        )
        if self._is_progress_query(user_text) and elapsed_seconds >= PENDING_REMINDER_SECONDS:
            text += "若紧急可联系人事行政催办。"
        return ChatHandleResult(
            handled=False,
            reason="file_lookup_pending_approval",
            intent="file_request",
            reply=AgentReply(channel="text", text=text),
        )

    def _build_terminal_status_result(self, *, record: _MutableFileApprovalRecord) -> ChatHandleResult:
        decided_at = self._format_time(record.decision_at)
        if record.status == "delivered":
            text = f"当前审批状态：已通过并发送。处理时间：{decided_at}。"
            reason = "file_approval_approved"
        elif record.status == "rejected":
            text = (
                "当前审批状态：已拒绝。"
                f"处理时间：{decided_at}。如需继续办理，请补充用途后重新申请。"
            )
            reason = "file_approval_rejected"
        else:
            text = f"当前申请状态：已取消（未提交审批）。处理时间：{decided_at}。"
            reason = "file_request_cancelled"
        return ChatHandleResult(
            handled=False,
            reason=reason,
            intent="file_request",
            reply=AgentReply(channel="text", text=text),
        )

    @staticmethod
    def _format_time(value: datetime | None) -> str:
        if value is None:
            return "unknown"
        return value.astimezone(_SHANGHAI_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S Asia/Shanghai")

    def _build_requester_confirmation_card_payload(self, *, record: _MutableFileApprovalRecord) -> dict[str, object]:
        variant_label = "扫描件" if record.variant == "scan" else "纸质版"
        summary_variant = "纸质版" if record.fallback_from_scan_to_paper else variant_label
        summary = f"已找到《{record.asset.title}》（{summary_variant}）\n确认发起申请吗？"
        return {
            "card_type": "file_request_confirmation",
            "request_id": record.request_id,
            "title": "确认文件申请",
            "summary": summary,
            "render_action_hint": False,
        }

    def _build_approval_card_payload(self, *, record: _MutableFileApprovalRecord) -> dict[str, object]:
        variant_label = "扫描件" if record.variant == "scan" else "纸质版"
        approve_btn = {
            "id": f"approve::{record.request_id}",
            "label": "同意",
            "text": "同意",
            "action": "approve",
            "approval_action": "approve",
            "request_id": record.request_id,
        }
        reject_btn = {
            "id": f"reject::{record.request_id}",
            "label": "拒绝",
            "text": "拒绝",
            "action": "reject",
            "approval_action": "reject",
            "request_id": record.request_id,
        }
        return {
            "card_type": "file_access_approval",
            "request_id": record.request_id,
            "title": "文件发放审批",
            "summary": f"{record.requester_display_name} 申请查阅文件，请审批。",
            "draft_fields": {
                "申请人": record.requester_display_name,
                "文件名称": record.asset.title,
                "请求版本": variant_label,
                "请求关键词": record.query_text,
            },
            "actions": [approve_btn, reject_btn],
            "btns": [approve_btn, reject_btn],
        }

    @staticmethod
    def _build_delivery_replies(*, record: _MutableFileApprovalRecord) -> tuple[AgentReply, ...]:
        if record.variant == "scan":
            first = "明白，优先为您提供扫描件版本，请稍等。"
        elif record.fallback_from_scan_to_paper:
            first = "当前未找到扫描件，已切换为纸质版处理，请稍等。"
        else:
            first = "明白，优先为您提供纸质版，请稍等。"

        file_url = record.asset.file_url
        second = (
            f"已找到匹配的合同文件（{record.asset.title}），正在发送给您。\n"
            f"点击下载：[下载文件]({file_url})\n"
            f"复制链接：{file_url}"
        )
        third = "文件已发送，请查收。如需原件或其他相关资料（如发票、付款记录），也可以告诉我。"
        return (
            AgentReply(channel="text", text=first),
            AgentReply(channel="text", text=second),
            AgentReply(channel="text", text=third),
        )
