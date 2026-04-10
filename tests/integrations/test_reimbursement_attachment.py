from __future__ import annotations

import unittest
from unittest.mock import Mock
from unittest.mock import patch

import requests

from app.integrations.dingtalk.reimbursement_attachment import (
    DingTalkReimbursementAttachmentProcessor,
    ReimbursementAttachmentSettings,
    _ScreenshotBytesResolution,
    _normalize_amount_text,
    _normalize_label_text,
    build_default_reimbursement_attachment_processor,
    load_reimbursement_attachment_settings,
)
from app.schemas.dingtalk_chat import IncomingChatMessage


class _FakeResponse:
    def __init__(
        self,
        payload: dict[str, object],
        status_code: int = 200,
        content: bytes = b"",
        headers: dict[str, str] | None = None,
    ) -> None:
        self._payload = payload
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            response = requests.Response()
            response.status_code = self.status_code
            raise requests.HTTPError("http error", response=response)

    def json(self):  # type: ignore[no-untyped-def]
        return dict(self._payload)


class _FakeScreenshotExtractor:
    def __init__(self, payload: dict[str, object] | None = None, *, should_raise: bool = False) -> None:
        self._payload = payload or {
            "department_label": "部门",
            "department": "总经办",
            "amount": "106",
            "amount_row_header": "合计",
            "amount_col_header": "合计金额",
            "evidence": {
                "template": "模板已命中",
                "department": "部门标签右侧识别为总经办",
                "amount": "合计行与合计金额列交叉值为106",
            },
        }
        self._should_raise = should_raise
        self.calls = 0

    def extract(
        self,
        *,
        image_bytes: bytes,
        conversation_id: str,
        sender_id: str,
    ) -> dict[str, object]:
        del image_bytes, conversation_id, sender_id
        self.calls += 1
        if self._should_raise:
            raise RuntimeError("vision unavailable")
        return dict(self._payload)


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

    def test_normalize_label_text_accepts_colon_variants(self) -> None:
        self.assertEqual("部门", _normalize_label_text("部门"))
        self.assertEqual("部门", _normalize_label_text("部门："))
        self.assertEqual("部门", _normalize_label_text(" 部门 : "))
        self.assertEqual("所属部门", _normalize_label_text("所属部门："))

    def test_load_reimbursement_attachment_settings_defaults_to_screenshot_mode(self) -> None:
        settings = load_reimbursement_attachment_settings(
            {
                "DINGTALK_REIMBURSE_APPROVAL_ENABLED": "true",
            }
        )
        self.assertTrue(settings.enabled)
        self.assertEqual("screenshot_only", settings.attachment_mode)
        self.assertEqual("qwen-vl-ocr-latest", settings.vision_model)

    def test_build_default_processor_prefers_dedicated_reimburse_vision_endpoint_and_key(self) -> None:
        env = {
            "DINGTALK_REIMBURSE_APPROVAL_ENABLED": "true",
            "DINGTALK_CLIENT_ID": "cid",
            "DINGTALK_CLIENT_SECRET": "secret",
            "DINGTALK_REIMBURSE_VISION_MODEL": "Qwen3.5-Flash",
            "DINGTALK_REIMBURSE_VISION_API_KEY": "vision-key",
            "DINGTALK_REIMBURSE_VISION_BASE_URL": "https://vision.example/v1",
            "LLM_API_KEY": "shared-key",
            "LLM_BASE_URL": "https://shared.example/v1",
        }
        fake_client = Mock()
        fake_extractor = Mock()

        with patch("app.integrations.dingtalk.reimbursement_attachment.HttpQwenChatClient", return_value=fake_client) as mock_client_cls:
            with patch(
                "app.integrations.dingtalk.reimbursement_attachment.QwenReimbursementScreenshotFieldExtractor",
                return_value=fake_extractor,
            ) as mock_extractor_cls:
                processor = build_default_reimbursement_attachment_processor(env)

        self.assertIsNotNone(processor)
        mock_client_cls.assert_called_once_with(api_key="vision-key", endpoint="https://vision.example/v1")
        mock_extractor_cls.assert_called_once()
        self.assertEqual("Qwen3.5-Flash", mock_extractor_cls.call_args.kwargs["model"])
        self.assertIs(fake_client, mock_extractor_cls.call_args.kwargs["llm_client"])

        extractor = _FakeScreenshotExtractor()
        processor = self._build_processor(screenshot_field_extractor=extractor)
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(content=b"png-binary")  # type: ignore[method-assign]

        result = processor.process(
            message=IncomingChatMessage(
                event_id="evt-file-002",
                conversation_id="conv-file-002",
                conversation_type="single",
                sender_id="user-file-002",
                message_type="file",
                text="",
                file_name="差旅费报销单.xlsx",
                file_content_base64="ZmFrZQ==",
            ),
            conversation_id="conv-file-002",
            sender_id="user-file-002",
        )

        self.assertFalse(result.success)
        self.assertIn("请发送单张完整报销单截图", result.reason)
        self.assertEqual(0, extractor.calls)

    def test_process_disabled_reason_uses_screenshot_wording(self) -> None:
        processor = DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=ReimbursementAttachmentSettings(
                enabled=False,
                attachment_mode="screenshot_only",
                vision_model="qwen-vl-ocr-latest",
                openapi_endpoint="https://api.dingtalk.com",
                legacy_openapi_endpoint="https://oapi.dingtalk.com",
                upload_media_type="file",
            ),
            screenshot_field_extractor=_FakeScreenshotExtractor(),
        )

        result = processor.process(
            message=self._picture_message(),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertFalse(result.success)
        self.assertIn("截图识别能力未启用", result.reason)

    def test_resolve_file_bytes_prefers_inline_base64(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="aW1hZ2UtYmluYXJ5",
        )

        result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"image-binary", result.content)
        self.assertEqual("inline_base64", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_treats_empty_base64_as_missing_and_uses_inline_url(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-001-empty",
            conversation_id="conv-file-001-empty",
            conversation_type="single",
            sender_id="user-file-001-empty",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="",
            file_download_url="https://example.local/picture-empty.png",
        )
        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            self.assertEqual("https://example.local/picture-empty.png", str(url))
            return _FakeResponse({}, content=b"image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"image-binary", result.content)
        self.assertEqual("inline_url", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_falls_back_to_inline_url_when_base64_invalid(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-invalid-base64-001",
            conversation_id="conv-file-invalid-base64-001",
            conversation_type="single",
            sender_id="user-file-invalid-base64-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="not-base64-@@@",
            file_download_url="https://example.local/picture-fallback.png",
        )

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            self.assertEqual("https://example.local/picture-fallback.png", str(url))
            return _FakeResponse({}, content=b"fallback-image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"fallback-image-binary", result.content)
        self.assertEqual("inline_url", result.diagnostics.get("source_used"))
        self.assertTrue(result.diagnostics.get("inline_base64_invalid"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_reports_inline_base64_invalid_when_url_missing(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-invalid-base64-002",
            conversation_id="conv-file-invalid-base64-002",
            conversation_type="single",
            sender_id="user-file-invalid-base64-002",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="invalid@@base64",
            file_download_url="",
            file_source_path="root.file_content_base64",
        )

        result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("inline_base64_invalid", result.failure_category)
        self.assertEqual("inline_base64_decode", result.failure_stage)
        self.assertIn("格式异常", result.reason)
        self.assertEqual("root.file_content_base64", result.diagnostics.get("source_path", ""))

    def test_resolve_file_bytes_reports_inline_url_expired_or_unauthorized(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-inline-url-401-001",
            conversation_id="conv-file-inline-url-401-001",
            conversation_type="single",
            sender_id="user-file-inline-url-401-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_url="https://example.local/picture-401.png",
        )

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs, url
            response = requests.Response()
            response.status_code = 403
            raise requests.HTTPError("forbidden", response=response)

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("inline_url_expired_or_unauthorized", result.failure_category)
        self.assertEqual("inline_url_fetch", result.failure_stage)
        self.assertIn("请重发", result.reason)

    def test_resolve_file_bytes_reports_inline_url_transport_error(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-inline-url-500-001",
            conversation_id="conv-file-inline-url-500-001",
            conversation_type="single",
            sender_id="user-file-inline-url-500-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_url="https://example.local/picture-500.png",
        )

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs, url
            response = requests.Response()
            response.status_code = 500
            raise requests.HTTPError("server error", response=response)

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("inline_url_transport_error", result.failure_category)
        self.assertEqual("inline_url_fetch", result.failure_stage)
        self.assertIn("稍后重试", result.reason)

    def test_resolve_file_bytes_reports_missing_inline_source_when_both_sources_absent(self) -> None:
        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-inline-source-missing-001",
            conversation_id="conv-file-inline-source-missing-001",
            conversation_type="single",
            sender_id="user-file-inline-source-missing-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="",
            file_download_url="",
        )

        result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("missing_inline_image_source", result.failure_category)
        self.assertEqual("inline_message_payload", result.failure_stage)
        self.assertIn("请直接发送原始截图图片", result.reason)

    def test_resolve_file_bytes_uses_download_code_fallback_when_inline_sources_absent(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-success-001",
            conversation_id="conv-file-download-code-success-001",
            conversation_type="single",
            sender_id="user-file-download-code-success-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="",
            file_download_url="",
            file_download_code="picture-code-001",
            robot_code="dingrobot-001",
        )

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            self.assertEqual("https://api.dingtalk.com/v1.0/robot/messageFiles/download", str(url))
            headers = kwargs.get("headers") or {}
            self.assertEqual("token-001", headers.get("x-acs-dingtalk-access-token"))
            self.assertEqual({"downloadCode": "picture-code-001", "robotCode": "dingrobot-001"}, kwargs.get("json"))
            return _FakeResponse({}, content=b"download-code-image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"download-code-image-binary", result.content)
        self.assertEqual("download_code", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_falls_back_to_download_code_after_inline_url_failure(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-fallback-001",
            conversation_id="conv-file-download-code-fallback-001",
            conversation_type="single",
            sender_id="user-file-download-code-fallback-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_url="https://example.local/expired-inline-url.png",
            file_download_code="picture-code-fallback",
            robot_code="dingrobot-fallback",
        )

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs, url
            response = requests.Response()
            response.status_code = 403
            raise requests.HTTPError("forbidden", response=response)

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            self.assertEqual("https://api.dingtalk.com/v1.0/robot/messageFiles/download", str(url))
            self.assertEqual("picture-code-fallback", (kwargs.get("json") or {}).get("downloadCode"))
            return _FakeResponse({}, content=b"download-code-fallback-image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
            with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
                result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"download-code-fallback-image-binary", result.content)
        self.assertEqual("download_code", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_uses_download_code_json_url_when_download_api_returns_json_body(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-json-url-001",
            conversation_id="conv-file-download-code-json-url-001",
            conversation_type="single",
            sender_id="user-file-download-code-json-url-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_code="picture-code-json-url-001",
            robot_code="dingrobot-json-url-001",
        )

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            del url, kwargs
            return _FakeResponse(
                {"downloadUrl": "https://example.local/downloaded-picture.png"},
                content=b'{"downloadUrl":"https://example.local/downloaded-picture.png"}',
            )

        def _fake_get(url, **kwargs):  # type: ignore[no-untyped-def]
            del kwargs
            self.assertEqual("https://example.local/downloaded-picture.png", url)
            return _FakeResponse({}, content=b"download-code-json-url-image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            with patch("app.integrations.dingtalk.reimbursement_attachment.requests.get", side_effect=_fake_get):
                result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"download-code-json-url-image-binary", result.content)
        self.assertEqual("download_code_json_url", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

    def test_resolve_file_bytes_uses_download_code_json_base64_when_download_api_returns_json_body(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-json-base64-001",
            conversation_id="conv-file-download-code-json-base64-001",
            conversation_type="single",
            sender_id="user-file-download-code-json-base64-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_code="picture-code-json-base64-001",
            robot_code="dingrobot-json-base64-001",
        )

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            del url, kwargs
            return _FakeResponse(
                {"fileContentBase64": "aW1hZ2UtYnl0ZXM="},
                content=b'{"fileContentBase64":"aW1hZ2UtYnl0ZXM="}',
                headers={"Content-Type": "application/json"},
            )

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"image-bytes", result.content)
        self.assertEqual("download_code_json_base64", result.diagnostics.get("source_used"))
        self.assertEqual("", result.failure_category)

        processor = self._build_processor()
        message = IncomingChatMessage(
            event_id="evt-file-download-code-missing-robot-001",
            conversation_id="conv-file-download-code-missing-robot-001",
            conversation_type="single",
            sender_id="user-file-download-code-missing-robot-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="",
            file_download_url="",
            file_download_code="picture-code-missing-robot",
            robot_code="",
        )

        result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("download_code_missing_robot_code", result.failure_category)
        self.assertEqual("download_code_prepare", result.failure_stage)
        self.assertIn("参数缺失", result.reason)

    def test_resolve_file_bytes_reports_download_code_expired_when_download_api_returns_400(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-expired-001",
            conversation_id="conv-file-download-code-expired-001",
            conversation_type="single",
            sender_id="user-file-download-code-expired-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_code="picture-code-expired",
            robot_code="dingrobot-expired",
        )

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            del url, kwargs
            response = requests.Response()
            response.status_code = 400
            raise requests.HTTPError("bad request", response=response)

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            result = processor._resolve_file_bytes(message=message)

        self.assertEqual(b"", result.content)
        self.assertEqual("download_code_expired_or_invalid", result.failure_category)
        self.assertEqual("download_code_request", result.failure_stage)
        self.assertIn("下载码已失效", result.reason)

    def test_resolve_file_bytes_retries_download_code_on_500_then_succeeds(self) -> None:
        processor = self._build_processor()
        processor._get_access_token = lambda: "token-001"  # type: ignore[method-assign]
        message = IncomingChatMessage(
            event_id="evt-file-download-code-retry-001",
            conversation_id="conv-file-download-code-retry-001",
            conversation_type="single",
            sender_id="user-file-download-code-retry-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_download_code="picture-code-retry-001",
            robot_code="dingrobot-retry-001",
        )
        attempts = {"count": 0}

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            del url, kwargs
            attempts["count"] += 1
            if attempts["count"] < 3:
                response = requests.Response()
                response.status_code = 500
                raise requests.HTTPError("server error", response=response)
            return _FakeResponse({}, content=b"download-code-retry-image-binary")

        with patch("app.integrations.dingtalk.reimbursement_attachment.requests.post", side_effect=_fake_post):
            with patch("app.integrations.dingtalk.reimbursement_attachment.sleep", return_value=None):
                result = processor._resolve_file_bytes(message=message)

        self.assertEqual(3, attempts["count"])
        self.assertEqual(b"download-code-retry-image-binary", result.content)
        self.assertEqual("", result.failure_category)
        self.assertEqual("download_code", result.diagnostics.get("source_used"))

    def test_process_succeeds_and_reuses_original_picture_media_id(self) -> None:
        extractor = _FakeScreenshotExtractor()
        processor = self._build_processor(screenshot_field_extractor=extractor)
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(content=b"png-binary")  # type: ignore[method-assign]

        result = processor.process(
            message=self._picture_message(file_media_id="media-picture-1"),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertTrue(result.success)
        self.assertEqual("总经办", result.department)
        self.assertEqual("106", result.amount)
        self.assertEqual("media-picture-1", result.attachment_media_id)
        self.assertEqual("screenshot", result.amount_source)
        self.assertEqual(1, extractor.calls)
        self.assertTrue(result.extraction_evidence["department_match"]["hit"])
        self.assertTrue(result.extraction_evidence["amount_match"]["row_hit"])
        self.assertTrue(result.extraction_evidence["amount_match"]["col_hit"])

    def test_process_uploads_original_picture_when_media_id_missing(self) -> None:
        processor = self._build_processor(screenshot_field_extractor=_FakeScreenshotExtractor())
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(content=b"png-binary")  # type: ignore[method-assign]
        uploaded: list[tuple[bytes, str, str]] = []

        def _fake_upload(*, file_bytes: bytes, filename: str, content_type: str) -> str:
            uploaded.append((file_bytes, filename, content_type))
            return "media-uploaded-1"

        processor._upload_binary_and_get_media_id = _fake_upload  # type: ignore[method-assign]
        result = processor.process(
            message=self._picture_message(file_media_id=""),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertTrue(result.success)
        self.assertEqual("media-uploaded-1", result.attachment_media_id)
        self.assertEqual([(b"png-binary", "报销单截图.png", "image/png")], uploaded)

    def test_process_rejects_when_amount_headers_do_not_match(self) -> None:
        processor = self._build_processor(
            screenshot_field_extractor=_FakeScreenshotExtractor(
                {
                    "department_label": "部门",
                    "department": "总经办",
                    "amount": "106",
                    "amount_row_header": "合计",
                    "amount_col_header": "金额",
                    "evidence": {
                        "template": "模板已命中",
                        "department": "部门标签右侧识别为总经办",
                        "amount": "识别到金额列标题为金额",
                    },
                }
            )
        )
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(content=b"png-binary")  # type: ignore[method-assign]

        result = processor.process(
            message=self._picture_message(),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertFalse(result.success)
        self.assertIn("合计 × 合计金额", result.reason)
        self.assertFalse(result.extraction_evidence["amount_match"]["col_hit"])

    def test_process_rejects_excel_date_serial_noise(self) -> None:
        processor = self._build_processor(
            screenshot_field_extractor=_FakeScreenshotExtractor(
                {
                    "department_label": "部门",
                    "department": "总经办",
                    "amount": "46115",
                    "amount_row_header": "合计",
                    "amount_col_header": "合计金额",
                    "evidence": {
                        "template": "模板已命中",
                        "department": "部门标签右侧识别为总经办",
                        "amount": "识别结果为46115",
                    },
                }
            )
        )
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(content=b"png-binary")  # type: ignore[method-assign]

        result = processor.process(
            message=self._picture_message(),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertFalse(result.success)
        self.assertIn("有效合计金额", result.reason)
        self.assertTrue(result.extraction_evidence["amount_match"]["excel_date_serial_suspected"])

    def test_process_emits_structured_log_events_for_start_and_failure(self) -> None:
        logger = Mock()
        processor = self._build_processor(
            screenshot_field_extractor=_FakeScreenshotExtractor(should_raise=True),
            logger=logger,
        )
        processor._resolve_file_bytes = lambda **kwargs: _ScreenshotBytesResolution(  # type: ignore[method-assign]
            content=b"\x89PNG\r\n\x1a\nmock-image-bytes",
            diagnostics={"source_used": "download_code"},
        )

        result = processor.process(
            message=self._picture_message(),
            conversation_id="conv-file-001",
            sender_id="user-file-001",
        )

        self.assertFalse(result.success)
        info_events = self._extract_obs_events(logger_method=logger.info)
        warning_events = self._extract_obs_events(logger_method=logger.warning)
        exception_events = self._extract_obs_events(logger_method=logger.exception)
        self.assertIn("reimbursement_screenshot_parse_started", info_events)
        self.assertIn("reimbursement_screenshot_parse_failed", warning_events)
        self.assertIn("reimbursement_screenshot_extractor_failed", exception_events)
        self.assertEqual("download_code", result.extraction_evidence["extractor_diagnostics"]["source_used"])
        self.assertEqual("png", result.extraction_evidence["extractor_diagnostics"]["image_format"])
        self.assertEqual("89504e470d0a1a0a6d6f636b", result.extraction_evidence["extractor_diagnostics"]["image_signature"])
        exception_obs = logger.exception.call_args.kwargs["extra"]["obs"]
        self.assertEqual("RuntimeError", exception_obs["diagnostics"]["exception_type"])
        self.assertEqual("vision unavailable", exception_obs["diagnostics"]["exception_message"])

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
    def _picture_message(*, file_media_id: str = "media-picture-1") -> IncomingChatMessage:
        return IncomingChatMessage(
            event_id="evt-file-001",
            conversation_id="conv-file-001",
            conversation_type="single",
            sender_id="user-file-001",
            message_type="picture",
            text="",
            file_name="报销单截图.png",
            file_content_base64="ZmFrZQ==",
            file_media_id=file_media_id,
        )

    @staticmethod
    def _build_processor(
        *,
        screenshot_field_extractor: _FakeScreenshotExtractor | None = None,
        logger: Mock | None = None,
    ) -> DingTalkReimbursementAttachmentProcessor:
        settings = ReimbursementAttachmentSettings(
            enabled=True,
            attachment_mode="screenshot_only",
            vision_model="qwen-vl-ocr-latest",
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
            upload_media_type="file",
        )
        return DingTalkReimbursementAttachmentProcessor(
            client_id="cid",
            client_secret="secret",
            settings=settings,
            screenshot_field_extractor=screenshot_field_extractor or _FakeScreenshotExtractor(),
            logger=logger,
        )


if __name__ == "__main__":
    unittest.main()
