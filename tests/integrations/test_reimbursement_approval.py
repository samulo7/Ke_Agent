from __future__ import annotations

import unittest
from unittest.mock import patch

from app.integrations.dingtalk.reimbursement_approval import (
    DingTalkReimbursementApprovalCreator,
    ReimbursementApprovalSettings,
)
from app.schemas.reimbursement import ReimbursementApprovalSubmission


class _FakeResponse:
    def __init__(self, payload: dict[str, object], status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise RuntimeError("http error")

    def json(self):  # type: ignore[no-untyped-def]
        return dict(self._payload)


class ReimbursementApprovalCreatorTests(unittest.TestCase):
    def test_submit_builds_expected_form_component_values_in_order(self) -> None:
        settings = ReimbursementApprovalSettings(
            enabled=True,
            process_code="PROC-RMB",
            field_travel_instance="关联出差申请",
            field_company="公司",
            field_department="部门",
            field_cost_company="费用归属公司",
            field_date="日期",
            field_amount="金额(元)",
            field_over_5000="是否超过5千",
            field_attachment="附件",
            openapi_endpoint="https://api.dingtalk.com",
            legacy_openapi_endpoint="https://oapi.dingtalk.com",
        )
        creator = DingTalkReimbursementApprovalCreator(
            client_id="cid",
            client_secret="secret",
            settings=settings,
        )
        submission = ReimbursementApprovalSubmission(
            originator_user_id="user-1",
            travel_process_instance_id="trip-proc-1",
            department="总经办",
            fixed_company="YXQY",
            cost_company="SY",
            date="2026-04-01",
            amount="106",
            over_five_thousand="否",
            attachment_media_id="media-pdf-1",
        )
        post_calls: list[dict[str, object]] = []

        def _fake_post(url, **kwargs):  # type: ignore[no-untyped-def]
            post_calls.append({"url": url, **kwargs})
            if "oauth2/accessToken" in str(url):
                return _FakeResponse({"accessToken": "token-1", "expireIn": 7200})
            return _FakeResponse({"errcode": 0, "process_instance_id": "proc-rmb-1"})

        with patch("app.integrations.dingtalk.reimbursement_approval.requests.post", side_effect=_fake_post):
            result = creator.submit(submission)

        self.assertTrue(result.success)
        self.assertEqual("submitted", result.reason)
        process_call = post_calls[1]
        payload = process_call["json"]
        assert isinstance(payload, dict)
        self.assertEqual("PROC-RMB", payload["process_code"])
        form_values = payload["form_component_values"]
        assert isinstance(form_values, list)
        expected = [
            {"name": "关联出差申请", "value": "trip-proc-1"},
            {"name": "公司", "value": "YXQY"},
            {"name": "部门", "value": "总经办"},
            {"name": "费用归属公司", "value": "SY"},
            {"name": "日期", "value": "2026-04-01"},
            {"name": "金额(元)", "value": "106"},
            {"name": "是否超过5千", "value": "否"},
            {"name": "附件", "value": '[{"mediaId": "media-pdf-1"}]'},
        ]
        self.assertEqual(expected, form_values)


if __name__ == "__main__":
    unittest.main()
