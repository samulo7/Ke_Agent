from __future__ import annotations

import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.tone_resolver import ToneResolver


class KnowledgeAnswerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=self.repository, top_k=5),
            repository=self.repository,
            tone_resolver=ToneResolver(default_tone="conversational"),
        )

    def test_policy_question_returns_structured_text_and_citations(self) -> None:
        answer = self.service.answer(question="宴请标准是什么", intent="policy_process")
        self.assertTrue(answer.found)
        self.assertIn("结论：", answer.text)
        self.assertIn("步骤：", answer.text)
        self.assertIn("来源：", answer.text)
        self.assertIn("下一步：", answer.text)
        self.assertIn("doc-policy-banquet-2026-01", answer.source_ids)
        self.assertEqual("allow", answer.permission_decision)
        self.assertEqual("a08-sample-v1", answer.knowledge_version)
        self.assertTrue(answer.answered_at)
        self.assertGreaterEqual(len(answer.citations), 1)
        self.assertNotIn("适用于：适用于", answer.text)
        self.assertLess(answer.text.index("结论："), answer.text.index("步骤："))
        self.assertLess(answer.text.index("步骤："), answer.text.index("来源："))
        self.assertLess(answer.text.index("来源："), answer.text.index("下一步："))

    def test_fixed_quote_hit_uses_same_unified_template(self) -> None:
        answer = self.service.answer(question="XX定影器多少钱", intent="fixed_quote")
        self.assertTrue(answer.found)
        self.assertIn("结论：", answer.text)
        self.assertIn("步骤：", answer.text)
        self.assertIn("来源：", answer.text)
        self.assertIn("下一步：", answer.text)
        self.assertIn("faq-quote-fuser-xx-2026-03", answer.source_ids)

    def test_fixed_quote_no_hit_does_not_fabricate_price(self) -> None:
        answer = self.service.answer(question="Z9特殊组件成本核算", intent="fixed_quote")
        self.assertFalse(answer.found)
        self.assertIn("不提供推测价格", answer.text)
        self.assertEqual(0, len(answer.source_ids))
        self.assertEqual(0, len(answer.citations))

    def test_formal_tone_changes_step_and_no_hit_wording(self) -> None:
        service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=self.repository, top_k=5),
            repository=self.repository,
            tone_resolver=ToneResolver(default_tone="formal"),
        )

        hit = service.answer(question="宴请标准是什么", intent="policy_process")
        self.assertTrue(hit.found)
        self.assertIn("请先确认适用范围", hit.text)

        no_hit = service.answer(question="Z9特殊组件成本核算", intent="fixed_quote")
        self.assertFalse(no_hit.found)
        self.assertIn("当前未检索到对应固定报价", no_hit.text)


if __name__ == "__main__":
    unittest.main()
