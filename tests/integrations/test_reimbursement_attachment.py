from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

from app.integrations.dingtalk.reimbursement_attachment import (
    DingTalkReimbursementAttachmentProcessor,
    ReimbursementAttachmentSettings,
    _extract_department_and_amount_from_rows,
    _normalize_amount_text,
)
from app.schemas.dingtalk_chat import IncomingChatMessage


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200, content: bytes = b"") -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):  # type: ignore[no-untyped-def]
        return dict(self._payload)


class _FakeUppercaseAmountConverter:
    def __init__(self, amount: str = "", *, should_raise: bool = False) -> None:
        self._amount = amount
        self._should_raise = should_raise

    def convert(
        self,
        *,
        uppercase_amount_text: str,
        table_amount: str,
        conversation_id: str,
        sender_id: str,
    ) -> str:
        del uppercase_amount_text, table_amount, conversation_id, sender_id
        if self._should_raise:
            raise RuntimeError("llm unavailable")
        return self._amount


class _FakeTableFieldExtractor:
    def __init__(self, *, department: str = "", table_amount: str = "", uppercase_amount_text: str = "") -> None:
        self._department = department
        self._table_amount = table_amount
        self._uppercase_amount_text = uppercase_amount_text
        self.called = False

    def extract(
        self,
        *,
        sheet_text: str,
        conversation_id: str,
        sender_id: str,
    ) -> tuple[str, str, str]:
        del sheet_text, conversation_id, sender_id
        self.called = True
        return self._department, self._table_amount, self._uppercase_amount_text


class ReimbursementAttachmentProcessorTests(unittest.TestCase):
    def test_normalize_amount_text_supports_required_equivalent_forms(self) -> None:
        cases = {
            "壹佰零陆元整": "106",
            "壹佰零陆元": "106",
            "106元整": "106",
            "一百零六元整": "106",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, _normalize_amount_text(raw))

    def test_normalize_amount_text_supports_spaces_and_punctuation(self) -> None:
        cases = {
            "  壹佰零陆  元整 ": "106",
            "人民币一百零六元整。": "106",
            "106 元 ": "106",
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(expected, _normalize_amount_text(raw))

    def test_normalize_amount_text_returns_empty_for_invalid_values(self) -> None:
        for raw in ("", "   ", "不是金额", "abc"):
            with self.subTest(raw=raw):
                self.assertEqual("", _normalize_amount_text(raw))

    def test_extract_department_and_amount_uses_total_intersection_and_ignores_unrelated_numbers(self) -> None:
        rows = [
            ["部门", "总经办", "", "日期", "46115"],
            ["费用项目", "金额(元)"],
            ["交通", "50"],
            ["住宿", "56"],
            ["合计", "106", "", "大写金额", "壹佰零陆元整"],
        ]
        department, amount = _extract_department_and_amount_from_rows(rows=rows)
        self.assertEqual("总经办", department)
        self.assertEqual("106", amount)

    def test_extract_department_supports_label_value_in_next_row(self) -> None:
        rows = [
            ["部门", "", "", "日期", "2024-09-20"],
            ["总经办", "", "", "金额(元)", "106"],
            ["合计", "106", "大写金额", "壹佰零陆元整"],
        ]
        department, amount = _extract_department_and_amount_from_rows(rows=rows)
        self.assertEqual("总经办", department)
        self.assertEqual("106", amount)

    def test_resolve_file_bytes_supports_download_code_path(self) -> None:
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
        )
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_download_code="download-code-001",
            robot_code="dingbot-001",
        )

        post_calls: list[tuple[str, dict[str, object]]] = []
        get_calls: list[str] = []

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            post_calls.append((str(url), kwargs))
            if str(url).endswith("/v1.0/oauth2/accessToken"):
                return _FakeResponse({"accessToken": "token-1", "expireIn": 7200})
            return _FakeResponse({"downloadUrl": "https://example.local/file.xlsx"})

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            get_calls.append(str(url))
            return _FakeResponse({}, content=b"xlsx-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
                result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"xlsx-binary", result)
        self.assertEqual(2, len(post_calls))
        self.assertTrue(post_calls[1][0].endswith("/v1.0/robot/messageFiles/download"))
        self.assertEqual(
            {
                "downloadCode": "download-code-001",
                "robotCode": "dingbot-001",
            },
            post_calls[1][1]["json"],
        )
        self.assertEqual(["https://example.local/file.xlsx"], get_calls)

    def test_process_uses_table_amount_as_primary_when_uppercase_matches(self) -> None:
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            uppercase_amount_converter=_FakeUppercaseAmountConverter("106"),
            logger=Mock(),
        )
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_content_base64="ZmFrZQ==",
        )
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._parse_xlsx_fields = (  # type: ignore[method-assign]
            lambda file_bytes, conversation_id="", sender_id="": ("总经办", "106", "未知金额格式")
        )
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("106", result.amount)
        self.assertEqual("106", result.table_amount)
        self.assertEqual("106", result.uppercase_amount_numeric)
        self.assertFalse(result.amount_conflict)
        self.assertEqual("table", result.amount_source)
        self.assertIn("校验通过", result.amount_source_note)

    def test_process_falls_back_to_table_amount_when_uppercase_parse_fails(self) -> None:
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            uppercase_amount_converter=_FakeUppercaseAmountConverter("", should_raise=True),
            logger=Mock(),
        )
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_content_base64="ZmFrZQ==",
        )
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._parse_xlsx_fields = (  # type: ignore[method-assign]
            lambda file_bytes, conversation_id="", sender_id="": ("总经办", "106", "未知金额格式")
        )
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("106", result.amount)
        self.assertEqual("106", result.table_amount)
        self.assertEqual("", result.uppercase_amount_numeric)
        self.assertFalse(result.amount_conflict)
        self.assertEqual("table_fallback", result.amount_source)
        self.assertIn("采用表格金额", result.amount_source_note)

    def test_process_marks_conflict_when_uppercase_amount_differs_from_table(self) -> None:
        logger = Mock()
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            uppercase_amount_converter=_FakeUppercaseAmountConverter("107"),
            logger=logger,
        )
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_content_base64="ZmFrZQ==",
        )
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._parse_xlsx_fields = (  # type: ignore[method-assign]
            lambda file_bytes, conversation_id="", sender_id="": ("总经办", "106", "未知金额格式")
        )
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("106", result.amount)
        self.assertEqual("106", result.table_amount)
        self.assertEqual("107", result.uppercase_amount_numeric)
        self.assertTrue(result.amount_conflict)
        self.assertIn("不一致", result.amount_conflict_note)
        self.assertEqual("table_conflict", result.amount_source)
        self.assertTrue(logger.warning.called)

    def test_process_uses_llm_table_field_fallback_when_department_missing(self) -> None:
        table_field_extractor = _FakeTableFieldExtractor(department="总经办")
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            table_field_extractor=table_field_extractor,
            logger=Mock(),
        )
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_content_base64="ZmFrZQ==",
        )
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_rows",
            return_value=[
                ["部门", "", "", "日期", "2024-09-20"],
                ["", "", "", "金额(元)", "106"],
                ["合计", "106", "大写金额", "壹佰零陆元整"],
            ],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("总经办", result.department)
        self.assertEqual("106", result.amount)
        self.assertTrue(table_field_extractor.called)


if __name__ == "__main__":
    unittest.main()
