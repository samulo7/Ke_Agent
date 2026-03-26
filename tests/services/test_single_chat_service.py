from __future__ import annotations

import unittest

from app.schemas.dingtalk_chat import IncomingChatMessage
from app.services.single_chat import SingleChatService


def make_message(
    *,
    conversation_type: str = "single",
    message_type: str = "text",
    text: str = "hello",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-1",
        conversation_id="conv-1",
        conversation_type=conversation_type,  # type: ignore[arg-type]
        sender_id="user-1",
        message_type=message_type,
        text=text,
    )


class SingleChatServiceTests(unittest.TestCase):
    def test_returns_text_for_generic_message(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="hello there"))
        self.assertTrue(result.handled)
        self.assertEqual("text", result.reply.channel)

    def test_returns_flow_guidance_card(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="请假流程入口在哪"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])

    def test_returns_application_draft_card(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我要申请项目资料"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("application_draft", result.reply.interactive_card["card_type"])

    def test_rejects_non_single_chat(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(conversation_type="group"))
        self.assertFalse(result.handled)
        self.assertEqual("non_single_chat", result.reason)


if __name__ == "__main__":
    unittest.main()
