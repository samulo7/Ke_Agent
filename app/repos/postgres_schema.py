from __future__ import annotations

import re
from typing import Any

from app.repos.runtime_connection import RuntimeConnection, wrap_runtime_connection

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def bootstrap_postgres_schema(
    connection: Any,
    *,
    docs_table: str = "knowledge_docs",
    chunks_table: str = "doc_chunks",
    quote_fields_table: str = "knowledge_quote_fields",
    validation_runs_table: str = "knowledge_validation_runs",
    publish_logs_table: str = "knowledge_publish_logs",
) -> None:
    runtime = wrap_runtime_connection(connection)
    docs = _validate_identifier(docs_table)
    chunks = _validate_identifier(chunks_table)
    quote_fields = _validate_identifier(quote_fields_table)
    validation_runs = _validate_identifier(validation_runs_table)
    publish_logs = _validate_identifier(publish_logs_table)
    runtime.execute(
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
            permitted_depts_csv TEXT NOT NULL DEFAULT '',
            knowledge_kind TEXT NOT NULL DEFAULT 'policy_doc',
            review_status TEXT NOT NULL DEFAULT 'draft',
            created_by TEXT NOT NULL DEFAULT '',
            updated_by TEXT NOT NULL DEFAULT '',
            published_by TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            last_validated_at TEXT NOT NULL DEFAULT '',
            is_deleted INTEGER NOT NULL DEFAULT 0
        )
        """
    )
    runtime.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {chunks} (
            chunk_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES {docs}(doc_id),
            chunk_index INTEGER NOT NULL DEFAULT 0,
            chunk_text TEXT NOT NULL,
            chunk_vector TEXT NOT NULL DEFAULT ''
        )
        """
    )
    runtime.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {quote_fields} (
            doc_id TEXT PRIMARY KEY REFERENCES {docs}(doc_id),
            quote_item_name TEXT NOT NULL DEFAULT '',
            quote_item_code TEXT NOT NULL DEFAULT '',
            spec_model TEXT NOT NULL DEFAULT '',
            quote_category TEXT NOT NULL DEFAULT '',
            price_amount DOUBLE PRECISION NOT NULL DEFAULT 0,
            price_currency TEXT NOT NULL DEFAULT 'CNY',
            unit TEXT NOT NULL DEFAULT '',
            tax_included INTEGER NOT NULL DEFAULT 1,
            effective_date TEXT NOT NULL DEFAULT '',
            expire_date TEXT NOT NULL DEFAULT '',
            quote_version TEXT NOT NULL DEFAULT '',
            non_standard_action TEXT NOT NULL DEFAULT '',
            source_note TEXT NOT NULL DEFAULT '',
            has_price_conflict INTEGER NOT NULL DEFAULT 0,
            price_conflict_note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    runtime.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {validation_runs} (
            validation_id TEXT PRIMARY KEY,
            doc_id TEXT REFERENCES {docs}(doc_id),
            question TEXT NOT NULL DEFAULT '',
            role_context TEXT NOT NULL DEFAULT '',
            dept_context TEXT NOT NULL DEFAULT '',
            matched_doc_ids_json TEXT NOT NULL DEFAULT '[]',
            reply_channel TEXT NOT NULL DEFAULT 'text',
            reply_preview_json TEXT NOT NULL DEFAULT '{{}}',
            permission_decision TEXT NOT NULL DEFAULT 'allow',
            validation_result TEXT NOT NULL DEFAULT 'failed',
            validated_by TEXT NOT NULL DEFAULT '',
            validated_at TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    runtime.execute(
        f"""
        CREATE TABLE IF NOT EXISTS {publish_logs} (
            publish_log_id TEXT PRIMARY KEY,
            doc_id TEXT NOT NULL REFERENCES {docs}(doc_id),
            publish_action TEXT NOT NULL DEFAULT 'publish',
            publish_status TEXT NOT NULL DEFAULT 'failed',
            validation_id TEXT REFERENCES {validation_runs}(validation_id),
            published_by TEXT NOT NULL DEFAULT '',
            published_at TEXT NOT NULL DEFAULT '',
            note TEXT NOT NULL DEFAULT ''
        )
        """
    )
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{chunks}_doc_id ON {chunks}(doc_id)")
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{docs}_status ON {docs}(status)")
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{docs}_review_status ON {docs}(review_status)")
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{docs}_knowledge_kind ON {docs}(knowledge_kind)")
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{validation_runs}_doc_id ON {validation_runs}(doc_id)")
    runtime.execute(f"CREATE INDEX IF NOT EXISTS idx_{publish_logs}_doc_id ON {publish_logs}(doc_id)")
    runtime.commit()
