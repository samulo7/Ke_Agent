from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.schemas.dingtalk_chat import IntentType, PermissionDecision

KnowledgeSourceType = Literal["document", "faq"]
KnowledgeRestrictionScope = Literal["department", "sensitive"]


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class KnowledgeAccessContext:
    user_id: str = ""
    dept_id: str = ""


@dataclass(frozen=True)
class KnowledgeEntry:
    source_id: str
    source_type: KnowledgeSourceType
    title: str
    summary: str
    applicability: str
    next_step: str
    source_uri: str
    updated_at: str
    keywords: tuple[str, ...]
    intents: tuple[IntentType, ...]


@dataclass(frozen=True)
class RestrictedKnowledgeEntry:
    source_id: str
    source_type: KnowledgeSourceType
    title: str
    summary: str
    next_step: str
    owner: str
    permission_scope: KnowledgeRestrictionScope
    updated_at: str
    keywords: tuple[str, ...]
    intents: tuple[IntentType, ...]


@dataclass(frozen=True)
class KnowledgeCitation:
    source_id: str
    source_type: KnowledgeSourceType
    title: str
    source_uri: str
    updated_at: str

    def to_dict(self) -> dict[str, str]:
        return {
            "source_id": self.source_id,
            "source_type": self.source_type,
            "title": self.title,
            "source_uri": self.source_uri,
            "updated_at": self.updated_at,
        }


@dataclass(frozen=True)
class RetrievedEvidence:
    entry: KnowledgeEntry
    score: int
    rank: int

    def to_citation(self) -> KnowledgeCitation:
        return KnowledgeCitation(
            source_id=self.entry.source_id,
            source_type=self.entry.source_type,
            title=self.entry.title,
            source_uri=self.entry.source_uri,
            updated_at=self.entry.updated_at,
        )


@dataclass(frozen=True)
class RestrictedEvidence:
    entry: RestrictedKnowledgeEntry
    score: int
    rank: int


@dataclass(frozen=True)
class KnowledgeAnswer:
    found: bool
    text: str
    source_ids: tuple[str, ...]
    permission_decision: PermissionDecision
    knowledge_version: str
    answered_at: str
    citations: tuple[KnowledgeCitation, ...]

    @classmethod
    def not_found(
        cls,
        *,
        text: str,
        knowledge_version: str,
        permission_decision: PermissionDecision = "allow",
    ) -> "KnowledgeAnswer":
        return cls(
            found=False,
            text=text,
            source_ids=(),
            permission_decision=permission_decision,
            knowledge_version=knowledge_version,
            answered_at=utc_now_iso(),
            citations=(),
        )

    @classmethod
    def restricted(
        cls,
        *,
        text: str,
        knowledge_version: str,
        permission_decision: PermissionDecision,
        source_ids: tuple[str, ...],
    ) -> "KnowledgeAnswer":
        return cls(
            found=False,
            text=text,
            source_ids=source_ids,
            permission_decision=permission_decision,
            knowledge_version=knowledge_version,
            answered_at=utc_now_iso(),
            citations=(),
        )
