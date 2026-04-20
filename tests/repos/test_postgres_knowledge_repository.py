from __future__ import annotations

import os
import unittest

import psycopg
from psycopg.rows import dict_row

from app.repos.postgres_knowledge_repository import PostgresKnowledgeRepository
from app.repos.postgres_schema import bootstrap_postgres_schema
from app.schemas.knowledge import KnowledgeAccessContext


@unittest.skipUnless(os.getenv("PG_TEST_DSN"), "PG_TEST_DSN is required for PostgreSQL integration tests")
class PostgresKnowledgeRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = psycopg.connect(os.environ["PG_TEST_DSN"], row_factory=dict_row)
        bootstrap_postgres_schema(self.connection)
        self.connection.execute("DELETE FROM doc_chunks")
        self.connection.execute("DELETE FROM knowledge_docs")
        self.connection.execute(
            """
            INSERT INTO knowledge_docs (
                doc_id, source_type, title, summary, applicability, next_step, source_uri, updated_at,
                status, owner, category, version_tag, keywords_csv, intents_csv, permission_scope,
                permitted_depts_csv, knowledge_kind, review_status, created_by, updated_by,
                published_by, published_at, last_validated_at, is_deleted
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                "doc-hr-faq",
                "faq",
                "试用期员工可以请假吗",
                "试用期员工可以按公司制度申请事假/病假。",
                "全体员工",
                "如为病假，请补充证明材料。",
                "employee-handbook-v3",
                "2026-04-20T09:12:00+08:00",
                "active",
                "hr",
                "faq",
                "v1",
                "试用期,请假,病假",
                "policy_process,leave",
                "public",
                "",
                "faq",
                "published",
                "system",
                "system",
                "system",
                "2026-04-20T09:12:00+08:00",
                "2026-04-20T09:12:00+08:00",
                0,
            ),
        )
        self.connection.execute(
            "INSERT INTO doc_chunks (chunk_id, doc_id, chunk_index, chunk_text, chunk_vector) VALUES (%s, %s, %s, %s, %s)",
            (
                "chunk-doc-hr-faq-0",
                "doc-hr-faq",
                0,
                "试用期员工可以请假吗\n试用期员工可以按公司制度申请事假/病假。",
                "[]",
            ),
        )
        self.connection.commit()
        self.repository = PostgresKnowledgeRepository(connection=self.connection)

    def tearDown(self) -> None:
        self.connection.execute("DELETE FROM doc_chunks")
        self.connection.execute("DELETE FROM knowledge_docs")
        self.connection.commit()
        self.connection.close()

    def test_list_entries_for_retrieval_returns_active_entry(self) -> None:
        items = self.repository.list_entries_for_retrieval(
            intent="leave",
            access_context=KnowledgeAccessContext(user_id="u-1", dept_id="hr"),
        )
        self.assertEqual(1, len(items))
        self.assertEqual("doc-hr-faq", items[0].source_id)

    def test_knowledge_version_defaults_to_pg_version(self) -> None:
        self.assertEqual("a10-pg-v1", self.repository.knowledge_version())
