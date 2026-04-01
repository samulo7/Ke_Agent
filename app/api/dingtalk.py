from __future__ import annotations

import json
import re
from collections.abc import Mapping
from typing import Any

from fastapi import APIRouter, Body, Request
from fastapi.responses import JSONResponse

from app.integrations.dingtalk.reply_builder import build_dingtalk_payload
from app.integrations.dingtalk.stream_parser import parse_stream_event
from app.schemas.dingtalk_chat import AgentReply
from app.services.single_chat import SingleChatService
from app.services.user_context import UserContextResolver

router = APIRouter()
UTF8_JSON_MEDIA_TYPE = "application/json; charset=utf-8"
_REQUEST_ID_PATTERN = re.compile(r"(file-req-[a-zA-Z0-9]+)")


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


def _pick_string_from_candidates(candidates: list[Mapping[str, Any]], keys: tuple[str, ...]) -> str:
    for candidate in candidates:
        value = _pick_string(candidate, keys)
        if value:
            return value
    return ""


def _parse_action_from_button_id(button_id: str) -> tuple[str, str] | None:
    raw = (button_id or "").strip()
    direct_action = _normalize_approval_action_text(raw)
    if direct_action:
        return direct_action, ""
    if "::" not in raw:
        return None
    action, request_id = raw.split("::", 1)
    action = _normalize_approval_action_text(action)
    request_id = request_id.strip()
    if not action or not request_id:
        return None
    return action, request_id


def _extract_file_approval_action(payload: Mapping[str, Any]) -> dict[str, str] | None:
    data = _extract_mapping(payload)
    candidates = _collect_mapping_candidates(data)

    action = ""
    request_id = ""
    for candidate in candidates:
        action_value = _pick_string(
            candidate,
            (
                "approval_action",
                "approvalAction",
                "action",
                "action_name",
                "actionName",
                "button_text",
                "buttonText",
            ),
        )
        normalized_action = _normalize_approval_action_text(action_value)
        if normalized_action:
            action = normalized_action
        text_action_value = _pick_string(candidate, ("text", "content", "title", "name"))
        normalized_text_action = _normalize_explicit_text_approval_action(text_action_value)
        if normalized_text_action:
            action = normalized_text_action
        request_id = request_id or _pick_string(candidate, ("request_id", "requestId"))
        if not request_id:
            request_id = _find_request_id_in_mapping(candidate)
        if action and request_id:
            break

        parsed_card_action, parsed_card_request_id = _extract_action_from_card_private_data(candidate)
        if parsed_card_action:
            action = parsed_card_action
            if parsed_card_request_id:
                request_id = parsed_card_request_id
            if action and request_id:
                break

        button_id = _pick_string(
            candidate,
            ("button_id", "buttonId", "action_id", "actionId", "id", "componentId", "component_id"),
        )
        parsed = _parse_action_from_button_id(button_id)
        if parsed is not None:
            action = parsed[0]
            if parsed[1]:
                request_id = parsed[1]
            if action and request_id:
                break
    if not action:
        return None
    approver_user_id = _pick_string_from_candidates(
        candidates,
        ("approver_user_id", "approverUserId", "sender_id", "senderStaffId", "senderId", "userId", "user_id", "userid"),
    )
    return {
        "action": action,
        "request_id": request_id,
        "approver_user_id": approver_user_id or "unknown",
        "conversation_id": _pick_string_from_candidates(
            candidates,
            (
                "conversation_id",
                "conversationId",
                "cid",
                "openConversationId",
                "open_conversation_id",
                "spaceId",
                "space_id",
                "openSpaceId",
                "open_space_id",
            ),
        ),
        "sender_id": _pick_string_from_candidates(
            candidates,
            ("sender_id", "senderStaffId", "senderId", "staffId", "userId", "user_id", "userid"),
        )
        or approver_user_id
        or "unknown",
    }


def _extract_leave_confirmation_action(payload: Mapping[str, Any]) -> dict[str, str] | None:
    data = _extract_mapping(payload)
    candidates = _collect_mapping_candidates(data)
    leave_callback_context = _is_leave_callback_context(candidates)

    action = ""
    for candidate in candidates:
        action_value = _pick_string(
            candidate,
            (
                "leave_action",
                "leaveAction",
                "action",
                "action_name",
                "actionName",
                "button_text",
                "buttonText",
            ),
        )
        normalized_action = _normalize_leave_action_text(action_value, allow_file_alias=leave_callback_context)
        if normalized_action:
            action = normalized_action
            break

        parsed_private_action = _extract_leave_action_from_card_private_data(
            candidate,
            allow_file_alias=leave_callback_context,
        )
        if parsed_private_action:
            action = parsed_private_action
            break

        button_id = _pick_string(
            candidate,
            ("button_id", "buttonId", "action_id", "actionId", "id", "componentId", "component_id"),
        )
        parsed_action = _parse_leave_action_from_button_id(button_id, allow_file_alias=leave_callback_context)
        if parsed_action:
            action = parsed_action
            break

    if not action:
        return None

    return {
        "action": action,
        "conversation_id": _pick_string_from_candidates(
            candidates,
            (
                "conversation_id",
                "conversationId",
                "cid",
                "openConversationId",
                "open_conversation_id",
                "spaceId",
                "space_id",
                "openSpaceId",
                "open_space_id",
            ),
        ),
        "sender_id": _pick_string_from_candidates(
            candidates,
            ("sender_id", "senderStaffId", "senderId", "staffId", "userId", "user_id", "userid"),
        )
        or "unknown",
    }


def _extract_reimbursement_confirmation_action(payload: Mapping[str, Any]) -> dict[str, str] | None:
    data = _extract_mapping(payload)
    candidates = _collect_mapping_candidates(data)
    reimbursement_context = _is_reimbursement_callback_context(candidates)

    action = ""
    for candidate in candidates:
        action_value = _pick_string(
            candidate,
            (
                "reimbursement_action",
                "reimbursementAction",
                "action",
                "action_name",
                "actionName",
                "button_text",
                "buttonText",
            ),
        )
        normalized_action = _normalize_reimbursement_action_text(action_value, allow_alias=reimbursement_context)
        if normalized_action:
            action = normalized_action
            break

        parsed_private_action = _extract_reimbursement_action_from_card_private_data(
            candidate,
            allow_alias=reimbursement_context,
        )
        if parsed_private_action:
            action = parsed_private_action
            break

        button_id = _pick_string(
            candidate,
            ("button_id", "buttonId", "action_id", "actionId", "id", "componentId", "component_id"),
        )
        parsed_action = _parse_reimbursement_action_from_button_id(button_id, allow_alias=reimbursement_context)
        if parsed_action:
            action = parsed_action
            break

    if not action:
        return None

    return {
        "action": action,
        "conversation_id": _pick_string_from_candidates(
            candidates,
            (
                "conversation_id",
                "conversationId",
                "cid",
                "openConversationId",
                "open_conversation_id",
                "spaceId",
                "space_id",
                "openSpaceId",
                "open_space_id",
            ),
        ),
        "sender_id": _pick_string_from_candidates(
            candidates,
            ("sender_id", "senderStaffId", "senderId", "staffId", "userId", "user_id", "userid"),
        )
        or "unknown",
    }


def _is_leave_callback_context(candidates: list[Mapping[str, Any]]) -> bool:
    workflow_type = _pick_string_from_candidates(candidates, ("workflow_type", "workflowType")).strip().lower()
    if workflow_type == "leave":
        return True
    out_track_id = _pick_string_from_candidates(candidates, ("outTrackId", "out_track_id")).strip().lower()
    return out_track_id.startswith("leave-confirm-")


def _is_reimbursement_callback_context(candidates: list[Mapping[str, Any]]) -> bool:
    workflow_type = _pick_string_from_candidates(candidates, ("workflow_type", "workflowType")).strip().lower()
    if workflow_type == "reimbursement":
        return True
    out_track_id = _pick_string_from_candidates(candidates, ("outTrackId", "out_track_id")).strip().lower()
    return out_track_id.startswith("reimbursement-confirm-")


def _normalize_approval_action_text(raw: str) -> str:
    normalized = "".join((raw or "").strip().lower().split())
    if normalized in {"confirm_request", "confirm", "确认申请", "确认", "提交申请", "发起申请"}:
        return "confirm_request"
    if normalized in {"cancel_request", "cancel", "取消", "不用了", "算了", "先不申请"}:
        return "cancel_request"
    if normalized in {"approve", "approved", "agree", "同意", "通过", "pass"}:
        return "approve"
    if normalized in {"reject", "rejected", "refuse", "拒绝", "驳回"}:
        return "reject"
    return ""


def _normalize_reimbursement_action_text(raw: str, *, allow_alias: bool = False) -> str:
    normalized = "".join((raw or "").strip().lower().split())
    if normalized in {"reimbursement_confirm_submit", "reimbursementconfirmsubmit"}:
        return "reimbursement_confirm_submit"
    if normalized in {"reimbursement_cancel_submit", "reimbursementcancelsubmit"}:
        return "reimbursement_cancel_submit"
    if normalized in {"确认提交报销", "确认报销", "提交报销"}:
        return "reimbursement_confirm_submit"
    if normalized in {"取消报销"}:
        return "reimbursement_cancel_submit"
    if allow_alias and normalized == "confirm_request":
        return "reimbursement_confirm_submit"
    if allow_alias and normalized == "cancel_request":
        return "reimbursement_cancel_submit"
    return ""


def _normalize_leave_action_text(raw: str, *, allow_file_alias: bool = False) -> str:
    normalized = "".join((raw or "").strip().lower().split())
    if normalized in {"leave_confirm_submit", "leaveconfirmsubmit"}:
        return "leave_confirm_submit"
    if normalized in {"leave_cancel_submit", "leavecancelsubmit"}:
        return "leave_cancel_submit"
    if normalized in {"确认提交请假", "确认请假", "提交请假"}:
        return "leave_confirm_submit"
    if normalized in {"取消请假"}:
        return "leave_cancel_submit"
    if allow_file_alias and normalized == "confirm_request":
        return "leave_confirm_submit"
    if allow_file_alias and normalized == "cancel_request":
        return "leave_cancel_submit"
    return ""


def _normalize_explicit_text_approval_action(raw: str) -> str:
    normalized = "".join((raw or "").strip().lower().split())
    if normalized in {"确认申请", "提交申请", "发起申请"}:
        return "confirm_request"
    if normalized in {"cancel_request", "cancel", "取消申请", "不用申请", "放弃申请", "先不申请"}:
        return "cancel_request"
    return ""


def _parse_leave_action_from_button_id(button_id: str, *, allow_file_alias: bool) -> str:
    raw = (button_id or "").strip()
    if not raw:
        return ""
    head = raw.split("::", 1)[0]
    return _normalize_leave_action_text(head, allow_file_alias=allow_file_alias)


def _parse_reimbursement_action_from_button_id(button_id: str, *, allow_alias: bool) -> str:
    raw = (button_id or "").strip()
    if not raw:
        return ""
    head = raw.split("::", 1)[0]
    return _normalize_reimbursement_action_text(head, allow_alias=allow_alias)


def _extract_action_from_card_private_data(candidate: Mapping[str, Any]) -> tuple[str, str]:
    private_data = _extract_card_private_data_mapping(candidate)
    if private_data is None:
        return "", ""

    request_id = ""
    params = private_data.get("params")
    if isinstance(params, Mapping):
        request_id = _pick_string(params, ("request_id", "requestId", "id"))
        if not request_id:
            request_id = _find_request_id_in_mapping(params)

    action_ids = private_data.get("actionIds")
    if isinstance(action_ids, list):
        for item in action_ids:
            if not isinstance(item, str) or not item.strip():
                continue
            parsed = _parse_action_from_button_id(item)
            if parsed is not None:
                action, parsed_request_id = parsed
                return action, parsed_request_id or request_id
            normalized_action = _normalize_approval_action_text(item)
            if normalized_action:
                return normalized_action, request_id

    action_value = _pick_string(private_data, ("action", "action_name", "actionName"))
    normalized_action = _normalize_approval_action_text(action_value)
    if normalized_action:
        return normalized_action, request_id
    return "", ""


def _extract_leave_action_from_card_private_data(candidate: Mapping[str, Any], *, allow_file_alias: bool) -> str:
    private_data = _extract_card_private_data_mapping(candidate)
    if private_data is None:
        return ""

    action_ids = private_data.get("actionIds")
    if isinstance(action_ids, list):
        for item in action_ids:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized_action = _parse_leave_action_from_button_id(item, allow_file_alias=allow_file_alias)
            if normalized_action:
                return normalized_action

    action_value = _pick_string(private_data, ("action", "action_name", "actionName"))
    return _normalize_leave_action_text(action_value, allow_file_alias=allow_file_alias)


def _extract_reimbursement_action_from_card_private_data(candidate: Mapping[str, Any], *, allow_alias: bool) -> str:
    private_data = _extract_card_private_data_mapping(candidate)
    if private_data is None:
        return ""

    action_ids = private_data.get("actionIds")
    if isinstance(action_ids, list):
        for item in action_ids:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized_action = _parse_reimbursement_action_from_button_id(item, allow_alias=allow_alias)
            if normalized_action:
                return normalized_action

    action_value = _pick_string(private_data, ("action", "action_name", "actionName"))
    return _normalize_reimbursement_action_text(action_value, allow_alias=allow_alias)


def _extract_card_private_data_mapping(candidate: Mapping[str, Any]) -> Mapping[str, Any] | None:
    direct_private = candidate.get("cardPrivateData")
    if isinstance(direct_private, Mapping):
        return direct_private

    if "actionIds" in candidate and isinstance(candidate.get("actionIds"), list):
        return candidate

    content_value = candidate.get("content")
    if isinstance(content_value, Mapping):
        nested_private = content_value.get("cardPrivateData")
        if isinstance(nested_private, Mapping):
            return nested_private
    return None


def _find_request_id_in_mapping(payload: Mapping[str, Any]) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        return ""
    match = _REQUEST_ID_PATTERN.search(text)
    return match.group(1) if match else ""


def _is_plain_text_single_chat(candidates: list[Mapping[str, Any]]) -> bool:
    raw_conversation_type = _pick_string_from_candidates(candidates, ("conversation_type", "conversationType")).lower()
    conversation_type = (
        "single"
        if raw_conversation_type in {"single", "single_chat", "1", "1v1", "private"}
        else raw_conversation_type
    )
    message_type = _pick_string_from_candidates(candidates, ("message_type", "messageType", "msgtype")).lower()
    return conversation_type == "single" and message_type == "text"


def _collect_mapping_candidates(root: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    result: list[Mapping[str, Any]] = []
    stack: list[Mapping[str, Any]] = [root]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        result.append(current)
        for value in current.values():
            if isinstance(value, Mapping):
                stack.append(value)
                continue
            if isinstance(value, list):
                for item in value:
                    if isinstance(item, Mapping):
                        stack.append(item)
                continue
            if isinstance(value, str):
                stack.extend(_parse_json_mappings_from_string(value))
    return result


def _parse_json_mappings_from_string(value: str) -> list[Mapping[str, Any]]:
    raw = (value or "").strip()
    if not raw.startswith("{") and not raw.startswith("["):
        return []
    try:
        parsed = json.loads(raw)
    except Exception:
        return []
    if isinstance(parsed, Mapping):
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, Mapping)]
    return []


def _serialize_replies(*, replies: list[Any]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    serialized = [item.to_dict() for item in replies]
    payloads = [build_dingtalk_payload(item) for item in replies]
    return serialized, payloads


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
    request.state.llm_trace = {}

    single_chat_service: SingleChatService = request.app.state.single_chat_service
    plain_text_single_chat = _is_plain_text_single_chat(_collect_mapping_candidates(_extract_mapping(payload)))
    reimbursement_action = _extract_reimbursement_confirmation_action(payload)
    if reimbursement_action is not None:
        reimbursement_outcome = single_chat_service.handle_reimbursement_confirmation_action_by_session(
            action=reimbursement_action["action"],
            conversation_id=reimbursement_action.get("conversation_id", ""),
            sender_id=reimbursement_action.get("sender_id", ""),
        )
        request.state.intent = "reimbursement"
        replies = list(reimbursement_outcome.all_replies())
        serialized_replies, dingtalk_payloads = _serialize_replies(replies=replies)
        primary_reply = (
            serialized_replies[0] if serialized_replies else {"channel": "text", "text": "", "interactive_card": None}
        )
        primary_payload = dingtalk_payloads[0] if dingtalk_payloads else {"msgtype": "text", "text": {"content": ""}}
        reason = str(reimbursement_outcome.reason or "")
        if reason == "reimbursement_travel_submitted":
            reimbursement_status = "submitted"
        elif reason == "reimbursement_travel_cancelled":
            reimbursement_status = "cancelled"
        elif reason in {"reimbursement_travel_not_found", "reimbursement_travel_confirmation_expired"}:
            reimbursement_status = "not_found"
        elif reason == "reimbursement_travel_handoff_fallback":
            reimbursement_status = "fallback"
        else:
            reimbursement_status = "pending"
        return JSONResponse(
            status_code=200,
            media_type=UTF8_JSON_MEDIA_TYPE,
            content={
                "ack": "ok",
                "trace_id": trace_id,
                "handled": reimbursement_outcome.handled,
                "reason": reimbursement_outcome.reason,
                "intent": "reimbursement",
                "reimbursement_action": reimbursement_action["action"],
                "reimbursement_status": reimbursement_status,
                "reply": primary_reply,
                "replies": serialized_replies,
                "dingtalk_payload": primary_payload,
                "dingtalk_payloads": dingtalk_payloads,
                "source_ids": [],
                "permission_decision": "allow",
                "knowledge_version": "",
                "answered_at": "",
                "citations": [],
                "llm_trace": {},
            },
        )

    leave_action = _extract_leave_confirmation_action(payload)
    if leave_action is not None:
        leave_outcome = single_chat_service.handle_leave_confirmation_action_by_session(
            action=leave_action["action"],
            conversation_id=leave_action.get("conversation_id", ""),
            sender_id=leave_action.get("sender_id", ""),
        )
        request.state.intent = "leave"
        replies = list(leave_outcome.all_replies())
        serialized_replies, dingtalk_payloads = _serialize_replies(replies=replies)
        primary_reply = (
            serialized_replies[0] if serialized_replies else {"channel": "text", "text": "", "interactive_card": None}
        )
        primary_payload = dingtalk_payloads[0] if dingtalk_payloads else {"msgtype": "text", "text": {"content": ""}}
        reason = str(leave_outcome.reason or "")
        if reason == "leave_workflow_submitted":
            leave_status = "submitted"
        elif reason == "leave_workflow_cancelled":
            leave_status = "cancelled"
        elif reason in {"leave_workflow_not_found", "leave_workflow_confirmation_expired"}:
            leave_status = "not_found"
        elif reason == "leave_workflow_handoff_fallback":
            leave_status = "fallback"
        elif reason == "leave_workflow_handoff":
            leave_status = "handoff"
        else:
            leave_status = "pending"
        return JSONResponse(
            status_code=200,
            media_type=UTF8_JSON_MEDIA_TYPE,
            content={
                "ack": "ok",
                "trace_id": trace_id,
                "handled": leave_outcome.handled,
                "reason": leave_outcome.reason,
                "intent": "leave",
                "leave_action": leave_action["action"],
                "leave_status": leave_status,
                "reply": primary_reply,
                "replies": serialized_replies,
                "dingtalk_payload": primary_payload,
                "dingtalk_payloads": dingtalk_payloads,
                "source_ids": [],
                "permission_decision": "allow",
                "knowledge_version": "",
                "answered_at": "",
                "citations": [],
                "llm_trace": {},
            },
        )

    approval_action = _extract_file_approval_action(payload)
    if approval_action is not None:
        request_id = approval_action.get("request_id", "")
        if request_id:
            approval_outcome = single_chat_service.handle_file_approval_action(
                request_id=request_id,
                action=approval_action["action"],
                approver_user_id=approval_action["approver_user_id"],
            )
        else:
            approval_outcome = single_chat_service.handle_file_approval_action_by_session(
                action=approval_action["action"],
                approver_user_id=approval_action["approver_user_id"],
                conversation_id=approval_action.get("conversation_id", ""),
                sender_id=approval_action.get("sender_id", ""),
            )
        if (
            not request_id
            and plain_text_single_chat
            and approval_outcome.reason == "file_approval_not_found"
        ):
            approval_action = None
        else:
            request.state.intent = "file_request"
            replies = list(approval_outcome.replies)
            if not replies and approval_outcome.reason == "file_approval_not_found":
                replies = [
                    AgentReply(
                        channel="text",
                        text="已收到按钮点击，但未定位到待处理申请。请重新发送“我要采购合同文件”后再确认申请。",
                    )
                ]
            serialized_replies, dingtalk_payloads = _serialize_replies(replies=replies)
            primary_reply = (
                serialized_replies[0] if serialized_replies else {"channel": "text", "text": "", "interactive_card": None}
            )
            primary_payload = dingtalk_payloads[0] if dingtalk_payloads else {"msgtype": "text", "text": {"content": ""}}
            return JSONResponse(
                status_code=200,
                media_type=UTF8_JSON_MEDIA_TYPE,
                content={
                    "ack": "ok",
                    "trace_id": trace_id,
                    "handled": approval_outcome.handled,
                    "reason": approval_outcome.reason,
                    "intent": "file_request",
                    "request_id": approval_outcome.request_id,
                    "approval_action": approval_outcome.action,
                    "approval_status": approval_outcome.status,
                    "reply": primary_reply,
                    "replies": serialized_replies,
                    "dingtalk_payload": primary_payload,
                    "dingtalk_payloads": dingtalk_payloads,
                    "source_ids": [],
                    "permission_decision": "allow",
                    "knowledge_version": "",
                    "answered_at": "",
                    "citations": [],
                    "llm_trace": {},
                },
            )

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

    outcome = single_chat_service.handle(incoming_message, user_context=user_context)
    request.state.intent = outcome.intent
    request.state.source_ids = list(outcome.source_ids)
    request.state.permission_decision = outcome.permission_decision
    request.state.knowledge_version = outcome.knowledge_version
    request.state.answered_at = outcome.answered_at
    request.state.llm_trace = dict(outcome.llm_trace)
    if outcome.reason == "system_fallback":
        request.state.error_category = "dependency_error"
    replies = list(outcome.all_replies())
    serialized_replies, dingtalk_payloads = _serialize_replies(replies=replies)
    primary_reply = serialized_replies[0] if serialized_replies else {"channel": "text", "text": "", "interactive_card": None}
    primary_payload = dingtalk_payloads[0] if dingtalk_payloads else {"msgtype": "text", "text": {"content": ""}}
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
            "llm_trace": dict(outcome.llm_trace),
            "user_context": user_context.to_dict(),
            "reply": primary_reply,
            "replies": serialized_replies,
            "dingtalk_payload": primary_payload,
            "dingtalk_payloads": dingtalk_payloads,
        },
    )
