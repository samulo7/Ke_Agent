from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import psycopg
from psycopg.rows import dict_row

from app.core.env_loader import load_project_env
from app.repos.postgres_schema import bootstrap_postgres_schema

TABLES: tuple[str, ...] = (
    "knowledge_docs",
    "doc_chunks",
    "knowledge_quote_fields",
    "knowledge_validation_runs",
    "knowledge_publish_logs",
)


def _sqlite_path() -> Path:
    raw_path = (os.getenv("KEAGENT_SQLITE_PATH") or "").strip()
    if raw_path:
        return Path(raw_path)
    return Path(__file__).resolve().parents[2] / ".local" / "keagent_runtime.sqlite3"


def _connect_postgres() -> psycopg.Connection:
    return psycopg.connect(
        host=(os.getenv("PG_HOST") or "").strip(),
        port=int((os.getenv("PG_PORT") or "5432").strip() or "5432"),
        dbname=(os.getenv("PG_DATABASE") or "").strip(),
        user=(os.getenv("PG_USER") or "").strip(),
        password=(os.getenv("PG_PASSWORD") or "").strip(),
        row_factory=dict_row,
    )


def migrate() -> None:
    load_project_env()
    sqlite_path = _sqlite_path()
    source = sqlite3.connect(str(sqlite_path))
    source.row_factory = sqlite3.Row
    target = _connect_postgres()
    bootstrap_postgres_schema(target)

    try:
        for table in TABLES:
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if not rows:
                continue
            columns = [str(column) for column in rows[0].keys()]
            placeholder_sql = ", ".join(["%s"] * len(columns))
            updates = ", ".join(f"{column} = EXCLUDED.{column}" for column in columns[1:])
            insert_sql = (
                f"INSERT INTO {table} ({', '.join(columns)}) "
                f"VALUES ({placeholder_sql}) "
                f"ON CONFLICT ({columns[0]}) DO UPDATE SET {updates}"
            )
            for row in rows:
                values = []
                for column in columns:
                    value = row[column]
                    if table == "knowledge_validation_runs" and column == "doc_id" and value == "":
                        value = None
                    if table == "knowledge_publish_logs" and column == "validation_id" and value == "":
                        value = None
                    values.append(value)
                target.execute(insert_sql, tuple(values))
        target.commit()
    finally:
        source.close()
        target.close()


if __name__ == "__main__":
    migrate()
