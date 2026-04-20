from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Literal

DBBackend = Literal["sqlite", "postgres"]
_PLACEHOLDER_RE = re.compile(r"\?")


@dataclass(frozen=True)
class RuntimeConnection:
    backend: DBBackend
    raw: Any

    def execute(self, query: str, params: tuple[Any, ...] | list[Any] | None = None) -> Any:
        payload = () if params is None else tuple(params)
        return self.raw.execute(_adapt_query(query, backend=self.backend), payload)

    def commit(self) -> None:
        self.raw.commit()

    def close(self) -> None:
        self.raw.close()


def wrap_runtime_connection(connection: Any) -> RuntimeConnection:
    if isinstance(connection, RuntimeConnection):
        return connection
    return RuntimeConnection(backend=detect_backend(connection), raw=connection)


def detect_backend(connection: Any) -> DBBackend:
    raw = connection.raw if isinstance(connection, RuntimeConnection) else connection
    if isinstance(raw, sqlite3.Connection):
        return "sqlite"
    return "postgres"


def _adapt_query(query: str, *, backend: DBBackend) -> str:
    if backend == "sqlite":
        return query
    return _PLACEHOLDER_RE.sub("%s", query)
