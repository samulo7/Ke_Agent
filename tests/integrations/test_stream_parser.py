from __future__ import annotations

import unittest

from app.integrations.dingtalk.stream_parser import parse_stream_event


def _payload_with_text(text: str) -> dict[str, str]:
    return {
        "event_id": "evt-parser-001",
        "conversation_id": "conv-parser-001",
        "conversation_type": "single",
        "sender_id": "user-parser-001",
        "message_type": "text",
        "text": text,
    }


class StreamParserTests(unittest.TestCase):
    def test_parse_stream_event_keeps_normal_cjk_text(self) -> None:
        message = parse_stream_event(_payload_with_text("宴请标准是什么"))
        self.assertEqual("宴请标准是什么", message.text)

    def test_parse_stream_event_repairs_utf8_latin1_mojibake(self) -> None:
        original = "宴请标准是什么"
        mojibake = original.encode("utf-8").decode("latin-1")
        message = parse_stream_event(_payload_with_text(mojibake))
        self.assertEqual(original, message.text)

    def test_parse_stream_event_extracts_file_payload_fields(self) -> None:
        payload = {
            "data": {
                "event_id": "evt-parser-file-001",
                "conversation_id": "conv-parser-file-001",
                "conversation_type": "single",
                "sender_id": "user-parser-file-001",
                "message_type": "file",
                "content": {
                    "fileName": "差旅费报销单.xlsx",
                    "downloadUrl": "https://example.local/file.xlsx",
                    "mediaId": "media-file-001",
                    "contentBase64": "ZmFrZQ==",
                },
            }
        }
        message = parse_stream_event(payload)
        self.assertEqual("file", message.message_type)
        self.assertEqual("差旅费报销单.xlsx", message.file_name)
        self.assertEqual("https://example.local/file.xlsx", message.file_download_url)
        self.assertEqual("media-file-001", message.file_media_id)
        self.assertEqual("ZmFrZQ==", message.file_content_base64)


if __name__ == "__main__":
    unittest.main()
