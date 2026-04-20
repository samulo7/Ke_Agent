from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from app.repos.knowledge_repository import KnowledgeRepository
from app.repos.runtime_connection import RuntimeConnection, wrap_runtime_connection
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeAccessContext, KnowledgeEntry, RestrictedKnowledgeEntry


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in str(raw or "").split(",") if item.strip())


def _normalize_source_type(raw: str) -> str:
    source_type = (raw or "").strip().lower()
    return "faq" if source_type == "faq" else "document"


def _normalize_permission_scope(raw: str) -> str:
    permission_scope = (raw or "").strip().lower()
    return "sensitive" if permission_scope == "sensitive" else "department"


class PostgresKnowledgeRepository(KnowledgeRepository):
    def __init__(
        self,
        *,
        connection: Any,
        docs_table: str = "knowledge_docs",
        chunks_table: str = "doc_chunks",
        version: str = "a10-pg-v1",
    ) -> None:
        self._connection = wrap_runtime_connection(connection)
        self._docs_table = docs_table
        self._chunks_table = chunks_table
        self._version = version

    def list_entries(self) -> Sequence[KnowledgeEntry]:
        rows = self._connection.execute(
            f"""
            SELECT DISTINCT
                d.doc_id,
                d.source_type,
                d.title,
                d.summary,
                d.applicability,
                d.next_step,
                d.source_uri,
                d.updated_at,
                d.keywords_csv,
                d.intents_csv,
                COALESCE(
                    (
                        SELECT string_agg(chunk_text, E'\n' ORDER BY chunk_index)
                        FROM {self._chunks_table} chunk_source
                        WHERE chunk_source.doc_id = d.doc_id
                    ),
                    ''
                ) AS search_text
            FROM {self._chunks_table} c
            JOIN {self._docs_table} d
                ON d.doc_id = c.doc_id
            WHERE d.status = %s
            ORDER BY d.source_type, d.doc_id
            """,
            ("active",),
        ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def list_entries_for_retrieval(
        self,
        *,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> Sequence[KnowledgeEntry]:
        department = (access_context.dept_id if access_context is not None else "").strip()
        intent_pattern = f"%,{intent},%"
        department_pattern = f"%,{department},%"
        rows = self._connection.execute(
            f"""
            SELECT DISTINCT
                d.doc_id,
                d.source_type,
                d.title,
                d.summary,
                d.applicability,
                d.next_step,
                d.source_uri,
                d.updated_at,
                d.keywords_csv,
                d.intents_csv,
                COALESCE(
                    (
                        SELECT string_agg(chunk_text, E'\n' ORDER BY chunk_index)
                        FROM {self._chunks_table} chunk_source
                        WHERE chunk_source.doc_id = d.doc_id
                    ),
                    ''
                ) AS search_text
            FROM {self._chunks_table} c
            JOIN {self._docs_table} d
                ON d.doc_id = c.doc_id
            WHERE d.status = %s
              AND REPLACE(',' || COALESCE(d.intents_csv, '') || ',', ' ', '') LIKE %s
              AND (
                  d.permission_scope = 'public'
                  OR (
                      %s <> ''
                      AND d.permission_scope IN ('department', 'sensitive')
                      AND REPLACE(',' || COALESCE(d.permitted_depts_csv, '') || ',', ' ', '') LIKE %s
                  )
              )
            ORDER BY d.source_type, d.doc_id
            """,
            ("active", intent_pattern, department, department_pattern),
        ).fetchall()
        return tuple(self._row_to_entry(row) for row in rows)

    def list_restricted_entries_for_retrieval(
        self,
        *,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> Sequence[RestrictedKnowledgeEntry]:
        department = (access_context.dept_id if access_context is not None else "").strip()
        intent_pattern = f"%,{intent},%"
        department_pattern = f"%,{department},%"
        rows = self._connection.execute(
            f"""
            SELECT DISTINCT
                d.doc_id,
                d.source_type,
                d.title,
                d.summary,
                d.next_step,
                d.owner,
                d.permission_scope,
                d.updated_at,
                d.keywords_csv,
                d.intents_csv,
                COALESCE(
                    (
                        SELECT string_agg(chunk_text, E'\n' ORDER BY chunk_index)
                        FROM {self._chunks_table} chunk_source
                        WHERE chunk_source.doc_id = d.doc_id
                    ),
                    ''
                ) AS search_text
            FROM {self._docs_table} d
            WHERE d.status = %s
              AND REPLACE(',' || COALESCE(d.intents_csv, '') || ',', ' ', '') LIKE %s
              AND d.permission_scope IN ('department', 'sensitive')
              AND NOT (
                  %s <> ''
                  AND REPLACE(',' || COALESCE(d.permitted_depts_csv, '') || ',', ' ', '') LIKE %s
              )
            ORDER BY d.doc_id
            """,
            ("active", intent_pattern, department, department_pattern),
        ).fetchall()
        return tuple(self._row_to_restricted_entry(row) for row in rows)

    def knowledge_version(self) -> str:
        return self._version

    @staticmethod
    def _row_to_entry(row: dict[str, Any]) -> KnowledgeEntry:
        return KnowledgeEntry(
            source_id=str(row["doc_id"]),
            source_type=_normalize_source_type(str(row["source_type"])),
            title=str(row["title"]),
            summary=str(row["summary"]),
            applicability=str(row["applicability"]),
            next_step=str(row["next_step"]),
            source_uri=str(row["source_uri"]),
            updated_at=str(row["updated_at"]),
            keywords=_parse_csv(str(row.get("keywords_csv", ""))),
            intents=_parse_csv(str(row.get("intents_csv", ""))),
            search_text=str(row.get("search_text", "")),
        )

    @staticmethod
    def _row_to_restricted_entry(row: dict[str, Any]) -> RestrictedKnowledgeEntry:
        return RestrictedKnowledgeEntry(
            source_id=str(row["doc_id"]),
            source_type=_normalize_source_type(str(row["source_type"])),
            title=str(row["title"]),
            summary=str(row["summary"]),
            next_step=str(row["next_step"]),
            owner=str(row["owner"]),
            permission_scope=_normalize_permission_scope(str(row["permission_scope"])),
            updated_at=str(row["updated_at"]),
            keywords=_parse_csv(str(row.get("keywords_csv", ""))),
            intents=_parse_csv(str(row.get("intents_csv", ""))),
            search_text=str(row.get("search_text", "")),
        )
