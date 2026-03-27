from __future__ import annotations

import sqlite3
import unittest

from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import (
    SQLKnowledgeRepository,
    bootstrap_sqlite_schema,
)
from app.schemas.knowledge import KnowledgeAccessContext


class SQLKnowledgeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        bootstrap_sqlite_schema(self.connection)
        self._seed_data()
        self.repository = SQLKnowledgeRepository(connection=self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_data(self) -> None:
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
                    "报销,流程,发票",
                    "policy_process",
                    "department",
                    "finance,ops",
                ),
                (
                    "doc-archived-policy",
                    "document",
                    "历史流程归档",
                    "历史资料不参与检索。",
                    "历史归档",
                    "查看最新版文档",
                    "https://example.local/docs/archived",
                    "2025-12-01",
                    "archived",
                    "ops-team",
                    "policy",
                    "v0",
                    "报销,流程",
                    "policy_process",
                    "public",
                    "",
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
                ("chunk-finance-1", "doc-finance-policy", 0, "财务制度细则中的报销要求", "[0.4,0.6]"),
                ("chunk-archived-1", "doc-archived-policy", 0, "历史归档报销资料", "[0.9,0.3]"),
            ),
        )
        self.connection.commit()

    def test_permission_columns_exist_only_in_knowledge_docs(self) -> None:
        docs_columns = self._table_columns("knowledge_docs")
        chunk_columns = self._table_columns("doc_chunks")

        self.assertIn("permission_scope", docs_columns)
        self.assertIn("permitted_depts_csv", docs_columns)
        self.assertNotIn("permission_scope", chunk_columns)
        self.assertNotIn("permitted_depts_csv", chunk_columns)

    def test_sql_join_permission_filter_matches_department_access(self) -> None:
        sales_entries = self.repository.list_entries_for_retrieval(
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-sales", dept_id="sales"),
        )
        finance_entries = self.repository.list_entries_for_retrieval(
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-finance", dept_id="finance"),
        )

        sales_ids = {item.source_id for item in sales_entries}
        finance_ids = {item.source_id for item in finance_entries}

        self.assertEqual({"doc-public-policy"}, sales_ids)
        self.assertEqual({"doc-public-policy", "doc-finance-policy"}, finance_ids)

    def test_retriever_uses_sql_permission_filtered_candidates(self) -> None:
        retriever = KnowledgeRetriever(repository=self.repository, top_k=5)
        sales_evidences = retriever.retrieve(
            question="报销流程入口怎么走",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-sales", dept_id="sales"),
        )
        finance_evidences = retriever.retrieve(
            question="报销流程入口怎么走",
            intent="policy_process",
            access_context=KnowledgeAccessContext(user_id="u-finance", dept_id="finance"),
        )

        sales_ids = {item.entry.source_id for item in sales_evidences}
        finance_ids = {item.entry.source_id for item in finance_evidences}

        self.assertEqual({"doc-public-policy"}, sales_ids)
        self.assertEqual({"doc-public-policy", "doc-finance-policy"}, finance_ids)

    def _table_columns(self, table_name: str) -> set[str]:
        rows = self.connection.execute(f"PRAGMA table_info({table_name})").fetchall()
        return {str(row["name"]) for row in rows}


if __name__ == "__main__":
    unittest.main()
