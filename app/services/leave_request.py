from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Callable, Literal, Protocol
from zoneinfo import ZoneInfo

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult
from app.schemas.user_context import UserContext

SESSION_TIMEOUT_SECONDS = 30 * 60
CONFIRMATION_TIMEOUT_SECONDS = 5 * 60
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")

_DEFAULT_START_HOUR = 9
_DEFAULT_END_HOUR = 18

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

_LEAVE_TYPE_NORMALIZATION = {
    "年休假": "年假",
    "年假": "年假",
    "病假": "病假",
    "事假": "事假",
    "调休": "调休",
    "婚假": "婚假",
    "产假": "产假",
    "陪产假": "陪产假",
    "丧假": "丧假",
}

_REASON_PREFIXES = ("原因是", "事由是", "因为", "事由", "原因")
_GENERIC_PREFIXES = ("我要", "我想", "帮我", "请帮我", "发起", "提交", "申请")

_DATE_TOKEN_PATTERN = re.compile(r"(20\d{2}[./-]\d{1,2}[./-]\d{1,2}|\d{1,2}月\d{1,2}[日号]?|明后天|今天|明天|后天)")
_TIME_TOKEN_PATTERN = re.compile(r"(\d{1,2}[:：]\d{2}|\d{1,2}点半?|\d{1,2}点)")
_RANGE_CONNECTOR_PATTERN = re.compile(r"(到|至|~|～)")
_DURATION_HINT_PATTERN = re.compile(r"[一二两三四五六七八九十0-9]+天")

_RELATIVE_DAY_OFFSETS = {
    "今天": 0,
    "明天": 1,
    "后天": 2,
}

_RELATIVE_DAY_RANGE_OFFSETS = {
    "明后天": (1, 2),
}


@dataclass(frozen=True)
class LeaveApprovalSubmission:
    originator_user_id: str
    applicant_name: str
    department: str
    department_id: str
    leave_type: str
    leave_time: str
    leave_start_time: str
    leave_end_time: str
    leave_reason: str


@dataclass(frozen=True)
class LeaveApprovalResult:
    success: bool
    reason: str
    process_instance_id: str = ""


class LeaveApprovalCreator(Protocol):
    def submit(self, submission: LeaveApprovalSubmission) -> LeaveApprovalResult: ...


@dataclass
class _LeaveSession:
    originator_user_id: str
    applicant_name: str
    department: str
    department_id: str
    leave_type: str = ""
    leave_start_time: str = ""
    leave_end_time: str = ""
    leave_reason: str = ""
    stage: Literal["collecting", "awaiting_confirmation"] = "collecting"
    created_at: datetime | None = None
    updated_at: datetime | None = None
    confirmation_started_at: datetime | None = None


class LeaveRequestOrchestrator:
    """Collect and confirm a leave request draft before OA handoff."""

    def __init__(
        self,
        *,
        approval_creator: LeaveApprovalCreator | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ) -> None:
        self._sessions: dict[str, _LeaveSession] = {}
        self._approval_creator = approval_creator
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
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
        text: str,
        user_context: UserContext | None,
        force_start: bool,
    ) -> ChatHandleResult | None:
        key = self._session_key(conversation_id=conversation_id, sender_id=sender_id)
        now = self._now()
        normalized = self._normalize(text)
        session = self._sessions.get(key)

        if session is None:
            if not force_start:
                return None
            session = _LeaveSession(
                originator_user_id=self._resolve_originator_user_id(user_context=user_context, sender_id=sender_id),
                applicant_name=self._resolve_applicant_name(user_context=user_context, sender_id=sender_id),
                department=self._resolve_department(user_context=user_context),
                department_id=self._resolve_department_id(user_context=user_context),
                created_at=now,
                updated_at=now,
            )
            _, reset_confirmation_timer = self._apply_updates(session=session, text=text, now=now)
            self._transition_stage(session=session, now=now, reset_confirmation_timer=reset_confirmation_timer)
            self._sessions[key] = session
            return self._result_for(session=session)

        if self._is_session_timed_out(session=session, now=now):
            self._sessions.pop(key, None)
            return self._timeout_result()

        self._refresh_context_fields(session=session, user_context=user_context, sender_id=sender_id)
        if session.stage == "collecting" and self._is_cancel(normalized):
            self._sessions.pop(key, None)
            return self._cancelled_result()

        if self._is_confirmation_expired(session=session, now=now):
            self._sessions.pop(key, None)
            return self._confirmation_expired_result()

        if session.stage == "awaiting_confirmation" and self._is_text_confirmation_or_cancel(normalized):
            return self._waiting_button_action_result()

        updated, reset_confirmation_timer = self._apply_updates(session=session, text=text, now=now)
        related = self._looks_leave_related(text)
        if not updated and not related:
            return None

        self._transition_stage(session=session, now=now, reset_confirmation_timer=reset_confirmation_timer)
        self._sessions[key] = session
        return self._result_for(session=session)

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

        if session.stage != "awaiting_confirmation":
            return self._not_found_result()

        normalized_action = self._normalize(action)
        if normalized_action == "leave_cancel_submit":
            self._sessions.pop(key, None)
            return self._cancelled_result()

        if normalized_action != "leave_confirm_submit":
            return self._waiting_button_action_result()

        self._sessions.pop(key, None)
        return self._confirm_result(session=session)

    @staticmethod
    def _normalize(text: str) -> str:
        return "".join((text or "").strip().split())

    @staticmethod
    def _is_cancel(normalized: str) -> bool:
        return normalized in _CANCEL_TOKENS

    @staticmethod
    def _is_text_confirmation_or_cancel(normalized: str) -> bool:
        return normalized in _TEXT_CONFIRM_OR_CANCEL_TOKENS

    @staticmethod
    def _resolve_applicant_name(*, user_context: UserContext | None, sender_id: str) -> str:
        if user_context is None:
            return sender_id
        user_name = (user_context.user_name or "").strip()
        return user_name if user_name and user_name != "unknown" else user_context.user_id or sender_id

    @staticmethod
    def _resolve_department(*, user_context: UserContext | None) -> str:
        if user_context is None:
            return "unknown"
        dept = (user_context.dept_name or "").strip()
        if dept and dept != "unknown":
            return dept
        dept_id = (user_context.dept_id or "").strip()
        return dept_id if dept_id else "unknown"

    @staticmethod
    def _resolve_department_id(*, user_context: UserContext | None) -> str:
        if user_context is None:
            return ""
        dept_id = (user_context.dept_id or "").strip()
        if not dept_id or dept_id == "unknown":
            return ""
        return dept_id

    @staticmethod
    def _resolve_originator_user_id(*, user_context: UserContext | None, sender_id: str) -> str:
        if user_context is None:
            return sender_id.strip()
        user_id = (user_context.user_id or "").strip()
        if user_id and user_id != "unknown":
            return user_id
        return sender_id.strip()

    @staticmethod
    def _is_session_timed_out(*, session: _LeaveSession, now: datetime) -> bool:
        anchor = session.updated_at or session.created_at or now
        return (now - anchor).total_seconds() > SESSION_TIMEOUT_SECONDS

    @staticmethod
    def _is_confirmation_expired(*, session: _LeaveSession, now: datetime) -> bool:
        if session.stage != "awaiting_confirmation":
            return False
        anchor = session.confirmation_started_at
        if anchor is None:
            return False
        return (now - anchor).total_seconds() > CONFIRMATION_TIMEOUT_SECONDS

    def _refresh_context_fields(self, *, session: _LeaveSession, user_context: UserContext | None, sender_id: str) -> None:
        if not session.department_id:
            session.department_id = self._resolve_department_id(user_context=user_context)
        if session.department == "unknown":
            resolved_department = self._resolve_department(user_context=user_context)
            if resolved_department != "unknown":
                session.department = resolved_department
        if not session.originator_user_id or session.originator_user_id == "unknown":
            session.originator_user_id = self._resolve_originator_user_id(user_context=user_context, sender_id=sender_id)

    def _apply_updates(self, *, session: _LeaveSession, text: str, now: datetime) -> tuple[bool, bool]:
        updated = False
        reset_confirmation_timer = False

        leave_type = self._extract_leave_type(text)
        if leave_type and leave_type != session.leave_type:
            session.leave_type = leave_type
            updated = True
            reset_confirmation_timer = True

        leave_start_time, leave_end_time = self._extract_leave_range(text, now=now)
        if leave_start_time and leave_start_time != session.leave_start_time:
            session.leave_start_time = leave_start_time
            updated = True
            reset_confirmation_timer = True
        if leave_end_time and leave_end_time != session.leave_end_time:
            session.leave_end_time = leave_end_time
            updated = True
            reset_confirmation_timer = True

        leave_reason = self._extract_leave_reason(text)
        if leave_reason and leave_reason != session.leave_reason:
            session.leave_reason = leave_reason
            updated = True

        if updated:
            session.updated_at = now
        return updated, reset_confirmation_timer

    @staticmethod
    def _extract_leave_type(text: str) -> str:
        compact = "".join((text or "").strip().split())
        ordered_aliases = sorted(_LEAVE_TYPE_NORMALIZATION.keys(), key=len, reverse=True)
        for alias in ordered_aliases:
            if alias in compact:
                return _LEAVE_TYPE_NORMALIZATION[alias]
        return ""

    @classmethod
    def _extract_leave_range(cls, text: str, *, now: datetime) -> tuple[str, str]:
        compact = "".join((text or "").strip().split())
        if not cls._contains_temporal_hint(compact):
            return "", ""

        reference_date = now.astimezone(LOCAL_TIMEZONE).date()
        range_with_connector = cls._parse_range_with_connector(compact, reference_date=reference_date)
        if range_with_connector is not None:
            return cls._format_datetime(range_with_connector[0]), cls._format_datetime(range_with_connector[1])

        range_without_connector = cls._parse_range_without_connector(compact, reference_date=reference_date)
        if range_without_connector is None:
            return "", ""
        return cls._format_datetime(range_without_connector[0]), cls._format_datetime(range_without_connector[1])

    @classmethod
    def _contains_temporal_hint(cls, compact: str) -> bool:
        if not compact:
            return False
        if _DATE_TOKEN_PATTERN.search(compact):
            return True
        if _TIME_TOKEN_PATTERN.search(compact):
            return True
        return any(token in compact for token in ("到", "至", "~", "～"))

    @classmethod
    def _parse_range_with_connector(
        cls,
        compact: str,
        *,
        reference_date: date,
    ) -> tuple[datetime, datetime] | None:
        for connector_match in _RANGE_CONNECTOR_PATTERN.finditer(compact):
            left = compact[: connector_match.start()]
            right = compact[connector_match.end() :]
            if not left or not right:
                continue
            start_dt = cls._parse_datetime_fragment(
                left,
                reference_date=reference_date,
                fallback_date=None,
                is_start=True,
            )
            if start_dt is None:
                continue
            end_dt = cls._parse_datetime_fragment(
                right,
                reference_date=reference_date,
                fallback_date=start_dt.date(),
                is_start=False,
            )
            if end_dt is None:
                continue
            if end_dt < start_dt:
                continue
            return start_dt, end_dt
        return None

    @classmethod
    def _parse_range_without_connector(
        cls,
        compact: str,
        *,
        reference_date: date,
    ) -> tuple[datetime, datetime] | None:
        dates = cls._extract_dates(compact, reference_date=reference_date)
        if not dates:
            return None
        times = cls._extract_times(compact)

        if len(dates) >= 2:
            start_date = dates[0]
            end_date = dates[1]
            if len(times) >= 2:
                start_time = times[0]
                end_time = times[1]
            elif len(times) == 1:
                start_time = times[0]
                end_time = (_DEFAULT_END_HOUR, 0)
            else:
                start_time = (_DEFAULT_START_HOUR, 0)
                end_time = (_DEFAULT_END_HOUR, 0)
        else:
            start_date = dates[0]
            end_date = dates[0]
            if len(times) >= 2:
                start_time = times[0]
                end_time = times[1]
            elif not times:
                start_time = (_DEFAULT_START_HOUR, 0)
                end_time = (_DEFAULT_END_HOUR, 0)
            else:
                return None

        start_dt = datetime(
            start_date.year,
            start_date.month,
            start_date.day,
            start_time[0],
            start_time[1],
            tzinfo=LOCAL_TIMEZONE,
        )
        end_dt = datetime(
            end_date.year,
            end_date.month,
            end_date.day,
            end_time[0],
            end_time[1],
            tzinfo=LOCAL_TIMEZONE,
        )
        if end_dt < start_dt:
            return None
        return start_dt, end_dt

    @classmethod
    def _parse_datetime_fragment(
        cls,
        fragment: str,
        *,
        reference_date: date,
        fallback_date: date | None,
        is_start: bool,
    ) -> datetime | None:
        dates = cls._extract_dates(fragment, reference_date=reference_date)
        times = cls._extract_times(fragment)

        if dates:
            target_date = dates[0]
        elif fallback_date is not None:
            target_date = fallback_date
        else:
            return None

        if times:
            target_time = times[0]
        elif is_start:
            target_time = (_DEFAULT_START_HOUR, 0)
        else:
            target_time = (_DEFAULT_END_HOUR, 0)

        return datetime(
            target_date.year,
            target_date.month,
            target_date.day,
            target_time[0],
            target_time[1],
            tzinfo=LOCAL_TIMEZONE,
        )

    @classmethod
    def _extract_dates(cls, text: str, *, reference_date: date) -> list[date]:
        results: list[date] = []
        for match in _DATE_TOKEN_PATTERN.finditer(text):
            token = match.group(0)
            relative_range = _RELATIVE_DAY_RANGE_OFFSETS.get(token)
            if relative_range is not None:
                base = reference_date.toordinal()
                for offset in relative_range:
                    results.append(reference_date.fromordinal(base + offset))
                continue

            parsed = cls._parse_date_token(token, reference_date=reference_date)
            if parsed is not None:
                results.append(parsed)
        return results

    @classmethod
    def _parse_date_token(cls, token: str, *, reference_date: date) -> date | None:
        normalized = token.strip()
        offset = _RELATIVE_DAY_OFFSETS.get(normalized)
        if offset is not None:
            return reference_date.fromordinal(reference_date.toordinal() + offset)

        if "月" in normalized:
            match = re.match(r"(\d{1,2})月(\d{1,2})", normalized)
            if match is None:
                return None
            month = int(match.group(1))
            day = int(match.group(2))
            year = reference_date.year
            try:
                return date(year, month, day)
            except ValueError:
                return None

        if re.match(r"20\d{2}[./-]\d{1,2}[./-]\d{1,2}", normalized):
            parts = re.split(r"[./-]", normalized)
            if len(parts) != 3:
                return None
            try:
                year = int(parts[0])
                month = int(parts[1])
                day = int(parts[2])
                return date(year, month, day)
            except ValueError:
                return None
        return None

    @staticmethod
    def _extract_times(text: str) -> list[tuple[int, int]]:
        results: list[tuple[int, int]] = []
        for match in _TIME_TOKEN_PATTERN.finditer(text):
            parsed = LeaveRequestOrchestrator._parse_time_token(match.group(0))
            if parsed is not None:
                results.append(parsed)
        return results

    @staticmethod
    def _parse_time_token(token: str) -> tuple[int, int] | None:
        normalized = token.strip().replace("：", ":")
        if ":" in normalized:
            parts = normalized.split(":", 1)
            if len(parts) != 2:
                return None
            try:
                hour = int(parts[0])
                minute = int(parts[1])
            except ValueError:
                return None
            if hour < 0 or hour > 23 or minute < 0 or minute > 59:
                return None
            return hour, minute

        if normalized.endswith("点半"):
            raw_hour = normalized[: -2]
            if not raw_hour.isdigit():
                return None
            hour = int(raw_hour)
            if hour < 0 or hour > 23:
                return None
            return hour, 30

        if normalized.endswith("点"):
            raw_hour = normalized[: -1]
            if not raw_hour.isdigit():
                return None
            hour = int(raw_hour)
            if hour < 0 or hour > 23:
                return None
            return hour, 0
        return None

    @staticmethod
    def _format_datetime(value: datetime) -> str:
        return value.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M")

    @staticmethod
    def _extract_leave_reason(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""
        compact = "".join(raw.split())
        for prefix in _REASON_PREFIXES:
            if prefix in compact:
                value = compact.split(prefix, 1)[1].strip("：:，,。.!！?？")
                if len(value) >= 2:
                    return value
        return ""

    @staticmethod
    def _looks_leave_related(text: str) -> bool:
        compact = "".join((text or "").strip().split())
        if not compact:
            return False
        if any(alias in compact for alias in _LEAVE_TYPE_NORMALIZATION):
            return True
        if any(token in compact for token in ("请假", "改成", "修改", "换成", "原因", "事由")):
            return True
        if _DATE_TOKEN_PATTERN.search(compact):
            return True
        if _TIME_TOKEN_PATTERN.search(compact):
            return True
        return bool(_DURATION_HINT_PATTERN.search(compact))

    @staticmethod
    def _transition_stage(*, session: _LeaveSession, now: datetime, reset_confirmation_timer: bool) -> None:
        required_filled = bool(session.leave_type and session.leave_start_time and session.leave_end_time)
        if required_filled:
            session.stage = "awaiting_confirmation"
            if reset_confirmation_timer or session.confirmation_started_at is None:
                session.confirmation_started_at = now
            return
        session.stage = "collecting"
        session.confirmation_started_at = None

    def _result_for(self, *, session: _LeaveSession) -> ChatHandleResult:
        if session.stage == "awaiting_confirmation":
            return self._ready_result(session=session)
        return self._collecting_result(session=session)

    def _confirm_result(self, *, session: _LeaveSession) -> ChatHandleResult:
        if self._approval_creator is None:
            return self._handoff_result(session=session)

        submission = LeaveApprovalSubmission(
            originator_user_id=session.originator_user_id,
            applicant_name=session.applicant_name,
            department=session.department,
            department_id=session.department_id,
            leave_type=session.leave_type,
            leave_time=self._build_leave_time_text(session=session),
            leave_start_time=session.leave_start_time,
            leave_end_time=session.leave_end_time,
            leave_reason=session.leave_reason,
        )
        approval_result = self._approval_creator.submit(submission)
        if approval_result.success:
            self._logger.info(
                "leave.approval.submit",
                extra={
                    "obs": {
                        "module": "services.leave_request",
                        "event": "leave_approval_submitted",
                        "originator_user_id": submission.originator_user_id,
                        "leave_type": submission.leave_type,
                        "reason": approval_result.reason,
                        "process_instance_id": approval_result.process_instance_id,
                    }
                },
            )
            return self._submitted_result(session=session)

        self._logger.warning(
            "leave.approval.submit failed",
            extra={
                "obs": {
                    "module": "services.leave_request",
                    "event": "leave_approval_fallback",
                    "originator_user_id": submission.originator_user_id,
                    "leave_type": submission.leave_type,
                    "reason": approval_result.reason,
                }
            },
        )
        return self._fallback_handoff_result(session=session)

    @classmethod
    def _collecting_result(cls, *, session: _LeaveSession) -> ChatHandleResult:
        if not session.leave_type and not session.leave_start_time and not session.leave_end_time:
            next_question = "想请哪种假？再告诉我开始和结束时间，例如“年假，4月1日到4月2日”。"
        elif not session.leave_type:
            next_question = "请先告诉我请假类型，例如年假、病假、事假或调休。"
        else:
            next_question = "请告诉我请假时间（开始和结束），例如“4月1日到4月2日”。"
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_collecting",
            intent="leave",
            reply=AgentReply(channel="text", text=next_question),
        )

    @classmethod
    def _ready_result(cls, *, session: _LeaveSession) -> ChatHandleResult:
        card = {
            "card_type": "leave_request_ready",
            "title": "请假确认",
            "summary": cls._build_ready_summary(session=session),
            "actions": [
                {"label": "确认提交", "action": "leave_confirm_submit", "status": "primary"},
                {"label": "取消", "action": "leave_cancel_submit", "status": "warning"},
            ],
            "next_action": "请在 5 分钟内点击卡片按钮确认提交或取消。",
        }
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_ready",
            intent="leave",
            reply=AgentReply(channel="interactive_card", interactive_card=card),
        )

    @classmethod
    def _build_ready_summary(cls, *, session: _LeaveSession) -> str:
        if session.leave_reason:
            return (
                f"我将按{session.leave_type}、{session.leave_start_time} 到 {session.leave_end_time}"
                f"发起请假审批，事由是{session.leave_reason}。"
            )
        return f"我将按{session.leave_type}、{session.leave_start_time} 到 {session.leave_end_time}发起请假审批。"

    @staticmethod
    def _build_leave_time_text(*, session: _LeaveSession) -> str:
        return f"{session.leave_start_time} 到 {session.leave_end_time}".strip()

    @classmethod
    def _waiting_button_action_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_waiting_button_action",
            intent="leave",
            reply=AgentReply(channel="text", text="请点击卡片按钮完成操作：确认提交 或 取消。"),
        )

    @classmethod
    def _submitted_result(cls, *, session: _LeaveSession) -> ChatHandleResult:
        text = (
            f"好的，已帮你发起“{session.leave_type} / {session.leave_start_time} 到 {session.leave_end_time}”请假审批。"
            "你可以在钉钉 OA 审批里查看进度；如果要改，重新告诉我即可。"
        )
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_submitted",
            intent="leave",
            reply=AgentReply(channel="text", text=text),
        )

    @classmethod
    def _handoff_result(cls, *, session: _LeaveSession) -> ChatHandleResult:
        text = (
            f"好的，已按“{session.leave_type} / {session.leave_start_time} 到 {session.leave_end_time}”整理。"
            "请现在到 控制台/工作台 > OA审批 > 请假 提交；"
            "如果还要改，重新告诉我即可。"
        )
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_handoff",
            intent="leave",
            reply=AgentReply(channel="text", text=text),
        )

    @classmethod
    def _fallback_handoff_result(cls, *, session: _LeaveSession) -> ChatHandleResult:
        text = (
            f"暂时没能直接帮你发起钉钉审批，已按“{session.leave_type} / {session.leave_start_time} 到 {session.leave_end_time}”整理。"
            "请现在到 控制台/工作台 > OA审批 > 请假 提交；"
            "如果还要改，重新告诉我即可。"
        )
        return ChatHandleResult(
            handled=True,
            reason="leave_workflow_handoff_fallback",
            intent="leave",
            reply=AgentReply(channel="text", text=text),
        )

    @classmethod
    def _cancelled_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="leave_workflow_cancelled",
            intent="leave",
            reply=AgentReply(channel="text", text="已取消本次请假办理。如需重新发起，请发送“我要请假”。"),
        )

    @classmethod
    def _not_found_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="leave_workflow_not_found",
            intent="leave",
            reply=AgentReply(channel="text", text="未找到待确认请假，请重新发送“我要请假”。"),
        )

    @classmethod
    def _confirmation_expired_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="leave_workflow_confirmation_expired",
            intent="leave",
            reply=AgentReply(channel="text", text="本次请假确认已过期，请重新发送“我要请假”开始。"),
        )

    @classmethod
    def _timeout_result(cls) -> ChatHandleResult:
        return ChatHandleResult(
            handled=False,
            reason="leave_workflow_timeout",
            intent="leave",
            reply=AgentReply(channel="text", text="本次请假会话已超时，请重新发送“我要请假”开始。"),
        )


def build_default_leave_request_orchestrator(
    *,
    now_provider: Callable[[], datetime] | None = None,
) -> LeaveRequestOrchestrator:
    from app.integrations.dingtalk.leave_approval import build_default_leave_approval_creator

    return LeaveRequestOrchestrator(
        approval_creator=build_default_leave_approval_creator(),
        now_provider=now_provider,
    )
