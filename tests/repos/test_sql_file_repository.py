from __future__ import annotations

import sqlite3
import unittest

from app.repos.sql_file_repository import SQLFileRepository, bootstrap_file_assets_sqlite_schema


class SQLFileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:")
        self.connection.row_factory = sqlite3.Row
        bootstrap_file_assets_sqlite_schema(self.connection)
        self._seed_data()
        self.repository = SQLFileRepository(connection=self.connection)

    def tearDown(self) -> None:
        self.connection.close()

    def _seed_data(self) -> None:
        self.connection.executemany(
            """
            INSERT INTO file_assets (
                file_id,
                contract_key,
                title,
                variant,
                file_url,
                tags_csv,
                status,
                updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (
                    "file-scan-1",
                    "dingyingqi_contract",
                    "定影器采购合同-2024版",
                    "scan",
                    "https://example.local/files/dingyingqi-contract-2024-scan",
                    "采购,合同,定影器,2024",
                    "active",
                    "2026-03-27",
                ),
                (
                    "file-paper-1",
                    "dingyingqi_contract",
                    "定影器采购合同-2024版",
                    "paper",
                    "https://example.local/files/dingyingqi-contract-2024-paper",
                    "采购,合同,定影器,2024",
                    "active",
                    "2026-03-27",
                ),
                (
                    "file-inactive-1",
                    "dingyingqi_contract",
                    "定影器采购合同-历史归档",
                    "scan",
                    "https://example.local/files/dingyingqi-contract-archive",
                    "采购,合同,定影器,归档",
                    "archived",
                    "2024-01-01",
                ),
                (
                    "file-scan-2",
                    "printer_contract",
                    "打印机采购合同-2023版",
                    "scan",
                    "https://example.local/files/printer-contract-2023-scan",
                    "采购,合同,打印机,2023",
                    "active",
                    "2026-03-26",
                ),
                (
                    "file-scan-3",
                    "copier_contract",
                    "复印机采购合同-2024版",
                    "scan",
                    "https://example.local/files/copier-contract-2024-scan",
                    "采购,合同,复印机,2024",
                    "active",
                    "2026-03-25",
                ),
            ),
        )
        self.connection.commit()

    def test_search_hits_contract_key_and_variant(self) -> None:
        result = self.repository.search(query_text="帮我找定影器采购合同", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-scan-1", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_hits_when_query_uses_want_and_file_words(self) -> None:
        result = self.repository.search(query_text="我想要定影器采购合同文件", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-scan-1", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_hits_when_query_uses_apply_language(self) -> None:
        result = self.repository.search(query_text="我要申请定影器采购合同", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-scan-1", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_returns_correct_variant_for_same_contract_key(self) -> None:
        result = self.repository.search(query_text="定影器采购合同", variant="paper")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-paper-1", result.asset.file_id)
        self.assertEqual("paper", result.asset.variant)

    def test_search_ignores_inactive_assets(self) -> None:
        self.connection.execute("UPDATE file_assets SET status = 'archived' WHERE file_id IN ('file-scan-1', 'file-paper-1')")
        self.connection.commit()

        result = self.repository.search(query_text="定影器采购合同", variant="scan")
        self.assertFalse(result.matched)
        self.assertIsNone(result.asset)

    def test_search_returns_ranked_candidates_for_multi_match_query(self) -> None:
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
