from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass, field
from io import BytesIO
import logging
import os
import posixpath
import re
from time import time
from typing import Any, Protocol
import xml.etree.ElementTree as ET
import zipfile

import requests

from app.integrations.dingtalk.openapi_identity import LEGACY_DINGTALK_BASE
from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.reimbursement import ReimbursementAttachmentProcessResult
from app.services.reimbursement_request import ReimbursementAttachmentProcessor

DEFAULT_OPENAPI_ENDPOINT = "https://api.dingtalk.com"
_NUMERIC_PATTERN = re.compile(r"[0-9]+(?:\.[0-9]{1,2})?")
_CELL_REFERENCE_PATTERN = re.compile(r"^([A-Z]+)([0-9]+)$")
_DEPARTMENT_INLINE_PATTERN = re.compile(r"(?:所属部门|部门)[:：]\s*([^\s,，;；:：]+)")
_AMOUNT_INLINE_PATTERN = re.compile(r"(?:合计金额|金额\(元\)|金额)[:：]\s*([0-9]+(?:\.[0-9]{1,2})?)")
_UPPERCASE_INLINE_PATTERN = re.compile(
    r"(?:大写金额|金额大写|人民币(?:金额)?大写|人民币大写金额)[:：]\s*([^\s,，;；]+)"
)
_MERGE_RANGE_PATTERN = re.compile(r"^([A-Z]+[0-9]+)(?::([A-Z]+[0-9]+))?$")
_TARGET_SHEET_NAME = "差旅费报销单"
_TARGET_SHEET_MARKER = "差旅费报销单"
_DEPARTMENT_ANCHOR_TOKENS = ("部门", "所属部门")
_TOTAL_AMOUNT_ANCHOR_TOKENS = ("合计", "合计金额", "费用合计", "总计")
_UPPERCASE_AMOUNT_ANCHOR_TOKENS = (
    "大写金额",
    "金额大写",
    "人民币大写",
    "人民币金额大写",
    "人民币大写金额",
)
_CHINESE_AMOUNT_HINT_PATTERN = re.compile(r"[零〇○一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬億元圆整正角分]")
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
_UPPERCASE_AMOUNT_SYSTEM_PROMPT = (
    "你是金额转换器。"
    "输入是中文大写人民币金额。"
    "你只能输出JSON对象，字段必须是 amount。"
    "amount 必须是数字字符串，不要带货币单位，不要输出其他字段。"
    "示例: {\"amount\":\"106\"}"
)
_TABLE_FIELDS_SYSTEM_PROMPT = (
    "你是报销单字段抽取器。"
    "输入是从Excel读取的二维表文本。"
    "你只能输出JSON对象，字段必须是 department, table_amount, uppercase_amount_text。"
    "department 是部门名称字符串；table_amount 是数字字符串；uppercase_amount_text 是中文大写金额字符串。"
    "字段缺失时返回空字符串。"
)


class UppercaseAmountConverter(Protocol):
    def convert(
        self,
        *,
        uppercase_amount_text: str,
        table_amount: str,
        conversation_id: str,
        sender_id: str,
    ) -> str: ...


class ReimbursementTableFieldExtractor(Protocol):
    def extract(
        self,
        *,
        sheet_text: str,
        conversation_id: str,
        sender_id: str,
    ) -> tuple[str, str, str]: ...


class QwenUppercaseAmountConverter:
    def __init__(
        self,
        *,
        llm_client: QwenChatClient,
        model: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self._llm_client = llm_client
        self._model = model.strip() or "qwen-plus"
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, min(2, int(max_retries)))

    def convert(
        self,
        *,
        uppercase_amount_text: str,
        table_amount: str,
        conversation_id: str,
        sender_id: str,
    ) -> str:
        normalized_uppercase = uppercase_amount_text.strip()
        if not normalized_uppercase:
            return ""
        user_prompt = (
            "请把以下中文大写人民币金额转换成数字字符串。\n"
            f"大写金额: {normalized_uppercase}\n"
            f"表格金额参考: {(table_amount or '').strip()}\n"
            f"会话: {conversation_id}\n"
            f"用户: {sender_id}\n"
            "只输出JSON: {\"amount\":\"...\"}"
        )
        payload = self._llm_client.generate_json(
            model=self._model,
            system_prompt=_UPPERCASE_AMOUNT_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        amount = payload.get("amount")
        if isinstance(amount, (int, float)):
            return _normalize_amount_text(str(amount))
        if isinstance(amount, str):
            return _normalize_amount_text(amount)
        return ""


class QwenReimbursementTableFieldExtractor:
    def __init__(
        self,
        *,
        llm_client: QwenChatClient,
        model: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> None:
        self._llm_client = llm_client
        self._model = model.strip() or "qwen-plus"
        self._timeout_seconds = max(1, int(timeout_seconds))
        self._max_retries = max(0, min(2, int(max_retries)))

    def extract(
        self,
        *,
        sheet_text: str,
        conversation_id: str,
        sender_id: str,
    ) -> tuple[str, str, str]:
        prompt = (
            "请从下面的报销单表格文本中抽取字段。\n"
            "要求:\n"
            "1) 优先抽取部门(如 总经办)\n"
            "2) table_amount 优先抽取“合计金额/合计行”的数字，不要抽日期序列号\n"
            "3) uppercase_amount_text 抽取“大写金额”后文本\n"
            f"会话: {conversation_id}\n"
            f"用户: {sender_id}\n\n"
            f"表格文本:\n{sheet_text}\n\n"
            "只输出JSON: {\"department\":\"...\",\"table_amount\":\"...\",\"uppercase_amount_text\":\"...\"}"
        )
        payload = self._llm_client.generate_json(
            model=self._model,
            system_prompt=_TABLE_FIELDS_SYSTEM_PROMPT,
            user_prompt=prompt,
            timeout_seconds=self._timeout_seconds,
            max_retries=self._max_retries,
        )
        department = str(payload.get("department") or "").strip()
        table_amount = _normalize_amount_text(str(payload.get("table_amount") or "").strip())
        uppercase_amount_text = str(payload.get("uppercase_amount_text") or "").strip()
        return department, table_amount, uppercase_amount_text


@dataclass(frozen=True)
class ReimbursementAttachmentSettings:
    enabled: bool
    openapi_endpoint: str
    legacy_openapi_endpoint: str
    upload_media_type: str


@dataclass(frozen=True)
class _ResolvedAmountDecision:
    amount: str
    table_amount: str
    uppercase_amount_raw: str
    uppercase_amount_numeric: str
    amount_conflict: bool
    amount_conflict_note: str
    amount_source: str
    amount_source_note: str


@dataclass(frozen=True)
class _MergedRange:
    start_row: int
    start_col: int
    end_row: int
    end_col: int


@dataclass(frozen=True)
class _SheetModel:
    index: int
    name: str
    path: str
    rows: list[list[str]]
    merged_ranges: tuple[_MergedRange, ...] = ()
    merged_parent: dict[tuple[int, int], tuple[int, int]] = field(default_factory=dict)


@dataclass(frozen=True)
class _ParsedXlsxFields:
    department: str = ""
    table_amount: str = ""
    uppercase_amount_text: str = ""
    error_code: str = ""
    extraction_evidence: dict[str, Any] = field(default_factory=dict)


class DingTalkReimbursementAttachmentProcessor(ReimbursementAttachmentProcessor):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: ReimbursementAttachmentSettings,
        uppercase_amount_converter: UppercaseAmountConverter | None = None,
        table_field_extractor: ReimbursementTableFieldExtractor | None = None,
        timeout_seconds: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._settings = settings
        self._uppercase_amount_converter = uppercase_amount_converter
        self._table_field_extractor = table_field_extractor
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
                reason="附件处理能力未启用，请联系管理员检查配置。",
            )

        file_bytes = self._resolve_file_bytes(message=message)
        if not file_bytes:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未获取到Excel文件内容，请重新上传。",
            )

        parse_result = self._parse_xlsx_fields(
            file_bytes=file_bytes,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        extraction_evidence = dict(parse_result.extraction_evidence)
        if parse_result.error_code == "sheet_not_found":
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未找到差旅费报销单",
                extraction_evidence=extraction_evidence,
            )
        if parse_result.error_code:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="报销单解析失败，请检查文件后重传。",
                extraction_evidence=extraction_evidence,
            )
        if not parse_result.department:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到部门（锚点窗口未命中有效候选）",
                extraction_evidence=extraction_evidence,
            )
        if not parse_result.table_amount:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到合计金额（锚点窗口未命中有效候选）",
                extraction_evidence=extraction_evidence,
            )
        if not parse_result.uppercase_amount_text:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到大写金额（如模板确无字段请人工确认）",
                extraction_evidence=extraction_evidence,
            )
        department = parse_result.department
        table_amount = parse_result.table_amount
        uppercase_amount_text = parse_result.uppercase_amount_text
        amount_decision = self._resolve_final_amount(
            table_amount=table_amount,
            uppercase_amount_text=uppercase_amount_text,
            conversation_id=conversation_id,
            sender_id=sender_id,
            evidence_summary=_build_evidence_summary(extraction_evidence),
        )
        extraction_evidence["amount_validation"] = {
            "table_amount": amount_decision.table_amount,
            "uppercase_amount_raw": amount_decision.uppercase_amount_raw,
            "uppercase_amount_numeric": amount_decision.uppercase_amount_numeric,
            "amount_conflict": amount_decision.amount_conflict,
            "amount_conflict_note": amount_decision.amount_conflict_note,
        }
        if not amount_decision.amount:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到金额，请检查报销单模板后重传。",
                extraction_evidence=extraction_evidence,
            )

        pdf_bytes = self._build_pdf_bytes(
            lines=(
                "Travel Reimbursement Summary",
                f"Department: {department}",
                f"Amount: {amount_decision.amount}",
                "Generated by Ke Agent",
            )
        )
        media_id = self._upload_pdf_and_get_media_id(pdf_bytes=pdf_bytes)
        if not media_id:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="附件上传失败，请稍后重试。",
            )

        return ReimbursementAttachmentProcessResult(
            success=True,
            reason="processed",
            department=department,
            amount=amount_decision.amount,
            attachment_media_id=media_id,
            table_amount=amount_decision.table_amount,
            uppercase_amount_text=uppercase_amount_text,
            uppercase_amount_raw=amount_decision.uppercase_amount_raw,
            uppercase_amount_numeric=amount_decision.uppercase_amount_numeric,
            amount_conflict=amount_decision.amount_conflict,
            amount_conflict_note=amount_decision.amount_conflict_note,
            amount_source=amount_decision.amount_source,
            amount_source_note=amount_decision.amount_source_note,
            extraction_evidence=extraction_evidence,
        )

    def _resolve_file_bytes(self, *, message: IncomingChatMessage) -> bytes:
        encoded = (message.file_content_base64 or "").strip()
        if encoded:
            try:
                return base64.b64decode(encoded)
            except Exception:
                self._logger.warning("reimbursement.attachment.base64_decode_failed")
        download_url = (message.file_download_url or "").strip()
        if download_url:
            try:
                response = requests.get(download_url, timeout=self._timeout_seconds)
                response.raise_for_status()
                return bytes(response.content or b"")
            except requests.RequestException:
                self._logger.exception("reimbursement.attachment.download_failed")
                return b""

        download_code = (message.file_download_code or "").strip()
        robot_code = (message.robot_code or "").strip() or str(os.getenv("DINGTALK_ROBOT_CODE") or "").strip()
        if download_code and robot_code:
            resolved_url = self._resolve_download_url_by_download_code(
                download_code=download_code,
                robot_code=robot_code,
            )
            if resolved_url:
                try:
                    response = requests.get(resolved_url, timeout=self._timeout_seconds)
                    response.raise_for_status()
                    return bytes(response.content or b"")
                except requests.RequestException:
                    self._logger.exception("reimbursement.attachment.download_by_code_failed")
                    return b""
        return b""

    def _resolve_download_url_by_download_code(self, *, download_code: str, robot_code: str) -> str:
        try:
            access_token = self._get_access_token()
            response = requests.post(
                f"{self._settings.openapi_endpoint}/v1.0/robot/messageFiles/download",
                headers={
                    "Content-Type": "application/json",
                    "Accept": "application/json",
                    "x-acs-dingtalk-access-token": access_token,
                },
                json={
                    "downloadCode": download_code,
                    "robotCode": robot_code,
                },
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException:
            self._logger.exception("reimbursement.attachment.resolve_download_url_transport_error")
            return ""
        except ValueError:
            self._logger.exception("reimbursement.attachment.resolve_download_url_invalid_json")
            return ""

        candidates: list[Mapping[str, Any]] = []
        if isinstance(payload, Mapping):
            result = payload.get("result")
            data = payload.get("data")
            if isinstance(result, Mapping):
                candidates.append(result)
            if isinstance(data, Mapping):
                candidates.append(data)
            candidates.append(payload)

        for candidate in candidates:
            value = str(candidate.get("downloadUrl") or candidate.get("download_url") or candidate.get("url") or "").strip()
            if value:
                return value
        return ""

    def _parse_xlsx_fields(
        self,
        *,
        file_bytes: bytes,
        conversation_id: str = "",
        sender_id: str = "",
    ) -> _ParsedXlsxFields:
        try:
            sheets = _extract_xlsx_sheets(file_bytes=file_bytes)
        except Exception:
            self._logger.exception("reimbursement.attachment.xlsx_parse_failed")
            return _ParsedXlsxFields(error_code="xlsx_parse_failed")

        selected_sheet, selection_evidence = _select_target_sheet(sheets=sheets)
        self._logger.info(
            "reimbursement.attachment.sheet_selected",
            extra={
                "obs": {
                    "module": "integrations.dingtalk.reimbursement_attachment",
                    "event": "reimbursement_attachment_sheet_selected",
                    "selected_sheet_index": selection_evidence.get("selected_sheet_index"),
                    "selected_sheet_name": selection_evidence.get("selected_sheet_name"),
                    "fallback_used": selection_evidence.get("fallback_used", False),
                    "marker_matches": len(selection_evidence.get("marker_matches", [])),
                    "conversation_id": conversation_id,
                    "sender_id": sender_id,
                }
            },
        )
        if selected_sheet is None:
            return _ParsedXlsxFields(
                error_code="sheet_not_found",
                extraction_evidence={"sheet_selection": selection_evidence},
            )

        department, table_amount, uppercase_amount_text, field_evidence = _extract_reimbursement_fields_from_sheet(
            sheet=selected_sheet
        )
        merged_hits = _count_merged_hits(field_evidence=field_evidence)
        extraction_evidence: dict[str, Any] = {
            "sheet_selection": selection_evidence,
            "merged_cells": {
                "total_ranges": len(selected_sheet.merged_ranges),
                "candidate_merged_hits": merged_hits,
            },
            "department": field_evidence.get("department", {}),
            "total_amount": field_evidence.get("total_amount", {}),
            "uppercase_amount": field_evidence.get("uppercase_amount", {}),
        }
        for field_name, evidence_key in (
            ("department", "department"),
            ("total_amount", "total_amount"),
            ("uppercase_amount", "uppercase_amount"),
        ):
            _emit_field_extraction_log(
                logger=self._logger,
                field_name=field_name,
                evidence=field_evidence.get(evidence_key, {}),
                conversation_id=conversation_id,
                sender_id=sender_id,
            )

        return _ParsedXlsxFields(
            department=department,
            table_amount=table_amount,
            uppercase_amount_text=uppercase_amount_text,
            extraction_evidence=extraction_evidence,
        )

    def _parse_xlsx_department_and_amount(self, *, file_bytes: bytes) -> tuple[str, str]:
        parsed = self._parse_xlsx_fields(file_bytes=file_bytes)
        return parsed.department, parsed.table_amount

    def _resolve_final_amount(
        self,
        *,
        table_amount: str,
        uppercase_amount_text: str,
        conversation_id: str,
        sender_id: str,
        evidence_summary: Mapping[str, Any] | None = None,
    ) -> _ResolvedAmountDecision:
        normalized_table_amount = _normalize_amount_text(table_amount)
        normalized_uppercase_raw = uppercase_amount_text.strip()
        normalized_uppercase_numeric = self._convert_uppercase_amount(
            uppercase_amount_text=normalized_uppercase_raw,
            table_amount=normalized_table_amount,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

        if normalized_table_amount and normalized_uppercase_numeric:
            if _amounts_mismatch(left=normalized_table_amount, right=normalized_uppercase_numeric):
                note = "合计金额与大写金额不一致，请确认提交金额来源。"
                self._logger.warning(
                    "reimbursement.attachment.amount_mismatch",
                    extra={
                        "obs": {
                            "module": "integrations.dingtalk.reimbursement_attachment",
                            "event": "reimbursement_attachment_amount_mismatch",
                            "table_amount": normalized_table_amount,
                            "uppercase_amount_numeric": normalized_uppercase_numeric,
                            "uppercase_amount_text": normalized_uppercase_raw,
                            "evidence_summary": dict(evidence_summary or {}),
                        }
                    },
                )
                return _ResolvedAmountDecision(
                    amount=normalized_table_amount,
                    table_amount=normalized_table_amount,
                    uppercase_amount_raw=normalized_uppercase_raw,
                    uppercase_amount_numeric=normalized_uppercase_numeric,
                    amount_conflict=True,
                    amount_conflict_note=note,
                    amount_source="table_conflict",
                    amount_source_note="检测到金额冲突，待人工确认",
                )
            return _ResolvedAmountDecision(
                amount=normalized_table_amount,
                table_amount=normalized_table_amount,
                uppercase_amount_raw=normalized_uppercase_raw,
                uppercase_amount_numeric=normalized_uppercase_numeric,
                amount_conflict=False,
                amount_conflict_note="",
                amount_source="table",
                amount_source_note="大写金额校验通过，采用合计金额",
            )

        if normalized_table_amount:
            if normalized_uppercase_raw:
                return _ResolvedAmountDecision(
                    amount=normalized_table_amount,
                    table_amount=normalized_table_amount,
                    uppercase_amount_raw=normalized_uppercase_raw,
                    uppercase_amount_numeric="",
                    amount_conflict=False,
                    amount_conflict_note="",
                    amount_source="table_fallback",
                    amount_source_note="大写金额解析失败，采用表格金额",
                )
            return _ResolvedAmountDecision(
                amount=normalized_table_amount,
                table_amount=normalized_table_amount,
                uppercase_amount_raw="",
                uppercase_amount_numeric="",
                amount_conflict=False,
                amount_conflict_note="",
                amount_source="table",
                amount_source_note="未检测到大写金额，采用表格金额",
            )

        if normalized_uppercase_numeric:
            return _ResolvedAmountDecision(
                amount=normalized_uppercase_numeric,
                table_amount="",
                uppercase_amount_raw=normalized_uppercase_raw,
                uppercase_amount_numeric=normalized_uppercase_numeric,
                amount_conflict=False,
                amount_conflict_note="",
                amount_source="uppercase_only",
                amount_source_note="未识别到合计金额，采用大写金额转换值",
            )
        return _ResolvedAmountDecision(
            amount="",
            table_amount="",
            uppercase_amount_raw=normalized_uppercase_raw,
            uppercase_amount_numeric="",
            amount_conflict=False,
            amount_conflict_note="",
            amount_source="",
            amount_source_note="",
        )

    def _convert_uppercase_amount(
        self,
        *,
        uppercase_amount_text: str,
        table_amount: str,
        conversation_id: str,
        sender_id: str,
    ) -> str:
        normalized_uppercase = uppercase_amount_text.strip()
        if not normalized_uppercase:
            return ""
        normalized_local = _normalize_amount_text(normalized_uppercase)
        if normalized_local:
            return normalized_local
        if self._uppercase_amount_converter is None:
            return ""
        try:
            value = self._uppercase_amount_converter.convert(
                uppercase_amount_text=normalized_uppercase,
                table_amount=table_amount,
                conversation_id=conversation_id,
                sender_id=sender_id,
            )
        except Exception:
            self._logger.exception(
                "reimbursement.attachment.uppercase_amount_llm_failed",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.reimbursement_attachment",
                        "event": "uppercase_amount_llm_failed",
                        "uppercase_amount_text": normalized_uppercase,
                    }
                },
            )
            return ""
        return _normalize_amount_text(value)

    def _upload_pdf_and_get_media_id(self, *, pdf_bytes: bytes) -> str:
        if not pdf_bytes:
            return ""
        try:
            access_token = self._get_access_token()
            response = requests.post(
                f"{self._settings.legacy_openapi_endpoint}/media/upload",
                params={"access_token": access_token, "type": self._settings.upload_media_type},
                files={"media": ("travel_reimbursement.pdf", pdf_bytes, "application/pdf")},
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

    @staticmethod
    def _build_pdf_bytes(*, lines: tuple[str, ...]) -> bytes:
        text_lines = [line.strip() for line in lines if line.strip()]
        if not text_lines:
            text_lines = ["Ke Agent Reimbursement Attachment"]

        content_ops = ["BT", "/F1 12 Tf", "50 780 Td"]
        for index, line in enumerate(text_lines):
            if index > 0:
                content_ops.append("0 -18 Td")
            safe_line = (
                line.encode("latin-1", "replace")
                .decode("latin-1")
                .replace("\\", "\\\\")
                .replace("(", "\\(")
                .replace(")", "\\)")
            )
            content_ops.append(f"({safe_line}) Tj")
        content_ops.append("ET")
        stream = "\n".join(content_ops).encode("latin-1", "replace")

        objects = [
            b"1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj",
            b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj",
            b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 595 842] /Contents 5 0 R /Resources << /Font << /F1 4 0 R >> >> >> endobj",
            b"4 0 obj << /Type /Font /Subtype /Type1 /BaseFont /Helvetica >> endobj",
            b"5 0 obj << /Length %d >> stream\n%s\nendstream endobj" % (len(stream), stream),
        ]

        out = BytesIO()
        out.write(b"%PDF-1.4\n")
        offsets = [0]
        for obj in objects:
            offsets.append(out.tell())
            out.write(obj)
            out.write(b"\n")
        xref_start = out.tell()
        out.write(f"xref\n0 {len(offsets)}\n".encode("latin-1"))
        out.write(b"0000000000 65535 f \n")
        for offset in offsets[1:]:
            out.write(f"{offset:010d} 00000 n \n".encode("latin-1"))
        out.write(
            (
                f"trailer << /Size {len(offsets)} /Root 1 0 R >>\n"
                f"startxref\n{xref_start}\n%%EOF"
            ).encode("latin-1")
        )
        return out.getvalue()


def _extract_xlsx_rows(*, file_bytes: bytes) -> list[list[str]]:
    merged_rows: list[list[str]] = []
    for sheet in _extract_xlsx_sheets(file_bytes=file_bytes):
        merged_rows.extend(sheet.rows)
    return merged_rows


def _extract_xlsx_sheets(*, file_bytes: bytes) -> list[_SheetModel]:
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    sheets: list[_SheetModel] = []
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        shared_strings = _load_shared_strings(zf)
        sheet_entries = _resolve_sheet_entries(zf=zf)
        for index, sheet_name, worksheet_path in sheet_entries:
            if worksheet_path not in zf.namelist():
                continue
            with zf.open(worksheet_path) as fp:
                tree = ET.parse(fp)
            root = tree.getroot()
            sheet_rows = _extract_rows_from_sheet_root(
                root=root,
                shared_strings=shared_strings,
                ns=ns,
            )
            merged_ranges = tuple(_extract_merge_ranges_from_sheet_root(root=root, ns=ns))
            merged_parent = _build_merged_parent_map(merged_ranges=merged_ranges)
            sheets.append(
                _SheetModel(
                    index=index,
                    name=sheet_name,
                    path=worksheet_path,
                    rows=sheet_rows,
                    merged_ranges=merged_ranges,
                    merged_parent=merged_parent,
                )
            )
    if not sheets:
        raise ValueError("xlsx worksheet is missing")
    return sheets


def _column_index_from_cell_reference(reference: str) -> int | None:
    match = _CELL_REFERENCE_PATTERN.match(reference)
    if match is None:
        return None
    letters = match.group(1)
    index = 0
    for char in letters:
        index = index * 26 + (ord(char) - 64)
    return max(index - 1, 0)


def _load_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    with zf.open("xl/sharedStrings.xml") as fp:
        tree = ET.parse(fp)
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    values: list[str] = []
    for item in tree.getroot().findall(".//s:si", ns):
        text_nodes = item.findall(".//s:t", ns)
        text = "".join(node.text or "" for node in text_nodes).strip()
        values.append(text)
    return values


def _extract_rows_from_sheet_root(
    *,
    root: ET.Element,
    shared_strings: list[str],
    ns: dict[str, str],
) -> list[list[str]]:
    rows: list[list[str]] = []
    for row in root.findall(".//s:sheetData/s:row", ns):
        values_by_column: dict[int, str] = {}
        next_column_index = 0
        max_column_index = -1
        for cell in row.findall("s:c", ns):
            reference = str(cell.attrib.get("r") or "").strip().upper()
            column_index = _column_index_from_cell_reference(reference)
            if column_index is None:
                column_index = next_column_index
            value = _extract_cell_value(cell=cell, shared_strings=shared_strings, ns=ns)
            values_by_column[column_index] = value
            next_column_index = column_index + 1
            if column_index > max_column_index:
                max_column_index = column_index
        if max_column_index < 0:
            continue
        values = [""] * (max_column_index + 1)
        for index, value in values_by_column.items():
            values[index] = value
        if any(item.strip() for item in values):
            rows.append(values)
    return rows


def _resolve_sheet_entries(*, zf: zipfile.ZipFile) -> list[tuple[int, str, str]]:
    workbook_path = "xl/workbook.xml"
    rels_path = "xl/_rels/workbook.xml.rels"
    if workbook_path not in zf.namelist():
        fallback_paths = _pick_sheet_paths(zf)
        return [
            (index, f"sheet{index}", path)
            for index, path in enumerate(fallback_paths, start=1)
        ]

    ns_main = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    relation_key = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    with zf.open(workbook_path) as fp:
        workbook_tree = ET.parse(fp)
    rel_map = _load_workbook_relationships(zf=zf, rels_path=rels_path)

    entries: list[tuple[int, str, str]] = []
    for index, node in enumerate(workbook_tree.getroot().findall(".//s:sheets/s:sheet", ns_main), start=1):
        name = str(node.attrib.get("name") or f"sheet{index}").strip() or f"sheet{index}"
        rel_id = str(node.attrib.get(relation_key) or "").strip()
        target = rel_map.get(rel_id, "")
        worksheet_path = _normalize_sheet_target_path(target=target) if target else f"xl/worksheets/sheet{index}.xml"
        entries.append((index, name, worksheet_path))

    if entries:
        return entries
    fallback_paths = _pick_sheet_paths(zf)
    return [
        (index, f"sheet{index}", path)
        for index, path in enumerate(fallback_paths, start=1)
    ]


def _load_workbook_relationships(*, zf: zipfile.ZipFile, rels_path: str) -> dict[str, str]:
    if rels_path not in zf.namelist():
        return {}
    with zf.open(rels_path) as fp:
        rels_tree = ET.parse(fp)
    rels_ns = "http://schemas.openxmlformats.org/package/2006/relationships"
    mapping: dict[str, str] = {}
    for rel in rels_tree.getroot().findall(f".//{{{rels_ns}}}Relationship"):
        relation_type = str(rel.attrib.get("Type") or "")
        if not relation_type.endswith("/worksheet"):
            continue
        rel_id = str(rel.attrib.get("Id") or "").strip()
        target = str(rel.attrib.get("Target") or "").strip()
        if rel_id and target:
            mapping[rel_id] = target
    return mapping


def _normalize_sheet_target_path(*, target: str) -> str:
    cleaned = str(target or "").strip().replace("\\", "/")
    if not cleaned:
        return ""
    if cleaned.startswith("/"):
        cleaned = cleaned.lstrip("/")
    if cleaned.startswith("xl/"):
        return posixpath.normpath(cleaned)
    return posixpath.normpath(posixpath.join("xl", cleaned))


def _pick_sheet_paths(zf: zipfile.ZipFile) -> tuple[str, ...]:
    sheet_paths = sorted(name for name in zf.namelist() if name.startswith("xl/worksheets/sheet") and name.endswith(".xml"))
    if not sheet_paths:
        raise ValueError("xlsx worksheet is missing")
    return tuple(sheet_paths)


def _extract_cell_value(*, cell: ET.Element, shared_strings: list[str], ns: dict[str, str]) -> str:
    cell_type = (cell.attrib.get("t") or "").strip()
    value_node = cell.find("s:v", ns)
    inline_node = cell.find("s:is/s:t", ns)
    if inline_node is not None and inline_node.text:
        return inline_node.text.strip()
    if value_node is None or value_node.text is None:
        return ""
    raw = value_node.text.strip()
    if cell_type == "s":
        try:
            index = int(raw)
        except ValueError:
            return raw
        if 0 <= index < len(shared_strings):
            return shared_strings[index].strip()
        return raw
    return raw


def _extract_merge_ranges_from_sheet_root(*, root: ET.Element, ns: dict[str, str]) -> list[_MergedRange]:
    ranges: list[_MergedRange] = []
    for node in root.findall(".//s:mergeCells/s:mergeCell", ns):
        ref = str(node.attrib.get("ref") or "").strip().upper()
        merged_range = _parse_merge_range(ref=ref)
        if merged_range is not None:
            ranges.append(merged_range)
    return ranges


def _parse_merge_range(*, ref: str) -> _MergedRange | None:
    match = _MERGE_RANGE_PATTERN.match(ref)
    if match is None:
        return None
    start_ref = match.group(1)
    end_ref = match.group(2) or start_ref
    start = _parse_cell_reference_to_position(reference=start_ref)
    end = _parse_cell_reference_to_position(reference=end_ref)
    if start is None or end is None:
        return None
    start_row = min(start[0], end[0])
    start_col = min(start[1], end[1])
    end_row = max(start[0], end[0])
    end_col = max(start[1], end[1])
    return _MergedRange(
        start_row=start_row,
        start_col=start_col,
        end_row=end_row,
        end_col=end_col,
    )


def _parse_cell_reference_to_position(*, reference: str) -> tuple[int, int] | None:
    match = _CELL_REFERENCE_PATTERN.match(reference)
    if match is None:
        return None
    letters = match.group(1)
    row_text = match.group(2)
    col_index = 0
    for char in letters:
        col_index = col_index * 26 + (ord(char) - 64)
    try:
        row_index = int(row_text)
    except ValueError:
        return None
    return max(row_index - 1, 0), max(col_index - 1, 0)


def _build_merged_parent_map(*, merged_ranges: tuple[_MergedRange, ...]) -> dict[tuple[int, int], tuple[int, int]]:
    mapping: dict[tuple[int, int], tuple[int, int]] = {}
    for merged in merged_ranges:
        anchor = (merged.start_row, merged.start_col)
        for row in range(merged.start_row, merged.end_row + 1):
            for col in range(merged.start_col, merged.end_col + 1):
                mapping[(row, col)] = anchor
    return mapping


def _extract_reimbursement_fields_from_rows(*, rows: list[list[str]]) -> tuple[str, str, str]:
    temp_sheet = _SheetModel(index=1, name="sheet1", path="", rows=rows)
    department, table_amount, uppercase_amount_text, _ = _extract_reimbursement_fields_from_sheet(sheet=temp_sheet)
    return department, table_amount, uppercase_amount_text


def _extract_department_and_amount_from_rows(*, rows: list[list[str]]) -> tuple[str, str]:
    department, table_amount, _ = _extract_reimbursement_fields_from_rows(rows=rows)
    return department, table_amount


def _select_target_sheet(*, sheets: list[_SheetModel]) -> tuple[_SheetModel | None, dict[str, Any]]:
    evidence: dict[str, Any] = {
        "strategy": "second_sheet_name_then_marker_scan",
        "target_sheet_name": _TARGET_SHEET_NAME,
        "marker_keyword": _TARGET_SHEET_MARKER,
        "fallback_used": False,
        "second_sheet_checked": len(sheets) >= 2,
        "second_sheet_name": sheets[1].name if len(sheets) >= 2 else "",
        "selected_sheet_index": None,
        "selected_sheet_name": "",
        "marker_matches": [],
    }
    if len(sheets) >= 2:
        second = sheets[1]
        if _normalize_text(second.name) == _normalize_text(_TARGET_SHEET_NAME):
            evidence["selected_sheet_index"] = second.index
            evidence["selected_sheet_name"] = second.name
            return second, evidence

    marker = _normalize_text(_TARGET_SHEET_MARKER)
    evidence["fallback_used"] = True
    selected_sheet: _SheetModel | None = None
    marker_matches: list[dict[str, Any]] = []
    for sheet in sheets:
        for row_index, row in enumerate(sheet.rows):
            for col_index, value in enumerate(row):
                normalized = _normalize_text(value)
                if not normalized or marker not in normalized:
                    continue
                match = {
                    "sheet_index": sheet.index,
                    "sheet_name": sheet.name,
                    "row": row_index + 1,
                    "col": col_index + 1,
                    "value": str(value or "").strip(),
                }
                marker_matches.append(match)
                if selected_sheet is None:
                    selected_sheet = sheet
        if selected_sheet is not None:
            break
    evidence["marker_matches"] = marker_matches[:8]
    if selected_sheet is not None:
        evidence["selected_sheet_index"] = selected_sheet.index
        evidence["selected_sheet_name"] = selected_sheet.name
    return selected_sheet, evidence


def _extract_reimbursement_fields_from_sheet(
    *,
    sheet: _SheetModel,
) -> tuple[str, str, str, dict[str, dict[str, Any]]]:
    department, department_evidence = _extract_department_from_sheet(sheet=sheet)
    table_amount, total_evidence = _extract_total_amount_from_sheet(sheet=sheet)
    uppercase_amount_text, uppercase_evidence = _extract_uppercase_amount_from_sheet(sheet=sheet)
    evidence = {
        "department": department_evidence,
        "total_amount": total_evidence,
        "uppercase_amount": uppercase_evidence,
    }
    return department, table_amount, uppercase_amount_text, evidence


def _extract_department_from_sheet(*, sheet: _SheetModel) -> tuple[str, dict[str, Any]]:
    return _extract_with_anchor_window(
        sheet=sheet,
        anchor_predicate=_is_department_anchor,
        row_offset_min=0,
        row_offset_max=2,
        col_offset_min=1,
        col_offset_max=8,
        evaluator=_evaluate_department_candidate,
    )


def _extract_total_amount_from_sheet(*, sheet: _SheetModel) -> tuple[str, dict[str, Any]]:
    return _extract_with_anchor_window(
        sheet=sheet,
        anchor_predicate=_is_total_amount_anchor,
        row_offset_min=-1,
        row_offset_max=1,
        col_offset_min=0,
        col_offset_max=12,
        evaluator=_evaluate_total_amount_candidate,
    )


def _extract_uppercase_amount_from_sheet(*, sheet: _SheetModel) -> tuple[str, dict[str, Any]]:
    primary_value, primary_evidence = _extract_with_anchor_window(
        sheet=sheet,
        anchor_predicate=_is_uppercase_amount_anchor,
        row_offset_min=0,
        row_offset_max=1,
        col_offset_min=1,
        col_offset_max=8,
        evaluator=_evaluate_uppercase_amount_candidate,
    )
    primary_evidence["window_profile"] = "primary"
    if primary_value:
        return primary_value, primary_evidence

    fallback_value, fallback_evidence = _extract_with_anchor_window(
        sheet=sheet,
        anchor_predicate=_is_uppercase_amount_anchor,
        row_offset_min=0,
        row_offset_max=2,
        col_offset_min=0,
        col_offset_max=12,
        evaluator=_evaluate_uppercase_amount_candidate,
    )
    fallback_evidence["window_profile"] = "fallback_extended"
    fallback_evidence["primary_anchor_count"] = len(primary_evidence.get("anchors", []))
    fallback_evidence["primary_candidate_count"] = len(primary_evidence.get("candidates", []))
    if fallback_value:
        return fallback_value, fallback_evidence
    if fallback_evidence.get("anchors") or fallback_evidence.get("candidates"):
        return "", fallback_evidence
    return "", primary_evidence


def _extract_with_anchor_window(
    *,
    sheet: _SheetModel,
    anchor_predicate: Any,
    row_offset_min: int,
    row_offset_max: int,
    col_offset_min: int,
    col_offset_max: int,
    evaluator: Any,
) -> tuple[str, dict[str, Any]]:
    anchors = _find_anchor_cells(sheet=sheet, predicate=anchor_predicate)
    evidence: dict[str, Any] = {
        "anchors": [
            {"row": row + 1, "col": col + 1, "value": value}
            for row, col, value in anchors
        ],
        "window": {
            "row_offset": [row_offset_min, row_offset_max],
            "col_offset": [col_offset_min, col_offset_max],
        },
        "candidates": [],
        "selected": None,
    }
    if not anchors:
        return "", evidence

    candidates = _collect_window_candidates(
        sheet=sheet,
        anchors=anchors,
        row_offset_min=row_offset_min,
        row_offset_max=row_offset_max,
        col_offset_min=col_offset_min,
        col_offset_max=col_offset_max,
    )
    ranked: list[tuple[int, str, dict[str, Any]]] = []
    for candidate in candidates:
        accepted, normalized_value, score, reasons = evaluator(candidate)
        candidate_evidence = {
            "anchor_row": candidate["anchor_row"] + 1,
            "anchor_col": candidate["anchor_col"] + 1,
            "row": candidate["row"] + 1,
            "col": candidate["col"] + 1,
            "source_row": candidate["source_row"] + 1,
            "source_col": candidate["source_col"] + 1,
            "value": candidate["value"],
            "normalized_value": normalized_value,
            "is_merged": candidate["is_merged"],
            "accepted": accepted,
            "score": score,
            "reasons": reasons,
        }
        evidence["candidates"].append(candidate_evidence)
        if accepted and normalized_value:
            ranked.append((score, normalized_value, candidate_evidence))

    if not ranked:
        return "", evidence

    ranked.sort(key=lambda item: item[0], reverse=True)
    best = ranked[0]
    evidence["selected"] = dict(best[2])
    return best[1], evidence


def _find_anchor_cells(
    *,
    sheet: _SheetModel,
    predicate: Any,
) -> list[tuple[int, int, str]]:
    anchors: list[tuple[int, int, str]] = []
    for row_index, row in enumerate(sheet.rows):
        for col_index, raw in enumerate(row):
            value = str(raw or "").strip()
            if not value:
                continue
            if predicate(value):
                anchors.append((row_index, col_index, value))
    return anchors


def _collect_window_candidates(
    *,
    sheet: _SheetModel,
    anchors: list[tuple[int, int, str]],
    row_offset_min: int,
    row_offset_max: int,
    col_offset_min: int,
    col_offset_max: int,
) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[int, int, int, int]] = set()
    for anchor_row, anchor_col, _ in anchors:
        for row in range(max(0, anchor_row + row_offset_min), anchor_row + row_offset_max + 1):
            if row >= len(sheet.rows):
                break
            for col in range(max(0, anchor_col + col_offset_min), anchor_col + col_offset_max + 1):
                value, source_row, source_col, is_merged = _resolve_sheet_cell_value(sheet=sheet, row=row, col=col)
                if not value:
                    continue
                dedupe_key = (anchor_row, anchor_col, source_row, source_col)
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                candidates.append(
                    {
                        "anchor_row": anchor_row,
                        "anchor_col": anchor_col,
                        "row": row,
                        "col": col,
                        "source_row": source_row,
                        "source_col": source_col,
                        "value": value,
                        "is_merged": is_merged,
                    }
                )
    return candidates


def _resolve_sheet_cell_value(*, sheet: _SheetModel, row: int, col: int) -> tuple[str, int, int, bool]:
    anchor_row, anchor_col = sheet.merged_parent.get((row, col), (row, col))
    raw = _get_raw_cell_value(rows=sheet.rows, row=row, col=col)
    anchor_raw = _get_raw_cell_value(rows=sheet.rows, row=anchor_row, col=anchor_col)
    value = str(raw or "").strip() or str(anchor_raw or "").strip()
    is_merged = (anchor_row, anchor_col) != (row, col) or (row, col) in sheet.merged_parent
    return value, anchor_row, anchor_col, is_merged


def _get_raw_cell_value(*, rows: list[list[str]], row: int, col: int) -> str:
    if row < 0 or col < 0 or row >= len(rows):
        return ""
    current = rows[row]
    if col >= len(current):
        return ""
    return str(current[col] or "").strip()


def _extract_department_inline(value: str) -> str:
    normalized = "".join((value or "").split())
    if not normalized:
        return ""
    match = _DEPARTMENT_INLINE_PATTERN.search(normalized)
    if match:
        return _sanitize_department_value(match.group(1))
    return ""


def _is_department_label(value: str) -> bool:
    normalized = "".join((value or "").split())
    return normalized in {"部门", "部门:", "部门：", "所属部门", "所属部门:", "所属部门："}


def _sanitize_department_value(value: str) -> str:
    text = "".join((value or "").split()).strip("：:，,；;。.")
    return text


def _is_department_anchor(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if _DEPARTMENT_INLINE_PATTERN.search(normalized):
        return True
    return any(normalized == token or normalized.startswith(token + "：") or normalized.startswith(token + ":") for token in _DEPARTMENT_ANCHOR_TOKENS)


def _is_total_amount_anchor(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if _AMOUNT_INLINE_PATTERN.search(normalized):
        return True
    return any(token in normalized for token in _TOTAL_AMOUNT_ANCHOR_TOKENS)


def _is_uppercase_amount_anchor(value: str) -> bool:
    normalized = _normalize_text(value)
    if not normalized:
        return False
    if _UPPERCASE_INLINE_PATTERN.search(normalized):
        return True
    return _looks_like_uppercase_amount_anchor_label(normalized)


def _evaluate_department_candidate(candidate: dict[str, Any]) -> tuple[bool, str, int, list[str]]:
    raw = str(candidate.get("value") or "").strip()
    inline = _extract_department_inline(raw)
    normalized = _sanitize_department_value(inline or raw)
    reasons: list[str] = []
    if not normalized:
        return False, "", -100, ["empty"]
    if _normalize_numeric_amount(normalized):
        return False, "", -90, ["numeric_noise"]
    if _is_department_label(normalized):
        return False, "", -80, ["label_text"]
    score = 0
    anchor_row = int(candidate.get("anchor_row") or 0)
    anchor_col = int(candidate.get("anchor_col") or 0)
    row = int(candidate.get("row") or 0)
    col = int(candidate.get("col") or 0)
    if inline:
        score += 80
        reasons.append("inline_match")
    if row == anchor_row:
        score += 30
        reasons.append("same_row")
    if col > anchor_col:
        score += 20
        reasons.append("right_side")
    if row > anchor_row:
        score += 10
        reasons.append("below_anchor")
    distance = abs(row - anchor_row) + abs(col - anchor_col)
    score += max(0, 20 - distance * 3)
    if _contains_cjk(normalized):
        score += 15
        reasons.append("contains_cjk")
    if candidate.get("is_merged"):
        score += 3
        reasons.append("merged_cell")
    if _looks_like_department_name(normalized):
        score += 8
        reasons.append("department_like")
    return True, normalized, score, reasons


def _evaluate_total_amount_candidate(candidate: dict[str, Any]) -> tuple[bool, str, int, list[str]]:
    raw = str(candidate.get("value") or "").strip()
    inline = _extract_numeric_amount_from_inline_amount(raw)
    normalized = inline or _normalize_numeric_amount(raw)
    reasons: list[str] = []
    if not normalized:
        return False, "", -100, ["not_numeric"]
    if _is_zero_amount(normalized):
        return False, "", -70, ["zero_amount_noise"]
    score = 0
    anchor_row = int(candidate.get("anchor_row") or 0)
    anchor_col = int(candidate.get("anchor_col") or 0)
    row = int(candidate.get("row") or 0)
    col = int(candidate.get("col") or 0)
    if inline:
        score += 60
        reasons.append("inline_amount")
    if row == anchor_row:
        score += 40
        reasons.append("same_row")
    if col >= anchor_col:
        score += 15 + min(col - anchor_col, 8)
        reasons.append("right_side")
    if "元" in raw:
        score += 8
        reasons.append("currency_marker")
    if candidate.get("is_merged"):
        score += 3
        reasons.append("merged_cell")
    if _looks_like_date_serial(raw=raw, normalized_amount=normalized):
        score -= 35
        reasons.append("date_serial_penalty")
    return True, normalized, score, reasons


def _evaluate_uppercase_amount_candidate(candidate: dict[str, Any]) -> tuple[bool, str, int, list[str]]:
    raw = str(candidate.get("value") or "").strip().strip("：:，,；;。.")
    inline = _extract_uppercase_inline(raw)
    normalized = (inline or raw).strip()
    reasons: list[str] = []
    if not normalized:
        return False, "", -100, ["empty"]
    if _is_uppercase_amount_label(normalized):
        return False, "", -90, ["label_text"]
    numeric_like = _normalize_numeric_amount(normalized)
    has_currency_unit = _has_currency_unit_token(normalized)
    if numeric_like and not has_currency_unit:
        return False, "", -80, ["numeric_noise"]
    amount_like = bool(_CHINESE_AMOUNT_HINT_PATTERN.search(normalized)) or has_currency_unit
    if not amount_like and not _normalize_amount_text(normalized):
        return False, "", -75, ["not_amount_like"]
    score = 0
    anchor_row = int(candidate.get("anchor_row") or 0)
    anchor_col = int(candidate.get("anchor_col") or 0)
    row = int(candidate.get("row") or 0)
    col = int(candidate.get("col") or 0)
    if inline:
        score += 70
        reasons.append("inline_match")
    if row == anchor_row:
        score += 30
        reasons.append("same_row")
    if col > anchor_col:
        score += 20
        reasons.append("right_side")
    distance = abs(row - anchor_row) + abs(col - anchor_col)
    score += max(0, 18 - distance * 3)
    if _CHINESE_AMOUNT_HINT_PATTERN.search(normalized):
        score += 25
        reasons.append("amount_hint")
    if "元" in normalized or "整" in normalized:
        score += 10
        reasons.append("currency_wording")
    if _normalize_amount_text(normalized):
        score += 6
        reasons.append("amount_normalizable")
    if candidate.get("is_merged"):
        score += 3
        reasons.append("merged_cell")
    return True, normalized, score, reasons


def _looks_like_department_name(value: str) -> bool:
    text = _sanitize_department_value(value)
    if not text:
        return False
    department_hints = ("部", "中心", "办", "组", "科", "处", "室", "项目")
    return any(hint in text for hint in department_hints) or _contains_cjk(text)


def _looks_like_date_serial(*, raw: str, normalized_amount: str) -> bool:
    text = _normalize_text(raw)
    if any(token in text for token in ("年", "月", "日", "-", "/", ":")):
        return True
    if "." in normalized_amount:
        return False
    try:
        numeric = int(normalized_amount)
    except ValueError:
        return False
    return 30000 <= numeric <= 70000 and len(normalized_amount) >= 5


def _contains_cjk(text: str) -> bool:
    return any("\u4e00" <= ch <= "\u9fff" for ch in text)


def _has_currency_unit_token(value: str) -> bool:
    normalized = _normalize_text(value)
    return any(token in normalized for token in ("元", "圆", "整", "正", "角", "分"))


def _is_zero_amount(value: str) -> bool:
    try:
        return float(value) == 0.0
    except ValueError:
        return False


def _normalize_text(value: str) -> str:
    return "".join((value or "").strip().split())


def _extract_numeric_amount_from_inline_amount(value: str) -> str:
    normalized = "".join((value or "").split())
    if not normalized:
        return ""
    match = _AMOUNT_INLINE_PATTERN.search(normalized)
    if match:
        return _normalize_numeric_amount(match.group(1))
    return ""


def _extract_uppercase_inline(value: str) -> str:
    normalized = "".join((value or "").split())
    if not normalized:
        return ""
    for delimiter in ("：", ":"):
        if delimiter not in normalized:
            continue
        left, right = normalized.split(delimiter, 1)
        if _looks_like_uppercase_amount_anchor_label(left):
            return right.strip("：:，,；;。.")
    match = _UPPERCASE_INLINE_PATTERN.search(normalized)
    if match:
        return match.group(1).strip("：:，,；;。.")
    return ""


def _is_uppercase_amount_label(value: str) -> bool:
    normalized = "".join((value or "").split()).strip("：:")
    if not normalized or not _looks_like_uppercase_amount_anchor_label(normalized):
        return False
    if _extract_uppercase_inline(normalized):
        return False
    stripped = normalized
    for token in (
        "人民币",
        "大写金额",
        "金额大写",
        "大写",
        "金额",
        "（",
        "）",
        "(",
        ")",
    ):
        stripped = stripped.replace(token, "")
    return not stripped


def _looks_like_uppercase_amount_anchor_label(value: str) -> bool:
    normalized = _normalize_label_text(value)
    if not normalized:
        return False
    if any(token in normalized for token in _UPPERCASE_AMOUNT_ANCHOR_TOKENS):
        return True
    return "大写" in normalized and ("金额" in normalized or "人民币" in normalized)


def _normalize_label_text(value: str) -> str:
    normalized = _normalize_text(value)
    if not normalized:
        return ""
    for token in ("（", "）", "(", ")", "：", ":", "【", "】", "[", "]", "。", "，", ",", "；", ";"):
        normalized = normalized.replace(token, "")
    return normalized


def _count_merged_hits(*, field_evidence: dict[str, dict[str, Any]]) -> int:
    count = 0
    for evidence in field_evidence.values():
        for candidate in evidence.get("candidates", []):
            if bool(candidate.get("is_merged")):
                count += 1
    return count


def _emit_field_extraction_log(
    *,
    logger: logging.Logger,
    field_name: str,
    evidence: dict[str, Any],
    conversation_id: str,
    sender_id: str,
) -> None:
    candidates = evidence.get("candidates", [])
    selected = evidence.get("selected")
    obs = {
        "module": "integrations.dingtalk.reimbursement_attachment",
        "field_name": field_name,
        "anchors": len(evidence.get("anchors", [])),
        "candidate_count": len(candidates),
        "conversation_id": conversation_id,
        "sender_id": sender_id,
    }
    if isinstance(selected, Mapping):
        obs.update(
            {
                "event": "reimbursement_attachment_field_extracted",
                "selected_row": selected.get("source_row"),
                "selected_col": selected.get("source_col"),
                "selected_value": selected.get("normalized_value") or selected.get("value"),
                "selected_score": selected.get("score"),
            }
        )
        logger.info(
            "reimbursement.attachment.field_extracted",
            extra={"obs": obs},
        )
        return
    obs["event"] = "reimbursement_attachment_field_missing"
    logger.warning(
        "reimbursement.attachment.field_missing",
        extra={"obs": obs},
    )


def _build_evidence_summary(extraction_evidence: Mapping[str, Any]) -> dict[str, Any]:
    sheet_selection = extraction_evidence.get("sheet_selection")
    merged_cells = extraction_evidence.get("merged_cells")
    department = extraction_evidence.get("department")
    total_amount = extraction_evidence.get("total_amount")
    uppercase_amount = extraction_evidence.get("uppercase_amount")
    return {
        "selected_sheet_index": sheet_selection.get("selected_sheet_index") if isinstance(sheet_selection, Mapping) else None,
        "selected_sheet_name": sheet_selection.get("selected_sheet_name") if isinstance(sheet_selection, Mapping) else "",
        "fallback_used": bool(sheet_selection.get("fallback_used")) if isinstance(sheet_selection, Mapping) else False,
        "merged_range_count": int(merged_cells.get("total_ranges", 0)) if isinstance(merged_cells, Mapping) else 0,
        "department_candidates": len(department.get("candidates", [])) if isinstance(department, Mapping) else 0,
        "total_amount_candidates": len(total_amount.get("candidates", [])) if isinstance(total_amount, Mapping) else 0,
        "uppercase_amount_candidates": len(uppercase_amount.get("candidates", [])) if isinstance(uppercase_amount, Mapping) else 0,
    }


def _extract_numeric_amount(value: str) -> str:
    text = (value or "").replace(",", "").replace("，", "").replace("元", "").strip()
    if not text:
        return ""
    match = _NUMERIC_PATTERN.search(text)
    if match is None:
        return ""
    return _normalize_numeric_amount(match.group(0))


def _normalize_numeric_amount(value: str) -> str:
    text = (value or "").replace(",", "").replace("，", "").replace("元", "").strip()
    if not text:
        return ""
    match = _NUMERIC_PATTERN.search(text)
    if match is None:
        return ""
    normalized = match.group(0).strip().strip(".")
    if not normalized:
        return ""
    try:
        numeric_value = float(normalized)
    except ValueError:
        return ""
    if numeric_value.is_integer():
        return str(int(numeric_value))
    return f"{numeric_value:.2f}".rstrip("0").rstrip(".")


def _normalize_amount_text(value: str) -> str:
    raw = (value or "").strip()
    if not raw:
        return ""
    numeric_amount = _normalize_numeric_amount(raw)
    if numeric_amount:
        return numeric_amount
    chinese_amount = _normalize_chinese_amount(raw)
    if chinese_amount:
        return chinese_amount
    return ""


def _normalize_chinese_amount(value: str) -> str:
    text = "".join((value or "").split())
    if not text:
        return ""
    text = text.replace("人民币", "").replace("圆", "元")

    integer_part = text
    decimal_part = ""
    if "元" in text:
        integer_part, decimal_part = text.split("元", 1)

    integer_value = _parse_chinese_integer(integer_part)
    if integer_value is None:
        return ""

    decimal_text = decimal_part.replace("整", "").replace("正", "")
    jiao = _parse_chinese_fraction_digit(decimal_text, "角")
    fen = _parse_chinese_fraction_digit(decimal_text, "分")
    total_cents = integer_value * 100 + jiao * 10 + fen
    if total_cents % 100 == 0:
        return str(total_cents // 100)
    return f"{total_cents / 100:.2f}".rstrip("0").rstrip(".")


def _parse_chinese_integer(value: str) -> int | None:
    text = _AMOUNT_TEXT_CLEAN_PATTERN.sub("", value or "")
    if not text:
        return None
    if text.isdigit():
        return int(text)

    result = 0
    section = 0
    number = 0
    has_token = False
    for char in text:
        if char in _CHINESE_DIGITS_MAP:
            number = _CHINESE_DIGITS_MAP[char]
            has_token = True
            continue
        if char in _CHINESE_SMALL_UNITS:
            has_token = True
            unit = _CHINESE_SMALL_UNITS[char]
            if number == 0:
                number = 1
            section += number * unit
            number = 0
            continue
        if char in _CHINESE_SECTION_UNITS:
            has_token = True
            unit = _CHINESE_SECTION_UNITS[char]
            section += number
            if section == 0:
                section = 1
            result += section * unit
            section = 0
            number = 0
            continue
        return None
    if not has_token:
        return None
    return result + section + number


def _parse_chinese_fraction_digit(value: str, unit: str) -> int:
    if not value or unit not in value:
        return 0
    index = value.find(unit)
    if index <= 0:
        return 0
    prev = value[index - 1]
    if prev in _CHINESE_DIGITS_MAP:
        return _CHINESE_DIGITS_MAP[prev]
    if prev.isdigit():
        return int(prev)
    return 0


def _amounts_mismatch(*, left: str, right: str) -> bool:
    normalized_left = _normalize_amount_text(left)
    normalized_right = _normalize_amount_text(right)
    if not normalized_left or not normalized_right:
        return False
    return normalized_left != normalized_right


def _build_rows_preview(*, rows: list[list[str]], max_rows: int = 80, max_cols: int = 20) -> str:
    lines: list[str] = []
    for row_index, row in enumerate(rows[:max_rows], start=1):
        cells: list[str] = []
        for cell in row[:max_cols]:
            text = (cell or "").strip()
            cells.append(text)
        if not any(cells):
            continue
        lines.append(f"R{row_index}: " + " | ".join(cells))
    return "\n".join(lines).strip()


def load_reimbursement_attachment_settings(raw_env: Mapping[str, str] | None = None) -> ReimbursementAttachmentSettings:
    env = raw_env if raw_env is not None else os.environ
    enabled = str(env.get("DINGTALK_REIMBURSE_APPROVAL_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    legacy_openapi_endpoint = str(env.get("DINGTALK_LEGACY_OPENAPI_ENDPOINT") or LEGACY_DINGTALK_BASE).strip()
    media_type = str(env.get("DINGTALK_REIMBURSE_ATTACHMENT_MEDIA_TYPE") or "file").strip() or "file"
    return ReimbursementAttachmentSettings(
        enabled=enabled,
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
    llm_api_key = str(env.get("LLM_API_KEY") or env.get("QWEN_API_KEY") or "").strip()
    uppercase_amount_converter: UppercaseAmountConverter | None = None
    table_field_extractor: ReimbursementTableFieldExtractor | None = None
    if llm_api_key:
        endpoint = str(env.get("LLM_BASE_URL") or env.get("QWEN_BASE_URL") or DEFAULT_QWEN_CHAT_ENDPOINT).strip()
        model = str(env.get("LLM_CHAT_MODEL") or env.get("QWEN_CHAT_MODEL") or "qwen-plus").strip() or "qwen-plus"
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
        uppercase_amount_converter = QwenUppercaseAmountConverter(
            llm_client=llm_client,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
        table_field_extractor = QwenReimbursementTableFieldExtractor(
            llm_client=llm_client,
            model=model,
            timeout_seconds=timeout_seconds,
            max_retries=max_retries,
        )
    return DingTalkReimbursementAttachmentProcessor(
        client_id=client_id,
        client_secret=client_secret,
        settings=settings,
        uppercase_amount_converter=uppercase_amount_converter,
        table_field_extractor=table_field_extractor,
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
