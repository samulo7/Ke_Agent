from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import logging
import os
from typing import Callable, Literal, Protocol
from zoneinfo import ZoneInfo

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult, IncomingChatMessage
from app.schemas.reimbursement import (
    ReimbursementApprovalResult,
    ReimbursementApprovalSubmission,
    ReimbursementAttachmentProcessResult,
    TravelApplication,
)
from app.schemas.user_context import UserContext

SESSION_TIMEOUT_SECONDS = 30 * 60
CONFIRMATION_TIMEOUT_SECONDS = 5 * 60
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

_CANCEL_TOKENS = {"取消", "算了", "不用了", "终止", "结束"}
_TEXT_CONFIRM_OR_CANCEL_TOKENS = {
    "可以",
    "可以了",
    "确认",
    "确认提交",
    "提交",
    "就这样",
    "没问题",
    "取消",
    "算了",
    "不用了",
}
_DEFAULT_COMPANY_OPTIONS = (
    "YXQY",
    "YXXX",
    "DL",
    "SZB",
    "BWZC",
    "ZZZC",
    "LNTL",
    "YNC",
    "SY",
    "JA",
    "YL",
    "DR",
)
_DEFAULT_FIXED_COMPANY = "YXQY"
_SUPPORTED_ATTACHMENT_SUFFIXES = (".xlsx",)


class TravelApplicationProvider(Protocol):
    def list_recent_approved(
        self,
        *,
        originator_user_id: str,
        lookback_days: int,
        now: datetime,
    ) -> list[TravelApplication]: ...


class ReimbursementAttachmentProcessor(Protocol):
    def process(
        self,
        *,
        message: IncomingChatMessage,
        conversation_id: str,
        sender_id: str,
    ) -> ReimbursementAttachmentProcessResult: ...


class ReimbursementApprovalCreator(Protocol):
    def submit(self, submission: ReimbursementApprovalSubmission) -> ReimbursementApprovalResult: ...


@dataclass
class _ReimbursementSession:
    originator_user_id: str
    stage: Literal[
        "collecting_trip",
        "collecting_attachment",
        "collecting_company",
        "awaiting_amount_conflict_confirmation",
        "awaiting_confirmation",
    ] = "collecting_trip"
    travel_candidates: tuple[TravelApplication, ...] = ()
    travel_process_instance_id: str = ""
    travel_display: str = ""
    department: str = ""
    amount: str = ""
    table_amount: str = ""
    uppercase_amount_text: str = ""
    uppercase_amount_raw: str = ""
    uppercase_amount_numeric: str = ""
    amount_conflict: bool = False
    amount_conflict_note: str = ""
    amount_source: str = "table"
    amount_source_note: str = ""
    cost_company: str = ""
    attachment_media_id: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    confirmation_started_at: datetime | None = None


class _StaticTravelApplicationProvider:
    def list_recent_approved(
        self,
        *,
        originator_user_id: str,
        lookback_days: int,
        now: datetime,
    ) -> list[TravelApplication]:
        del originator_user_id, lookback_days, now
        return []


class ReimbursementRequestOrchestrator:
    def __init__(
        self,
        *,
        travel_application_provider: TravelApplicationProvider | None = None,
        attachment_processor: ReimbursementAttachmentProcessor | None = None,
        approval_creator: ReimbursementApprovalCreator | None = None,
        now_provider: Callable[[], datetime] | None = None,
        company_options: tuple[str, ...] = _DEFAULT_COMPANY_OPTIONS,
        fixed_company: str = _DEFAULT_FIXED_COMPANY,
        travel_lookback_days: int = 30,
    ) -> None:
        self._sessions: dict[str, _ReimbursementSession] = {}
        self._travel_application_provider = travel_application_provider or _StaticTravelApplicationProvider()
        self._attachment_processor = attachment_processor
        self._approval_creator = approval_creator
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._company_options = tuple(item.strip().upper() for item in company_options if item.strip())
        self._fixed_company = fixed_company.strip().upper() or _DEFAULT_FIXED_COMPANY
        self._travel_lookback_days = max(1, int(travel_lookback_days))
        self._logger = logging.getLogger("keagent.observability")

    @staticmethod
    def _session_key(*, conversation_id: str, sender_id: str) -> str:
        return f"{conversation_id}::{sender_id}"

    def has_active_session(self, *, conversation_id: str, sender_id: str) -> bool:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        return key in self._sessions

    def handle(
        self,
        *,
        conversation_id: str,
        sender_id: str,
        message: IncomingChatMessage,
        user_context: UserContext | None,
        force_start: bool,
    ) -> ChatHandleResult | None:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        now = self._now()
        normalized = self._normalize(message.text) if message.message_type == "text" else ""
        session = self._sessions.get(key)

        if session is None:
            if not force_start:
                return None
            session = _ReimbursementSession(
                originator_user_id=self._resolve_originator_user_id(user_context=user_context, sender_id=sender_id),
                created_at=now,
                updated_at=now,
            )
            candidates = self._travel_application_provider.list_recent_approved(
                originator_user_id=session.originator_user_id,
                lookback_days=self._travel_lookback_days,
                now=now,
            )
            if not candidates:
                return self._no_trip_result()
            session.travel_candidates = tuple(candidates)
            self._sessions[key] = session
            return self._collecting_trip_result(session=session)

        if self._is_session_timed_out(session=session, now=now):
            self._sessions.pop(key, None)
            return self._timeout_result()

        if normalized in _CANCEL_TOKENS:
            self._sessions.pop(key, None)
            return self._cancelled_result()

        if self._is_confirmation_expired(session=session, now=now):
            self._sessions.pop(key, None)
            return self._confirmation_expired_result()

        if session.stage == "collecting_trip":
            if message.message_type != "text":
                return self._collecting_trip_result(session=session, invalid=True)
            selected = self._select_trip(session=session, text=message.text)
            if selected is None:
                return self._collecting_trip_result(session=session, invalid=True)
            session.travel_process_instance_id = selected.process_instance_id
            session.travel_display = selected.display_text()
            session.stage = "collecting_attachment"
            session.updated_at = now
            self._sessions[key] = session
            return self._collecting_attachment_result()

        if session.stage == "collecting_attachment":
            if message.message_type != "file":
                return self._collecting_attachment_result(remind_upload=True)
            suffix = (message.file_name or "").strip().lower()
            if not any(suffix.endswith(ext) for ext in _SUPPORTED_ATTACHMENT_SUFFIXES):
                return self._attachment_failed_result("仅支持上传 .xlsx 报销单，请重新发送。")
            if self._attachment_processor is None:
                return self._attachment_failed_result("当前未启用附件处理能力，请联系管理员检查配置。")
            process_result = self._attachment_processor.process(
                message=message,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
            if not process_result.success:
                reason = process_result.reason.strip() or "附件处理失败，请重新发送文件。"
                return self._attachment_failed_result(reason)
            session.department = process_result.department.strip()
            session.amount = process_result.amount.strip()
            session.table_amount = process_result.table_amount.strip() or session.amount
            session.uppercase_amount_text = process_result.uppercase_amount_text.strip()
            session.uppercase_amount_raw = process_result.uppercase_amount_raw.strip() or session.uppercase_amount_text
            session.uppercase_amount_numeric = process_result.uppercase_amount_numeric.strip()
            session.amount_conflict = bool(process_result.amount_conflict)
            session.amount_conflict_note = process_result.amount_conflict_note.strip()
            session.amount_source = process_result.amount_source.strip() or "table"
            session.amount_source_note = process_result.amount_source_note.strip()
            session.attachment_media_id = process_result.attachment_media_id.strip()
            if not session.department or not session.amount or not session.attachment_media_id:
                return self._attachment_failed_result("附件解析不完整，请确认模板后重新上传。")
            session.stage = "collecting_company"
            session.updated_at = now
            self._sessions[key] = session
            return self._collecting_company_result(session=session)

        if session.stage == "collecting_company":
            if message.message_type != "text":
                return self._collecting_company_result(session=session, invalid=True)
            company = self._normalize_company_option(message.text)
            if company not in self._company_options:
                return self._collecting_company_result(session=session, invalid=True)
            session.cost_company = company
            if session.amount_conflict:
                session.stage = "awaiting_amount_conflict_confirmation"
                session.updated_at = now
                session.confirmation_started_at = now
                self._sessions[key] = session
                return self._amount_conflict_confirmation_result(session=session)
            session.stage = "awaiting_confirmation"
            session.updated_at = now
            session.confirmation_started_at = now
            self._sessions[key] = session
            return self._ready_result(session=session)

        if session.stage == "awaiting_amount_conflict_confirmation":
            return self._amount_conflict_confirmation_result(session=session, remind=True)

        if session.stage == "awaiting_confirmation":
            if normalized in _TEXT_CONFIRM_OR_CANCEL_TOKENS:
                return self._waiting_button_action_result()
            return self._waiting_button_action_result()

        return None

    def handle_confirmation_action_by_session(
        self,
        *,
        action: str,
        conversation_id: str,
        sender_id: str,
    ) -> ChatHandleResult:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        session = self._sessions.get(key)
        if session is None:
            return self._not_found_result()

        now = self._now()
        if self._is_session_timed_out(session=session, now=now) or self._is_confirmation_expired(session=session, now=now):
            self._sessions.pop(key, None)
            return self._confirmation_expired_result()

        normalized_action = self._normalize(action)
        if normalized_action == "reimbursement_cancel_submit":
            self._sessions.pop(key, None)
            return self._cancelled_result()

        if session.stage == "awaiting_amount_conflict_confirmation":
            if normalized_action == "reimbursement_amount_use_table":
                if session.table_amount:
                    session.amount = self._normalize_amount_text(session.table_amount)
                session.amount_conflict = False
                session.amount_conflict_note = "已选择按合计金额提交。"
                session.amount_source = "table"
                session.amount_source_note = "金额冲突已确认：按合计金额提交"
                session.stage = "awaiting_confirmation"
                session.updated_at = now
                session.confirmation_started_at = now
                self._sessions[key] = session
                return self._ready_result(session=session)
            if normalized_action == "reimbursement_amount_use_uppercase":
                if not session.uppercase_amount_numeric:
                    return self._amount_conflict_confirmation_result(session=session, remind=True)
                session.amount = self._normalize_amount_text(session.uppercase_amount_numeric)
                session.amount_conflict = False
                session.amount_conflict_note = "已选择按大写金额提交。"
                session.amount_source = "uppercase"
                session.amount_source_note = "金额冲突已确认：按大写金额提交"
                session.stage = "awaiting_confirmation"
                session.updated_at = now
                session.confirmation_started_at = now
                self._sessions[key] = session
                return self._ready_result(session=session)
            return self._amount_conflict_confirmation_result(session=session, remind=True)

        if session.stage != "awaiting_confirmation":
            return self._not_found_result()

        if normalized_action != "reimbursement_confirm_submit":
            return self._waiting_button_action_result()

        self._sessions.pop(key, None)
        return self._confirm_result(session=session)

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join((text or "").strip().split())

    @staticmethod
    def _resolve_originator_user_id(*, user_context: UserContext | None, sender_id: str) -> str:
        if user_context is None:
            return sender_id.strip()
        user_id = (user_context.user_id or "").strip()
        if user_id and user_id != "unknown":
            return user_id
        return sender_id.strip()

    @staticmethod
    def _is_session_timed_out(*, session: _ReimbursementSession, now: datetime) -> bool:
        anchor = session.updated_at or session.created_at or now
        return (now - anchor).total_seconds() > SESSION_TIMEOUT_SECONDS

    @staticmethod
    def _is_confirmation_expired(*, session: _ReimbursementSession, now: datetime) -> bool:
        if session.stage not in {"awaiting_confirmation", "awaiting_amount_conflict_confirmation"}:
            return False
        anchor = session.confirmation_started_at
        if anchor is None:
            return False
        return (now - anchor).total_seconds() > CONFIRMATION_TIMEOUT_SECONDS

    @staticmethod
    def _normalize_company_option(text: str) -> str:
        return "".join((text or "").strip().upper().split())

    def _select_trip(self, *, session: _ReimbursementSession, text: str) -> TravelApplication | None:
        normalized = self._normalize(text)
        if normalized.isdigit():
            index = int(normalized)
            if 1 <= index <= len(session.travel_candidates):
                return session.travel_candidates[index - 1]
            return None
        for item in session.travel_candidates:
            destination = self._normalize(item.destination)
            purpose = self._normalize(item.purpose)
            if destination and destination in normalized:
                return item
            if purpose and purpose in normalized:
                return item
        return None

    def _confirm_result(self, *, session: _ReimbursementSession) -> ChatHandleResult:
        if self._approval_creator is None:
            return self._handoff_fallback_result()

        now_local = self._now().astimezone(LOCAL_TIMEZONE)
        amount_numeric = self._parse_amount_value(session.amount)
        over_five_thousand = "是" if amount_numeric > 5000 else "否"
        submission = ReimbursementApprovalSubmission(
            originator_user_id=session.originator_user_id,
            travel_process_instance_id=session.travel_process_instance_id,
            department=session.department,
            fixed_company=self._fixed_company,
            cost_company=session.cost_company,
            date=now_local.strftime("%Y-%m-%d"),
            amount=self._normalize_amount_text(session.amount),
            over_five_thousand=over_five_thousand,
            attachment_media_id=session.attachment_media_id,
        )
        result = self._approval_creator.submit(submission)
        if result.success:
            self._logger.info(
                "reimbursement.approval.submit",
                extra={
                    "obs": {
                        "module": "services.reimbursement_request",
                        "event": "reimbursement_submitted",
                        "originator_user_id": submission.originator_user_id,
                        "travel_process_instance_id": submission.travel_process_instance_id,
                        "amount": submission.amount,
                    }
                },
            )
            return self._submitted_result()
        self._logger.warning(
            "reimbursement.approval.submit failed",
            extra={
                "obs": {
                    "module": "services.reimbursement_request",
                    "event": "reimbursement_submit_fallback",
                    "originator_user_id": submission.originator_user_id,
                    "reason": result.reason,
                    "failure_category": result.failure_category,
                    "errcode": result.raw_errcode,
                    "errmsg": result.raw_errmsg,
                }
            },
        )
        short_reason, suggestion = self._map_submit_failure_to_user_hint(result)
        return self._handoff_fallback_result(short_reason=short_reason, suggestion=suggestion)

    @staticmethod
    def _parse_amount_value(raw: str) -> float:
        text = (raw or "").strip().replace("元", "")
        if not text:
            return 0.0
        keep_chars = [ch for ch in text if ch.isdigit() or ch == "."]
        normalized = "".join(keep_chars).strip(".")
        if not normalized:
            return 0.0
        try:
            return float(normalized)
        except ValueError:
            return 0.0

    @classmethod
    def _normalize_amount_text(cls, raw: str) -> str:
        value = cls._parse_amount_value(raw)
        if value.is_integer():
            return str(int(value))
        return f"{value:.2f}".rstrip("0").rstrip(".")

    @classmethod
    def _collecting_trip_result(cls, *, session: _ReimbursementSession, invalid: bool = False) -> ChatHandleResult:
        lines = ["你最近的出差申请："]
        for index, item in enumerate(session.travel_candidates, start=1):
            lines.append(f"{index}. {item.display_text()}")
        lines.append("选哪个？")
        if invalid:
            lines.insert(0, "没有匹配到申请，请选序号或说出差目的地。")
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_collecting_trip",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="\n".join(lines)),
        )

    @classmethod
    def _collecting_attachment_result(cls, *, remind_upload: bool = False) -> ChatHandleResult:
        text = "请发送差旅费报销单（Excel）"
        if remind_upload:
            text = "当前步骤需要上传差旅费报销单（Excel），请直接发送 .xlsx 文件。"
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_collecting_attachment",
            intent="reimbursement",
            reply=AgentReply(channel="text", text=text),
        )

    def _collecting_company_result(self, *, session: _ReimbursementSession, invalid: bool = False) -> ChatHandleResult:
        options = " / ".join(self._company_options)
        if invalid:
            prefix = "费用归属公司无效，请从以下选项中选择：\n"
        else:
            source_line = f"金额来源：{session.amount_source_note}\n" if session.amount_source_note else ""
            prefix = (
                "已解析：\n"
                f"部门：{session.department}，金额：{self._normalize_amount_text(session.amount)}元\n"
                f"{source_line}\n"
                "费用归属公司选哪个？\n"
            )
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_collecting_company",
            intent="reimbursement",
            reply=AgentReply(channel="text", text=f"{prefix}{options}"),
        )

    @classmethod
    def _attachment_failed_result(cls, reason: str) -> ChatHandleResult:
        text = f"{reason}\n请重新上传差旅费报销单（Excel）。"
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_attachment_failed",
            intent="reimbursement",
            reply=AgentReply(channel="text", text=text),
        )

    def _ready_result(self, *, session: _ReimbursementSession) -> ChatHandleResult:
        amount_text = f"{self._normalize_amount_text(session.amount)}元"
        over_five_thousand = "是" if self._parse_amount_value(session.amount) > 5000 else "否"
        amount_source_line = f"\n金额来源：{session.amount_source_note}" if session.amount_source_note else ""
        card = {
            "card_type": "reimbursement_request_ready",
            "title": "差旅报销确认",
            "summary": (
                f"关联申请：{session.travel_display}\n"
                f"部门：{session.department}\n"
                f"金额：{amount_text}\n"
                f"归属公司：{session.cost_company}\n"
                f"是否超5千：{over_five_thousand}\n"
                f"附件：已处理 ✓{amount_source_line}"
            ),
            "linked_trip": session.travel_display,
            "department": session.department,
            "amount": amount_text,
            "amount_source": session.amount_source_note or session.amount_source,
            "cost_company": session.cost_company,
            "over_5000": over_five_thousand,
            "attachment_status": "已处理 ✓",
            "actions": [
                {"label": "确认提交", "action": "reimbursement_confirm_submit", "status": "primary"},
                {"label": "取消", "action": "reimbursement_cancel_submit", "status": "warning"},
            ],
            "next_action": "请在 5 分钟内点击卡片按钮确认提交或取消。",
        }
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_ready",
            intent="reimbursement",
            reply=AgentReply(channel="interactive_card", interactive_card=card),
        )

    def _amount_conflict_confirmation_result(
        self,
        *,
        session: _ReimbursementSession,
        remind: bool = False,
    ) -> ChatHandleResult:
        table_amount = f"{self._normalize_amount_text(session.table_amount)}元" if session.table_amount else "未识别"
        uppercase_raw = session.uppercase_amount_raw or session.uppercase_amount_text or "未识别"
        uppercase_numeric = (
            f"{self._normalize_amount_text(session.uppercase_amount_numeric)}元"
            if session.uppercase_amount_numeric
            else "未识别"
        )
        note = session.amount_conflict_note or "检测到合计金额与大写金额不一致，请确认提交金额来源。"
        summary = (
            f"合计金额：{table_amount}\n"
            f"大写金额：{uppercase_raw}\n"
            f"大写换算：{uppercase_numeric}\n"
            f"{note}"
        )
        if remind:
            summary = "请先完成金额冲突确认。\n" + summary
        card = {
            "card_type": "reimbursement_amount_conflict_confirmation",
            "title": "报销金额冲突确认",
            "summary": summary,
            "table_amount": table_amount,
            "uppercase_amount_raw": uppercase_raw,
            "uppercase_amount_numeric": uppercase_numeric,
            "conflict_note": note,
            "actions": [
                {"label": "按合计提交", "action": "reimbursement_amount_use_table", "status": "primary"},
                {"label": "按大写提交", "action": "reimbursement_amount_use_uppercase", "status": "primary"},
                {"label": "取消", "action": "reimbursement_cancel_submit", "status": "warning"},
            ],
            "next_action": "请选择金额来源后再继续提交。",
        }
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_amount_conflict_confirmation",
            intent="reimbursement",
            reply=AgentReply(channel="interactive_card", interactive_card=card),
        )

    @classmethod
    def _waiting_button_action_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_waiting_button_action",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="请点击卡片按钮完成操作：确认提交 或 取消。"),
        )

    @classmethod
    def _submitted_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=True,
            reason="reimbursement_travel_submitted",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="已提交，审批中。"),
        )

    @classmethod
    def _handoff_fallback_result_with_hint(cls, *, short_reason: str, suggestion: str) -> ChatHandleResult:
        text = "自动提交流程失败，请到 OA 审批入口手动提交。"
        normalized_reason = short_reason.strip()
        normalized_suggestion = suggestion.strip()
        if normalized_reason and normalized_suggestion:
            text = (
                "自动提交流程失败。\n"
                f"失败原因：{normalized_reason}\n"
                f"建议：{normalized_suggestion}"
            )
        elif normalized_reason:
            text = (
                "自动提交流程失败。\n"
                f"失败原因：{normalized_reason}\n"
                "建议：请稍后重试，若持续失败请联系管理员核查审批配置。"
            )
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_handoff_fallback",
            intent="reimbursement",
            reply=AgentReply(channel="text", text=text),
        )

    @classmethod
    def _handoff_fallback_result(cls, *, short_reason: str = "", suggestion: str = "") -> ChatHandleResult:
        if short_reason or suggestion:
            return cls._handoff_fallback_result_with_hint(short_reason=short_reason, suggestion=suggestion)
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_handoff_fallback",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="自动提交流程失败，请到 OA 审批入口手动提交。"),
        )

    @staticmethod
    def _map_submit_failure_to_user_hint(result: ReimbursementApprovalResult) -> tuple[str, str]:
        category = (result.failure_category or "").strip().lower()
        mapping: dict[str, tuple[str, str]] = {
            "field_mapping": (
                "审批字段映射与模板不一致。",
                "请联系管理员核对审批表单字段名称与系统配置后重试。",
            ),
            "value_format": (
                "审批字段值格式不符合模板要求。",
                "请检查金额、日期、费用归属公司后重试。",
            ),
            "permission_identity": (
                "当前账号暂无该审批发起权限。",
                "请确认机器人应用授权与发起人审批权限后重试。",
            ),
            "transport_error": (
                "审批接口调用失败或超时。",
                "请稍后重试，若持续失败请联系管理员检查网络与OpenAPI配置。",
            ),
            "unknown": (
                "审批接口返回未知错误。",
                "请稍后重试并联系管理员查看日志中的错误码详情。",
            ),
        }
        if category in mapping:
            return mapping[category]
        reason = (result.reason or "").strip()
        if reason == "transport_error":
            return mapping["transport_error"]
        return mapping["unknown"]

    @classmethod
    def _cancelled_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_cancelled",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="已取消本次差旅报销办理。"),
        )

    @classmethod
    def _not_found_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_not_found",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="未找到待确认报销，请重新发送“我要报销差旅费”。"),
        )

    @classmethod
    def _confirmation_expired_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_confirmation_expired",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="本次报销确认已过期，请重新发送“我要报销差旅费”。"),
        )

    @classmethod
    def _timeout_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_timeout",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="本次报销会话已超时，请重新发送“我要报销差旅费”。"),
        )

    @classmethod
    def _no_trip_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="reimbursement_travel_no_trip",
            intent="reimbursement",
            reply=AgentReply(channel="text", text="未找到近30天已通过的出差申请，请先完成出差审批后再报销。"),
        )


def _parse_company_options(raw: str | None) -> tuple[str, ...]:
    text = (raw or "").strip()
    if not text:
        return _DEFAULT_COMPANY_OPTIONS
    values = tuple(item.strip().upper() for item in text.replace("，", ",").split(",") if item.strip())
    return values or _DEFAULT_COMPANY_OPTIONS


def build_default_reimbursement_request_orchestrator(
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> ReimbursementRequestOrchestrator:
    from app.integrations.dingtalk.reimbursement_attachment import build_default_reimbursement_attachment_processor
    from app.integrations.dingtalk.reimbursement_approval import build_default_reimbursement_approval_creator
    from app.integrations.dingtalk.travel_approval import build_default_travel_application_provider

    company_options = _parse_company_options(os.getenv("DINGTALK_REIMBURSE_COMPANY_OPTIONS"))
    fixed_company = str(os.getenv("DINGTALK_REIMBURSE_FIXED_COMPANY") or _DEFAULT_FIXED_COMPANY).strip() or _DEFAULT_FIXED_COMPANY
    lookback_raw = str(os.getenv("DINGTALK_REIMBURSE_TRAVEL_LOOKBACK_DAYS") or "30").strip()
    try:
        lookback_days = max(1, int(lookback_raw))
    except ValueError:
        lookback_days = 30

    return ReimbursementRequestOrchestrator(
        travel_application_provider=build_default_travel_application_provider(),
        attachment_processor=build_default_reimbursement_attachment_processor(),
        approval_creator=build_default_reimbursement_approval_creator(),
        now_provider=now_provider,
        company_options=company_options,
        fixed_company=fixed_company,
        travel_lookback_days=lookback_days,
    )
