from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.schemas.knowledge import KnowledgeEntry


class KnowledgeRepository(ABC):
    """Repository contract for A-08 knowledge retrieval."""

    @abstractmethod
    def list_entries(self) -> Sequence[KnowledgeEntry]:
        raise NotImplementedError

    @abstractmethod
    def knowledge_version(self) -> str:
        raise NotImplementedError
