from __future__ import annotations

import unittest

from app.repos.in_memory_file_repository import InMemoryFileRepository
from app.schemas.file_asset import FileAsset


class InMemoryFileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryFileRepository()

    def test_search_hits_when_query_uses_want_and_file_words(self) -> None:
        result = self.repository.search(query_text="我想要定影器采购合同文件", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-dingyingqi-contract-2024-scan", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_hits_when_query_uses_apply_language(self) -> None:
        result = self.repository.search(query_text="我要申请定影器采购合同", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-dingyingqi-contract-2024-scan", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_returns_no_hit_for_unrelated_file(self) -> None:
        result = self.repository.search(query_text="我想要薪酬调整明细文件", variant="scan")

        self.assertFalse(result.matched)
        self.assertIsNone(result.asset)

    def test_search_returns_ranked_candidates_for_multi_match_query(self) -> None:
        repository = InMemoryFileRepository(
            assets=(
                FileAsset(
                    file_id="scan-1",
                    contract_key="dingyingqi_procurement_contract",
                    title="定影器采购合同-2024版",
                    variant="scan",
                    file_url="https://example.local/files/dingyingqi-contract-2024-scan",
                    tags=("采购", "合同", "定影器", "2024"),
                    status="active",
                    updated_at="2026-03-30",
                ),
                FileAsset(
                    file_id="scan-2",
                    contract_key="printer_procurement_contract",
                    title="打印机采购合同-2023版",
                    variant="scan",
                    file_url="https://example.local/files/printer-contract-2023-scan",
                    tags=("采购", "合同", "打印机", "2023"),
                    status="active",
                    updated_at="2026-03-30",
                ),
                FileAsset(
                    file_id="scan-3",
                    contract_key="copier_procurement_contract",
                    title="复印机采购合同-2024版",
                    variant="scan",
                    file_url="https://example.local/files/copier-contract-2024-scan",
                    tags=("采购", "合同", "复印机", "2024"),
                    status="active",
                    updated_at="2026-03-30",
                ),
            )
        )

        result = repository.search(query_text="我要采购合同", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertGreaterEqual(len(result.candidates), 3)
        candidate_titles = [item.asset.title for item in result.candidates]
        self.assertIn("定影器采购合同-2024版", candidate_titles)
        self.assertIn("打印机采购合同-2023版", candidate_titles)
        self.assertIn("复印机采购合同-2024版", candidate_titles)

    def test_default_assets_support_multi_match_for_procurement_contract_query(self) -> None:
        result = self.repository.search(query_text="我要采购合同", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertGreaterEqual(len(result.candidates), 3)
        candidate_titles = [item.asset.title for item in result.candidates]
        self.assertIn("定影器采购合同-2024版", candidate_titles)
        self.assertIn("打印机采购合同-2023版", candidate_titles)
        self.assertIn("复印机采购合同-2024版", candidate_titles)


if __name__ == "__main__":
    unittest.main()
