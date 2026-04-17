from __future__ import annotations

import sqlite3
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.repos.sql_knowledge_repository import bootstrap_sqlite_schema
from app.services.admin_knowledge import AdminKnowledgeService


class AdminPagesTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        bootstrap_sqlite_schema(self.connection)
        self.service = AdminKnowledgeService(connection=self.connection)
        self.app = create_app(admin_knowledge_service=self.service)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.connection.close()

    def test_knowledge_page_renders_for_hr(self) -> None:
        response = self.client.get(
            "/admin/ui/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("机器人知识管理", response.text)
        self.assertIn("新增 FAQ", response.text)
        self.assertNotIn("新增固定报价", response.text)

    def test_fixed_quote_form_for_hr_is_forbidden(self) -> None:
        response = self.client.get(
            "/admin/ui/knowledge/fixed-quote/new",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
        )
        self.assertEqual(403, response.status_code)

    def test_fixed_quote_form_renders_for_business(self) -> None:
        response = self.client.get(
            "/admin/ui/knowledge/fixed-quote/new",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("新增固定报价", response.text)

    def test_validation_page_renders(self) -> None:
        response = self.client.get(
            "/admin/ui/validation",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("钉钉对话验证", response.text)
        self.assertIn("钉钉单聊预览", response.text)

    def test_role_switch_query_param_overrides_default_role(self) -> None:
        response = self.client.get("/admin/ui/knowledge?as_role=business")
        self.assertEqual(200, response.status_code)
        self.assertIn("当前角色：business", response.text)
        self.assertIn("新增固定报价", response.text)
        self.assertNotIn("新增 FAQ", response.text)

    def test_publish_page_renders(self) -> None:
        response = self.client.get(
            "/admin/ui/publish",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
        )
        self.assertEqual(200, response.status_code)
        self.assertIn("发布到机器人", response.text)


if __name__ == "__main__":
    unittest.main()
