from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app.integrations.dingtalk.reply_builder import build_dingtalk_payload
from app.integrations.dingtalk.stream_parser import parse_stream_event
from app.services.single_chat import SingleChatService
from app.services.user_context import UserContextResolver

router = APIRouter()
UTF8_JSON_MEDIA_TYPE = "application/json; charset=utf-8"


@router.post("/dingtalk/stream/events")
async def receive_dingtalk_stream_event(
    request: Request,
    payload: dict[str, Any] = Body(...),
) -> JSONResponse:
    trace_id = getattr(request.state, "trace_id", "")
    request.state.user_id = "unknown"
    request.state.dept_id = "unknown"
    request.state.intent = "other"
    request.state.identity_source = "event_fallback"
    request.state.is_degraded = True
    request.state.source_ids = []
    request.state.permission_decision = "allow"
    request.state.knowledge_version = ""
    request.state.answered_at = ""

    try:
        incoming_message = parse_stream_event(payload)
    except ValueError as exc:
        request.state.error_category = "client_error"
        return JSONResponse(
            status_code=400,
            media_type=UTF8_JSON_MEDIA_TYPE,
            content={
                "ack": "invalid",
                "trace_id": trace_id,
                "error": str(exc),
            },
        )

    user_context_resolver: UserContextResolver = request.app.state.user_context_resolver
    user_context = user_context_resolver.resolve(incoming_message)
    request.state.user_id = user_context.user_id
    request.state.dept_id = user_context.dept_id
    request.state.identity_source = user_context.identity_source
    request.state.is_degraded = user_context.is_degraded
    request.state.user_context = user_context.to_dict()

    single_chat_service: SingleChatService = request.app.state.single_chat_service
    outcome = single_chat_service.handle(incoming_message)
    request.state.intent = outcome.intent
    request.state.source_ids = list(outcome.source_ids)
    request.state.permission_decision = outcome.permission_decision
    request.state.knowledge_version = outcome.knowledge_version
    request.state.answered_at = outcome.answered_at
    return JSONResponse(
        status_code=200,
        media_type=UTF8_JSON_MEDIA_TYPE,
        content={
            "ack": "ok",
            "trace_id": trace_id,
            "event_id": incoming_message.event_id,
            "conversation_id": incoming_message.conversation_id,
            "sender_id": incoming_message.sender_id,
            "handled": outcome.handled,
            "reason": outcome.reason,
            "intent": outcome.intent,
            "source_ids": list(outcome.source_ids),
            "permission_decision": outcome.permission_decision,
            "knowledge_version": outcome.knowledge_version,
            "answered_at": outcome.answered_at,
            "citations": [dict(item) for item in outcome.citations],
            "user_context": user_context.to_dict(),
            "reply": outcome.reply.to_dict(),
            "dingtalk_payload": build_dingtalk_payload(outcome.reply),
        },
    )
