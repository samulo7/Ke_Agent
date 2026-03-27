from __future__ import annotations

import unittest

from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.file_asset import FileAsset, FileSearchResult
from app.services.file_request import FileRequestService


class _FakeFileRepository:
    def __init__(self, *, result: FileSearchResult | None) -> None:
        self._result = result
        self.calls: list[tuple[str, str]] = []

    def search(self, *, query_text: str, variant: str, requester_context=None):  # type: ignore[no-untyped-def]
        self.calls.append((query_text, variant))
        if self._result is None:
            return FileSearchResult.no_hit()
        return self._result


def make_message(*, text: str, conversation_id: str = "conv-file-1", sender_id: str = "user-file-1") -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-file-1",
        conversation_id=conversation_id,
        conversation_type="single",
        sender_id=sender_id,
        message_type="text",
        text=text,
    )


class FileRequestServiceTests(unittest.TestCase):
    def test_first_turn_collects_variant(self) -> None:
        service = FileRequestService(file_repository=_FakeFileRepository(result=None))

        result = service.handle(message=make_message(text="帮我找一下定影器的采购合同"), query_text="帮我找一下定影器的采购合同")

        self.assertFalse(result.handled)
        self.assertEqual("file_lookup_collecting", result.reason)
        self.assertEqual("file_request", result.intent)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("纸质版还是扫描版", result.reply.text or "")

    def test_second_turn_with_variant_returns_sent_sequence(self) -> None:
        repository = _FakeFileRepository(
            result=FileSearchResult(
                matched=True,
                match_score=0.98,
                asset=FileAsset(
                    file_id="file-1",
                    contract_key="dingyingqi",
                    title="定影器采购合同-2024版",
                    variant="scan",
                    file_url="https://example.local/files/dingyingqi-2024-scan",
                    tags=("采购", "合同", "定影器"),
                    status="active",
                    updated_at="2026-03-27",
                ),
            )
        )
        service = FileRequestService(file_repository=repository)
        service.handle(message=make_message(text="帮我找一下定影器的采购合同"), query_text="帮我找一下定影器的采购合同")

        result = service.handle(
            message=make_message(text="扫描版"),
            query_text="扫描版",
        )

        self.assertTrue(result.handled)
        self.assertEqual("file_lookup_sent", result.reason)
        self.assertEqual("file_request", result.intent)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("优先为您提供扫描版", result.reply.text or "")
        self.assertEqual(2, len(result.followup_replies))
        self.assertIn("已在文件库找到匹配文件", result.followup_replies[0].text or "")
        self.assertIn("文件已发送，请查收", result.followup_replies[1].text or "")
        self.assertIn("https://example.local/files/dingyingqi-2024-scan", result.followup_replies[0].text or "")
        self.assertEqual([("帮我找一下定影器的采购合同", "scan")], repository.calls)

    def test_second_turn_no_hit_returns_executable_fallback(self) -> None:
        repository = _FakeFileRepository(result=None)
        service = FileRequestService(file_repository=repository)
        service.handle(message=make_message(text="帮我找一下定影器的采购合同"), query_text="帮我找一下定影器的采购合同")

        result = service.handle(
            message=make_message(text="纸质版"),
            query_text="纸质版",
        )

        self.assertFalse(result.handled)
        self.assertEqual("file_lookup_no_hit", result.reason)
        self.assertEqual("text", result.reply.channel)
        self.assertIn("补充关键词", result.reply.text or "")
        self.assertIn("人事行政", result.reply.text or "")
        self.assertEqual([("帮我找一下定影器的采购合同", "paper")], repository.calls)


if __name__ == "__main__":
    unittest.main()
