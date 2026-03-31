from __future__ import annotations

import re
from collections.abc import Sequence

from app.repos.file_repository import FileRepository
from app.schemas.file_asset import FileAsset, FileSearchCandidate, FileSearchResult, FileVariant
from app.schemas.user_context import UserContext

_TOKEN_RE = re.compile(r"[A-Za-z0-9\u4e00-\u9fff]{2,}")
_STOP_TOKENS = (
    "请帮我申请",
    "帮我申请",
    "请申请",
    "申请",
    "请帮我",
    "帮我",
    "我想要",
    "想要",
    "我要",
    "我想",
    "需要",
    "给我",
    "发我",
    "一下",
    "查下",
    "查找",
    "检索",
    "找",
    "查",
    "扫描版",
    "纸质版",
    "扫描件",
    "纸质件",
    "文件",
    "文档",
    "资料",
    "的",
)
_STOP_TOKEN_SET = set(_STOP_TOKENS)


def _normalize(text: str) -> str:
    return "".join(text.strip().lower().split())


def _extract_tokens(text: str) -> tuple[str, ...]:
    normalized = _normalize(text)
    tokens = [item for item in _TOKEN_RE.findall(normalized) if item and item not in _STOP_TOKEN_SET]
    return tuple(dict.fromkeys(tokens))


def _strip_stop_tokens(text: str) -> str:
    normalized = _normalize(text)
    for token in _STOP_TOKENS:
        normalized = normalized.replace(_normalize(token), "")
    return normalized


def _score_asset(*, query_text: str, asset: FileAsset) -> int:
    normalized_query = _normalize(query_text)
    condensed_query = _strip_stop_tokens(query_text)
    title = _normalize(asset.title)
    contract_key = _normalize(asset.contract_key)
    tags = tuple(_normalize(tag) for tag in asset.tags)
    score = 0

    if normalized_query and normalized_query in title:
        score += 120
    if normalized_query and normalized_query in contract_key:
        score += 120

    if condensed_query and condensed_query in title:
        score += 90
    if condensed_query and condensed_query in contract_key:
        score += 90

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
        scored_assets: list[tuple[int, FileAsset]] = []
        for asset in self._assets:
            if asset.status != "active":
                continue
            if asset.variant != variant:
                continue
            score = _score_asset(query_text=query_text, asset=asset)
            if score > 0:
                scored_assets.append((score, asset))

        if not scored_assets:
            return FileSearchResult.no_hit()

        scored_assets.sort(key=lambda item: item[0], reverse=True)
        best_score, best_asset = scored_assets[0]
        candidates = tuple(
            FileSearchCandidate(asset=asset, match_score=float(score))
            for score, asset in scored_assets[:5]
        )
        return FileSearchResult(
            matched=True,
            asset=best_asset,
            match_score=float(best_score),
            candidates=candidates,
        )

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
                file_id="file-printer-contract-2023-scan",
                contract_key="printer_contract",
                title="打印机采购合同-2023版",
                variant="scan",
                file_url="https://example.local/files/printer-contract-2023-scan",
                tags=("采购", "合同", "打印机", "2023"),
                status="active",
                updated_at="2026-03-30",
            ),
            FileAsset(
                file_id="file-copier-contract-2024-scan",
                contract_key="copier_contract",
                title="复印机采购合同-2024版",
                variant="scan",
                file_url="https://example.local/files/copier-contract-2024-scan",
                tags=("采购", "合同", "复印机", "2024"),
                status="active",
                updated_at="2026-03-30",
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
