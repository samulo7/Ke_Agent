from __future__ import annotations

import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.services.knowledge_answering import KnowledgeAnswerService

FAQ_EVAL_QUERIES: tuple[tuple[str, str], ...] = (
    ("XX定影器多少钱", "faq-quote-fuser-xx-2026-03"),
    ("定影器报价是多少", "faq-quote-fuser-xx-2026-03"),
    ("A1维护套件价格", "faq-quote-kit-a1-2026-03"),
    ("A1套件多少钱", "faq-quote-kit-a1-2026-03"),
    ("B2鼓组件报价", "faq-quote-drum-b2-2026-03"),
    ("B2鼓组件价格多少", "faq-quote-drum-b2-2026-03"),
    ("标准定影器单价", "faq-quote-fuser-xx-2026-03"),
    ("维护套件A1报价是多少", "faq-quote-kit-a1-2026-03"),
    ("B2配件单价", "faq-quote-drum-b2-2026-03"),
    ("这个定影器价格1200吗", "faq-quote-fuser-xx-2026-03"),
)

DOC_EVAL_QUERIES: tuple[tuple[str, str], ...] = (
    ("宴请标准是什么", "人均 300 元"),
    ("商务接待宴请规则", "人均 300 元"),
    ("财务制度报销总则是什么", "报销须提供合规发票"),
    ("费用报销制度要求", "报销须提供合规发票"),
    ("门禁权限怎么申请", "门禁权限申请需部门负责人审批"),
    ("访客管理规范有哪些", "访客需提前登记"),
    ("请假流程步骤说明", "请假需按假别提交申请"),
    ("出差报销操作手册内容", "出差报销需要行程单"),
    ("出差报销流程怎么走", "出差报销需要行程单"),
    ("病假材料规范", "请假需按假别提交申请"),
)


class RetrievalEvaluationTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryKnowledgeRepository()
        self.retriever = KnowledgeRetriever(repository=self.repository, top_k=3)
        self.answer_service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=self.repository, top_k=5),
            repository=self.repository,
        )

    def test_faq_top3_hit_rate_is_at_least_80_percent(self) -> None:
        hits = 0
        for question, expected_source_id in FAQ_EVAL_QUERIES:
            evidences = self.retriever.retrieve(question=question, intent="fixed_quote")
            top3_ids = [item.entry.source_id for item in evidences[:3]]
            if expected_source_id in top3_ids:
                hits += 1
        hit_rate = hits / len(FAQ_EVAL_QUERIES)
        self.assertGreaterEqual(hit_rate, 0.8, msg=f"faq top3 hit rate too low: {hit_rate:.2%}")

    def test_document_answer_accuracy_is_at_least_80_percent(self) -> None:
        correct = 0
        for question, expected_fragment in DOC_EVAL_QUERIES:
            answer = self.answer_service.answer(question=question, intent="policy_process")
            if answer.found and expected_fragment in answer.text:
                correct += 1
        accuracy = correct / len(DOC_EVAL_QUERIES)
        self.assertGreaterEqual(accuracy, 0.8, msg=f"document answer accuracy too low: {accuracy:.2%}")

    def test_citation_validity_is_at_least_95_percent(self) -> None:
        valid_source_ids = {entry.source_id for entry in self.repository.list_entries()}
        total_citations = 0
        valid_citations = 0

        for question, _ in FAQ_EVAL_QUERIES:
            answer = self.answer_service.answer(question=question, intent="fixed_quote")
            for citation in answer.citations:
                total_citations += 1
                if (
                    citation.source_id in valid_source_ids
                    and bool(citation.title.strip())
                    and bool(citation.source_uri.strip())
                    and bool(citation.updated_at.strip())
                ):
                    valid_citations += 1

        for question, _ in DOC_EVAL_QUERIES:
            answer = self.answer_service.answer(question=question, intent="policy_process")
            for citation in answer.citations:
                total_citations += 1
                if (
                    citation.source_id in valid_source_ids
                    and bool(citation.title.strip())
                    and bool(citation.source_uri.strip())
                    and bool(citation.updated_at.strip())
                ):
                    valid_citations += 1

        self.assertGreater(total_citations, 0)
        validity = valid_citations / total_citations
        self.assertGreaterEqual(validity, 0.95, msg=f"citation validity too low: {validity:.2%}")


if __name__ == "__main__":
    unittest.main()
