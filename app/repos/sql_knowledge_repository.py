from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from typing import Any

from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeAccessContext, KnowledgeEntry, RestrictedKnowledgeEntry

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _normalize_source_type(raw: str) -> str:
    source_type = (raw or "").strip().lower()
    return "faq" if source_type == "faq" else "document"


def _normalize_permission_scope(raw: str) -> str:
    permission_scope = (raw or "").strip().lower()
    return "sensitive" if permission_scope == "sensitive" else "department"


def bootstrap_sqlite_schema(
    connection: sqlite3.Connection,
    *,
    docs_table: str = "knowledge_docs",
    chunks_table: str = "doc_chunks",
) -> None:
    """Create A-10/B-13 split tables for SQLite-based local verification."""

    docs = _validate_identifier(docs_table)
    chunks = _validate_identifier(chunks_table)
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {docs} (
            doc_id TEXT PRIMARY KEY,
            source_type TEXT NOT NULL,
            title TEXT NOT NULL,
            summary TEXT NOT NULL,
            applicability TEXT NOT NULL,
            next_step TEXT NOT NULL,
            source_uri TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'active',
            owner TEXT NOT NULL DEFAULT '',
            category TEXT NOT NULL DEFAULT '',
            version_tag TEXT NOT NULL DEFAULT '',
            keywords_csv TEXT NOT NULL DEFAULT '',
            intents_csv TEXT NOT NULL DEFAULT '',
            permission_scope TEXT NOT NULL DEFAULT 'public',
            permitted_depts_csv TEXT NOT NULL DEFAULT ''
        );

        CREATE TABLE IF NOT EXISTS {chunks} (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL,
            chunk_index INTEGER NOT NULL DEFAULT 0,
            chunk_text TEXT NOT NULL,
            chunk_vector TEXT NOT NULL DEFAULT '',
            FOREIGN KEY (doc_id) REFERENCES {docs}(doc_id)
        );

        CREATE INDEX IF NOT EXISTS idx_{chunks}_doc_id ON {chunks}(doc_id);
        CREATE INDEX IF NOT EXISTS idx_{docs}_status ON {docs}(status);
        """
    )


class SQLKnowledgeRepository(KnowledgeRepository):
    """A-10/B-13 SQL-backed repository with JOIN filtering and restricted-hit probing."""

    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        docs_table: str = "knowledge_docs",
        chunks_table: str = "doc_chunks",
        version: str = "a10-sql-v1",
    ) -> None:
        self._connection = connection
        self._docs_table = _validate_identifier(docs_table)
        self._chunks_table = _validate_identifier(chunks_table)
        self._version = version

    def list_entries(self) -> Sequence[KnowledgeEntry]:
        query = f"""
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
            d.intents_csv
        FROM {self._chunks_table} c
        JOIN {self._docs_table} d
            ON d.doc_id = c.doc_id
        WHERE d.status = ?
        ORDER BY d.source_type, d.doc_id
        """
        rows = self._connection.execute(query, ("active",)).fetchall()
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

        query = f"""
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
            d.intents_csv
        FROM {self._chunks_table} c
        JOIN {self._docs_table} d
            ON d.doc_id = c.doc_id
        WHERE d.status = ?
          AND REPLACE(',' || COALESCE(d.intents_csv, '') || ',', ' ', '') LIKE ?
          AND (
              d.permission_scope = 'public'
              OR (
                  ? <> ''
                  AND d.permission_scope IN ('department', 'sensitive')
                  AND REPLACE(',' || COALESCE(d.permitted_depts_csv, '') || ',', ' ', '') LIKE ?
              )
          )
        ORDER BY d.source_type, d.doc_id
        """
        rows = self._connection.execute(
            query,
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

        query = f"""
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
            d.intents_csv
        FROM {self._docs_table} d
        WHERE d.status = ?
          AND REPLACE(',' || COALESCE(d.intents_csv, '') || ',', ' ', '') LIKE ?
          AND d.permission_scope IN ('department', 'sensitive')
          AND NOT (
              ? <> ''
              AND REPLACE(',' || COALESCE(d.permitted_depts_csv, '') || ',', ' ', '') LIKE ?
          )
        ORDER BY d.doc_id
        """
        rows = self._connection.execute(
            query,
            ("active", intent_pattern, department, department_pattern),
        ).fetchall()
        return tuple(self._row_to_restricted_entry(row) for row in rows)

    def knowledge_version(self) -> str:
        return self._version

    @staticmethod
    def _row_to_entry(row: Any) -> KnowledgeEntry:
        keywords = _parse_csv(str(row[8] or ""))
        intents = _parse_csv(str(row[9] or ""))
        return KnowledgeEntry(
            source_id=str(row[0]),
            source_type=_normalize_source_type(str(row[1])),  # type: ignore[arg-type]
            title=str(row[2]),
            summary=str(row[3]),
            applicability=str(row[4]),
            next_step=str(row[5]),
            source_uri=str(row[6]),
            updated_at=str(row[7]),
            keywords=keywords,
            intents=intents or ("other",),  # type: ignore[arg-type]
        )

    @staticmethod
    def _row_to_restricted_entry(row: Any) -> RestrictedKnowledgeEntry:
        keywords = _parse_csv(str(row[8] or ""))
        intents = _parse_csv(str(row[9] or ""))
        return RestrictedKnowledgeEntry(
            source_id=str(row[0]),
            source_type=_normalize_source_type(str(row[1])),  # type: ignore[arg-type]
            title=str(row[2]),
            summary=str(row[3]),
            next_step=str(row[4]),
            owner=str(row[5] or ""),
            permission_scope=_normalize_permission_scope(str(row[6])),  # type: ignore[arg-type]
            updated_at=str(row[7]),
            keywords=keywords,
            intents=intents or ("other",),  # type: ignore[arg-type]
        )
