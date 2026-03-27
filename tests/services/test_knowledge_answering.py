from __future__ import annotations

import sqlite3
import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.in_memory_knowledge_repository import InMemoryKnowledgeRepository
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository, bootstrap_sqlite_schema
from app.schemas.knowledge import KnowledgeAccessContext
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


class KnowledgeAnswerServicePermissionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        bootstrap_sqlite_schema(self.connection)
        self.connection.executemany(
            """
            INSERT INTO knowledge_docs (
                doc_id,
                source_type,
                title,
                summary,
                applicability,
                next_step,
                source_uri,
                updated_at,
                status,
                owner,
                category,
                version_tag,
                keywords_csv,
                intents_csv,
                permission_scope,
                permitted_depts_csv
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "doc-public-policy",
                    "document",
                    "报销流程入口说明",
                    "公共流程说明，员工可查看报销入口和步骤。",
                    "适用于全员",
                    "打开钉钉审批-报销入口",
                    "https://example.local/docs/public-policy",
                    "2026-03-20",
                    "active",
                    "hr-team",
                    "policy",
                    "v1",
                    "报销,流程,入口",
                    "policy_process",
                    "public",
                    "",
                ),
                (
                    "doc-finance-policy",
                    "document",
                    "财务报销制度细则",
                    "含票据规范与财务专项口径。",
                    "适用于财务部门",
                    "联系财务专员确认口径",
                    "https://example.local/docs/finance-policy",
                    "2026-03-20",
                    "active",
                    "finance-team",
                    "policy",
                    "v3",
                    "财务,发票,口径",
                    "policy_process",
                    "department",
                    "finance",
                ),
                (
                    "doc-sensitive-budget",
                    "document",
                    "高管预算审批规则",
                    "高管预算审批阈值与审批链路说明。",
                    "适用于财务预算审批岗",
                    "通过钉钉“预算审批”流程提交并抄送财务负责人",
                    "https://example.local/docs/sensitive-budget",
                    "2026-03-21",
                    "active",
                    "finance-owner",
                    "budget",
                    "v2",
                    "高管,预算,审批,敏感",
                    "policy_process",
                    "sensitive",
                    "finance",
                ),
            ),
        )
        self.connection.executemany(
            """
            INSERT INTO doc_chunks (
                chunk_id,
                doc_id,
                chunk_index,
                chunk_text,
                chunk_vector
            ) VALUES (?, ?, ?, ?, ?)
            """,
            (
                ("chunk-public-1", "doc-public-policy", 0, "公共报销流程入口说明", "[0.1,0.2]"),
                ("chunk-finance-1", "doc-finance-policy", 0, "财务制度细则中的发票口径", "[0.4,0.6]"),
                ("chunk-sensitive-1", "doc-sensitive-budget", 0, "敏感预算审批流程说明", "[0.8,0.6]"),
            ),
        )
        self.connection.commit()

        self.repository = SQLKnowledgeRepository(connection=self.connection, version="b13-sql-v1")
        self.service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=self.repository, top_k=5),
            repository=self.repository,
            tone_resolver=ToneResolver(default_tone="neutral"),
        )

    def tearDown(self) -> None:
        self.connection.close()

    def test_allow_for_authorized_department(self) -> None:
        answer = self.service.answer(
            question="财务发票口径怎么执行",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-fin", dept_id="finance"),
        )
        self.assertTrue(answer.found)
        self.assertEqual("allow", answer.permission_decision)
        self.assertIn("doc-finance-policy", answer.source_ids)

    def test_summary_only_for_department_restricted_doc(self) -> None:
        answer = self.service.answer(
            question="财务发票口径怎么执行",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-sales", dept_id="sales"),
        )
        self.assertFalse(answer.found)
        self.assertEqual("summary_only", answer.permission_decision)
        self.assertIn("不可直接查看", answer.text)
        self.assertIn("脱敏摘要", answer.text)
        self.assertIn("申请路径", answer.text)
        self.assertIn("申请草稿", answer.text)

    def test_deny_for_sensitive_restricted_doc(self) -> None:
        answer = self.service.answer(
            question="高管预算审批阈值是多少",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-sales", dept_id="sales"),
        )
        self.assertFalse(answer.found)
        self.assertEqual("deny", answer.permission_decision)
        self.assertIn("受控", answer.text)
        self.assertIn("申请路径", answer.text)
        self.assertNotIn("高管预算审批阈值与审批链路说明", answer.text)

    def test_no_hit_remains_allow_without_permission_misclassification(self) -> None:
        answer = self.service.answer(
            question="完全不存在的问题条目",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-sales", dept_id="sales"),
        )
        self.assertFalse(answer.found)
        self.assertEqual("allow", answer.permission_decision)


if __name__ == "__main__":
    unittest.main()
