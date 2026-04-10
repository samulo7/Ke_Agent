from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
import logging
import os
import re
from time import sleep, time
from typing import Any, Protocol

import requests

from app.integrations.dingtalk.openapi_identity import LEGACY_DINGTALK_BASE
from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.reimbursement import ReimbursementAttachmentProcessResult
from app.services.reimbursement_request import ReimbursementAttachmentProcessor

DEFAULT_OPENAPI_ENDPOINT = "https://api.dingtalk.com"
_NUMERIC_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")
_CHINESE_AMOUNT_HINT_PATTERN = re.compile(r"[零〇○一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億圆元整正角分]")
_AMOUNT_TEXT_CLEAN_PATTERN = re.compile(r"[人民币圆元整正\.\s,，。；;:：\-\(\)（）]")
_CHINESE_DIGITS_MAP = {
    "零": 0,
    "〇": 0,
    "○": 0,
    "一": 1,
    "壹": 1,
    "二": 2,
    "贰": 2,
    "两": 2,
    "兩": 2,
    "三": 3,
    "叁": 3,
    "四": 4,
    "肆": 4,
    "五": 5,
    "伍": 5,
    "六": 6,
    "陆": 6,
    "七": 7,
    "柒": 7,
    "八": 8,
    "捌": 8,
    "九": 9,
    "玖": 9,
}
_CHINESE_SMALL_UNITS = {"十": 10, "拾": 10, "百": 100, "佰": 100, "千": 1000, "仟": 1000}
_CHINESE_SECTION_UNITS = {"万": 10000, "萬": 10000, "亿": 100000000, "億": 100000000}
_SCREENSHOT_FIELDS_SYSTEM_PROMPT = (
    "你是报销单截图字段抽取器。"
    "输入是一张单张完整报销单截图。"
    "你只能输出JSON对象，字段必须是 department_label, department, amount, amount_row_header, amount_col_header, evidence。"
    "department_label 仅返回截图中部门值左侧标签原文；department 返回该标签对应的值。"
    "amount 仅返回 合计行 与 合计金额列 交叉单元格里的数字字符串；"
    "amount_row_header 返回该金额所在行表头原文；amount_col_header 返回该金额所在列表头原文。"
    "evidence 返回对象，至少包含 template, department, amount 三个字段，值为简短中文证据摘要；缺失时返回空字符串。"
)


def _detect_image_format(image_bytes: bytes) -> str:
    if image_bytes.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if image_bytes.startswith(b"\xff\xd8\xff"):
        return "jpeg"
    if image_bytes.startswith((b"GIF87a", b"GIF89a")):
        return "gif"
    if image_bytes.startswith(b"BM"):
        return "bmp"
    if image_bytes.startswith(b"RIFF") and image_bytes[8:12] == b"WEBP":
        return "webp"
    return "unknown"


def _image_signature(image_bytes: bytes, *, prefix_length: int = 12) -> str:
    if not image_bytes:
        return ""
    return image_bytes[:prefix_length].hex()


def _is_json_payload(content: bytes) -> bool:
    stripped = content.lstrip()
    return stripped.startswith(b"{") or stripped.startswith(b"[")


class ReimbursementScreenshotFieldExtractor(Protocol):
    def extract(
        self,
        *,
        image_bytes: bytes,
        conversation_id: str,
        sender_id: str,
    ) -> dict[str, Any]: ...


class QwenReimbursementScreenshotFieldExtractor:
    def __init__(
        self,
        *,
        llm_client: QwenChatClient,
        model: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self._llm_client = llm_client
        self._model = model.strip() or "qwen-vl-ocr-latest"
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, min(2, int(max_retries)))

    def extract(
        self,
        *,
        image_bytes: bytes,
        conversation_id: str,
        sender_id: str,
    ) -> dict[str, Any]:
        image_payload = base64.b64encode(image_bytes).decode("ascii")
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": (
                            "请识别这张差旅报销单截图，只抽取模板中的部门和合计金额。"
                            "部门只能取标签‘部门/所属部门’右侧的值；"
                            "金额只能取‘合计’行与‘合计金额’列交叉处的数字。"
                            f"会话: {conversation_id}；用户: {sender_id}。"
                        ),
                    },
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{image_payload}"},
                    },
                ],
            }
        ]
        return self._llm_client.generate_json_from_messages(
            model=self._model,
            system_prompt=_SCREENSHOT_FIELDS_SYSTEM_PROMPT,
            messages=messages,
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )


@dataclass(frozen=True)
class ReimbursementAttachmentSettings:
    enabled: bool
    attachment_mode: str
    vision_model: str
    openapi_endpoint: str
    legacy_openapi_endpoint: str
    upload_media_type: str


@dataclass(frozen=True)
class _ScreenshotBytesResolution:
    content: bytes = b""
    reason: str = ""
    failure_stage: str = ""
    failure_category: str = ""
    diagnostics: dict[str, Any] = field(default_factory=dict)


class DingTalkReimbursementAttachmentProcessor(ReimbursementAttachmentProcessor):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: ReimbursementAttachmentSettings,
        screenshot_field_extractor: ReimbursementScreenshotFieldExtractor | None = None,
        timeout_seconds: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._settings = settings
        self._screenshot_field_extractor = screenshot_field_extractor
        self._timeout_seconds = timeout_seconds
        self._logger = logger or logging.getLogger("keagent.observability")
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def process(
        self,
        *,
        message: IncomingChatMessage,
        conversation_id: str,
        sender_id: str,
    ) -> ReimbursementAttachmentProcessResult:
        if not self._settings.enabled:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="截图识别能力未启用，请联系管理员检查配置。",
            )
        if (message.message_type or "").strip().lower() != "picture":
            return self._failed_screenshot_result(
                reason="请发送单张完整报销单截图。",
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        self._logger.info(
            "reimbursement.screenshot.parse_started",
            extra={
                "obs": {
                    "module": "integrations.dingtalk.reimbursement_attachment",
                    "event": "reimbursement_screenshot_parse_started",
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "message_type": message.message_type,
                }
            },
        )
        file_bytes_resolution = self._resolve_file_bytes(
            message=message,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        if not file_bytes_resolution.content:
            return self._failed_screenshot_result(
                reason=file_bytes_resolution.reason or "未获取到截图内容，请重新发送单张完整报销单截图。",
                conversation_id=conversation_id,
                sender_id=sender_id,
                diagnostics=file_bytes_resolution.diagnostics,
            )
        if self._screenshot_field_extractor is None:
            return self._failed_screenshot_result(
                reason="当前未启用截图识别能力，请联系管理员检查配置。",
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        parse_result = self._parse_screenshot_fields(
            image_bytes=file_bytes_resolution.content,
            conversation_id=conversation_id,
            sender_id=sender_id,
            diagnostics=file_bytes_resolution.diagnostics,
        )
        if not parse_result.success:
            return self._failed_screenshot_result(
                reason=parse_result.reason,
                extraction_evidence=parse_result.extraction_evidence,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        attachment_media_id = self._resolve_attachment_media_id(message=message, file_bytes=file_bytes_resolution.content)
        if not attachment_media_id:
            return self._failed_screenshot_result(
                reason="原截图上传失败，请稍后重试。",
                extraction_evidence=parse_result.extraction_evidence,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        return ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department=parse_result.department,
            amount=parse_result.amount,
            attachment_media_id=attachment_media_id,
            table_amount=parse_result.amount,
            uppercase_amount_text="",
            uppercase_amount_raw="",
            uppercase_amount_numeric="",
            amount_conflict=False,
            amount_conflict_note="",
            amount_source="screenshot",
            amount_source_note="截图识别成功，待人工确认",
            extraction_evidence=parse_result.extraction_evidence,
        )

    def _failed_screenshot_result(
        self,
        *,
        reason: str,
        extraction_evidence: Mapping[str, Any] | None = None,
        conversation_id: str,
        sender_id: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> ReimbursementAttachmentProcessResult:
        evidence = dict(extraction_evidence or {})
        if diagnostics:
            evidence["byte_resolution"] = dict(diagnostics)
        self._logger.warning(
            "reimbursement.screenshot.parse_failed",
            extra={
                "obs": {
                    "module": "integrations.dingtalk.reimbursement_attachment",
                    "event": "reimbursement_screenshot_parse_failed",
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "reason": reason,
                    "extraction_evidence": evidence,
                    "failure_stage": str(evidence.get("byte_resolution", {}).get("failure_stage", "")),
                    "failure_category": str(evidence.get("byte_resolution", {}).get("failure_category", "")),
                }
            },
        )
        return ReimbursementAttachmentProcessResult(
            success=False,
            reason=reason,
            extraction_evidence=evidence,
        )

    def _parse_screenshot_fields(
        self,
        *,
        image_bytes: bytes,
        conversation_id: str,
        sender_id: str,
        diagnostics: Mapping[str, Any] | None = None,
    ) -> ReimbursementAttachmentProcessResult:
        extractor_name = type(self._screenshot_field_extractor).__name__ if self._screenshot_field_extractor is not None else ""
        extractor_diagnostics = {
            **dict(diagnostics or {}),
            "source_used": str((diagnostics or {}).get("source_used") or ""),
            "image_byte_length": len(image_bytes),
            "image_format": _detect_image_format(image_bytes),
            "image_signature": _image_signature(image_bytes),
            "extractor": extractor_name,
        }
        try:
            payload = self._screenshot_field_extractor.extract(  # type: ignore[union-attr]
                image_bytes=image_bytes,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
        except Exception as exc:
            self._logger.exception(
                "reimbursement.screenshot.extractor_failed",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.reimbursement_attachment",
                        "event": "reimbursement_screenshot_extractor_failed",
                        "conversation_id": conversation_id,
                        "sender_id": sender_id,
                        "diagnostics": {
                            **extractor_diagnostics,
                            "exception_type": type(exc).__name__,
                            "exception_message": str(exc),
                        },
                    }
                },
            )
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="截图识别失败，请重新发送单张完整报销单截图。",
                extraction_evidence={"extractor_diagnostics": extractor_diagnostics},
            )

        department_label = _normalize_label_text(str(payload.get("department_label") or ""))
        department = _sanitize_department_value(str(payload.get("department") or ""))
        amount = _normalize_amount_text(str(payload.get("amount") or ""))
        amount_row_header = _normalize_label_text(str(payload.get("amount_row_header") or ""))
        amount_col_header = _normalize_label_text(str(payload.get("amount_col_header") or ""))
        evidence_payload = payload.get("evidence")
        evidence_details = dict(evidence_payload) if isinstance(evidence_payload, Mapping) else {}

        department_label_hit = department_label in {"部门", "所属部门"}
        amount_row_hit = "合计" in amount_row_header
        amount_col_hit = "合计金额" in amount_col_header
        suspicious_amount = _looks_like_excel_date_serial(amount)
        template_hit = bool(_normalize_label_text(str(evidence_details.get("template") or "")))
        extraction_evidence = {
            "template_match": {
                "hit": template_hit,
                "evidence": str(evidence_details.get("template") or "").strip(),
            },
            "department_match": {
                "label": department_label,
                "value": department,
                "hit": department_label_hit and bool(department),
                "evidence": str(evidence_details.get("department") or "").strip(),
            },
            "amount_match": {
                "amount": amount,
                "row_header": amount_row_header,
                "col_header": amount_col_header,
                "row_hit": amount_row_hit,
                "col_hit": amount_col_hit,
                "excel_date_serial_suspected": suspicious_amount,
                "evidence": str(evidence_details.get("amount") or "").strip(),
            },
        }

        if not department_label_hit or not department:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到有效部门，请重新发送单张完整报销单截图。",
                extraction_evidence=extraction_evidence,
            )
        if not amount or suspicious_amount:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到有效合计金额，请重新发送单张完整报销单截图。",
                extraction_evidence=extraction_evidence,
            )
        if not amount_row_hit or not amount_col_hit:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="金额定位未命中“合计 × 合计金额”，请重新发送单张完整报销单截图。",
                extraction_evidence=extraction_evidence,
            )

        return ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department=department,
            amount=amount,
            extraction_evidence=extraction_evidence,
        )

    def _resolve_attachment_media_id(self, *, message: IncomingChatMessage, file_bytes: bytes) -> str:
        media_id = (message.file_media_id or "").strip()
        if media_id:
            return media_id
        filename, content_type = self._resolve_upload_metadata(message=message)
        return self._upload_binary_and_get_media_id(file_bytes=file_bytes, filename=filename, content_type=content_type)

    @staticmethod
    def _resolve_upload_metadata(*, message: IncomingChatMessage) -> tuple[str, str]:
        file_name = (message.file_name or "").strip()
        lowered = file_name.lower()
        if lowered.endswith(".jpg") or lowered.endswith(".jpeg"):
            return file_name or "reimbursement_screenshot.jpg", "image/jpeg"
        if lowered.endswith(".webp"):
            return file_name or "reimbursement_screenshot.webp", "image/webp"
        return file_name or "reimbursement_screenshot.png", "image/png"

    def _resolve_file_bytes(
        self,
        *,
        message: IncomingChatMessage,
        conversation_id: str = "",
        sender_id: str = "",
    ) -> _ScreenshotBytesResolution:
        encoded = (message.file_content_base64 or "").strip()
        download_url = (message.file_download_url or "").strip()
        download_code = (message.file_download_code or "").strip()
        robot_code = (message.robot_code or "").strip()
        source_path = (message.file_source_path or "").strip()
        diagnostics = {
            "source_order": "inline_base64>inline_url>download_code",
            "has_base64": bool(encoded),
            "has_download_url": bool(download_url),
            "has_download_code": bool(download_code),
            "has_robot_code": bool(robot_code),
            "source_path": source_path,
        }
        inline_url_failure_category = ""
        inline_url_status_code = 0

        if encoded:
            try:
                decoded = base64.b64decode(encoded, validate=True)
                if decoded:
                    return _ScreenshotBytesResolution(
                        content=decoded,
                        diagnostics={**diagnostics, "source_used": "inline_base64"},
                    )
            except Exception:
                self._logger.warning("reimbursement.attachment.base64_decode_failed")
                if not download_url and not download_code:
                    return self._screenshot_bytes_failed(
                        reason="消息内截图内容格式异常，请重新发送单张完整报销单截图。",
                        failure_stage="inline_base64_decode",
                        failure_category="inline_base64_invalid",
                        diagnostics={**diagnostics, "source_used": "inline_base64"},
                        conversation_id=conversation_id,
                        sender_id=sender_id,
                    )
                diagnostics = {**diagnostics, "inline_base64_invalid": True}

        if download_url:
            try:
                response = requests.get(download_url, timeout=self._timeout_seconds)
                response.raise_for_status()
                content = bytes(response.content or b"")
                if content:
                    return _ScreenshotBytesResolution(
                        content=content,
                        diagnostics={**diagnostics, "source_used": "inline_url"},
                    )
                return self._screenshot_bytes_failed(
                    reason="消息内截图内容为空，请重新发送单张完整报销单截图。",
                    failure_stage="inline_url_fetch",
                    failure_category="inline_url_empty_content",
                    diagnostics={**diagnostics, "source_used": "inline_url"},
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )
            except requests.RequestException as exc:
                self._logger.exception("reimbursement.attachment.download_failed")
                inline_url_failure_category = self._classify_inline_url_failure(exc)
                inline_url_status_code = self._extract_status_code(exc)
                diagnostics = {
                    **diagnostics,
                    "inline_url_failure_category": inline_url_failure_category,
                    "inline_url_status_code": inline_url_status_code,
                }

        if download_code:
            if not robot_code:
                return self._screenshot_bytes_failed(
                    reason="截图下载参数缺失，请重发单张完整报销单截图。",
                    failure_stage="download_code_prepare",
                    failure_category="download_code_missing_robot_code",
                    diagnostics={**diagnostics, "source_used": "download_code"},
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )

            return self._resolve_file_bytes_by_download_code(
                download_code=download_code,
                robot_code=robot_code,
                diagnostics=diagnostics,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        if inline_url_failure_category:
            return self._screenshot_bytes_failed(
                reason=self._resolve_inline_url_failure_reason(failure_category=inline_url_failure_category),
                failure_stage="inline_url_fetch",
                failure_category=inline_url_failure_category,
                diagnostics={
                    **diagnostics,
                    "source_used": "inline_url",
                    "status_code": inline_url_status_code,
                },
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        return self._screenshot_bytes_failed(
            reason="未获取到消息内截图内容，请直接发送原始截图图片（请发送单张完整报销单截图，不要转发或发送文件）。",
            failure_stage="inline_message_payload",
            failure_category="missing_inline_image_source",
            diagnostics=diagnostics,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    def _screenshot_bytes_failed(
        self,
        *,
        reason: str,
        failure_stage: str,
        failure_category: str,
        diagnostics: Mapping[str, Any],
        conversation_id: str,
        sender_id: str,
    ) -> _ScreenshotBytesResolution:
        payload = {
            "failure_stage": failure_stage,
            "failure_category": failure_category,
            **dict(diagnostics),
        }
        self._logger.warning(
            "reimbursement.screenshot.bytes_resolve_failed",
            extra={
                "obs": {
                    "module": "integrations.dingtalk.reimbursement_attachment",
                    "event": "reimbursement_screenshot_bytes_resolve_failed",
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                    "reason": reason,
                    "failure_stage": failure_stage,
                    "failure_category": failure_category,
                    "diagnostics": payload,
                }
            },
        )
        return _ScreenshotBytesResolution(
            content=b"",
            reason=reason,
            failure_stage=failure_stage,
            failure_category=failure_category,
            diagnostics=payload,
        )

    def _resolve_file_bytes_by_download_code(
        self,
        *,
        download_code: str,
        robot_code: str,
        diagnostics: Mapping[str, Any],
        conversation_id: str,
        sender_id: str,
    ) -> _ScreenshotBytesResolution:
        response: requests.Response | None = None
        last_status_code = 0
        max_attempts = 3
        retry_delays = (0.2, 0.5)
        for attempt in range(1, max_attempts + 1):
            try:
                access_token = self._get_access_token()
                response = requests.post(
                    f"{self._settings.openapi_endpoint}/v1.0/robot/messageFiles/download",
                    headers={
                        "Content-Type": "application/json",
                        "Accept": "application/json",
                        "x-acs-dingtalk-access-token": access_token,
                    },
                    json={"downloadCode": download_code, "robotCode": robot_code},
                    timeout=self._timeout_seconds,
                )
                response.raise_for_status()
                break
            except requests.RequestException as exc:
                status_code = self._extract_status_code(exc)
                last_status_code = status_code
                should_retry = status_code in {0, 429, 500, 502, 503, 504}
                if attempt < max_attempts and should_retry:
                    self._logger.warning(
                        "reimbursement.attachment.download_code_request_retry",
                        extra={
                            "obs": {
                                "module": "integrations.dingtalk.reimbursement_attachment",
                                "event": "reimbursement_attachment_download_code_request_retry",
                                "conversation_id": conversation_id,
                                "sender_id": sender_id,
                                "attempt": attempt,
                                "max_attempts": max_attempts,
                                "status_code": status_code,
                                "download_code_retry": True,
                            }
                        },
                    )
                    sleep(retry_delays[min(attempt - 1, len(retry_delays) - 1)])
                    continue

                self._logger.exception("reimbursement.attachment.download_code_request_failed")
                failure_category = self._classify_download_code_failure(status_code=status_code)
                return self._screenshot_bytes_failed(
                    reason=self._resolve_download_code_failure_reason(failure_category=failure_category),
                    failure_stage="download_code_request",
                    failure_category=failure_category,
                    diagnostics={
                        **dict(diagnostics),
                        "source_used": "download_code",
                        "status_code": status_code,
                        "retry_attempts": attempt,
                    },
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )
            except Exception:
                self._logger.exception("reimbursement.attachment.download_code_unexpected_error")
                return self._screenshot_bytes_failed(
                    reason="截图下载失败，请稍后重试。",
                    failure_stage="download_code_request",
                    failure_category="download_code_transport_error",
                    diagnostics={**dict(diagnostics), "source_used": "download_code"},
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )

        if response is None:
            return self._screenshot_bytes_failed(
                reason="截图下载失败，请稍后重试。",
                failure_stage="download_code_request",
                failure_category="download_code_transport_error",
                diagnostics={
                    **dict(diagnostics),
                    "source_used": "download_code",
                    "status_code": last_status_code,
                    "retry_attempts": max_attempts,
                },
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        response_headers = getattr(response, "headers", {}) or {}
        content_type = str(
            response_headers.get("Content-Type") or response_headers.get("content-type") or ""
        ).split(";", 1)[0].strip().lower()
        content = bytes(response.content or b"")
        if content and not (content_type == "application/json" or _is_json_payload(content)):
            return _ScreenshotBytesResolution(
                content=content,
                diagnostics={**dict(diagnostics), "source_used": "download_code", "response_content_type": content_type},
            )

        # Defensive fallback: some environments may return JSON payload with an inline url/content.
        payload = {}
        try:
            payload = response.json()
        except Exception:
            payload = {}
        if not isinstance(payload, Mapping):
            payload = {}

        inline_base64 = str(
            payload.get("fileContentBase64")
            or payload.get("contentBase64")
            or payload.get("content")
            or ""
        ).strip()
        if inline_base64:
            try:
                decoded = base64.b64decode(inline_base64, validate=True)
                if decoded:
                    return _ScreenshotBytesResolution(
                        content=decoded,
                        diagnostics={**dict(diagnostics), "source_used": "download_code_json_base64"},
                    )
            except Exception:
                self._logger.warning("reimbursement.attachment.download_code_json_base64_decode_failed")

        nested_download_url = str(
            payload.get("downloadUrl")
            or payload.get("download_url")
            or payload.get("url")
            or ""
        ).strip()
        if nested_download_url:
            try:
                nested_response = requests.get(nested_download_url, timeout=self._timeout_seconds)
                nested_response.raise_for_status()
                nested_content = bytes(nested_response.content or b"")
                if nested_content:
                    return _ScreenshotBytesResolution(
                        content=nested_content,
                        diagnostics={**dict(diagnostics), "source_used": "download_code_json_url"},
                    )
            except requests.RequestException as exc:
                self._logger.exception("reimbursement.attachment.download_code_json_url_failed")
                status_code = self._extract_status_code(exc)
                failure_category = self._classify_download_code_failure(status_code=status_code)
                return self._screenshot_bytes_failed(
                    reason=self._resolve_download_code_failure_reason(failure_category=failure_category),
                    failure_stage="download_code_json_url_fetch",
                    failure_category=failure_category,
                    diagnostics={
                        **dict(diagnostics),
                        "source_used": "download_code_json_url",
                        "status_code": status_code,
                    },
                    conversation_id=conversation_id,
                    sender_id=sender_id,
                )

        return self._screenshot_bytes_failed(
            reason="截图下载内容为空，请重发单张完整报销单截图。",
            failure_stage="download_code_response",
            failure_category="download_code_empty_content",
            diagnostics={**dict(diagnostics), "source_used": "download_code"},
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    @staticmethod
    def _extract_status_code(exc: requests.RequestException) -> int:
        response = getattr(exc, "response", None)
        if response is None:
            return 0
        try:
            return int(getattr(response, "status_code", 0) or 0)
        except Exception:
            return 0

    @classmethod
    def _classify_inline_url_failure(cls, exc: requests.RequestException) -> str:
        status_code = cls._extract_status_code(exc)
        if status_code in {401, 403}:
            return "inline_url_expired_or_unauthorized"
        return "inline_url_transport_error"

    @staticmethod
    def _classify_download_code_failure(*, status_code: int) -> str:
        if status_code in {401, 403}:
            return "download_code_auth_error"
        if status_code in {400, 404}:
            return "download_code_expired_or_invalid"
        return "download_code_transport_error"

    @staticmethod
    def _resolve_inline_url_failure_reason(*, failure_category: str) -> str:
        mapping = {
            "inline_url_expired_or_unauthorized": "消息内截图链接已失效，请重发单张完整报销单截图。",
            "inline_url_transport_error": "截图下载失败，请稍后重试。",
            "inline_url_empty_content": "消息内截图内容为空，请重新发送单张完整报销单截图。",
        }
        return mapping.get(failure_category, "截图下载失败，请稍后重试。")

    @staticmethod
    def _resolve_download_code_failure_reason(*, failure_category: str) -> str:
        mapping = {
            "download_code_auth_error": "截图下载鉴权失败，请重发单张完整报销单截图。",
            "download_code_expired_or_invalid": "截图下载码已失效，请重发单张完整报销单截图。",
            "download_code_transport_error": "截图下载失败，请稍后重试。",
            "download_code_empty_content": "截图下载内容为空，请重发单张完整报销单截图。",
            "download_code_missing_robot_code": "截图下载参数缺失，请重发单张完整报销单截图。",
        }
        return mapping.get(failure_category, "截图下载失败，请稍后重试。")

    def _upload_binary_and_get_media_id(self, *, file_bytes: bytes, filename: str, content_type: str) -> str:
        if not file_bytes:
            return ""
        try:
            access_token = self._get_access_token()
            response = requests.post(
                f"{self._settings.legacy_openapi_endpoint}/media/upload",
                params={"access_token": access_token, "type": self._settings.upload_media_type},
                files={"media": (filename, file_bytes, content_type)},
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            self._logger.exception("reimbursement.attachment.media_upload_failed")
            return ""
        except ValueError:
            self._logger.exception("reimbursement.attachment.media_upload_invalid_json")
            return ""
        return str(payload.get("media_id") or payload.get("mediaId") or "").strip()

    def _get_access_token(self) -> str:
        now = time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        response = requests.post(
            f"{self._settings.openapi_endpoint}/v1.0/oauth2/accessToken",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"appKey": self._client_id, "appSecret": self._client_secret},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = str(payload.get("accessToken") or "").strip()
        expire_in = int(payload.get("expireIn") or 7200)
        if not access_token:
            raise RuntimeError("OpenAPI access token is empty")
        self._access_token = access_token
        self._access_token_expires_at = time() + max(expire_in - 300, 60)
        return self._access_token


def _normalize_label_text(value: str) -> str:
    normalized = "".join((value or "").strip().split()).replace("：", ":")
    return normalized.rstrip(":")


def _sanitize_department_value(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    cleaned = text.replace("：", ":").strip(" :：")
    return "".join(cleaned.split())


def _looks_like_excel_date_serial(value: str) -> bool:
    normalized = _normalize_amount_text(value)
    if not normalized:
        return False
    if "." in normalized:
        return False
    try:
        number = int(normalized)
    except ValueError:
        return False
    return 30000 <= number <= 60000


def _normalize_amount_text(value: str) -> str:
    raw = str(value or "").strip()
    if not raw:
        return ""

    # Prefer Arabic numerics when present.
    numeric_match = _NUMERIC_PATTERN.search(raw.replace(",", ""))
    if numeric_match is not None:
        return _format_numeric_string(numeric_match.group(0))

    if not _CHINESE_AMOUNT_HINT_PATTERN.search(raw):
        return ""

    normalized = _AMOUNT_TEXT_CLEAN_PATTERN.sub("", raw)
    if not normalized:
        return ""

    # Remove trailing non-numeric Chinese units if any remain.
    normalized = normalized.replace("角", "").replace("分", "")
    parsed = _parse_chinese_integer_amount(normalized)
    if parsed is None:
        return ""
    return str(parsed)


def _parse_chinese_integer_amount(value: str) -> int | None:
    total = 0
    section = 0
    number = 0
    seen = False
    for char in value:
        if char in _CHINESE_DIGITS_MAP:
            number = _CHINESE_DIGITS_MAP[char]
            seen = True
            continue
        unit = _CHINESE_SMALL_UNITS.get(char)
        if unit is not None:
            seen = True
            if number == 0:
                number = 1
            section += number * unit
            number = 0
            continue
        section_unit = _CHINESE_SECTION_UNITS.get(char)
        if section_unit is not None:
            seen = True
            section += number
            if section == 0:
                section = 1
            total += section * section_unit
            section = 0
            number = 0
            continue
        return None
    if not seen:
        return None
    return total + section + number


def _format_numeric_string(value: str) -> str:
    normalized = value.replace(",", "").strip()
    try:
        number = float(normalized)
    except ValueError:
        return ""
    if number.is_integer():
        return str(int(number))
    return f"{number:.2f}".rstrip("0").rstrip(".")


def load_reimbursement_attachment_settings(raw_env: Mapping[str, str] | None = None) -> ReimbursementAttachmentSettings:
    env = raw_env if raw_env is not None else os.environ
    enabled = str(env.get("DINGTALK_REIMBURSE_APPROVAL_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    # Keep compatibility with env key while fixing runtime behavior to screenshot-only.
    _ = str(env.get("DINGTALK_REIMBURSE_ATTACHMENT_MODE") or "").strip()
    attachment_mode = "screenshot_only"
    vision_model = str(env.get("DINGTALK_REIMBURSE_VISION_MODEL") or "qwen-vl-ocr-latest").strip() or "qwen-vl-ocr-latest"
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    legacy_openapi_endpoint = str(env.get("DINGTALK_LEGACY_OPENAPI_ENDPOINT") or LEGACY_DINGTALK_BASE).strip()
    media_type = str(env.get("DINGTALK_REIMBURSE_ATTACHMENT_MEDIA_TYPE") or "file").strip() or "file"
    return ReimbursementAttachmentSettings(
        enabled=enabled,
        attachment_mode=attachment_mode,
        vision_model=vision_model,
        openapi_endpoint=openapi_endpoint.rstrip("/") or DEFAULT_OPENAPI_ENDPOINT,
        legacy_openapi_endpoint=legacy_openapi_endpoint.rstrip("/") or LEGACY_DINGTALK_BASE,
        upload_media_type=media_type,
    )


def build_default_reimbursement_attachment_processor(
    raw_env: Mapping[str, str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> ReimbursementAttachmentProcessor | None:
    env = raw_env if raw_env is not None else os.environ
    settings = load_reimbursement_attachment_settings(env)
    if not settings.enabled:
        return None
    client_id = str(env.get("DINGTALK_CLIENT_ID") or "").strip()
    client_secret = str(env.get("DINGTALK_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret:
        obs_logger = logger or logging.getLogger("keagent.observability")
        obs_logger.warning(
            "reimbursement attachment processor disabled: missing DINGTALK_CLIENT_ID or DINGTALK_CLIENT_SECRET"
        )
        return None

    llm_api_key = str(
        env.get("DINGTALK_REIMBURSE_VISION_API_KEY")
        or env.get("LLM_API_KEY")
        or env.get("QWEN_API_KEY")
        or ""
    ).strip()
    screenshot_field_extractor: ReimbursementScreenshotFieldExtractor | None = None
    if llm_api_key:
        endpoint = str(
            env.get("DINGTALK_REIMBURSE_VISION_BASE_URL")
            or env.get("LLM_BASE_URL")
            or env.get("QWEN_BASE_URL")
            or DEFAULT_QWEN_CHAT_ENDPOINT
        ).strip()
        timeout_seconds = _parse_int_env(
            value=str(env.get("LLM_TIMEOUT_SECONDS") or env.get("QWEN_TIMEOUT_SECONDS") or "10"),
            default=10,
            minimum=1,
        )
        max_retries = _parse_int_env(
            value=str(env.get("LLM_MAX_RETRIES") or env.get("QWEN_MAX_RETRIES") or "2"),
            default=2,
            minimum=0,
            maximum=2,
        )
        llm_client = HttpQwenChatClient(api_key=llm_api_key, endpoint=endpoint)
        screenshot_field_extractor = QwenReimbursementScreenshotFieldExtractor(
            llm_client=llm_client,
            model=settings.vision_model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    return DingTalkReimbursementAttachmentProcessor(
        client_id=client_id,
        client_secret=client_secret,
        settings=settings,
        screenshot_field_extractor=screenshot_field_extractor,
        logger=logger,
    )


def _parse_int_env(*, value: str, default: int, minimum: int = 0, maximum: int | None = None) -> int:
    text = (value or "").strip()
    if not text:
        return default
    try:
        parsed = int(text)
    except ValueError:
        return default
    if parsed < minimum:
        return default
    if maximum is not None and parsed > maximum:
        return default
    return parsed
