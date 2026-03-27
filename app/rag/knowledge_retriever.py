from __future__ import annotations

import os
from dataclasses import dataclass

from app.repos.knowledge_repository import KnowledgeRepository
from app.schemas.dingtalk_chat import IntentType
from app.schemas.knowledge import (
    KnowledgeAccessContext,
    KnowledgeEntry,
    RestrictedEvidence,
    RestrictedKnowledgeEntry,
    RetrievedEvidence,
)

DEFAULT_TOP_K = 5


def _normalize(text: str) -> str:
    return "".join(text.strip().lower().split())


def resolve_top_k_from_env() -> int:
    raw_value = (os.getenv("PGVECTOR_TOP_K") or "").strip()
    if not raw_value:
        return DEFAULT_TOP_K
    try:
        parsed = int(raw_value)
    except ValueError:
        return DEFAULT_TOP_K
    return parsed if parsed > 0 else DEFAULT_TOP_K


@dataclass(frozen=True)
class _ScoredEntry:
    entry: KnowledgeEntry
    score: int


@dataclass(frozen=True)
class _ScoredRestrictedEntry:
    entry: RestrictedKnowledgeEntry
    score: int


class KnowledgeRetriever:
    """A-08/B-13 deterministic retriever with document-first ranking."""

    def __init__(self, *, repository: KnowledgeRepository, top_k: int | None = None) -> None:
        self._repository = repository
        self._top_k = top_k if top_k is not None and top_k > 0 else resolve_top_k_from_env()

    def retrieve(
        self,
        *,
        question: str,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> tuple[RetrievedEvidence, ...]:
        normalized_question = _normalize(question)
        if not normalized_question:
            return ()

        candidates: list[_ScoredEntry] = []
        for entry in self._repository.list_entries_for_retrieval(
            intent=intent,
            access_context=access_context,
        ):
            score = self._score_entry(entry=entry, question=normalized_question)
            if score <= 0:
                continue
            candidates.append(_ScoredEntry(entry=entry, score=score))

        sorted_candidates = sorted(
            candidates,
            key=lambda item: (
                0 if item.entry.source_type == "document" else 1,
                -item.score,
                item.entry.source_id,
            ),
        )
        selected = sorted_candidates[: self._top_k]
        return tuple(
            RetrievedEvidence(entry=item.entry, score=item.score, rank=index + 1)
            for index, item in enumerate(selected)
        )

    def retrieve_restricted(
        self,
        *,
        question: str,
        intent: IntentType,
        access_context: KnowledgeAccessContext | None = None,
    ) -> tuple[RestrictedEvidence, ...]:
        normalized_question = _normalize(question)
        if not normalized_question:
            return ()

        candidates: list[_ScoredRestrictedEntry] = []
        for entry in self._repository.list_restricted_entries_for_retrieval(
            intent=intent,
            access_context=access_context,
        ):
            score = self._score_restricted_entry(entry=entry, question=normalized_question)
            if score <= 0:
                continue
            candidates.append(_ScoredRestrictedEntry(entry=entry, score=score))

        sorted_candidates = sorted(
            candidates,
            key=lambda item: (-item.score, item.entry.source_id),
        )
        selected = sorted_candidates[: self._top_k]
        return tuple(
            RestrictedEvidence(entry=item.entry, score=item.score, rank=index + 1)
            for index, item in enumerate(selected)
        )

    @staticmethod
    def _score_entry(*, entry: KnowledgeEntry, question: str) -> int:
        return KnowledgeRetriever._score(
            keywords=entry.keywords,
            title=entry.title,
            summary=entry.summary,
            question=question,
        )

    @staticmethod
    def _score_restricted_entry(*, entry: RestrictedKnowledgeEntry, question: str) -> int:
        return KnowledgeRetriever._score(
            keywords=entry.keywords,
            title=entry.title,
            summary=entry.summary,
            question=question,
        )

    @staticmethod
    def _score(*, keywords: tuple[str, ...], title: str, summary: str, question: str) -> int:
        keyword_hits = sum(1 for keyword in keywords if keyword in question)
        title_hit = 1 if _normalize(title) in question else 0
        summary_hit = 1 if _normalize(summary) in question else 0
        return keyword_hits * 10 + title_hit * 2 + summary_hit
