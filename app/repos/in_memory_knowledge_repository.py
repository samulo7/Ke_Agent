from __future__ import annotations

from collections.abc import Sequence

from app.rag.sample_corpus import SAMPLE_KNOWLEDGE_VERSION, load_sample_entries
from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.knowledge import KnowledgeEntry


class InMemoryKnowledgeRepository(KnowledgeRepository):
    """A-08 sample repository for deterministic local retrieval tests."""

    def __init__(
        self,
        *,
        entries: Sequence[KnowledgeEntry] | None = None,
        version: str = SAMPLE_KNOWLEDGE_VERSION,
    ) -> None:
        self._entries = tuple(entries) if entries is not None else load_sample_entries()
        self._version = version

    def list_entries(self) -> Sequence[KnowledgeEntry]:
        return self._entries

    def knowledge_version(self) -> str:
        return self._version
