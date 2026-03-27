from __future__ import annotations

import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.schemas.knowledge import KnowledgeAnswer
from app.services.knowledge_answering import KnowledgeAnswerService

DOC_QUERIES: tuple[str, ...] = (
    "宴请标准是什么",
    "商务接待宴请规则",
    "财务制度报销总则是什么",
    "费用报销制度要求",
    "门禁权限怎么申请",
    "访客管理规范有哪些",
    "请假流程步骤说明",
    "出差报销操作手册内容",
    "出差报销流程怎么走",
    "病假材料规范",
)

FAQ_QUERIES: tuple[str, ...] = (
    "XX定影器多少钱",
    "定影器报价是多少",
    "A1维护套件价格",
    "A1套件多少钱",
    "B2鼓组件报价",
    "B2鼓组件价格多少",
    "标准定影器单价",
    "维护套件A1报价是多少",
    "B2配件单价",
    "这个定影器价格1200吗",
)


def _is_unified_template(answer: KnowledgeAnswer) -> bool:
    if not answer.found:
        return False
    text = answer.text
    markers = ("结论：", "步骤：", "来源：", "下一步：")
    if any(marker not in text for marker in markers):
        return False

    if not (
        text.index("结论：")
        < text.index("步骤：")
        < text.index("来源：")
        < text.index("下一步：")
    ):
        return False

    if "\n1. " not in text:
        return False

    if "\n1. [" not in text:
        return False

    return True


class AnswerTemplateComplianceTests(unittest.TestCase):
    def setUp(self) -> None:
        repository = InMemoryKnowledgeRepository()
        self.service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=repository, top_k=5),
            repository=repository,
        )

    def test_document_template_compliance_rate_is_at_least_95_percent(self) -> None:
        compliant = 0
        for question in DOC_QUERIES:
            answer = self.service.answer(question=question, intent="policy_process")
            if _is_unified_template(answer):
                compliant += 1
        rate = compliant / len(DOC_QUERIES)
        self.assertGreaterEqual(rate, 0.95, msg=f"document template compliance too low: {rate:.2%}")

    def test_faq_template_compliance_rate_is_at_least_95_percent(self) -> None:
        compliant = 0
        for question in FAQ_QUERIES:
            answer = self.service.answer(question=question, intent="fixed_quote")
            if _is_unified_template(answer):
                compliant += 1
        rate = compliant / len(FAQ_QUERIES)
        self.assertGreaterEqual(rate, 0.95, msg=f"faq template compliance too low: {rate:.2%}")


if __name__ == "__main__":
    unittest.main()
