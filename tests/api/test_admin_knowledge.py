from __future__ import annotations

import sqlite3
import unittest

from fastapi.testclient import TestClient

from app.api.main import create_app
from app.repos.sql_knowledge_repository import bootstrap_sqlite_schema
from app.services.admin_knowledge import AdminKnowledgeService


class AdminKnowledgeApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.connection = sqlite3.connect(":memory:", check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        bootstrap_sqlite_schema(self.connection)
        self.service = AdminKnowledgeService(connection=self.connection)
        self.app = create_app(admin_knowledge_service=self.service)
        self.client = TestClient(self.app)

    def tearDown(self) -> None:
        self.connection.close()

    def test_get_permissions_for_hr_role(self) -> None:
        response = self.client.get(
            "/admin/me/permissions",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual("hr", body["data"]["role_code"])
        self.assertFalse(body["data"]["menus"]["roles"])
        self.assertTrue(body["data"]["knowledge_permissions"]["faq"]["can_edit"])
        self.assertFalse(body["data"]["knowledge_permissions"]["fixed_quote"]["can_edit"])

    def test_hr_can_create_faq(self) -> None:
        response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={
                "knowledge_kind": "faq",
                "title": "试用期员工可以请假吗",
                "summary": "试用期员工可以按公司制度申请事假/病假。",
                "applicability": "全体员工",
                "next_step": "如为病假，请补充证明材料。",
                "source_uri": "employee-handbook-v3",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "hr",
                "department": "hr",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["试用期", "请假", "病假"],
                "intents": ["policy_process", "leave"],
                "version_tag": "v1",
                "category": "faq"
            },
        )
        self.assertEqual(200, response.status_code)
        body = response.json()
        self.assertTrue(body["ok"])
        self.assertEqual("draft", body["data"]["review_status"])

        list_response = self.client.get(
            "/admin/knowledge?knowledge_kind=faq",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
        )
        list_body = list_response.json()
        self.assertEqual(1, len(list_body["data"]["items"]))
        self.assertEqual("试用期员工可以请假吗", list_body["data"]["items"][0]["title"])
        self.assertTrue(list_body["data"]["items"][0]["can_edit"])

    def test_hr_cannot_create_fixed_quote(self) -> None:
        response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={
                "knowledge_kind": "fixed_quote",
                "title": "7788 黑色墨粉",
                "summary": "标准报价 1050 元/支（含税）。",
                "applicability": "适用于 7788 设备",
                "next_step": "如数量或折扣条件不同，请联系商务确认。",
                "source_uri": "quote-sheet-q2",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "business",
                "department": "business",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["7788", "黑色墨粉", "报价"],
                "intents": ["fixed_quote"],
                "version_tag": "V2026.04",
                "category": "quote",
                "quote_fields": {
                    "quote_item_name": "黑色墨粉",
                    "spec_model": "7788",
                    "quote_category": "consumable",
                    "price_amount": 1050,
                    "unit": "元/支",
                    "tax_included": True,
                    "effective_date": "2026-04-17",
                    "quote_version": "V2026.04",
                    "non_standard_action": "如数量或折扣条件不同，请联系商务确认。"
                }
            },
        )
        self.assertEqual(403, response.status_code)
        body = response.json()
        self.assertEqual("FORBIDDEN", body["detail"]["error"]["code"])

    def test_business_can_create_and_update_fixed_quote(self) -> None:
        create_response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
            json={
                "knowledge_kind": "fixed_quote",
                "title": "7788 黑色墨粉",
                "summary": "标准报价 1050 元/支（含税）。",
                "applicability": "适用于 7788 设备",
                "next_step": "如数量或折扣条件不同，请联系商务确认。",
                "source_uri": "quote-sheet-q2",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "business",
                "department": "business",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["7788", "黑色墨粉", "报价"],
                "intents": ["fixed_quote"],
                "version_tag": "V2026.04",
                "category": "quote",
                "quote_fields": {
                    "quote_item_name": "黑色墨粉",
                    "spec_model": "7788",
                    "quote_category": "consumable",
                    "price_amount": 1050,
                    "unit": "元/支",
                    "tax_included": True,
                    "effective_date": "2026-04-17",
                    "quote_version": "V2026.04",
                    "non_standard_action": "如数量或折扣条件不同，请联系商务确认。"
                }
            },
        )
        self.assertEqual(200, create_response.status_code)
        doc_id = create_response.json()["data"]["doc_id"]

        update_response = self.client.put(
            f"/admin/knowledge/{doc_id}",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
            json={
                "knowledge_kind": "fixed_quote",
                "title": "7788 黑色墨粉",
                "summary": "标准报价 1090 元/支（含税）。",
                "applicability": "适用于 7788 设备",
                "next_step": "如数量或折扣条件不同，请联系商务确认。",
                "source_uri": "quote-sheet-q2",
                "updated_at": "2026-04-17T10:12:00+08:00",
                "owner": "business",
                "department": "business",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["7788", "黑色墨粉", "报价"],
                "intents": ["fixed_quote"],
                "version_tag": "V2026.04",
                "category": "quote",
                "quote_fields": {
                    "quote_item_name": "黑色墨粉",
                    "spec_model": "7788",
                    "quote_category": "consumable",
                    "price_amount": 1090,
                    "unit": "元/支",
                    "tax_included": True,
                    "effective_date": "2026-04-17",
                    "quote_version": "V2026.04",
                    "non_standard_action": "如数量或折扣条件不同，请联系商务确认。"
                }
            },
        )
        self.assertEqual(200, update_response.status_code)
        body = update_response.json()
        self.assertEqual(doc_id, body["data"]["doc_id"])
        self.assertEqual("draft", body["data"]["review_status"])

    def test_finance_list_is_read_only(self) -> None:
        self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={
                "knowledge_kind": "faq",
                "title": "试用期员工可以请假吗",
                "summary": "试用期员工可以按公司制度申请事假/病假。",
                "applicability": "全体员工",
                "next_step": "如为病假，请补充证明材料。",
                "source_uri": "employee-handbook-v3",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "hr",
                "department": "hr",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["试用期", "请假", "病假"],
                "intents": ["policy_process", "leave"],
                "version_tag": "v1",
                "category": "faq"
            },
        )
        response = self.client.get(
            "/admin/knowledge",
            headers={"X-Admin-Role": "finance", "X-Admin-User-Id": "u-fin-01"},
        )
        self.assertEqual(200, response.status_code)
        item = response.json()["data"]["items"][0]
        self.assertTrue(item["can_view"])
        self.assertFalse(item["can_edit"])
        self.assertFalse(item["can_publish"])

    def test_preview_generates_dingtalk_reply_and_allows_publish_for_hr_faq(self) -> None:
        create_response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={
                "knowledge_kind": "faq",
                "title": "试用期员工可以请假吗",
                "summary": "试用期员工可以按公司制度申请事假/病假。",
                "applicability": "全体员工",
                "next_step": "如为病假，请补充证明材料。",
                "source_uri": "employee-handbook-v3",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "hr",
                "department": "hr",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["试用期", "请假", "病假"],
                "intents": ["policy_process", "leave"],
                "version_tag": "v1",
                "category": "faq"
            },
        )
        doc_id = create_response.json()["data"]["doc_id"]

        preview_response = self.client.post(
            "/admin/validation/dingtalk-preview",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={"question": "试用期员工可以请假吗？", "doc_id": doc_id, "dept_context": "hr"},
        )
        self.assertEqual(200, preview_response.status_code)
        preview_body = preview_response.json()
        self.assertTrue(preview_body["ok"])
        self.assertEqual("text", preview_body["data"]["reply_preview"]["channel"])
        self.assertIn("试用期员工可以按公司制度申请事假/病假", preview_body["data"]["reply_preview"]["text"])
        self.assertEqual("passed", preview_body["data"]["validation_result"])

        publish_response = self.client.post(
            f"/admin/publish/{doc_id}",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={"publish_note": "ready for robot"},
        )
        self.assertEqual(200, publish_response.status_code)
        publish_body = publish_response.json()
        self.assertEqual("published", publish_body["data"]["review_status"])
        self.assertEqual("u-hr-01", publish_body["data"]["published_by"])

    def test_preview_generates_fixed_quote_reply_and_business_can_publish(self) -> None:
        create_response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
            json={
                "knowledge_kind": "fixed_quote",
                "title": "7788 黑色墨粉",
                "summary": "标准报价 1050 元/支（含税）。",
                "applicability": "适用于 7788 设备",
                "next_step": "如数量或折扣条件不同，请联系商务确认。",
                "source_uri": "quote-sheet-q2",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "business",
                "department": "business",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["7788", "黑色墨粉", "报价"],
                "intents": ["fixed_quote"],
                "version_tag": "V2026.04",
                "category": "quote",
                "quote_fields": {
                    "quote_item_name": "黑色墨粉",
                    "spec_model": "7788",
                    "quote_category": "consumable",
                    "price_amount": 1050,
                    "unit": "元/支",
                    "tax_included": True,
                    "effective_date": "2026-04-17",
                    "quote_version": "V2026.04",
                    "non_standard_action": "如数量或折扣条件不同，请联系商务确认。"
                }
            },
        )
        doc_id = create_response.json()["data"]["doc_id"]

        preview_response = self.client.post(
            "/admin/validation/dingtalk-preview",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
            json={"question": "7788 黑色墨粉多少钱？", "doc_id": doc_id, "dept_context": "business"},
        )
        self.assertEqual(200, preview_response.status_code)
        preview_body = preview_response.json()
        self.assertIn("1050 元/支", preview_body["data"]["reply_preview"]["text"])
        self.assertEqual("passed", preview_body["data"]["validation_result"])

        publish_response = self.client.post(
            f"/admin/publish/{doc_id}",
            headers={"X-Admin-Role": "business", "X-Admin-User-Id": "u-biz-01"},
            json={"publish_note": "ready for robot"},
        )
        self.assertEqual(200, publish_response.status_code)
        self.assertEqual("published", publish_response.json()["data"]["review_status"])

    def test_published_faq_is_used_by_dingtalk_runtime_when_sharing_connection(self) -> None:
        create_response = self.client.post(
            "/admin/knowledge",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={
                "knowledge_kind": "faq",
                "title": "试用期员工可以请假吗",
                "summary": "试用期员工可以按公司制度申请事假/病假。",
                "applicability": "全体员工",
                "next_step": "如为病假，请补充证明材料。",
                "source_uri": "employee-handbook-v3",
                "updated_at": "2026-04-17T09:12:00+08:00",
                "owner": "hr",
                "department": "hr",
                "permission_scope": "public",
                "permitted_depts": [],
                "keywords": ["试用期", "请假", "病假"],
                "intents": ["policy_process", "leave"],
                "version_tag": "v1",
                "category": "faq"
            },
        )
        doc_id = create_response.json()["data"]["doc_id"]

        self.client.post(
            "/admin/validation/dingtalk-preview",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={"question": "试用期员工可以请假吗？", "doc_id": doc_id, "dept_context": "hr"},
        )
        self.client.post(
            f"/admin/publish/{doc_id}",
            headers={"X-Admin-Role": "hr", "X-Admin-User-Id": "u-hr-01"},
            json={"publish_note": "ready for robot"},
        )

        runtime_response = self.client.post(
            "/dingtalk/stream/events",
            json={
                "conversationType": "1",
                "conversationId": "conv-admin-share-1",
                "msgId": "msg-admin-share-1",
                "senderId": "sender-admin-share-1",
                "senderStaffId": "staff-admin-share-1",
                "senderNick": "tester",
                "text": {"content": "试用期员工可以请假吗？"},
                "msgtype": "text",
                "robotCode": "ding-admin-share",
            },
        )
        self.assertEqual(200, runtime_response.status_code)
        body = runtime_response.json()
        self.assertEqual("knowledge_answer", body["reason"])
        self.assertIn("试用期员工可以按公司制度申请事假/病假", body["reply"]["text"])


if __name__ == "__main__":
    unittest.main()
