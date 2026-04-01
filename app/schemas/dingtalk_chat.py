from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

ConversationType = Literal["single", "group", "unknown"]
OutputChannel = Literal["text", "interactive_card"]
IntentType = Literal["policy_process", "document_request", "file_request", "reimbursement", "leave", "fixed_quote", "other"]
PermissionDecision = Literal["allow", "summary_only", "deny"]


@dataclass(frozen=True)
class IncomingChatMessage:
    event_id: str
    conversation_id: str
    conversation_type: ConversationType
    sender_id: str
    message_type: str
    text: str
    sender_staff_id: str = ""
    sender_nick: str = ""
    file_name: str = ""
    file_download_url: str = ""
    file_media_id: str = ""
    file_content_base64: str = ""


@dataclass(frozen=True)
class AgentReply:
    channel: OutputChannel
    text: str | None = None
    interactive_card: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        if self.channel == "text":
            return {
                "channel": self.channel,
                "text": self.text or "",
                "interactive_card": None,
            }
        return {
            "channel": self.channel,
            "text": None,
            "interactive_card": self.interactive_card or {},
        }


@dataclass(frozen=True)
class ChatHandleResult:
    handled: bool
    reason: str
    intent: IntentType
    reply: AgentReply
    followup_replies: tuple[AgentReply, ...] = ()
    source_ids: tuple[str, ...] = ()
    permission_decision: PermissionDecision = "allow"
    knowledge_version: str = ""
    answered_at: str = ""
    citations: tuple[dict[str, str], ...] = ()
    llm_trace: dict[str, Any] = field(default_factory=dict)

    def all_replies(self) -> tuple[AgentReply, ...]:
        return (self.reply, *self.followup_replies)

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "reason": self.reason,
            "intent": self.intent,
            "reply": self.reply.to_dict(),
            "followup_replies": [item.to_dict() for item in self.followup_replies],
            "source_ids": list(self.source_ids),
            "permission_decision": self.permission_decision,
            "knowledge_version": self.knowledge_version,
            "answered_at": self.answered_at,
            "citations": [dict(item) for item in self.citations],
            "llm_trace": dict(self.llm_trace),
        }
