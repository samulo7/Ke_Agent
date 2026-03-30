from __future__ import annotations

import unittest

from app.repos.in_memory_file_repository import InMemoryFileRepository


class InMemoryFileRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.repository = InMemoryFileRepository()

    def test_search_hits_when_query_uses_want_and_file_words(self) -> None:
        result = self.repository.search(query_text="我想要定影器采购合同文件", variant="scan")

        self.assertTrue(result.matched)
        self.assertIsNotNone(result.asset)
        self.assertEqual("file-dingyingqi-contract-2024-scan", result.asset.file_id)
        self.assertEqual("scan", result.asset.variant)

    def test_search_returns_no_hit_for_unrelated_file(self) -> None:
        result = self.repository.search(query_text="我想要薪酬调整明细文件", variant="scan")

        self.assertFalse(result.matched)
        self.assertIsNone(result.asset)


if __name__ == "__main__":
    unittest.main()
