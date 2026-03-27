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


if __name__ == "__main__":
    unittest.main()
