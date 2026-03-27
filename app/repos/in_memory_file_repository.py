from __future__ import annotations

import re
from collections.abc import Sequence

from app.repos.file_repository import FileRepository
from app.schemas.file_asset import FileAsset, FileSearchResult, FileVariant
from app.schemas.user_context import UserContext

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,}")
_STOP_TOKENS = {
    "帮我",
    "请帮我",
    "一下",
    "找",
    "查",
    "查下",
    "查找",
    "检索",
    "我要",
    "我想",
    "需要",
    "给我",
    "发我",
    "扫描版",
    "纸质版",
    "扫描件",
    "纸质件",
}


def _normalize(text: str) -> str:
    return "".join(text.strip().lower().split())


def _extract_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize(text)
    tokens = [item for item in _TOKEN_RE.findall(normalized) if item and item not in _STOP_TOKENS]
    return tuple(dict.fromkeys(tokens))


def _score_asset(*, query_text: str, asset: FileAsset) -> int:
    normalized_query = _normalize(query_text)
    title = _normalize(asset.title)
    contract_key = _normalize(asset.contract_key)
    tags = tuple(_normalize(tag) for tag in asset.tags)
    score = 0

    if normalized_query and normalized_query in title:
        score += 120
    if normalized_query and normalized_query in contract_key:
        score += 120

    for token in _extract_tokens(query_text):
        if token in title:
            score += 25
        if token in contract_key:
            score += 30
        if any(token in tag for tag in tags):
            score += 15

    return score


class InMemoryFileRepository(FileRepository):
    def __init__(self, assets: Sequence[FileAsset] | None = None) -> None:
        self._assets = tuple(assets) if assets is not None else self._default_assets()

    def search(
        self,
        *,
        query_text: str,
        variant: FileVariant,
        requester_context: UserContext | None = None,
    ) -> FileSearchResult:
        del requester_context  # reserved for future permission expansion
        best_asset: FileAsset | None = None
        best_score = -1
        for asset in self._assets:
            if asset.status != "active":
                continue
            if asset.variant != variant:
                continue
            score = _score_asset(query_text=query_text, asset=asset)
            if score > best_score:
                best_score = score
                best_asset = asset

        if best_asset is None or best_score <= 0:
            return FileSearchResult.no_hit()

        return FileSearchResult(matched=True, asset=best_asset, match_score=float(best_score))

    @staticmethod
    def _default_assets() -> tuple[FileAsset, ...]:
        return (
            FileAsset(
                file_id="file-dingyingqi-contract-2024-scan",
                contract_key="dingyingqi_contract",
                title="定影器采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/dingyingqi-contract-2024-scan",
                tags=("采购", "合同", "定影器", "2024"),
                status="active",
                updated_at="2026-03-27",
            ),
            FileAsset(
                file_id="file-dingyingqi-contract-2024-paper",
                contract_key="dingyingqi_contract",
                title="定影器采购合同-2024版",
                variant="paper",
                file_url="https://example.local/files/dingyingqi-contract-2024-paper",
                tags=("采购", "合同", "定影器", "2024"),
                status="active",
                updated_at="2026-03-27",
            ),
        )
