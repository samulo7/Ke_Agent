from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

from app.integrations.dingtalk.reimbursement_attachment import (
    DingTalkReimbursementAttachmentProcessor,
    ReimbursementAttachmentSettings,
    _MergedRange,
    _SheetModel,
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

    def test_resolve_file_bytes_supports_download_code_path(self) -> None:
        processor = self._build_processor()
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

    def test_sheet_selection_prefers_second_sheet_with_exact_name(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet_cover = self._build_sheet(index=1, name="封面", rows=[["差旅费报销单"]])
        sheet_target = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["大写金额", "陆佰伍拾元整"],
            ],
        )
        sheet_other = self._build_sheet(
            index=3,
            name="其他",
            rows=[["部门", "错误部门"], ["合计", "999"], ["大写金额", "玖佰玖拾玖元整"]],
        )

        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[sheet_cover, sheet_target, sheet_other],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("职能部", result.department)
        self.assertEqual("650", result.amount)
        selection = result.extraction_evidence.get("sheet_selection", {})
        self.assertEqual(2, selection.get("selected_sheet_index"))
        self.assertEqual("差旅费报销单", selection.get("selected_sheet_name"))
        self.assertFalse(selection.get("fallback_used", True))

    def test_sheet_selection_falls_back_to_marker_scan_when_second_sheet_not_target(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet_one = self._build_sheet(index=1, name="封面", rows=[["说明"]])
        sheet_two = self._build_sheet(index=2, name="报销模板", rows=[["部门", "错误部门"]])
        sheet_three = self._build_sheet(
            index=3,
            name="sheet3",
            rows=[
                ["标题", "差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["大写金额", "陆佰伍拾元整"],
            ],
        )

        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[sheet_one, sheet_two, sheet_three],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("职能部", result.department)
        selection = result.extraction_evidence.get("sheet_selection", {})
        self.assertEqual(3, selection.get("selected_sheet_index"))
        self.assertTrue(selection.get("fallback_used", False))

    def test_process_fails_when_target_sheet_is_missing(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]

        sheet_one = self._build_sheet(index=1, name="封面", rows=[["说明"]])
        sheet_two = self._build_sheet(index=2, name="sheet2", rows=[["部门", "测试"]])

        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[sheet_one, sheet_two],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertFalse(result.success)
        self.assertEqual("未找到差旅费报销单", result.reason)
        self.assertIn("sheet_selection", result.extraction_evidence)

    def test_merged_cells_are_used_in_anchor_window_extraction(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "", "", ""],
                ["职能部", "", "", ""],
                ["合计", "", "", "650", ""],
                ["大写金额", "陆佰伍拾元整", "", "", ""],
            ],
            merged_ranges=(
                _MergedRange(start_row=2, start_col=0, end_row=2, end_col=2),
                _MergedRange(start_row=3, start_col=3, end_row=3, end_col=4),
                _MergedRange(start_row=4, start_col=1, end_row=4, end_col=3),
            ),
        )

        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("职能部", result.department)
        self.assertEqual("650", result.amount)
        merged_info = result.extraction_evidence.get("merged_cells", {})
        self.assertGreaterEqual(int(merged_info.get("total_ranges", 0)), 1)
        self.assertGreaterEqual(int(merged_info.get("candidate_merged_hits", 0)), 1)

    def test_total_amount_filter_penalizes_date_serial_noise(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "46115", "", "", "650"],
                ["大写金额", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("650", result.amount)

    def test_total_amount_filter_rejects_zero_noise(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "销售部"],
                ["合计金额", "0", "", "", "650"],
                ["大写金额", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("650", result.amount)

    def test_uppercase_anchor_supports_rmb_variant_label(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["人民币（大写）", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("650", result.amount)
        self.assertEqual("陆佰伍拾元整", result.uppercase_amount_text)

    def test_uppercase_extraction_uses_extended_window_when_primary_misses(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["大写金额", "", "", "", "", "", "", "", "", "", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("650", result.amount)
        uppercase_evidence = result.extraction_evidence.get("uppercase_amount", {})
        self.assertEqual("fallback_extended", uppercase_evidence.get("window_profile"))

    def test_uppercase_candidate_filter_rejects_non_amount_text(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "销售部"],
                ["合计金额", "650"],
                ["大写金额", "本次报销金额如下"],
                ["", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertEqual("650", result.amount)
        self.assertEqual("陆佰伍拾元整", result.uppercase_amount_text)

    def test_strict_missing_field_returns_locatable_reason(self) -> None:
        processor = self._build_processor()
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertFalse(result.success)
        self.assertEqual("未识别到大写金额（如模板确无字段请人工确认）", result.reason)
        self.assertIn("uppercase_amount", result.extraction_evidence)

    def test_process_emits_structured_log_events_for_sheet_and_fields(self) -> None:
        logger = Mock()
        processor = self._build_processor(logger=logger)
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["大写金额", "陆佰伍拾元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        info_events = self._extract_obs_events(logger_method=logger.info)
        self.assertIn("reimbursement_attachment_sheet_selected", info_events)
        self.assertIn("reimbursement_attachment_field_extracted", info_events)

    def test_process_marks_conflict_when_uppercase_amount_differs_from_table(self) -> None:
        logger = Mock()
        processor = self._build_processor(
            uppercase_amount_converter=_FakeUppercaseAmountConverter("651"),
            logger=logger,
        )
        message = self._file_message()
        processor._resolve_file_bytes = lambda message: b"xlsx"  # type: ignore[method-assign]
        processor._upload_pdf_and_get_media_id = lambda pdf_bytes: "media-1"  # type: ignore[method-assign]

        sheet = self._build_sheet(
            index=2,
            name="差旅费报销单",
            rows=[
                ["差旅费报销单"],
                ["部门", "职能部"],
                ["合计", "650"],
                ["大写金额", "金额待补元整"],
            ],
        )
        with patch(
            "app.integrations.dingtalk.reimbursement_attachment._extract_xlsx_sheets",
            return_value=[self._build_sheet(index=1, name="封面", rows=[["说明"]]), sheet],
        ):
            result = processor.process(message=message, conversation_id="conv-file-001", sender_id="user-file-001")

        self.assertTrue(result.success)
        self.assertTrue(result.amount_conflict)
        self.assertIn("不一致", result.amount_conflict_note)
        warning_events = self._extract_obs_events(logger_method=logger.warning)
        self.assertIn("reimbursement_attachment_amount_mismatch", warning_events)

    @staticmethod
    def _extract_obs_events(*, logger_method: Mock) -> list[str]:
        events: list[str] = []
        for call in logger_method.call_args_list:
            kwargs = call.kwargs or {}
            extra = kwargs.get("extra") if isinstance(kwargs, dict) else None
            if not isinstance(extra, dict):
                continue
            obs = extra.get("obs")
            if not isinstance(obs, dict):
                continue
            event = obs.get("event")
            if isinstance(event, str) and event:
                events.append(event)
        return events

    @staticmethod
    def _file_message() -> IncomingChatMessage:
        return IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="file",
            text="",
            file_name="差旅费报销单.xlsx",
            file_content_base64="ZmFrZQ==",
        )

    @staticmethod
    def _build_processor(
        *,
        uppercase_amount_converter: _FakeUppercaseAmountConverter | None = None,
        logger: Mock | None = None,
    ) -> DingTalkReimbursementAttachmentProcessor:
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        return DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            uppercase_amount_converter=uppercase_amount_converter,
            logger=logger,
        )

    @staticmethod
    def _build_sheet(
        *,
        index: int,
        name: str,
        rows: list[list[str]],
        merged_ranges: tuple[_MergedRange, ...] = (),
    ) -> _SheetModel:
        merged_parent: dict[tuple[int, int], tuple[int, int]] = {}
        for merged in merged_ranges:
            for row in range(merged.start_row, merged.end_row + 1):
                for col in range(merged.start_col, merged.end_col + 1):
                    merged_parent[(row, col)] = (merged.start_row, merged.start_col)
        return _SheetModel(
            index=index,
            name=name,
            path=f"xl/worksheets/sheet{index}.xml",
            rows=rows,
            merged_ranges=merged_ranges,
            merged_parent=merged_parent,
        )


if __name__ == "__main__":
    unittest.main()
