from __future__ import annotations

from typing import Protocol

from app.schemas.file_asset import FileSearchResult, FileVariant
from app.schemas.user_context import UserContext


class FileRepository(Protocol):
    def search(
        self,
        *,
        query_text: str,
        variant: FileVariant,
        requester_context: UserContext | None = None,
    ) -> FileSearchResult: ...
