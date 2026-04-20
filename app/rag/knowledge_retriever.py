from __future__ import annotations

import re
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


def _extract_search_terms(text: str) -> tuple[str, ...]:
    terms: list[str] = []
    for raw in re.findall(r"[\u4e00-\u9fff]{2,16}|[a-zA-Z0-9][a-zA-Z0-9_-]{1,31}", text):
        normalized = _normalize(raw)
        if len(normalized) < 2:
            continue
        candidates = [normalized]
        if all("\u4e00" <= char <= "\u9fff" for char in normalized) and len(normalized) > 4:
            for size in range(2, min(4, len(normalized)) + 1):
                for index in range(len(normalized) - size + 1):
                    candidates.append(normalized[index : index + size])
        for candidate in candidates:
            if len(candidate) < 2 or candidate in terms:
                continue
            terms.append(candidate)
    return tuple(terms)


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
            score = self._score_entry(entry=entry, question=question)
            if score <= 0:
                continue
            candidates.append(_ScoredEntry(entry=entry, score=score))

        sorted_candidates = sorted(
            candidates,
            key=lambda item: (
                0 if item.score >= 100 else 1,
                -item.score,
                0 if item.entry.source_type == "document" else 1,
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
            score = self._score_restricted_entry(entry=entry, question=question)
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
        search_text = "\n".join(
            part for part in (entry.summary, entry.applicability, entry.next_step, entry.search_text) if part.strip()
        )
        score = KnowledgeRetriever._score(
            keywords=entry.keywords,
            title=entry.title,
            summary=entry.summary,
            search_text=search_text,
            question=question,
        )
        if entry.source_type == "faq" and "faq" in question.lower():
            score += 15
        return score

    @staticmethod
    def _score_restricted_entry(*, entry: RestrictedKnowledgeEntry, question: str) -> int:
        search_text = "\n".join(part for part in (entry.summary, entry.next_step, entry.search_text) if part.strip())
        return KnowledgeRetriever._score(
            keywords=entry.keywords,
            title=entry.title,
            summary=entry.summary,
            search_text=search_text,
            question=question,
        )

    @staticmethod
    def _score(*, keywords: tuple[str, ...], title: str, summary: str, search_text: str, question: str) -> int:
        normalized_title = _normalize(title).rstrip("?？")
        normalized_question = question.rstrip("?？")
        normalized_summary = _normalize(summary)
        normalized_search_text = _normalize(search_text)
        keyword_hits = sum(1 for keyword in keywords if _normalize(keyword) in question)
        exact_title_hit = 1 if normalized_title == normalized_question else 0
        title_hit = 1 if normalized_title and (normalized_title in normalized_question or normalized_question in normalized_title) else 0
        summary_hit = 1 if normalized_question and normalized_question in normalized_summary else 0
        chunk_hit = 1 if normalized_question and normalized_question in normalized_search_text else 0
        matched_terms = [term for term in _extract_search_terms(question) if term in normalized_search_text]
        strong_term_hits = [term for term in matched_terms if len(term) >= 3]
        weak_term_hits = [term for term in matched_terms if len(term) == 2]
        term_score = len(strong_term_hits) * 4 + (2 if len(weak_term_hits) >= 2 else 0)
        if not any((exact_title_hit, keyword_hits, title_hit, summary_hit, chunk_hit)) and term_score == 0:
            return 0
        return exact_title_hit * 100 + keyword_hits * 10 + title_hit * 5 + summary_hit * 8 + chunk_hit * 20 + term_score
