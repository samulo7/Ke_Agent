from __future__ import annotations

import base64
from collections.abc import Mapping
from dataclasses import dataclass
from io import BytesIO
import logging
import os
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
_UPPERCASE_INLINE_PATTERN = re.compile(r"大写金额[:：]\s*([^\s,，;；]+)")
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

        department, table_amount, uppercase_amount_text = self._parse_xlsx_fields(
            file_bytes=file_bytes,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        if not department:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到部门，请检查报销单模板后重传。",
            )
        amount_decision = self._resolve_final_amount(
            table_amount=table_amount,
            uppercase_amount_text=uppercase_amount_text,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )
        if not amount_decision.amount:
            return ReimbursementAttachmentProcessResult(
                success=False,
                reason="未识别到金额，请检查报销单模板后重传。",
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
    ) -> tuple[str, str, str]:
        try:
            rows = _extract_xlsx_rows(file_bytes=file_bytes)
        except Exception:
            self._logger.exception("reimbursement.attachment.xlsx_parse_failed")
            return "", "", ""
        department, table_amount, uppercase_amount_text = _extract_reimbursement_fields_from_rows(rows=rows)
        if (not department or not table_amount) and self._table_field_extractor is not None:
            sheet_text = _build_rows_preview(rows=rows)
            if sheet_text:
                try:
                    llm_department, llm_table_amount, llm_uppercase = self._table_field_extractor.extract(
                        sheet_text=sheet_text,
                        conversation_id=conversation_id,
                        sender_id=sender_id,
                    )
                except Exception:
                    self._logger.exception(
                        "reimbursement.attachment.table_field_llm_failed",
                        extra={
                            "obs": {
                                "module": "integrations.dingtalk.reimbursement_attachment",
                                "event": "table_field_llm_failed",
                            }
                        },
                    )
                else:
                    if not department and llm_department:
                        department = _sanitize_department_value(llm_department)
                    if not table_amount and llm_table_amount:
                        table_amount = _normalize_amount_text(llm_table_amount)
                    if not uppercase_amount_text and llm_uppercase:
                        uppercase_amount_text = llm_uppercase.strip()
        return department, table_amount, uppercase_amount_text

    def _parse_xlsx_department_and_amount(self, *, file_bytes: bytes) -> tuple[str, str]:
        department, table_amount, _ = self._parse_xlsx_fields(file_bytes=file_bytes)
        return department, table_amount

    def _resolve_final_amount(
        self,
        *,
        table_amount: str,
        uppercase_amount_text: str,
        conversation_id: str,
        sender_id: str,
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
    ns = {"s": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    merged_rows: list[list[str]] = []
    with zipfile.ZipFile(BytesIO(file_bytes)) as zf:
        shared_strings = _load_shared_strings(zf)
        worksheet_paths = _pick_sheet_paths(zf)
        for worksheet_path in worksheet_paths:
            with zf.open(worksheet_path) as fp:
                tree = ET.parse(fp)
            sheet_rows = _extract_rows_from_sheet_root(
                root=tree.getroot(),
                shared_strings=shared_strings,
                ns=ns,
            )
            merged_rows.extend(sheet_rows)
    return merged_rows


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


def _extract_reimbursement_fields_from_rows(*, rows: list[list[str]]) -> tuple[str, str, str]:
    department = _extract_department_from_rows(rows=rows)
    table_amount = _extract_table_amount_from_rows(rows=rows)
    uppercase_amount_text = _extract_uppercase_amount_from_rows(rows=rows)
    return department, table_amount, uppercase_amount_text


def _extract_department_and_amount_from_rows(*, rows: list[list[str]]) -> tuple[str, str]:
    department, table_amount, _ = _extract_reimbursement_fields_from_rows(rows=rows)
    return department, table_amount


def _extract_department_from_rows(*, rows: list[list[str]]) -> str:
    for row_index, row in enumerate(rows):
        for index, value in enumerate(row):
            inline_value = _extract_department_inline(value)
            if inline_value:
                return inline_value
            if _is_department_label(value):
                candidate = _pick_nearest_non_empty_right(row=row, start_index=index)
                if _is_plausible_department(candidate):
                    return candidate
                candidate_below = _pick_nearest_non_empty_below(
                    rows=rows,
                    start_row_index=row_index,
                    start_col_index=index,
                )
                if _is_plausible_department(candidate_below):
                    return candidate_below
    return ""


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


def _is_plausible_department(value: str) -> bool:
    normalized = _sanitize_department_value(value)
    if not normalized:
        return False
    if _normalize_numeric_amount(normalized):
        return False
    invalid_tokens = {
        "部门",
        "所属部门",
        "日期",
        "金额",
        "金额(元)",
        "大写金额",
        "合计",
        "总计",
        "经理",
        "负责人",
    }
    if normalized in invalid_tokens:
        return False
    return True


def _extract_table_amount_from_rows(*, rows: list[list[str]]) -> str:
    for row in rows:
        if not _is_total_row(row=row):
            continue
        amount_from_row = _extract_amount_from_total_row(row=row)
        if amount_from_row:
            return amount_from_row

    amount_columns = _find_amount_column_candidates(rows=rows)
    for row in rows:
        for column in amount_columns:
            if column >= len(row):
                continue
            amount = _extract_numeric_amount(row[column])
            if amount:
                return amount

    for row in rows:
        amount_from_label = _extract_amount_from_label_adjacency(row=row)
        if amount_from_label:
            return amount_from_label
    return ""


def _find_amount_column_candidates(*, rows: list[list[str]]) -> tuple[int, ...]:
    indices: list[int] = []
    for row in rows:
        for index, value in enumerate(row):
            if _is_amount_column_label(value):
                indices.append(index)
    seen: set[int] = set()
    ordered: list[int] = []
    for index in indices:
        if index in seen:
            continue
        seen.add(index)
        ordered.append(index)
    return tuple(ordered)


def _is_amount_column_label(value: str) -> bool:
    normalized = "".join((value or "").split())
    return normalized in {"合计金额", "金额(元)", "金额", "报销金额", "金额（元）"}


def _is_total_row(*, row: list[str]) -> bool:
    tokens = {"合计", "总计", "合计金额", "费用合计", "合计:", "合计："}
    for value in row:
        normalized = "".join((value or "").split())
        if normalized in tokens:
            return True
        if normalized.startswith("合计金额"):
            return True
    return False


def _extract_amount_from_total_row(*, row: list[str]) -> str:
    for value in reversed(row):
        inline = _extract_numeric_amount_from_inline_amount(value)
        if inline:
            return inline
        amount = _extract_numeric_amount(value)
        if amount:
            return amount
    return ""


def _extract_amount_from_label_adjacency(*, row: list[str]) -> str:
    for index, value in enumerate(row):
        inline = _extract_numeric_amount_from_inline_amount(value)
        if inline:
            return inline
        normalized = "".join((value or "").split())
        if normalized in {"合计金额", "金额(元)", "金额（元）", "金额"}:
            candidate = _pick_nearest_non_empty_right(row=row, start_index=index)
            amount = _extract_numeric_amount(candidate)
            if amount:
                return amount
    return ""


def _extract_numeric_amount_from_inline_amount(value: str) -> str:
    normalized = "".join((value or "").split())
    if not normalized:
        return ""
    match = _AMOUNT_INLINE_PATTERN.search(normalized)
    if match:
        return _normalize_numeric_amount(match.group(1))
    return ""


def _extract_uppercase_amount_from_rows(*, rows: list[list[str]]) -> str:
    for row in rows:
        for index, value in enumerate(row):
            inline = _extract_uppercase_inline(value)
            if inline:
                return inline
            if _is_uppercase_amount_label(value):
                candidate = _pick_nearest_non_empty_right(row=row, start_index=index)
                if candidate:
                    return candidate
    return ""


def _extract_uppercase_inline(value: str) -> str:
    normalized = "".join((value or "").split())
    if not normalized:
        return ""
    match = _UPPERCASE_INLINE_PATTERN.search(normalized)
    if match:
        return match.group(1).strip("：:，,；;。.")
    return ""


def _is_uppercase_amount_label(value: str) -> bool:
    normalized = "".join((value or "").split())
    return normalized in {"大写金额", "大写金额:", "大写金额："}


def _pick_nearest_non_empty_right(*, row: list[str], start_index: int) -> str:
    for index in range(start_index + 1, len(row)):
        candidate = _sanitize_department_value(row[index])
        if candidate:
            return candidate
    return ""


def _pick_nearest_non_empty_below(
    *,
    rows: list[list[str]],
    start_row_index: int,
    start_col_index: int,
) -> str:
    max_scan_rows = min(start_row_index + 6, len(rows) - 1)
    for row_index in range(start_row_index + 1, max_scan_rows + 1):
        row = rows[row_index]
        for column_index in (start_col_index, start_col_index + 1, start_col_index + 2):
            if column_index < 0 or column_index >= len(row):
                continue
            candidate = _sanitize_department_value(row[column_index])
            if candidate:
                return candidate
    return ""


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
