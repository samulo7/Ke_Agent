from __future__ import annotations

import importlib
import logging
from dataclasses import dataclass
from time import perf_counter
from typing import Any, Mapping, Protocol
from uuid import uuid4

from app.core.trace_context import reset_trace_id, set_trace_id
from app.integrations.dingtalk.stream_parser import parse_stream_event
from app.services.single_chat import SingleChatService

DEFAULT_STREAM_ENDPOINT = "https://api.dingtalk.com/v1.0/gateway/connections/open"
DEFAULT_CHATBOT_TOPIC = "/v1.0/im/bot/messages/get"
OBS_LOGGER_NAME = "keagent.observability"


class StreamRuntimeError(RuntimeError):
    """Raised when stream runtime bootstrap/configuration fails."""


@dataclass(frozen=True)
class DingTalkStreamCredentials:
    client_id: str
    client_secret: str
    agent_id: str
    stream_endpoint: str = DEFAULT_STREAM_ENDPOINT


class ReplySender(Protocol):
    def send_text(self, text: str) -> None: ...

    def send_interactive_card(self, card_payload: Mapping[str, Any]) -> None: ...


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


def _extract_card_text_lines(card_payload: Mapping[str, Any]) -> tuple[str, list[str]]:
    title = str(card_payload.get("title") or "Agent Reply Card")
    lines: list[str] = []

    summary = card_payload.get("summary")
    if isinstance(summary, str) and summary.strip():
        lines.append(summary.strip())

    question = card_payload.get("question")
    if isinstance(question, str) and question.strip():
        lines.append(f"Question: {question.strip()}")

    steps = card_payload.get("steps")
    if isinstance(steps, list):
        for index, step in enumerate(steps, start=1):
            if isinstance(step, str) and step.strip():
                lines.append(f"{index}. {step.strip()}")

    draft_fields = card_payload.get("draft_fields")
    if isinstance(draft_fields, Mapping):
        lines.append("Draft fields:")
        for key, value in draft_fields.items():
            lines.append(f"- {key}: {value}")

    note = card_payload.get("note")
    if isinstance(note, str) and note.strip():
        lines.append(f"Note: {note.strip()}")

    if not lines:
        lines.append("No structured card fields provided.")

    return title, lines


def handle_single_chat_payload(
    payload: Mapping[str, Any],
    *,
    service: SingleChatService,
    sender: ReplySender,
) -> dict[str, Any]:
    incoming_message = parse_stream_event(payload)
    result = service.handle(incoming_message)
    reply = result.reply

    if reply.channel == "text":
        sender.send_text(reply.text or "")
    else:
        sender.send_interactive_card(reply.interactive_card or {})

    return {
        "handled": result.handled,
        "reason": result.reason,
        "channel": reply.channel,
    }


def _resolve_error_category(status_code: int, explicit: str | None) -> str | None:
    if explicit:
        return explicit
    if 400 <= status_code < 500:
        return "client_error"
    if status_code >= 500:
        return "server_error"
    return None


def _load_dingtalk_sdk() -> tuple[Any, Any, Any]:
    try:
        sdk = importlib.import_module("dingtalk_stream")
        stream_module = importlib.import_module("dingtalk_stream.stream")
        card_module = importlib.import_module("dingtalk_stream.interactive_card")
    except ModuleNotFoundError as exc:
        raise StreamRuntimeError(
            "dingtalk-stream package is not installed. Run: python -m pip install dingtalk-stream"
        ) from exc
    return sdk, stream_module, card_module


class _SdkReplySender:
    def __init__(self, *, handler: Any, incoming_message: Any, card_module: Any) -> None:
        self._handler = handler
        self._incoming_message = incoming_message
        self._card_module = card_module

    def send_text(self, text: str) -> None:
        content = text.strip() or "Message received."
        self._handler.reply_text(content, self._incoming_message)

    def send_interactive_card(self, card_payload: Mapping[str, Any]) -> None:
        title, lines = _extract_card_text_lines(card_payload)
        card_data = self._card_module.generate_multi_text_line_card_data(title=title, logo="", texts=lines)
        card_biz_id = self._handler.reply_card(card_data=card_data, incoming_message=self._incoming_message)
        if card_biz_id:
            return

        markdown = "\n\n".join(lines)
        self._handler.reply_markdown(title=title, text=markdown, incoming_message=self._incoming_message)


def build_stream_client(
    credentials: DingTalkStreamCredentials,
    *,
    single_chat_service: SingleChatService | None = None,
    stream_logger: logging.Logger | None = None,
    observability_logger: logging.Logger | None = None,
) -> Any:
    sdk, stream_module, card_module = _load_dingtalk_sdk()
    stream_module.DingTalkStreamClient.OPEN_CONNECTION_API = credentials.stream_endpoint

    service = single_chat_service or SingleChatService()
    sdk_logger = stream_logger or logging.getLogger("keagent.dingtalk.stream")
    obs_logger = observability_logger or logging.getLogger(OBS_LOGGER_NAME)

    class SingleChatCallbackHandler(sdk.ChatbotHandler):
        async def process(self, callback_message: Any):  # type: ignore[override]
            trace_id = getattr(callback_message.headers, "message_id", "") or str(uuid4())
            token = set_trace_id(trace_id)
            started = perf_counter()
            status_code = 200
            event = "stream_callback_completed"
            explicit_error_category: str | None = None

            try:
                incoming_message = sdk.ChatbotMessage.from_dict(callback_message.data)
                sender = _SdkReplySender(
                    handler=self,
                    incoming_message=incoming_message,
                    card_module=card_module,
                )
                outcome = handle_single_chat_payload(
                    incoming_message.to_dict(),
                    service=service,
                    sender=sender,
                )
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

    client = sdk.DingTalkStreamClient(sdk.Credential(credentials.client_id, credentials.client_secret), logger=sdk_logger)
    client.register_callback_handler(
        getattr(sdk.ChatbotMessage, "TOPIC", DEFAULT_CHATBOT_TOPIC),
        SingleChatCallbackHandler(),
    )
    return client


def run_stream_client_forever(
    credentials: DingTalkStreamCredentials,
    *,
    single_chat_service: SingleChatService | None = None,
    stream_logger: logging.Logger | None = None,
    observability_logger: logging.Logger | None = None,
) -> None:
    client = build_stream_client(
        credentials,
        single_chat_service=single_chat_service,
        stream_logger=stream_logger,
        observability_logger=observability_logger,
    )
    client.start_forever()
