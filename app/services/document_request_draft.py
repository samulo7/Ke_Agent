from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable

from app.schemas.dingtalk_chat import AgentReply, ChatHandleResult
from app.schemas.user_context import UserContext
from app.services.llm_draft_generation import LLMDraftGenerationService, build_default_llm_draft_generation_service

MAX_FOLLOWUP_ROUNDS = 1
SESSION_TIMEOUT_SECONDS = 5 * 60

_CANCEL_TOKENS = {"取消", "算了", "不用了", "终止", "结束"}
_PREFIXES = (
    "我想申请",
    "我要申请",
    "帮我申请",
    "请帮我申请",
    "申请",
    "我想要",
    "我要",
    "我需要",
    "需要",
)
_PURPOSE_PREFIXES = ("用作", "作为", "用于")
_PUNCTUATION_RE = re.compile(r"[;；\n]")


@dataclass
class _DraftSession:
    requested_item: str
    applicant_name: str
    department: str
    request_purpose: str = ""
    followup_rounds: int = 0
    created_at: datetime | None = None


class DocumentRequestDraftOrchestrator:
    """B-14 simplified document-request draft collection flow."""

    def __init__(
        self,
        *,
        now_provider: Callable[[], datetime] | None = None,
        llm_draft_service: LLMDraftGenerationService | None = None,
    ) -> None:
        self._sessions: dict[str, _DraftSession] = {}
        self._now = now_provider or (lambda: datetime.now(timezone.utc))
        self._llm_draft_service = llm_draft_service or build_default_llm_draft_generation_service()

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
        session = self._sessions.get(key)

        if session is None:
            if not force_start:
                return None
            llm_initial = self._llm_draft_service.extract_initial(
                text=text,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
            session = _DraftSession(
                requested_item=llm_initial.requested_item or self._extract_requested_item(text),
                applicant_name=self._resolve_applicant_name(user_context=user_context, sender_id=sender_id),
                department=self._resolve_department(user_context=user_context),
                request_purpose=llm_initial.request_purpose,
                created_at=now,
            )
            self._sessions[key] = session
            if session.request_purpose:
                polished = self._llm_draft_service.polish_purpose(
                    purpose=session.request_purpose,
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )
                session.request_purpose = polished.request_purpose or session.request_purpose
                self._sessions.pop(key, None)
                return self._ready_result(session=session)
            return self._collecting_result(session=session)

        if self._is_cancel(text):
            self._sessions.pop(key, None)
            return ChatHandleResult(
                handled=False,
                reason="application_draft_cancelled",
                intent="document_request",
                reply=AgentReply(
                    channel="text",
                    text="已取消本次文档申请草稿。如需重新发起，直接说“我要申请XX文件”。",
                ),
            )

        created_at = session.created_at or now
        if (now - created_at).total_seconds() > SESSION_TIMEOUT_SECONDS:
            self._sessions.pop(key, None)
            return ChatHandleResult(
                handled=False,
                reason="application_draft_timeout",
                intent="document_request",
                reply=AgentReply(
                    channel="text",
                    text="本次申请已超时未完成。你可以重新发起，或直接联系人事行政处理。",
                ),
            )

        llm_followup = self._llm_draft_service.extract_followup(
            text=text,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        purpose = llm_followup.request_purpose or self._extract_request_purpose(text)
        if purpose:
            session.request_purpose = purpose

        if session.request_purpose:
            polished = self._llm_draft_service.polish_purpose(
                purpose=session.request_purpose,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
            session.request_purpose = polished.request_purpose or session.request_purpose
            self._sessions.pop(key, None)
            return self._ready_result(session=session)

        session.followup_rounds += 1
        if session.followup_rounds >= MAX_FOLLOWUP_ROUNDS:
            self._sessions.pop(key, None)
            return ChatHandleResult(
                handled=False,
                reason="application_draft_incomplete",
                intent="document_request",
                reply=AgentReply(
                    channel="text",
                    text=(
                        "当前信息仍不完整，已结束本轮收集。"
                        "你可以稍后重新发起，或直接联系人事行政协助处理。"
                    ),
                ),
            )

        return self._collecting_result(session=session)

    @staticmethod
    def _is_cancel(text: str) -> bool:
        normalized = "".join((text or "").strip().split())
        return normalized in _CANCEL_TOKENS

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
    def _extract_requested_item(text: str) -> str:
        cleaned = (text or "").strip()
        normalized = "".join(cleaned.split())
        for prefix in _PREFIXES:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix) :]
                break
        normalized = normalized.strip("：:，,。.!！?？")
        return normalized or "相关资料"

    @staticmethod
    def _extract_request_purpose(text: str) -> str:
        raw = (text or "").strip()
        if not raw:
            return ""

        compact = "".join(raw.split())
        for prefix in _PURPOSE_PREFIXES:
            if compact.startswith(prefix):
                value = compact[len(prefix) :].strip("：:，,。.!！?？")
                if len(value) >= 2:
                    return value

        fragments = _PUNCTUATION_RE.split(raw.replace("。", ";"))
        for fragment in fragments:
            item = fragment.strip()
            if not item:
                continue
            if "用途" in item:
                candidate = item
                if "：" in item:
                    candidate = item.split("：", 1)[1]
                elif ":" in item:
                    candidate = item.split(":", 1)[1]
                elif "用途是" in item:
                    candidate = item.split("用途是", 1)[1]
                candidate = candidate.strip("：:，,。.!！?？ ")
                if len(candidate) >= 2:
                    return candidate
        return ""

    @staticmethod
    def _collecting_result(*, session: _DraftSession) -> ChatHandleResult:
        card = {
            "card_type": "application_draft_collecting",
            "title": f"申请信息收集 · {session.requested_item}",
            "missing_field": "申请用途",
            "draft_fields": {
                "applicant_name": session.applicant_name,
                "department": session.department,
                "requested_item": session.requested_item,
                "request_purpose": session.request_purpose or "",
            },
            "field_status": {
                "applicant_name": "filled",
                "department": "filled",
                "requested_item": "filled",
                "request_purpose": "missing",
            },
            "actions": ["取消"],
            "next_action": "请补充申请用途（例如：用于采购流程复盘）。",
        }
        return ChatHandleResult(
            handled=True,
            reason="application_draft_collecting",
            intent="document_request",
            reply=AgentReply(channel="interactive_card", interactive_card=card),
        )

    @staticmethod
    def _ready_result(*, session: _DraftSession) -> ChatHandleResult:
        card = {
            "card_type": "application_draft_ready",
            "title": "文档申请草稿",
            "draft_fields": {
                "applicant_name": session.applicant_name,
                "department": session.department,
                "requested_item": session.requested_item,
                "request_purpose": session.request_purpose,
                "suggested_approver": "人事行政",
            },
            "process_path": [
                "人事行政",
                "领导确认",
                "发放扫描件或纸质文件",
            ],
            "next_action": "请将以上草稿提交给人事行政，由其进入领导确认后安排发放。",
        }
        return ChatHandleResult(
            handled=True,
            reason="application_draft_ready",
            intent="document_request",
            reply=AgentReply(channel="interactive_card", interactive_card=card),
        )
