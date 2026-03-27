from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Sequence

from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import KnowledgeAccessContext, KnowledgeEntry, RestrictedKnowledgeEntry


class KnowledgeRepository(ABC):
    """Repository contract for A-08/B-13 knowledge retrieval."""

    @abstractmethod
    def list_entries(self) -> Sequence[KnowledgeEntry]:
        raise NotImplementedError

    def list_entries_for_retrieval(
        self,
        *,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> Sequence[KnowledgeEntry]:
        del access_context
        return tuple(entry for entry in self.list_entries() if intent in entry.intents)

    def list_restricted_entries_for_retrieval(
        self,
        *,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> Sequence[RestrictedKnowledgeEntry]:
        del intent
        del access_context
        return ()

    @abstractmethod
    def knowledge_version(self) -> str:
        raise NotImplementedError
