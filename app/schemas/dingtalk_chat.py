from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

ConversationType = Literal["single", "group", "unknown"]
OutputChannel = Literal["text", "interactive_card"]


@dataclass(frozen=True)
class IncomingChatMessage:
    event_id: str
    conversation_id: str
    conversation_type: ConversationType
    sender_id: str
    message_type: str
    text: str


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
    reply: AgentReply

    def to_dict(self) -> dict[str, Any]:
        return {
            "handled": self.handled,
            "reason": self.reason,
            "reply": self.reply.to_dict(),
        }
