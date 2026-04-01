from __future__ import annotations

import unittest

from app.services.flow_guidance import (
    FLOW_GUIDANCE_CANONICAL_BLOCKS,
    build_flow_guidance_card,
    build_reimbursement_guidance_fallback_text,
    build_reimbursement_guidance_prompt_fields,
)


class FlowGuidanceTests(unittest.TestCase):
    def test_canonical_blocks_include_required_non_empty_fields(self) -> None:
        required_fields = (
            "summary",
            "required_materials",
            "process_path",
            "common_errors",
            "entry_point",
            "next_action",
        )
        for intent in ("reimbursement", "leave"):
            card = build_flow_guidance_card(intent=intent, question="流程入口在哪")
            self.assertEqual("flow_guidance", card["card_type"])
            self.assertTrue(str(card.get("title", "")).strip())
            for field in required_fields:
                self.assertIn(field, card, f"missing field: {field}")
                value = card[field]
                if isinstance(value, list):
                    self.assertGreater(len(value), 0, f"field should not be empty: {field}")
                else:
                    self.assertTrue(str(value).strip(), f"field should not be blank: {field}")

    def test_reimbursement_common_errors_are_exactly_required_items(self) -> None:
        block = FLOW_GUIDANCE_CANONICAL_BLOCKS["reimbursement"]
        self.assertEqual(
            [
                "超过报销时限（出差后30天内）",
                "金额与发票不符",
            ],
            list(block.common_errors),
        )

    def test_leave_common_errors_are_exactly_required_items(self) -> None:
        block = FLOW_GUIDANCE_CANONICAL_BLOCKS["leave"]
        self.assertEqual(
            [
                "未提前申请（需提前1天）",
                "假种选择错误",
            ],
            list(block.common_errors),
        )

    def test_reimbursement_prompt_fields_include_canonical_block_details(self) -> None:
        fields = build_reimbursement_guidance_prompt_fields(user_input="出差报销怎么弄")
        self.assertEqual("出差报销怎么弄", fields["user_input"])
        canonical_block = fields["canonical_block"]
        self.assertIn("entry_point=钉钉 > 工作台 > 审批 > 报销", canonical_block)
        self.assertIn("common_errors=超过报销时限（出差后30天内）；金额与发票不符", canonical_block)

    def test_reimbursement_fallback_text_is_natural_and_complete(self) -> None:
        text = build_reimbursement_guidance_fallback_text(user_input="报销入口在哪")
        self.assertIn("发票", text)
        self.assertIn("行程单", text)
        self.assertIn("30天", text)
        self.assertIn("金额", text)
        self.assertNotIn("办理入口：", text)
        self.assertNotIn("准备材料：", text)
        self.assertNotIn("流程路径：", text)
        self.assertNotIn("下一步：", text)


if __name__ == "__main__":
    unittest.main()
