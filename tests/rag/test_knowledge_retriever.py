from __future__ import annotations

import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository


class KnowledgeRetrieverTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()

    def test_document_is_prioritized_over_faq_when_both_match(self) -> None:
        retriever = KnowledgeRetriever(repository=self.repository, top_k=6)
        evidences = retriever.retrieve(question="制度入口在哪里看", intent="policy_process")

        self.assertGreaterEqual(len(evidences), 2)
        self.assertEqual("document", evidences[0].entry.source_type)
        self.assertIn("faq-policy-entry-2026-02", [item.entry.source_id for item in evidences])

    def test_top_k_truncation_and_stable_order(self) -> None:
        retriever = KnowledgeRetriever(repository=self.repository, top_k=2)
        evidences = retriever.retrieve(question="出差报销流程发票", intent="policy_process")

        self.assertEqual(2, len(evidences))
        self.assertEqual(1, evidences[0].rank)
        self.assertEqual(2, evidences[1].rank)
        self.assertEqual("doc-process-reimbursement-2026-02", evidences[0].entry.source_id)
        self.assertEqual("doc-policy-finance-2026-02", evidences[1].entry.source_id)

    def test_low_match_query_returns_no_result(self) -> None:
        retriever = KnowledgeRetriever(repository=self.repository, top_k=5)
        evidences = retriever.retrieve(question="火星基地午餐菜单", intent="other")
        self.assertEqual(0, len(evidences))


if __name__ == "__main__":
    unittest.main()
