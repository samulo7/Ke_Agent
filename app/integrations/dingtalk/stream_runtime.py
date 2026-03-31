from __future__ import annotations

import asyncio
import importlib
import json
import logging
import os
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter, time
from typing import Any, Awaitable, Callable, Protocol
from uuid import uuid4

import requests

from app.core.trace_context import reset_trace_id, set_trace_id
from app.integrations.dingtalk.stream_parser import parse_stream_event
from app.schemas.dingtalk_chat import AgentReply
from app.services.file_request import FileApprovalNotifyResult, FileApprovalRequest, FileRequestService
from app.services.single_chat import SingleChatService
from app.services.user_context import UserContextResolver

DEFAULT_STREAM_ENDPOINT = "https://api.dingtalk.com/v1.0/gateway/connections/open"
DEFAULT_OPENAPI_ENDPOINT = "https://api.dingtalk.com"
DEFAULT_CHATBOT_TOPIC = "/v1.0/im/bot/messages/get"
OBS_LOGGER_NAME = "keagent.observability"
_REQUEST_ID_PATTERN = re.compile(r"(file-req-[a-zA-Z0-9]+)")


class StreamRuntimeError(RuntimeError):
    """Raised when stream runtime bootstrap/configuration fails."""


@dataclass(frozen=True)
class DingTalkStreamCredentials:
    client_id: str
    client_secret: str
    agent_id: str
    stream_endpoint: str = DEFAULT_STREAM_ENDPOINT


@dataclass(frozen=True)
class StreamingCardSettings:
    enabled: bool
    template_id: str
    content_key: str
    title_key: str
    title: str
    chunk_chars: int
    interval_seconds: float
    min_chars: int


@dataclass(frozen=True)
class HRApprovalCardSettings:
    enabled: bool
    approver_user_id: str
    template_id: str
    openapi_endpoint: str


class ReplySender(Protocol):
    def send_text(self, text: str) -> None: ...

    def send_interactive_card(self, card_payload: Mapping[str, Any]) -> None: ...


class _SdkLoggerAdapter:
    """Normalize malformed third-party logger calls into safe plain strings."""

    def __init__(self, delegate: logging.Logger) -> None:
        self._delegate = delegate

    def __getattr__(self, name: str) -> Any:
        return getattr(self._delegate, name)

    def debug(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._log("debug", message, *args, **kwargs)

    def info(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._log("info", message, *args, **kwargs)

    def warning(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._log("warning", message, *args, **kwargs)

    def error(self, message: Any, *args: Any, **kwargs: Any) -> None:
        self._log("error", message, *args, **kwargs)

    def exception(self, message: Any, *args: Any, **kwargs: Any) -> None:
        # SDK occasionally calls `logger.exception("text", exc)` without `%s`.
        # Force safe formatting so the real exception remains visible.
        kwargs.setdefault("exc_info", True)
        self._log("error", message, *args, **kwargs)

    def _log(self, level_name: str, message: Any, *args: Any, **kwargs: Any) -> None:
        method = getattr(self._delegate, level_name)
        method(self._coerce_message(message, args), **kwargs)

    @staticmethod
    def _coerce_message(message: Any, args: tuple[Any, ...]) -> str:
        text = str(message)
        if not args:
            return text
        try:
            return text % args
        except Exception:
            serialized = ", ".join(repr(item) for item in args)
            return f"{text} | args=[{serialized}]"


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def load_stream_credentials(raw_env: Mapping[str, str]) -> DingTalkStreamCredentials:
    client_id = (raw_env.get("DINGTALK_CLIENT_ID") or "").strip()
    client_secret = (raw_env.get("DINGTALK_CLIENT_SECRET") or "").strip()
    agent_id = (raw_env.get("DINGTALK_AGENT_ID") or "").strip()
    stream_endpoint = (raw_env.get("DINGTALK_STREAM_ENDPOINT") or DEFAULT_STREAM_ENDPOINT).strip()

    missing: list[str] = []
    if _is_blank(client_id):
        missing.append("DINGTALK_CLIENT_ID")
    if _is_blank(client_secret):
        missing.append("DINGTALK_CLIENT_SECRET")
    if _is_blank(agent_id):
        missing.append("DINGTALK_AGENT_ID")
    if missing:
        missing_text = ", ".join(missing)
        raise StreamRuntimeError(f"Missing required DingTalk keys: {missing_text}")

    return DingTalkStreamCredentials(
        client_id=client_id,
        client_secret=client_secret,
        agent_id=agent_id,
        stream_endpoint=stream_endpoint or DEFAULT_STREAM_ENDPOINT,
    )


def _parse_bool_env(value: str | None, default: bool) -> bool:
    if value is None:
        return default
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "y", "on"}:
        return True
    if normalized in {"0", "false", "no", "n", "off", ""}:
        return False
    return default


def _parse_positive_int_env(value: str | None, default: int, *, minimum: int = 1) -> int:
    if value is None:
        return default
    try:
        parsed = int(value.strip())
    except (TypeError, ValueError):
        return default
    return parsed if parsed >= minimum else default


def load_streaming_card_settings(raw_env: Mapping[str, str] | None = None) -> StreamingCardSettings:
    env = raw_env if raw_env is not None else os.environ
    enabled = _parse_bool_env(env.get("DINGTALK_AI_CARD_STREAMING_ENABLED"), False)
    template_id = str(env.get("DINGTALK_AI_CARD_TEMPLATE_ID") or "").strip()
    content_key = str(env.get("DINGTALK_AI_CARD_CONTENT_KEY") or "content").strip() or "content"
    title_key = str(env.get("DINGTALK_AI_CARD_TITLE_KEY") or "").strip()
    title = str(env.get("DINGTALK_AI_CARD_TITLE") or "企业 Agent").strip() or "企业 Agent"
    chunk_chars = _parse_positive_int_env(env.get("DINGTALK_AI_CARD_CHUNK_CHARS"), 20, minimum=1)
    interval_ms = _parse_positive_int_env(env.get("DINGTALK_AI_CARD_INTERVAL_MS"), 120, minimum=0)
    min_chars = _parse_positive_int_env(env.get("DINGTALK_AI_CARD_MIN_CHARS"), 80, minimum=1)
    return StreamingCardSettings(
        enabled=enabled,
        template_id=template_id,
        content_key=content_key,
        title_key=title_key,
        title=title,
        chunk_chars=chunk_chars,
        interval_seconds=interval_ms / 1000,
        min_chars=min_chars,
    )


def load_hr_approval_card_settings(raw_env: Mapping[str, str] | None = None) -> HRApprovalCardSettings:
    env = raw_env if raw_env is not None else os.environ
    approver_user_id = str(env.get("DINGTALK_HR_APPROVER_USER_ID") or "").strip()
    template_id = str(env.get("DINGTALK_HR_CARD_TEMPLATE_ID") or "").strip()
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    normalized_endpoint = openapi_endpoint.rstrip("/") or DEFAULT_OPENAPI_ENDPOINT
    return HRApprovalCardSettings(
        enabled=bool(approver_user_id and template_id),
        approver_user_id=approver_user_id,
        template_id=template_id,
        openapi_endpoint=normalized_endpoint,
    )


def _extract_card_text_lines(card_payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    field_label_map = {
        "applicant_name": "申请人姓名",
        "department": "所属部门",
        "requested_item": "申请资料名称",
        "request_purpose": "申请用途",
        "expected_use_time": "期望使用时间",
        "suggested_approver": "建议审批对象",
        "leave_type": "请假类型",
        "leave_time": "请假时间",
        "leave_reason": "请假事由",
        "entry_point": "办理入口",
        "required_materials": "准备材料",
    }
    title = str(card_payload.get("title") or "助手回复").strip() or "助手回复"
    lines: list[str] = []

    summary = card_payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(summary.strip())

    primary_action = card_payload.get("primary_action")
    if isinstance(primary_action, str) and primary_action.strip():
        lines.append(f"建议动作：{primary_action.strip()}")

    context = card_payload.get("context")
    if isinstance(context, str) and context.strip() and not lines:
        lines.append(context.strip())

    draft_fields = card_payload.get("draft_fields")
    if isinstance(draft_fields, Mapping):
        status_map = card_payload.get("field_status")
        field_status = status_map if isinstance(status_map, Mapping) else {}
        for key, value in draft_fields.items():
            label = field_label_map.get(str(key), str(key))
            status = str(field_status.get(key) or "").strip().lower()
            text_value = str(value).strip()
            if status == "missing":
                lines.append(f"【待补充】{label}: {text_value or '____'}")
            elif status == "needs_detail":
                lines.append(f"【需细化】{label}: {text_value or '____'}")
            else:
                lines.append(f"{label}: {text_value}")

    for key in ("entry_point", "required_materials"):
        value = card_payload.get(key)
        if isinstance(value, str) and value.strip():
            label = field_label_map.get(key, key)
            lines.append(f"{label}：{value.strip()}")

    process_path = card_payload.get("process_path")
    if isinstance(process_path, list):
        normalized_path = [str(item).strip() for item in process_path if str(item).strip()]
        if normalized_path:
            lines.append(f"流程路径：{' > '.join(normalized_path)}")

    question = card_payload.get("question")
    if isinstance(question, str) and question.strip() and not lines:
        lines.append(question.strip())

    steps = card_payload.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if isinstance(step, str) and step.strip():
                lines.append(f"{index}. {step.strip()}")

    actions = card_payload.get("actions")
    if not isinstance(actions, list):
        fallback_actions = card_payload.get("btns")
        actions = fallback_actions if isinstance(fallback_actions, list) else actions
    render_action_hint = bool(card_payload.get("render_action_hint", True))
    if render_action_hint and isinstance(actions, list) and actions:
        action_labels: list[str] = []
        for item in actions:
            if isinstance(item, Mapping):
                label = str(item.get("label") or "").strip()
                if label:
                    action_labels.append(label)
                    continue
            text = str(item).strip()
            if text:
                action_labels.append(text)
        if action_labels:
            lines.append(f"可操作：{' / '.join(action_labels)}")

    next_action = card_payload.get("next_action")
    if isinstance(next_action, str) and next_action.strip():
        lines.append(f"下一步：{next_action.strip()}")

    note = card_payload.get("note")
    if isinstance(note, str) and note.strip():
        lines.append(f"补充说明：{note.strip()}")

    if not lines:
        lines.append("未提供结构化卡片内容。")

    return title, lines


def _extract_card_actions(card_payload: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    raw_actions = card_payload.get("actions")
    if not isinstance(raw_actions, list):
        fallback_actions = card_payload.get("btns")
        raw_actions = fallback_actions if isinstance(fallback_actions, list) else []

    normalized_actions: list[Mapping[str, Any]] = []
    for item in raw_actions:
        if isinstance(item, Mapping):
            normalized_actions.append(item)
    return normalized_actions


def _normalize_button_status(value: str, *, index: int) -> str:
    normalized = value.strip().lower()
    if normalized in {"primary", "normal", "warning"}:
        return normalized
    return "primary" if index == 0 else "normal"


def _build_button_id(button: Mapping[str, Any]) -> str:
    for key in ("id", "buttonId", "button_id", "actionId", "action_id"):
        value = button.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()

    action = str(button.get("action") or button.get("approval_action") or "").strip()
    request_id = str(button.get("request_id") or "").strip()
    if action and request_id:
        return f"{action}::{request_id}"
    if action:
        return action
    return ""


def _build_standard_card_action_buttons(card_payload: Mapping[str, Any]) -> list[dict[str, Any]]:
    actions = _extract_card_actions(card_payload)
    buttons: list[dict[str, Any]] = []
    for index, action in enumerate(actions):
        label = str(action.get("label") or action.get("text") or "").strip()
        if not label:
            continue
        button_id = _build_button_id(action)
        status = _normalize_button_status(str(action.get("status") or ""), index=index)
        button: dict[str, Any] = {
            "type": "button",
            "label": {
                "type": "text",
                "text": label,
                "id": f"text_{uuid4().hex[:12]}",
            },
            "actionType": "request",
            "status": status,
            "id": button_id or f"button_{uuid4().hex[:12]}",
        }
        buttons.append(button)
    return buttons


def _build_standard_interactive_card_data(
    *,
    title: str,
    lines: list[str],
    action_buttons: list[dict[str, Any]],
) -> dict[str, Any]:
    contents: list[dict[str, Any]] = []
    for line in lines:
        contents.append(
            {
                "type": "markdown",
                "text": line,
                "id": f"text_{uuid4().hex[:12]}",
            }
        )
        contents.append(
            {
                "type": "divider",
                "id": f"divider_{uuid4().hex[:12]}",
            }
        )

    if action_buttons:
        contents.append(
            {
                "type": "action",
                "actions": action_buttons,
                "id": f"action_{uuid4().hex[:12]}",
            }
        )

    return {
        "config": {
            "autoLayout": True,
            "enableForward": True,
        },
        "header": {
            "title": {
                "type": "text",
                "text": title or "Agent Reply Card",
            },
            "logo": "",
        },
        "contents": contents or [{"type": "markdown", "text": "No content", "id": f"text_{uuid4().hex[:12]}"}],
    }


def _to_string_card_param_map_value(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return str(value)
    try:
        return json.dumps(value, ensure_ascii=False)
    except Exception:
        return str(value)


def _build_action_card_param_map(
    *,
    card_payload: Mapping[str, Any],
    title: str,
    lines: list[str],
) -> dict[str, str]:
    card_type = str(card_payload.get("card_type") or "").strip()
    workflow_type = "leave" if card_type == "leave_request_ready" else "file_request"
    summary = str(card_payload.get("summary") or "").strip()
    if not summary:
        summary = "\n".join(line for line in lines if line).strip()

    card_param_map: dict[str, Any] = {
        "summary": summary,
        "workflow_type": workflow_type,
        # Explicit unlocked state for first delivery to make template visibility deterministic.
        "actions_locked": "false",
        "approval_status": "awaiting_requester_confirmation",
        "submitted": "false",
    }
    normalized_title = str(card_payload.get("title") or title or "").strip()
    if normalized_title:
        card_param_map["title"] = normalized_title
    return {key: _to_string_card_param_map_value(value) for key, value in card_param_map.items()}


def _build_hr_approval_card_param_map(
    *,
    request: FileApprovalRequest,
    card_payload: Mapping[str, Any],
) -> dict[str, str]:
    variant_label = "扫描件" if request.variant == "scan" else "纸质版"
    if request.fallback_from_scan_to_paper:
        variant_label = "纸质版（当前未找到扫描件）"

    summary = str(card_payload.get("summary") or "").strip()
    if not summary:
        summary = f"{request.requester_display_name} 申请查阅文件，请审批。"

    param_map: dict[str, Any] = {
        "title": str(card_payload.get("title") or "文件发放审批").strip() or "文件发放审批",
        "summary": summary,
        "request_id": request.request_id,
        "requester_name": request.requester_display_name,
        "requester_user_id": request.requester_sender_id,
        "requester_conversation_id": request.requester_conversation_id,
        "file_title": request.asset.title,
        "request_variant": variant_label,
        "request_query": request.query_text,
        "approval_status": "pending",
    }
    return {key: _to_string_card_param_map_value(value) for key, value in param_map.items()}


class _StreamHRApprovalNotifier:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: HRApprovalCardSettings,
        timeout_seconds: float = 8.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._logger = logger or logging.getLogger(OBS_LOGGER_NAME)
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def notify(self, *, request: FileApprovalRequest, card_payload: dict[str, object]) -> FileApprovalNotifyResult:
        if not self._settings.enabled:
            return FileApprovalNotifyResult(success=False, reason="hr_notifier_disabled")
        if not self._client_id or not self._client_secret:
            return FileApprovalNotifyResult(success=False, reason="missing_client_credentials")
        if not self._settings.approver_user_id or not self._settings.template_id:
            return FileApprovalNotifyResult(success=False, reason="missing_hr_template_config")

        try:
            access_token = self._get_access_token()
            card_param_map = _build_hr_approval_card_param_map(
                request=request,
                card_payload=card_payload,
            )
            body: dict[str, Any] = {
                "userId": self._settings.approver_user_id,
                "userIdType": 1,
                "cardTemplateId": self._settings.template_id,
                "outTrackId": f"hr-approval-{request.request_id}",
                "callbackType": "STREAM",
                "cardData": {"cardParamMap": card_param_map},
                "imRobotOpenSpaceModel": {"supportForward": True},
                "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
                "openSpaceId": f"dtv1.card//im_robot.{self._settings.approver_user_id}",
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "*/*",
                "x-acs-dingtalk-access-token": access_token,
            }
            url = f"{self._settings.openapi_endpoint}/v1.0/card/instances/createAndDeliver"
            response = requests.post(url, headers=headers, json=body, timeout=self._timeout_seconds)
            response_text = response.text
            if response.status_code >= 400:
                self._logger.warning(
                    "hr approval createAndDeliver failed: status=%s body=%s",
                    response.status_code,
                    response_text[:500],
                )
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_http_error")

            response_payload: Any
            try:
                response_payload = response.json()
            except Exception:
                self._logger.warning("hr approval createAndDeliver returned non-JSON response")
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_non_json")

            if _card_delivery_response_has_failure(response_payload):
                self._logger.warning(
                    "hr approval createAndDeliver rejected: payload=%s",
                    _truncate_payload_for_log(response_payload),
                )
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_failed")

            self._logger.info(
                "file.approval.notify.stream",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.stream",
                        "event": "file_approval_card_delivered",
                        "request_id": request.request_id,
                        "requester_user_id": request.requester_sender_id,
                        "approver_user_id": self._settings.approver_user_id,
                        "card_template_id": self._settings.template_id,
                        "card_type": card_payload.get("card_type", ""),
                    }
                },
            )
            return FileApprovalNotifyResult(success=True, reason="delivered")
        except Exception:
            self._logger.exception("hr approval createAndDeliver exception")
            return FileApprovalNotifyResult(success=False, reason="delivery_exception")

    def _get_access_token(self) -> str:
        now = time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        url = f"{self._settings.openapi_endpoint}/v1.0/oauth2/accessToken"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"appKey": self._client_id, "appSecret": self._client_secret},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("result") if isinstance(payload.get("result"), Mapping) else payload.get("data")
        token_source = data if isinstance(data, Mapping) else payload
        access_token = str(token_source.get("accessToken") or payload.get("accessToken") or "").strip()
        if not access_token:
            raise RuntimeError("OpenAPI access token is empty")
        expire_in = int(token_source.get("expireIn") or payload.get("expireIn") or 7200)
        self._access_token = access_token
        self._access_token_expires_at = time() + max(expire_in - 300, 60)
        return self._access_token


class _RequesterResultCardNotifier:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        template_id: str,
        openapi_endpoint: str,
        timeout_seconds: float = 8.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._template_id = template_id.strip()
        self._openapi_endpoint = openapi_endpoint.strip().rstrip("/") or DEFAULT_OPENAPI_ENDPOINT
        self._timeout_seconds = timeout_seconds
        self._logger = logger or logging.getLogger(OBS_LOGGER_NAME)
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def notify(
        self,
        *,
        request_id: str,
        requester_user_id: str,
        approval_status: str,
        summary: str,
        file_title: str,
        download_url: str,
        show_download_button: str,
    ) -> FileApprovalNotifyResult:
        if not self._template_id:
            return FileApprovalNotifyResult(success=False, reason="missing_requester_template_config")
        if not self._client_id or not self._client_secret:
            return FileApprovalNotifyResult(success=False, reason="missing_client_credentials")
        if not requester_user_id:
            return FileApprovalNotifyResult(success=False, reason="missing_requester_user_id")
        if approval_status not in {"delivered", "rejected"}:
            return FileApprovalNotifyResult(success=False, reason="unsupported_result_status")

        try:
            access_token = self._get_access_token()
            card_param_map = {
                "summary": summary,
                "approval_status": approval_status,
                "actions_locked": "true",
                "submitted": "true",
                "request_id": request_id,
                "file_title": file_title,
                "download_url": download_url,
                "show_download_button": show_download_button,
            }
            body: dict[str, Any] = {
                "userId": requester_user_id,
                "userIdType": 1,
                "cardTemplateId": self._template_id,
                "outTrackId": f"requester-result-{request_id}-{approval_status}",
                "callbackType": "STREAM",
                "cardData": {"cardParamMap": card_param_map},
                "imRobotOpenSpaceModel": {"supportForward": True},
                "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
                "openSpaceId": f"dtv1.card//im_robot.{requester_user_id}",
            }
            headers = {
                "Content-Type": "application/json",
                "Accept": "*/*",
                "x-acs-dingtalk-access-token": access_token,
            }
            url = f"{self._openapi_endpoint}/v1.0/card/instances/createAndDeliver"
            response = requests.post(url, headers=headers, json=body, timeout=self._timeout_seconds)
            response_text = response.text
            if response.status_code >= 400:
                self._logger.warning(
                    "requester result createAndDeliver failed: status=%s body=%s",
                    response.status_code,
                    response_text[:500],
                )
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_http_error")

            response_payload: Any
            try:
                response_payload = response.json()
            except Exception:
                self._logger.warning("requester result createAndDeliver returned non-JSON response")
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_non_json")

            if _card_delivery_response_has_failure(response_payload):
                self._logger.warning(
                    "requester result createAndDeliver rejected: payload=%s",
                    _truncate_payload_for_log(response_payload),
                )
                return FileApprovalNotifyResult(success=False, reason="create_and_deliver_failed")

            self._logger.info(
                "file.approval.notify.requester_result",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.stream",
                        "event": "requester_result_card_delivered",
                        "request_id": request_id,
                        "requester_user_id": requester_user_id,
                        "approval_status": approval_status,
                        "card_template_id": self._template_id,
                    }
                },
            )
            return FileApprovalNotifyResult(success=True, reason="delivered")
        except Exception:
            self._logger.exception("requester result createAndDeliver exception")
            return FileApprovalNotifyResult(success=False, reason="delivery_exception")

    def _get_access_token(self) -> str:
        now = time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        url = f"{self._openapi_endpoint}/v1.0/oauth2/accessToken"
        response = requests.post(
            url,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"appKey": self._client_id, "appSecret": self._client_secret},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        data = payload.get("result") if isinstance(payload.get("result"), Mapping) else payload.get("data")
        token_source = data if isinstance(data, Mapping) else payload
        access_token = str(token_source.get("accessToken") or payload.get("accessToken") or "").strip()
        if not access_token:
            raise RuntimeError("OpenAPI access token is empty")
        expire_in = int(token_source.get("expireIn") or payload.get("expireIn") or 7200)
        self._access_token = access_token
        self._access_token_expires_at = time() + max(expire_in - 300, 60)
        return self._access_token


def _is_template_driven_action_card(card_payload: Mapping[str, Any]) -> bool:
    card_type = str(card_payload.get("card_type") or "").strip()
    return card_type in {"file_request_confirmation", "leave_request_ready"}


def _dump_stream_event_if_enabled(*, payload: Any, trace_id: str, logger: logging.Logger) -> None:
    dump_dir = (os.getenv("DINGTALK_STREAM_EVENT_DUMP_DIR") or "").strip()
    if not dump_dir:
        return
    try:
        output_dir = Path(dump_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        target = output_dir / f"event-{trace_id}.json"
        target.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        logger.exception("stream event dump failed")


def _build_approval_not_found_replies() -> tuple[AgentReply, ...]:
    return (
        AgentReply(
            channel="text",
            text="已收到按钮点击，但未定位到待处理申请。请重新发送“我要采购合同文件”后再确认申请。",
        ),
    )


def _resolve_file_approval_action(
    *,
    payload: Mapping[str, Any],
    service: SingleChatService,
) -> tuple[dict[str, str], Any] | None:
    approval_action = _extract_approval_action_payload(payload)
    if approval_action is None:
        return None

    request_id = approval_action.get("request_id", "")
    if request_id:
        approval_outcome = service.handle_file_approval_action(
            request_id=request_id,
            action=approval_action["action"],
            approver_user_id=approval_action["approver_user_id"],
        )
    else:
        approval_outcome = service.handle_file_approval_action_by_session(
            action=approval_action["action"],
            approver_user_id=approval_action["approver_user_id"],
            conversation_id=approval_action.get("conversation_id", ""),
            sender_id=approval_action.get("sender_id", ""),
        )
    return approval_action, approval_outcome


def _resolve_leave_confirmation_action(
    *,
    payload: Mapping[str, Any],
    service: SingleChatService,
) -> tuple[dict[str, str], Any] | None:
    leave_action = _extract_leave_action_payload(payload)
    if leave_action is None:
        return None
    leave_outcome = service.handle_leave_confirmation_action_by_session(
        action=leave_action["action"],
        conversation_id=leave_action.get("conversation_id", ""),
        sender_id=leave_action.get("sender_id", ""),
    )
    return leave_action, leave_outcome


def _build_leave_action_outcome_payload(
    *,
    leave_action: Mapping[str, str],
    leave_outcome: Any,
    replies: tuple[AgentReply, ...],
) -> dict[str, Any]:
    return {
        "handled": leave_outcome.handled,
        "reason": leave_outcome.reason,
        "intent": "leave",
        "channel": leave_outcome.reply.channel if replies else "none",
        "replies": [reply.to_dict() for reply in replies],
        "source_ids": [],
        "permission_decision": "allow",
        "knowledge_version": "",
        "answered_at": "",
        "citations": [],
        "llm_trace": {},
        "user_context": {
            "user_id": leave_action.get("sender_id", "unknown") or "unknown",
            "dept_id": "unknown",
            "identity_source": "event_fallback",
            "is_degraded": True,
        },
        "leave_action": leave_action.get("action", ""),
        "leave_status": _resolve_leave_callback_status(leave_outcome=leave_outcome),
    }


def _resolve_leave_callback_status(*, leave_outcome: Any) -> str:
    reason = str(leave_outcome.reason or "")
    if reason == "leave_workflow_submitted":
        return "submitted"
    if reason == "leave_workflow_cancelled":
        return "cancelled"
    if reason in {"leave_workflow_not_found", "leave_workflow_confirmation_expired"}:
        return "not_found"
    if reason == "leave_workflow_handoff_fallback":
        return "fallback"
    if reason == "leave_workflow_handoff":
        return "handoff"
    return "pending"


def _resolve_leave_callback_feedback_text(*, leave_outcome: Any, replies: tuple[AgentReply, ...]) -> str:
    reason = str(leave_outcome.reason or "")
    reason_text_map = {
        "leave_workflow_submitted": "请假审批已提交。",
        "leave_workflow_cancelled": "本次请假已取消。",
        "leave_workflow_not_found": "未找到待确认请假，请重新发送“我要请假”。",
        "leave_workflow_confirmation_expired": "请假确认已过期，请重新发送“我要请假”。",
        "leave_workflow_handoff_fallback": "自动提交流程失败，请到 OA 审批入口手动提交。",
    }
    mapped = reason_text_map.get(reason)
    if mapped:
        return mapped
    for reply in replies:
        if reply.channel == "text":
            text = (reply.text or "").strip()
            if text:
                return text
    return "请假操作已处理。"


def _build_leave_card_callback_response_payload(
    *,
    leave_action: Mapping[str, str],
    leave_outcome: Any,
    replies: tuple[AgentReply, ...],
) -> dict[str, Any]:
    status = _resolve_leave_callback_status(leave_outcome=leave_outcome)
    feedback = _resolve_leave_callback_feedback_text(leave_outcome=leave_outcome, replies=replies)
    actions_locked = status in {"submitted", "cancelled", "not_found", "fallback", "handoff"}
    card_param_map = {
        "leave_action": leave_action.get("action", ""),
        "leave_status": status,
        "approval_status": status,
        "approval_message": feedback,
        "summary": feedback,
        "actions_locked": "true" if actions_locked else "false",
    }
    string_card_param_map = {key: _to_string_card_param_map_value(value) for key, value in card_param_map.items()}
    return {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
        "cardData": {
            "cardParamMap": string_card_param_map,
        },
        "userPrivateData": {
            "cardParamMap": string_card_param_map,
        },
    }


def _build_file_approval_outcome_payload(
    *,
    approval_action: Mapping[str, str],
    approval_outcome: Any,
    replies: tuple[AgentReply, ...],
) -> dict[str, Any]:
    return {
        "handled": approval_outcome.handled,
        "reason": approval_outcome.reason,
        "intent": "file_request",
        "channel": "text" if replies else "none",
        "replies": [reply.to_dict() for reply in replies],
        "source_ids": [],
        "permission_decision": "allow",
        "knowledge_version": "",
        "answered_at": "",
        "citations": [],
        "llm_trace": {},
        "user_context": {
            "user_id": approval_action["approver_user_id"],
            "dept_id": "unknown",
            "identity_source": "event_fallback",
            "is_degraded": True,
        },
        "request_id": approval_outcome.request_id,
        "approval_action": approval_outcome.action,
        "approval_status": approval_outcome.status,
    }


def _resolve_card_callback_feedback_text(*, approval_outcome: Any, replies: tuple[AgentReply, ...]) -> str:
    reason_text_map = {
        "file_lookup_pending_approval": "申请已提交，审批通过后将自动发送文件。",
        "file_approval_notify_failed": "申请已记录，但通知审批人失败，请稍后重试。",
        "file_request_cancelled": "已取消本次文件申请。",
        "file_approval_not_found": "未定位到待处理申请，请重新发起申请。",
        "file_approval_invalid_action": "未识别到可执行按钮，请重试。",
        "file_approval_forbidden": "仅审批人可执行该操作。",
        "file_approval_approved": "审批已通过，文件已发送。",
        "file_approval_rejected": "审批已拒绝。",
        "file_approval_already_processed": "该申请已处理完成。",
    }
    mapped = reason_text_map.get(str(approval_outcome.reason or ""))
    if mapped:
        return mapped
    for reply in replies:
        if reply.channel == "text":
            text = (reply.text or "").strip()
            if text:
                return text
    return "操作已处理。"


def _build_card_callback_noop_response_payload() -> dict[str, Any]:
    return {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
        "cardData": {
            "cardParamMap": {
                "approval_message": "未识别到可执行操作。",
            }
        },
        "userPrivateData": {
            "cardParamMap": {
                "approval_message": "未识别到可执行操作。",
            }
        },
    }


def _build_card_callback_response_payload(
    *,
    approval_action: Mapping[str, str],
    approval_outcome: Any,
    replies: tuple[AgentReply, ...],
) -> dict[str, Any]:
    action = str(approval_outcome.action or approval_action.get("action") or "").strip()
    status = str(approval_outcome.status or "").strip()
    feedback = _resolve_card_callback_feedback_text(approval_outcome=approval_outcome, replies=replies)
    card_param_map = {
        "approval_action": action,
        "approval_status": status,
        "approval_message": feedback,
        "summary": feedback,
        "submitted": "true" if status == "pending" else "false",
        "actions_locked": "true" if status in {"pending", "cancelled", "delivered", "rejected"} else "false",
    }
    string_card_param_map = {key: _to_string_card_param_map_value(value) for key, value in card_param_map.items()}
    return {
        "cardUpdateOptions": {
            "updateCardDataByKey": True,
            "updatePrivateDataByKey": True,
        },
        "cardData": {
            "cardParamMap": string_card_param_map,
        },
        "userPrivateData": {
            "cardParamMap": string_card_param_map,
        },
    }


def _build_requester_result_summary(*, approval_outcome: Any, replies: tuple[AgentReply, ...]) -> str:
    status = str(approval_outcome.status or "").strip()
    file_title = str(getattr(approval_outcome, "file_title", "") or "").strip()
    if status == "delivered":
        if file_title:
            return f"《{file_title}》已审批通过"
        return "文件已审批通过"
    if status == "rejected":
        if file_title:
            return f"《{file_title}》审批未通过"
        return "文件审批未通过"
    return _resolve_card_callback_feedback_text(approval_outcome=approval_outcome, replies=replies)


def _build_requester_result_download_url(*, approval_outcome: Any) -> str:
    status = str(approval_outcome.status or "").strip()
    if status != "delivered":
        return ""
    return str(getattr(approval_outcome, "file_url", "") or "").strip()


def _build_requester_result_show_download_button(*, download_url: str) -> str:
    return "true" if bool(download_url) else "false"


def _extract_callback_route_hints(payload: Mapping[str, Any]) -> tuple[str, str]:
    event = payload.get("data")
    mapping = event if isinstance(event, Mapping) else payload
    candidates = _collect_mapping_candidates(mapping)
    out_track_id = _pick_string_from_candidates(candidates, ("outTrackId", "out_track_id"))
    space_id = _pick_string_from_candidates(candidates, ("spaceId", "openSpaceId", "space_id", "open_space_id"))
    return out_track_id, space_id


def _should_push_requester_result_card(*, approval_action: Mapping[str, str], approval_outcome: Any) -> bool:
    action = str(approval_action.get("action") or "").strip()
    status = str(approval_outcome.status or "").strip()
    if action not in {"approve", "reject"}:
        return False
    if not bool(approval_outcome.handled):
        return False
    return status in {"delivered", "rejected"}


def handle_single_chat_payload(
    payload: Mapping[str, Any],
    *,
    service: SingleChatService,
    sender: ReplySender,
    user_context_resolver: UserContextResolver,
) -> dict[str, Any]:
    leave_resolution = _resolve_leave_confirmation_action(payload=payload, service=service)
    if leave_resolution is not None:
        leave_action, leave_outcome = leave_resolution
        replies = leave_outcome.all_replies()
        _dispatch_replies(sender=sender, replies=replies)
        return _build_leave_action_outcome_payload(
            leave_action=leave_action,
            leave_outcome=leave_outcome,
            replies=replies,
        )

    approval_resolution = _resolve_file_approval_action(payload=payload, service=service)
    if approval_resolution is not None:
        approval_action, approval_outcome = approval_resolution
        plain_text_single_chat = _is_plain_text_single_chat(
            _collect_mapping_candidates(payload.get("data") if isinstance(payload.get("data"), Mapping) else payload)
        )
        if not approval_action.get("request_id") and plain_text_single_chat and approval_outcome.reason == "file_approval_not_found":
            approval_resolution = None
        else:
            replies = approval_outcome.replies
            if not replies and approval_outcome.reason == "file_approval_not_found":
                replies = _build_approval_not_found_replies()
            _dispatch_replies(sender=sender, replies=replies)
            return _build_file_approval_outcome_payload(
                approval_action=approval_action,
                approval_outcome=approval_outcome,
                replies=replies,
            )

    incoming_message = parse_stream_event(payload)
    user_context = user_context_resolver.resolve(incoming_message)
    result = service.handle(incoming_message, user_context=user_context)
    replies = result.all_replies()
    _dispatch_replies(sender=sender, replies=replies)

    return {
        "handled": result.handled,
        "reason": result.reason,
        "intent": result.intent,
        "channel": result.reply.channel,
        "replies": [item.to_dict() for item in replies],
        "source_ids": list(result.source_ids),
        "permission_decision": result.permission_decision,
        "knowledge_version": result.knowledge_version,
        "answered_at": result.answered_at,
        "citations": [dict(item) for item in result.citations],
        "llm_trace": dict(result.llm_trace),
        "user_context": user_context.to_dict(),
    }


def _dispatch_replies(*, sender: ReplySender, replies: tuple[AgentReply, ...]) -> None:
    for reply in replies:
        if reply.channel == "text":
            sender.send_text(reply.text or "")
        else:
            sender.send_interactive_card(reply.interactive_card or {})


def _extract_leave_action_payload(payload: Mapping[str, Any]) -> dict[str, str] | None:
    data = payload.get("data")
    event = data if isinstance(data, Mapping) else payload
    candidates = _collect_mapping_candidates(event)
    leave_callback_context = _is_leave_callback_context(candidates)

    action = ""
    for candidate in candidates:
        action_value = _pick_payload_string(
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

        button_id = _pick_payload_string(
            candidate,
            ("button_id", "buttonId", "action_id", "actionId", "id", "componentId", "component_id"),
        )
        normalized_button_action = _parse_leave_action_from_button_id(
            button_id,
            allow_file_alias=leave_callback_context,
        )
        if normalized_button_action:
            action = normalized_button_action
            break

    if not action:
        return None

    conversation_id = _pick_string_from_candidates(
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
    )
    sender_id = _pick_string_from_candidates(
        candidates,
        ("sender_id", "senderStaffId", "senderId", "staffId", "userId", "user_id", "userid"),
    )
    return {
        "action": action,
        "conversation_id": conversation_id,
        "sender_id": sender_id or "unknown",
    }


def _is_leave_callback_context(candidates: list[Mapping[str, Any]]) -> bool:
    workflow_type = _pick_string_from_candidates(candidates, ("workflow_type", "workflowType")).strip().lower()
    if workflow_type == "leave":
        return True
    out_track_id = _pick_string_from_candidates(candidates, ("outTrackId", "out_track_id")).strip().lower()
    return out_track_id.startswith("leave-confirm-")


def _parse_leave_action_from_button_id(button_id: str, *, allow_file_alias: bool) -> str:
    raw = (button_id or "").strip()
    if not raw:
        return ""
    head = raw.split("::", 1)[0]
    return _normalize_leave_action_text(head, allow_file_alias=allow_file_alias)


def _extract_leave_action_from_card_private_data(candidate: Mapping[str, Any], *, allow_file_alias: bool) -> str:
    private_data = _extract_card_private_data_mapping(candidate)
    if private_data is None:
        return ""

    action_ids = private_data.get("actionIds")
    if isinstance(action_ids, list):
        for item in action_ids:
            if not isinstance(item, str) or not item.strip():
                continue
            normalized = _parse_leave_action_from_button_id(item, allow_file_alias=allow_file_alias)
            if normalized:
                return normalized

    action_value = _pick_payload_string(private_data, ("action", "action_name", "actionName"))
    return _normalize_leave_action_text(action_value, allow_file_alias=allow_file_alias)


def _extract_approval_action_payload(payload: Mapping[str, Any]) -> dict[str, str] | None:
    if _is_card_callback_debug_enabled():
        logging.getLogger(OBS_LOGGER_NAME).warning(
            "[CARD_DEBUG] _extract_approval_action_payload payload=%s",
            _truncate_payload_for_log(payload),
        )
    data = payload.get("data")
    event = data if isinstance(data, Mapping) else payload
    candidates = _collect_mapping_candidates(event)

    request_id = ""
    action = ""
    for candidate in candidates:
        if not request_id:
            request_id = _pick_payload_string(candidate, ("request_id", "requestId"))
        if not request_id:
            request_id = _find_request_id_in_mapping(candidate)

        action_value = _pick_payload_string(
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
        if request_id and action:
            break

        text_action_value = _pick_payload_string(candidate, ("text", "content", "title", "name"))
        normalized_text_action = _normalize_explicit_text_approval_action(text_action_value)
        if normalized_text_action:
            action = normalized_text_action
        if request_id and action:
            break

        parsed_card_action, parsed_card_request_id = _extract_action_from_card_private_data(candidate)
        if parsed_card_action:
            action = parsed_card_action
            if parsed_card_request_id:
                request_id = parsed_card_request_id
            if action and request_id:
                break

        button_id = _pick_payload_string(
            candidate,
            ("button_id", "buttonId", "action_id", "actionId", "id", "componentId", "component_id"),
        )
        parsed = _parse_action_from_button_id(button_id)
        if parsed is not None:
            action, request_id = parsed
            break
    if not action:
        return None
    approver_user_id = _pick_string_from_candidates(
        candidates,
        ("approver_user_id", "approverUserId", "sender_id", "senderStaffId", "senderId", "userId", "user_id", "userid"),
    )
    conversation_id = _pick_string_from_candidates(
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
    )
    sender_id = _pick_string_from_candidates(
        candidates,
        ("sender_id", "senderStaffId", "senderId", "staffId", "userId", "user_id", "userid"),
    )
    return {
        "request_id": request_id,
        "action": action,
        "approver_user_id": approver_user_id or "unknown",
        "conversation_id": conversation_id,
        "sender_id": sender_id or approver_user_id or "unknown",
    }


def _pick_payload_string(payload: Mapping[str, Any], keys: tuple[str, ...]) -> str:
    for key in keys:
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _pick_string_from_candidates(candidates: list[Mapping[str, Any]], keys: tuple[str, ...]) -> str:
    for candidate in candidates:
        value = _pick_payload_string(candidate, keys)
        if value:
            return value
    return ""


def _is_plain_text_single_chat(candidates: list[Mapping[str, Any]]) -> bool:
    raw_conversation_type = _pick_string_from_candidates(candidates, ("conversation_type", "conversationType")).lower()
    conversation_type = (
        "single"
        if raw_conversation_type in {"single", "single_chat", "1", "1v1", "private"}
        else raw_conversation_type
    )
    message_type = _pick_string_from_candidates(candidates, ("message_type", "messageType", "msgtype")).lower()
    return conversation_type == "single" and message_type == "text"


def _parse_action_from_button_id(button_id: str) -> tuple[str, str] | None:
    raw = (button_id or "").strip()
    if not raw:
        return None
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


def _extract_action_from_card_private_data(candidate: Mapping[str, Any]) -> tuple[str, str]:
    private_data = _extract_card_private_data_mapping(candidate)
    if private_data is None:
        return "", ""

    request_id = ""
    params = private_data.get("params")
    if isinstance(params, Mapping):
        request_id = _pick_payload_string(params, ("request_id", "requestId", "id"))
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

    action_value = _pick_payload_string(private_data, ("action", "action_name", "actionName"))
    normalized_action = _normalize_approval_action_text(action_value)
    if normalized_action:
        return normalized_action, request_id
    return "", ""


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


def _find_request_id_in_mapping(payload: Mapping[str, Any]) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        return ""
    return _find_request_id_in_text(text)


def _find_request_id_in_text(text: str) -> str:
    match = _REQUEST_ID_PATTERN.search(text or "")
    return match.group(1) if match else ""


def _collect_mapping_candidates(root: Mapping[str, Any]) -> list[Mapping[str, Any]]:
    candidates: list[Mapping[str, Any]] = []
    stack: list[Mapping[str, Any]] = [root]
    visited: set[int] = set()
    while stack:
        current = stack.pop()
        marker = id(current)
        if marker in visited:
            continue
        visited.add(marker)
        candidates.append(current)
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
    return candidates


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


def _card_delivery_response_has_failure(payload: Any) -> bool:
    if not isinstance(payload, Mapping):
        return False
    if _is_delivery_failure_flag(payload.get("success")):
        return True
    if _has_non_empty_error(payload):
        return True
    result = payload.get("result")
    if isinstance(result, list):
        for item in result:
            if not isinstance(item, Mapping):
                continue
            if _is_delivery_failure_flag(item.get("success")):
                return True
            if _has_non_empty_error(item):
                return True
    return False


def _is_delivery_failure_flag(value: Any) -> bool:
    if value is False:
        return True
    if isinstance(value, str) and value.strip().lower() in {"false", "0", "no", "failed", "error"}:
        return True
    return False


def _has_non_empty_error(payload: Mapping[str, Any]) -> bool:
    error_text = str(payload.get("errorMsg") or payload.get("errmsg") or payload.get("message") or "").strip()
    if error_text:
        return True
    error_code = str(payload.get("errorCode") or payload.get("errcode") or "").strip()
    return bool(error_code)


def _resolve_error_category(status_code: int, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if 400 <= status_code < 500:
        return "client_error"
    if status_code >= 500:
        return "server_error"
    return None


def _is_card_callback_debug_enabled() -> bool:
    return _parse_bool_env(os.getenv("DINGTALK_CARD_CALLBACK_DEBUG"), False)


def _truncate_payload_for_log(payload: Any, *, max_chars: int = 1200) -> str:
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(payload)
    if len(text) <= max_chars:
        return text
    return f"{text[:max_chars]}...<truncated>"


def _load_dingtalk_sdk() -> tuple[Any, Any, Any, Any]:
    try:
        sdk = importlib.import_module("dingtalk_stream")
        stream_module = importlib.import_module("dingtalk_stream.stream")
        card_module = importlib.import_module("dingtalk_stream.interactive_card")
        card_replier_module = importlib.import_module("dingtalk_stream.card_replier")
    except ModuleNotFoundError as exc:
        raise StreamRuntimeError(
            "dingtalk-stream package is not installed. Run: python -m pip install dingtalk-stream"
        ) from exc
    return sdk, stream_module, card_module, card_replier_module


def _split_text_chunks(content: str, chunk_chars: int) -> list[str]:
    size = chunk_chars if chunk_chars > 0 else len(content)
    return [content[index : index + size] for index in range(0, len(content), size)]


class _SdkReplySender:
    def __init__(
        self,
        *,
        handler: Any,
        incoming_message: Any,
        card_module: Any,
        ai_card_replier_cls: Any = None,
        streaming_card_settings: StreamingCardSettings | None = None,
        action_card_template_id: str | None = None,
        leave_action_card_template_id: str | None = None,
        openapi_endpoint: str | None = None,
        async_sleep_fn: Callable[[float], Awaitable[Any]] = asyncio.sleep,
    ) -> None:
        self._handler = handler
        self._incoming_message = incoming_message
        self._card_module = card_module
        self._ai_card_replier_cls = ai_card_replier_cls
        self._streaming_card_settings = streaming_card_settings or StreamingCardSettings(
            enabled=False,
            template_id="",
            content_key="content",
            title_key="",
            title="企业 Agent",
            chunk_chars=20,
            interval_seconds=0.12,
            min_chars=80,
        )
        self._async_sleep_fn = async_sleep_fn
        self._logger = logging.getLogger("keagent.dingtalk.stream")
        self._action_card_template_id = (
            action_card_template_id
            if action_card_template_id is not None
            else str(os.getenv("DINGTALK_CARD_TEMPLATE_ID") or "").strip()
        )
        self._leave_action_card_template_id = (
            leave_action_card_template_id
            if leave_action_card_template_id is not None
            else str(os.getenv("DINGTALK_LEAVE_CARD_TEMPLATE_ID") or "").strip()
        )
        endpoint = (
            openapi_endpoint
            if openapi_endpoint is not None
            else str(os.getenv("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT)
        )
        self._openapi_endpoint = endpoint.strip().rstrip("/") or DEFAULT_OPENAPI_ENDPOINT

    def send_text(self, text: str) -> None:
        content = text.strip() or "Message received."
        if self._should_send_streaming_card(content):
            loop = self._resolve_running_loop()
            if loop is None:
                self._handler.reply_text(content, self._incoming_message)
                return
            loop.create_task(self._send_text_via_streaming_card(content))
            return
        self._handler.reply_text(content, self._incoming_message)

    def send_interactive_card(self, card_payload: Mapping[str, Any]) -> None:
        title, lines = _extract_card_text_lines(card_payload)
        action_buttons = _build_standard_card_action_buttons(card_payload)
        template_driven_card = _is_template_driven_action_card(card_payload)
        if _is_card_callback_debug_enabled():
            logging.getLogger(OBS_LOGGER_NAME).warning(
                "[CARD_DEBUG] send_interactive_card title=%s action_buttons=%s template_driven=%s",
                title,
                len(action_buttons),
                template_driven_card,
            )
        if template_driven_card:
            if self._send_action_card_via_create_and_deliver(
                card_payload=card_payload,
                title=title,
                lines=lines,
            ):
                return
            # Do not fallback to legacy request-button cards when card callback delivery
            # is unavailable; those cards may render but never produce callbacks.
            fallback_text_parts = [line.strip() for line in lines if isinstance(line, str) and line.strip()]
            card_type = str(card_payload.get("card_type") or "").strip()
            if card_type == "leave_request_ready":
                fallback_text_parts.append("当前确认卡片回调不可用，请重新发送“我要请假”后重试，或前往 OA 审批提交。")
            else:
                fallback_text_parts.append("请回复“确认申请”或“取消”。")
            self._handler.reply_text("\n".join(fallback_text_parts), self._incoming_message)
            return
        if action_buttons:
            card_data = _build_standard_interactive_card_data(
                title=title,
                lines=lines,
                action_buttons=action_buttons,
            )
        else:
            card_data = self._card_module.generate_multi_text_line_card_data(title=title, logo="", texts=lines)
        if action_buttons:
            card_biz_id = self._handler.reply_card(
                card_data=card_data,
                incoming_message=self._incoming_message,
                callbackType="STREAM",
            )
        else:
            card_biz_id = self._handler.reply_card(card_data=card_data, incoming_message=self._incoming_message)
        if _is_card_callback_debug_enabled():
            logging.getLogger(OBS_LOGGER_NAME).warning(
                "[CARD_DEBUG] reply_card returned card_biz_id=%s callbackType=%s",
                card_biz_id or "",
                "STREAM" if action_buttons else "NONE",
            )
        if card_biz_id:
            return

        markdown = "\n\n".join(lines)
        if _is_card_callback_debug_enabled():
            logging.getLogger(OBS_LOGGER_NAME).warning("[CARD_DEBUG] reply_card failed, fallback to markdown")
        self._handler.reply_markdown(title=title, text=markdown, incoming_message=self._incoming_message)

    def _send_action_card_via_create_and_deliver(
        self,
        *,
        card_payload: Mapping[str, Any],
        title: str,
        lines: list[str],
    ) -> bool:
        card_type = str(card_payload.get("card_type") or "").strip()
        if card_type == "leave_request_ready":
            template_id = self._leave_action_card_template_id.strip() or self._action_card_template_id.strip()
        else:
            template_id = self._action_card_template_id.strip()
        if not template_id:
            return False
        dingtalk_client = getattr(self._handler, "dingtalk_client", None)
        if dingtalk_client is None:
            return False
        access_token_getter = getattr(dingtalk_client, "get_access_token", None)
        if not callable(access_token_getter):
            return False
        access_token = str(access_token_getter() or "").strip()
        if not access_token:
            self._logger.warning("action card createAndDeliver skipped: access token unavailable")
            return False
        receiver_user_id = str(getattr(self._incoming_message, "sender_staff_id", "") or "").strip()
        if not receiver_user_id:
            self._logger.warning("action card createAndDeliver skipped: sender_staff_id is empty")
            return False

        if card_type == "leave_request_ready":
            out_track_id = f"leave-confirm-{uuid4().hex}"
        elif card_type == "file_request_confirmation":
            out_track_id = f"file-confirm-{uuid4().hex}"
        else:
            out_track_id = f"card-{uuid4().hex}"
        card_param_map = _build_action_card_param_map(
            card_payload=card_payload,
            title=title,
            lines=lines,
        )
        body: dict[str, Any] = {
            "userId": receiver_user_id,
            "userIdType": 1,
            "cardTemplateId": template_id,
            "outTrackId": out_track_id,
            "callbackType": "STREAM",
            "cardData": {"cardParamMap": card_param_map},
            "imRobotOpenSpaceModel": {"supportForward": True},
            "imRobotOpenDeliverModel": {"spaceType": "IM_ROBOT"},
            "openSpaceId": f"dtv1.card//im_robot.{receiver_user_id}",
        }

        url = f"{self._openapi_endpoint}/v1.0/card/instances/createAndDeliver"
        headers = {
            "Content-Type": "application/json",
            "Accept": "*/*",
            "x-acs-dingtalk-access-token": access_token,
        }
        try:
            response = requests.post(url, headers=headers, json=body, timeout=8)
            response_text = response.text
            if response.status_code >= 400:
                self._logger.warning(
                    "action card createAndDeliver failed: status=%s body=%s",
                    response.status_code,
                    response_text[:500],
                )
                return False
            response_payload: Any
            try:
                response_payload = response.json()
            except Exception:
                self._logger.warning("action card createAndDeliver returned non-JSON response")
                return False
            if _is_card_callback_debug_enabled():
                logging.getLogger(OBS_LOGGER_NAME).warning(
                    "[CARD_DEBUG] createAndDeliver response=%s",
                    _truncate_payload_for_log(response_payload),
                )
            if _card_delivery_response_has_failure(response_payload):
                self._logger.warning(
                    "action card createAndDeliver result indicates failure: payload=%s",
                    _truncate_payload_for_log(response_payload),
                )
                return False
            if _is_card_callback_debug_enabled():
                logging.getLogger(OBS_LOGGER_NAME).warning(
                    "[CARD_DEBUG] createAndDeliver success outTrackId=%s template=%s",
                    out_track_id,
                    template_id,
                )
            return True
        except Exception:
            self._logger.exception("action card createAndDeliver exception")
            return False

    def _should_send_streaming_card(self, content: str) -> bool:
        settings = self._streaming_card_settings
        if not settings.enabled:
            return False
        if not settings.template_id:
            return False
        if len(content) < settings.min_chars:
            return False
        return self._ai_card_replier_cls is not None

    def _build_streaming_card_data(self, content: str) -> dict[str, str]:
        settings = self._streaming_card_settings
        card_data = {settings.content_key: content}
        if settings.title_key:
            card_data[settings.title_key] = settings.title
        return card_data

    @staticmethod
    def _resolve_running_loop() -> asyncio.AbstractEventLoop | None:
        try:
            return asyncio.get_running_loop()
        except RuntimeError:
            return None

    async def _send_text_via_streaming_card(self, content: str) -> None:
        settings = self._streaming_card_settings
        replier = self._ai_card_replier_cls(self._handler.dingtalk_client, self._incoming_message)

        card_instance_id = ""
        streamed_content = ""
        try:
            card_instance_id = await replier.async_create_and_deliver_card(
                settings.template_id,
                self._build_streaming_card_data(""),
            )
            if not card_instance_id:
                self._handler.reply_text(content, self._incoming_message)
                return

            chunks = _split_text_chunks(content, settings.chunk_chars)
            for index, chunk in enumerate(chunks):
                streamed_content += chunk
                await replier.async_streaming(
                    card_instance_id=card_instance_id,
                    content_key=settings.content_key,
                    content_value=streamed_content,
                    append=False,
                    finished=False,
                    failed=False,
                )
                if index < len(chunks) - 1 and settings.interval_seconds > 0:
                    await self._async_sleep_fn(settings.interval_seconds)
            await replier.async_streaming(
                card_instance_id=card_instance_id,
                content_key=settings.content_key,
                content_value=streamed_content,
                append=False,
                finished=True,
                failed=False,
            )
        except Exception:
            self._logger.exception("streaming card send failed")
            if card_instance_id:
                try:
                    await replier.async_streaming(
                        card_instance_id=card_instance_id,
                        content_key=settings.content_key,
                        content_value=streamed_content,
                        append=False,
                        finished=False,
                        failed=True,
                    )
                except Exception:
                    self._logger.exception("streaming card fail-state update failed")
            self._handler.reply_text(content, self._incoming_message)


def build_stream_client(
    credentials: DingTalkStreamCredentials,
    *,
    single_chat_service: SingleChatService | None = None,
    user_context_resolver: UserContextResolver | None = None,
    stream_logger: logging.Logger | None = None,
    observability_logger: logging.Logger | None = None,
) -> Any:
    sdk, stream_module, card_module, card_replier_module = _load_dingtalk_sdk()
    stream_module.DingTalkStreamClient.OPEN_CONNECTION_API = credentials.stream_endpoint
    streaming_card_settings = load_streaming_card_settings()
    hr_approval_settings = load_hr_approval_card_settings()
    ai_card_replier_cls = getattr(card_replier_module, "AICardReplier", None)

    service = single_chat_service
    if service is None:
        if hr_approval_settings.enabled:
            hr_notifier = _StreamHRApprovalNotifier(
                client_id=credentials.client_id,
                client_secret=credentials.client_secret,
                settings=hr_approval_settings,
            )
            service = SingleChatService(
                file_request_service=FileRequestService(
                    approver_user_id=hr_approval_settings.approver_user_id,
                    approval_notifier=hr_notifier,
                )
            )
        else:
            if hr_approval_settings.approver_user_id or hr_approval_settings.template_id:
                logging.getLogger(OBS_LOGGER_NAME).warning(
                    "hr approval notifier disabled: incomplete config "
                    "(DINGTALK_HR_APPROVER_USER_ID and DINGTALK_HR_CARD_TEMPLATE_ID must be set together)"
                )
            service = SingleChatService()
    context_resolver = user_context_resolver
    if context_resolver is None:
        from app.integrations.dingtalk.openapi_identity import DingTalkOpenAPIIdentityClient

        context_resolver = UserContextResolver(
            identity_client=DingTalkOpenAPIIdentityClient(
                client_id=credentials.client_id,
                client_secret=credentials.client_secret,
            )
        )
    base_stream_logger = stream_logger or logging.getLogger("keagent.dingtalk.stream")
    sdk_logger = _SdkLoggerAdapter(base_stream_logger)
    obs_logger = observability_logger or logging.getLogger(OBS_LOGGER_NAME)
    requester_result_template_id = str(os.getenv("DINGTALK_CARD_TEMPLATE_ID") or "").strip()
    requester_result_card_notifier = _RequesterResultCardNotifier(
        client_id=credentials.client_id,
        client_secret=credentials.client_secret,
        template_id=requester_result_template_id,
        openapi_endpoint=str(os.getenv("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT),
        logger=obs_logger,
    )
    callback_handler_base = getattr(sdk, "CallbackHandler", sdk.ChatbotHandler)

    class ChatMessageCallbackHandler(sdk.ChatbotHandler):
        async def process(self, callback_message: Any):  # type: ignore[override]
            trace_id = getattr(callback_message.headers, "message_id", "") or str(uuid4())
            token = set_trace_id(trace_id)
            started = perf_counter()
            status_code = 200
            event = "stream_callback_completed"
            explicit_error_category: str | None = None

            try:
                if _is_card_callback_debug_enabled():
                    obs_logger.warning(
                        "[CARD_DEBUG] callback received topic=%s payload=%s",
                        getattr(callback_message.headers, "topic", "unknown"),
                        _truncate_payload_for_log(callback_message.data),
                    )
                _dump_stream_event_if_enabled(
                    payload=callback_message.data,
                    trace_id=trace_id,
                    logger=obs_logger,
                )
                incoming_message = sdk.ChatbotMessage.from_dict(callback_message.data)
                sender = _SdkReplySender(
                    handler=self,
                    incoming_message=incoming_message,
                    card_module=card_module,
                    ai_card_replier_cls=ai_card_replier_cls,
                    streaming_card_settings=streaming_card_settings,
                )
                outcome = handle_single_chat_payload(
                    callback_message.data,
                    service=service,
                    sender=sender,
                    user_context_resolver=context_resolver,
                )
                if outcome.get("reason") == "system_fallback":
                    event = "stream_callback_degraded"
                    explicit_error_category = "dependency_error"
                callback_message.extensions["user_context"] = outcome["user_context"]
                return sdk.AckMessage.STATUS_OK, {
                    "trace_id": trace_id,
                    "result": outcome,
                }
            except ValueError as exc:
                status_code = 400
                event = "stream_callback_rejected"
                explicit_error_category = "client_error"
                return sdk.AckMessage.STATUS_BAD_REQUEST, {
                    "trace_id": trace_id,
                    "error": str(exc),
                }
            except Exception:
                status_code = 500
                event = "stream_callback_failed"
                explicit_error_category = "dependency_error"
                obs_logger.exception("stream callback failed")
                return sdk.AckMessage.STATUS_SYSTEM_EXCEPTION, {
                    "trace_id": trace_id,
                    "error": "stream callback failed",
                }
            finally:
                elapsed_ms = round((perf_counter() - started) * 1000, 3)
                obs_logger.info(
                    "stream.callback",
                    extra={
                        "obs": {
                            "module": "integrations.dingtalk.stream",
                            "trace_id": trace_id,
                            "user_id": outcome.get("user_context", {}).get("user_id", "unknown")
                            if "outcome" in locals()
                            else "unknown",
                            "dept_id": outcome.get("user_context", {}).get("dept_id", "unknown")
                            if "outcome" in locals()
                            else "unknown",
                            "intent": outcome.get("intent", "other") if "outcome" in locals() else "other",
                            "identity_source": outcome.get("user_context", {}).get("identity_source", "event_fallback")
                            if "outcome" in locals()
                            else "event_fallback",
                            "is_degraded": outcome.get("user_context", {}).get("is_degraded", True)
                            if "outcome" in locals()
                            else True,
                            "source_ids": outcome.get("source_ids", []) if "outcome" in locals() else [],
                            "permission_decision": outcome.get("permission_decision", "allow")
                            if "outcome" in locals()
                            else "allow",
                            "knowledge_version": outcome.get("knowledge_version", "")
                            if "outcome" in locals()
                            else "",
                            "answered_at": outcome.get("answered_at", "") if "outcome" in locals() else "",
                            "llm_trace": outcome.get("llm_trace", {}) if "outcome" in locals() else {},
                            "event": event,
                            "path": getattr(callback_message.headers, "topic", DEFAULT_CHATBOT_TOPIC),
                            "method": "STREAM_CALLBACK",
                            "status_code": status_code,
                            "duration_ms": elapsed_ms,
                            "error_category": _resolve_error_category(status_code, explicit_error_category),
                        }
                    },
                )
                reset_trace_id(token)

    class CardCallbackHandler(callback_handler_base):
        async def process(self, callback_message: Any):  # type: ignore[override]
            trace_id = getattr(callback_message.headers, "message_id", "") or str(uuid4())
            token = set_trace_id(trace_id)
            started = perf_counter()
            status_code = 200
            event = "stream_card_callback_completed"
            explicit_error_category: str | None = None

            try:
                if _is_card_callback_debug_enabled():
                    obs_logger.warning(
                        "[CARD_DEBUG] callback received topic=%s payload=%s",
                        getattr(callback_message.headers, "topic", "unknown"),
                        _truncate_payload_for_log(callback_message.data),
                    )
                _dump_stream_event_if_enabled(
                    payload=callback_message.data,
                    trace_id=trace_id,
                    logger=obs_logger,
                )
                leave_resolution = _resolve_leave_confirmation_action(payload=callback_message.data, service=service)
                if leave_resolution is not None:
                    leave_action, leave_outcome = leave_resolution
                    leave_replies = leave_outcome.all_replies()
                    outcome = _build_leave_action_outcome_payload(
                        leave_action=leave_action,
                        leave_outcome=leave_outcome,
                        replies=leave_replies,
                    )
                    response_payload = _build_leave_card_callback_response_payload(
                        leave_action=leave_action,
                        leave_outcome=leave_outcome,
                        replies=leave_replies,
                    )
                    return sdk.AckMessage.STATUS_OK, response_payload

                approval_resolution = _resolve_file_approval_action(payload=callback_message.data, service=service)
                if approval_resolution is None:
                    outcome = {
                        "handled": False,
                        "reason": "card_callback_ignored",
                        "intent": "file_request",
                        "source_ids": [],
                        "permission_decision": "allow",
                        "knowledge_version": "",
                        "answered_at": "",
                        "llm_trace": {},
                        "user_context": {
                            "user_id": "unknown",
                            "dept_id": "unknown",
                            "identity_source": "event_fallback",
                            "is_degraded": True,
                        },
                    }
                    return sdk.AckMessage.STATUS_OK, _build_card_callback_noop_response_payload()

                approval_action, approval_outcome = approval_resolution
                replies = approval_outcome.replies
                if not replies and approval_outcome.reason == "file_approval_not_found":
                    replies = _build_approval_not_found_replies()
                outcome = _build_file_approval_outcome_payload(
                    approval_action=approval_action,
                    approval_outcome=approval_outcome,
                    replies=replies,
                )
                response_payload = _build_card_callback_response_payload(
                    approval_action=approval_action,
                    approval_outcome=approval_outcome,
                    replies=replies,
                )
                callback_payload = callback_message.data if isinstance(callback_message.data, Mapping) else {}
                out_track_id, space_id = _extract_callback_route_hints(callback_payload)
                callback_user_id = str(approval_action.get("approver_user_id") or "").strip() or "unknown"
                requester_user_id = str(getattr(approval_outcome, "requester_sender_id", "") or "").strip() or "unknown"
                request_id = str(getattr(approval_outcome, "request_id", "") or "").strip() or "unknown"
                action_name = str(approval_action.get("action") or "").strip() or "unknown"
                obs_logger.info(
                    "approval callback actor mapping",
                    extra={
                        "obs": {
                            "module": "integrations.dingtalk.stream",
                            "event": (
                                "approval_callback_actor_mapping "
                                f"request_id={request_id} action={action_name} "
                                f"callback_user_id={callback_user_id} requester_user_id={requester_user_id} "
                                f"outTrackId={out_track_id or '-'} spaceId={space_id or '-'}"
                            ),
                        }
                    },
                )
                if _is_card_callback_debug_enabled():
                    obs_logger.warning(
                        "[CARD_DEBUG] approval_callback_actor_mapping payload=%s",
                        _truncate_payload_for_log(callback_message.data),
                    )
                if _should_push_requester_result_card(
                    approval_action=approval_action,
                    approval_outcome=approval_outcome,
                ):
                    summary = _build_requester_result_summary(
                        approval_outcome=approval_outcome,
                        replies=replies,
                    )
                    file_title = str(getattr(approval_outcome, "file_title", "") or "").strip()
                    download_url = _build_requester_result_download_url(approval_outcome=approval_outcome)
                    show_download_button = _build_requester_result_show_download_button(download_url=download_url)
                    notify_result = requester_result_card_notifier.notify(
                        request_id=str(approval_outcome.request_id or ""),
                        requester_user_id=str(approval_outcome.requester_sender_id or ""),
                        approval_status=str(approval_outcome.status or ""),
                        summary=summary,
                        file_title=file_title,
                        download_url=download_url,
                        show_download_button=show_download_button,
                    )
                    if not notify_result.success:
                        obs_logger.warning(
                            "requester result push failed: request_id=%s action=%s callback_user_id=%s requester_user_id=%s reason=%s",
                            str(approval_outcome.request_id or ""),
                            action_name,
                            callback_user_id,
                            str(approval_outcome.requester_sender_id or "") or "unknown",
                            notify_result.reason,
                        )
                return sdk.AckMessage.STATUS_OK, response_payload
            except ValueError as exc:
                status_code = 400
                event = "stream_card_callback_rejected"
                explicit_error_category = "client_error"
                return sdk.AckMessage.STATUS_BAD_REQUEST, {
                    "error": str(exc),
                }
            except Exception:
                status_code = 500
                event = "stream_card_callback_failed"
                explicit_error_category = "dependency_error"
                obs_logger.exception("card callback failed")
                return sdk.AckMessage.STATUS_SYSTEM_EXCEPTION, {
                    "error": "card callback failed",
                }
            finally:
                elapsed_ms = round((perf_counter() - started) * 1000, 3)
                obs_logger.info(
                    "stream.callback",
                    extra={
                        "obs": {
                            "module": "integrations.dingtalk.stream",
                            "trace_id": trace_id,
                            "user_id": outcome.get("user_context", {}).get("user_id", "unknown")
                            if "outcome" in locals()
                            else "unknown",
                            "dept_id": outcome.get("user_context", {}).get("dept_id", "unknown")
                            if "outcome" in locals()
                            else "unknown",
                            "intent": outcome.get("intent", "other") if "outcome" in locals() else "other",
                            "identity_source": outcome.get("user_context", {}).get("identity_source", "event_fallback")
                            if "outcome" in locals()
                            else "event_fallback",
                            "is_degraded": outcome.get("user_context", {}).get("is_degraded", True)
                            if "outcome" in locals()
                            else True,
                            "source_ids": outcome.get("source_ids", []) if "outcome" in locals() else [],
                            "permission_decision": outcome.get("permission_decision", "allow")
                            if "outcome" in locals()
                            else "allow",
                            "knowledge_version": outcome.get("knowledge_version", "")
                            if "outcome" in locals()
                            else "",
                            "answered_at": outcome.get("answered_at", "") if "outcome" in locals() else "",
                            "llm_trace": outcome.get("llm_trace", {}) if "outcome" in locals() else {},
                            "event": event,
                            "path": getattr(callback_message.headers, "topic", "/v1.0/card/instances/callback"),
                            "method": "STREAM_CALLBACK",
                            "status_code": status_code,
                            "duration_ms": elapsed_ms,
                            "error_category": _resolve_error_category(status_code, explicit_error_category),
                        }
                    },
                )
                reset_trace_id(token)

    client = sdk.DingTalkStreamClient(sdk.Credential(credentials.client_id, credentials.client_secret), logger=sdk_logger)
    client.register_callback_handler(
        getattr(sdk.ChatbotMessage, "TOPIC", DEFAULT_CHATBOT_TOPIC),
        ChatMessageCallbackHandler(),
    )
    card_callback_topic = getattr(callback_handler_base, "TOPIC_CARD_CALLBACK", "/v1.0/card/instances/callback")
    client.register_callback_handler(card_callback_topic, CardCallbackHandler())
    return client


def run_stream_client_forever(
    credentials: DingTalkStreamCredentials,
    *,
    single_chat_service: SingleChatService | None = None,
    user_context_resolver: UserContextResolver | None = None,
    stream_logger: logging.Logger | None = None,
    observability_logger: logging.Logger | None = None,
) -> None:
    client = build_stream_client(
        credentials,
        single_chat_service=single_chat_service,
        user_context_resolver=user_context_resolver,
        stream_logger=stream_logger,
        observability_logger=observability_logger,
    )
    client.start_forever()
