from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from app.schemas.dingtalk_chat import ConversationType, IncomingChatMessage


def _extract_mapping(payload: Mapping[str, Any]) -> Mapping[str, Any]:
    nested = payload.get("data")
    if isinstance(nested, Mapping):
        return nested
    return payload


def _pick_string(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _normalize_conversation_type(raw_type: str) -> ConversationType:
    normalized = raw_type.strip().lower()
    if normalized in {"single", "single_chat", "1", "1v1", "private"}:
        return "single"
    if normalized in {"group", "group_chat", "2"}:
        return "group"
    return "unknown"


def _extract_text(payload: Mapping[str, Any]) -> str:
    direct = payload.get("text")
    if isinstance(direct, str):
        return direct

    content = payload.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, Mapping):
        nested_text = content.get("text")
        if isinstance(nested_text, str):
            return nested_text

    text_obj = payload.get("text")
    if isinstance(text_obj, Mapping):
        nested_content = text_obj.get("content")
        if isinstance(nested_content, str):
            return nested_content

    return ""


def parse_stream_event(payload: Mapping[str, Any]) -> IncomingChatMessage:
    event = _extract_mapping(payload)
    sender_id = _pick_string(event, ("sender_id", "senderStaffId", "senderId", "staffId", "userid"))
    if not sender_id:
        raise ValueError("sender_id is required for single chat handling")

    conversation_type_raw = _pick_string(event, ("conversation_type", "conversationType"))
    conversation_type = _normalize_conversation_type(conversation_type_raw)
    if conversation_type == "unknown":
        raise ValueError("conversation_type is required and must be single/group")

    message_type = _pick_string(event, ("message_type", "messageType", "msgtype")) or "text"

    return IncomingChatMessage(
        event_id=_pick_string(event, ("event_id", "eventId", "id")) or "unknown_event",
        conversation_id=_pick_string(event, ("conversation_id", "conversationId", "cid")) or "unknown_conversation",
        conversation_type=conversation_type,
        sender_id=sender_id,
        message_type=message_type.lower(),
        text=_extract_text(event),
    )
