from __future__ import annotations

from typing import Any

from app.schemas.dingtalk_chat import AgentReply


def build_dingtalk_payload(reply: AgentReply) -> dict[str, Any]:
    if reply.channel == "text":
        return {
            "msgtype": "text",
            "text": {"content": reply.text or ""},
        }
    return {
        "msgtype": "interactive_card",
        "interactive_card": reply.interactive_card or {},
    }
