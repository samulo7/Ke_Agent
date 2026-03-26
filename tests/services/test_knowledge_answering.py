from __future__ import annotations

import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.services.knowledge_answering import KnowledgeAnswerService


class KnowledgeAnswerServiceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=self.repository, top_k=5),
            repository=self.repository,
        )

    def test_policy_question_returns_structured_text_and_citations(self) -> None:
        answer = self.service.answer(question="宴请标准是什么", intent="policy_process")
        self.assertTrue(answer.found)
        self.assertIn("结论：", answer.text)
        self.assertIn("来源：", answer.text)
        self.assertIn("下一步：", answer.text)
        self.assertIn("doc-policy-banquet-2026-01", answer.source_ids)
        self.assertEqual("allow", answer.permission_decision)
        self.assertEqual("a08-sample-v1", answer.knowledge_version)
        self.assertTrue(answer.answered_at)
        self.assertGreaterEqual(len(answer.citations), 1)

    def test_fixed_quote_no_hit_does_not_fabricate_price(self) -> None:
        answer = self.service.answer(question="Z9特殊组件成本核算", intent="fixed_quote")
        self.assertFalse(answer.found)
        self.assertIn("不提供推测价格", answer.text)
        self.assertEqual(0, len(answer.source_ids))
        self.assertEqual(0, len(answer.citations))


if __name__ == "__main__":
    unittest.main()
