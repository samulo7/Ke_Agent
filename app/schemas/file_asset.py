from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from app.schemas.user_context import UserContext

FileVariant = Literal["scan", "paper"]


@dataclass(frozen=True)
class FileAsset:
    file_id: str
    contract_key: str
    title: str
    variant: FileVariant
    file_url: str
    tags: tuple[str, ...]
    status: str
    updated_at: str


@dataclass(frozen=True)
class FileSearchQuery:
    query_text: str
    variant: FileVariant
    requester_context: UserContext | None = None


@dataclass(frozen=True)
class FileSearchResult:
    matched: bool
    asset: FileAsset | None = None
    match_score: float = 0.0
    candidates: tuple["FileSearchCandidate", ...] = ()

    @classmethod
    def no_hit(cls) -> "FileSearchResult":
        return cls(matched=False, asset=None, match_score=0.0, candidates=())


@dataclass(frozen=True)
class FileSearchCandidate:
    asset: FileAsset
    match_score: float
