from __future__ import annotations

import re
import sqlite3
from typing import Any

from app.repos.file_repository import FileRepository
from app.schemas.file_asset import FileAsset, FileSearchResult, FileVariant
from app.schemas.user_context import UserContext

_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,}")
_STOP_TOKENS = (
    "请帮我",
    "帮我",
    "我想要",
    "想要",
    "我要",
    "我想",
    "需要",
    "给我",
    "发我",
    "一下",
    "查下",
    "查找",
    "检索",
    "找",
    "查",
    "扫描版",
    "纸质版",
    "扫描件",
    "纸质件",
    "文件",
    "文档",
    "资料",
    "的",
)
_STOP_TOKEN_SET = set(_STOP_TOKENS)


def _validate_identifier(name: str) -> str:
    if not _IDENTIFIER_RE.match(name):
        raise ValueError(f"invalid SQL identifier: {name!r}")
    return name


def _normalize(text: str) -> str:
    return "".join(text.strip().lower().split())


def _parse_csv(raw: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _extract_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize(text)
    tokens = [item for item in _TOKEN_RE.findall(normalized) if item and item not in _STOP_TOKEN_SET]
    return tuple(dict.fromkeys(tokens))


def _strip_stop_tokens(text: str) -> str:
    normalized = _normalize(text)
    for token in _STOP_TOKENS:
        normalized = normalized.replace(_normalize(token), "")
    return normalized


def _score_asset(*, query_text: str, asset: FileAsset) -> int:
    normalized_query = _normalize(query_text)
    condensed_query = _strip_stop_tokens(query_text)
    title = _normalize(asset.title)
    contract_key = _normalize(asset.contract_key)
    tags = tuple(_normalize(tag) for tag in asset.tags)
    score = 0

    if normalized_query and normalized_query in title:
        score += 120
    if normalized_query and normalized_query in contract_key:
        score += 120

    if condensed_query and condensed_query in title:
        score += 90
    if condensed_query and condensed_query in contract_key:
        score += 90

    for token in _extract_tokens(query_text):
        if token in title:
            score += 25
        if token in contract_key:
            score += 30
        if any(token in tag for tag in tags):
            score += 15

    return score


def bootstrap_file_assets_sqlite_schema(
    connection: sqlite3.Connection,
    *,
    table_name: str = "file_assets",
) -> None:
    table = _validate_identifier(table_name)
    connection.executescript(
        f"""
        CREATE TABLE IF NOT EXISTS {table} (
            file_id TEXT PRIMARY KEY,
            contract_key TEXT NOT NULL,
            title TEXT NOT NULL,
            variant TEXT NOT NULL,
            file_url TEXT NOT NULL,
            tags_csv TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            updated_at TEXT NOT NULL
        );

        CREATE INDEX IF NOT EXISTS idx_{table}_contract_key ON {table}(contract_key);
        CREATE INDEX IF NOT EXISTS idx_{table}_title ON {table}(title);
        CREATE INDEX IF NOT EXISTS idx_{table}_variant ON {table}(variant);
        CREATE INDEX IF NOT EXISTS idx_{table}_status ON {table}(status);
        """
    )


class SQLFileRepository(FileRepository):
    def __init__(
        self,
        *,
        connection: sqlite3.Connection,
        table_name: str = "file_assets",
    ) -> None:
        self._connection = connection
        self._table_name = _validate_identifier(table_name)

    def search(
        self,
        *,
        query_text: str,
        variant: FileVariant,
        requester_context: UserContext | None = None,
    ) -> FileSearchResult:
        del requester_context  # reserved for future permission expansion
        rows = self._connection.execute(
            f"""
            SELECT
                file_id,
                contract_key,
                title,
                variant,
                file_url,
                tags_csv,
                status,
                updated_at
            FROM {self._table_name}
            WHERE status = 'active'
              AND variant = ?
            ORDER BY updated_at DESC, file_id ASC
            """,
            (variant,),
        ).fetchall()

        best_asset: FileAsset | None = None
        best_score = -1
        for row in rows:
            asset = self._row_to_asset(row)
            score = _score_asset(query_text=query_text, asset=asset)
            if score > best_score:
                best_score = score
                best_asset = asset

        if best_asset is None or best_score <= 0:
            return FileSearchResult.no_hit()

        return FileSearchResult(matched=True, asset=best_asset, match_score=float(best_score))

    @staticmethod
    def _row_to_asset(row: Any) -> FileAsset:
        return FileAsset(
            file_id=str(row[0]),
            contract_key=str(row[1]),
            title=str(row[2]),
            variant=str(row[3]),  # type: ignore[arg-type]
            file_url=str(row[4]),
            tags=_parse_csv(str(row[5] or "")),
            status=str(row[6]),
            updated_at=str(row[7]),
        )
