from __future__ import annotations

import unittest

from app.schemas.dingtalk_chat import IncomingChatMessage
from app.services.single_chat import SingleChatService


class _RaisingKnowledgeAnswerService:
    def answer(self, *, question: str, intent: str):  # type: ignore[no-untyped-def]
        raise RuntimeError("simulated downstream failure")


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
    def test_returns_knowledge_text_answer_for_policy_question(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="宴请标准是什么"))
        self.assertTrue(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("policy_process", result.intent)
        self.assertEqual("knowledge_answer", result.reason)
        self.assertIn("doc-policy-banquet-2026-01", result.source_ids)
        self.assertEqual("allow", result.permission_decision)
        self.assertTrue(result.knowledge_version)
        self.assertTrue(result.answered_at)
        self.assertGreaterEqual(len(result.citations), 1)

    def test_returns_no_hit_when_knowledge_is_missing(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="hello there"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("other", result.intent)
        self.assertEqual("knowledge_no_hit", result.reason)
        self.assertEqual(0, len(result.source_ids))

    def test_returns_clarification_for_ambiguous_question(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="这个怎么弄"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("ambiguous_question", result.reason)
        self.assertIn("仅追问一次", result.reply.text or "")
        self.assertIn("请补充", result.reply.text or "")

    def test_returns_low_confidence_fallback_for_unclear_scope(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我想问个事儿，帮我处理一下"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("low_confidence_fallback", result.reason)
        self.assertIn("无法准确判断", result.reply.text or "")

    def test_returns_system_fallback_when_answer_service_raises(self) -> None:
        service = SingleChatService(knowledge_answer_service=_RaisingKnowledgeAnswerService())
        result = service.handle(make_message(text="宴请标准是什么"))
        self.assertFalse(result.handled)
        self.assertEqual("text", result.reply.channel)
        self.assertEqual("system_fallback", result.reason)
        self.assertIn("稍后再试", result.reply.text or "")
        self.assertIn("联系", result.reply.text or "")

    def test_returns_flow_guidance_card_for_leave(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="请假流程入口在哪"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])
        self.assertEqual("leave", result.intent)

    def test_returns_flow_guidance_card_for_reimbursement(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="出差报销怎么弄"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("flow_guidance", result.reply.interactive_card["card_type"])
        self.assertEqual("reimbursement", result.intent)

    def test_returns_application_draft_card(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(text="我要申请项目资料"))
        self.assertTrue(result.handled)
        self.assertEqual("interactive_card", result.reply.channel)
        self.assertEqual("application_draft", result.reply.interactive_card["card_type"])
        self.assertEqual("document_request", result.intent)

    def test_rejects_non_single_chat(self) -> None:
        service = SingleChatService()
        result = service.handle(make_message(conversation_type="group"))
        self.assertFalse(result.handled)
        self.assertEqual("non_single_chat", result.reason)
        self.assertEqual("other", result.intent)


if __name__ == "__main__":
    unittest.main()
