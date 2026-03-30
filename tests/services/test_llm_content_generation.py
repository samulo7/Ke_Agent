from __future__ import annotations

import unittest
from typing import Any

from app.services.llm_constants import SUMMARY_ONLY_ALLOWLIST
from app.services.llm_content_generation import LLMContentGenerationService


class _FakeLLMClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload
        self.last_user_prompt = ""

    def generate_json(  # type: ignore[no-untyped-def]
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict[str, Any]:
        self.last_user_prompt = user_prompt
        return dict(self.payload)


class LLMContentGenerationServiceTests(unittest.TestCase):
    def test_summary_only_prompt_respects_allowlist(self) -> None:
        client = _FakeLLMClient({"text": "该资料不可直接查看。申请路径：流程A。建议联系人：人事行政。"})
        service = LLMContentGenerationService(
            llm_client=client,
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.generate(
            mode="summary_only",
            question="我要看财务制度细则",
            prompt_fields={
                "summary": "财务制度摘要",
                "next_step": "流程A",
                "contact": "人事行政",
                "owner": "finance-team",
            },
            fallback_text="fallback",
            conversation_id="conv-1",
            sender_id="u-1",
        )

        self.assertFalse(result.fallback_used)
        self.assertIn("- summary:", client.last_user_prompt)
        self.assertIn("- next_step:", client.last_user_prompt)
        self.assertIn("- contact:", client.last_user_prompt)
        self.assertNotIn("- owner:", client.last_user_prompt)
        self.assertEqual({"summary", "next_step", "contact"}, SUMMARY_ONLY_ALLOWLIST)

    def test_fallback_when_output_contains_disallowed_content(self) -> None:
        client = _FakeLLMClient({"text": "敏感摘要：高管预算审批阈值与审批链路说明。申请路径：流程B。"})
        service = LLMContentGenerationService(
            llm_client=client,
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.generate(
            mode="deny",
            question="高管预算审批规则是什么",
            prompt_fields={"next_step": "流程B", "contact": "财务负责人"},
            fallback_text="该资料属于敏感受控内容，当前权限下不可查看，且无法提供摘要。\n申请路径：流程B\n建议联系人：财务负责人",
            conversation_id="conv-1",
            sender_id="u-1",
            disallowed_values=("高管预算审批阈值与审批链路说明",),
        )

        self.assertTrue(result.fallback_used)
        self.assertIn("不可查看", result.text)
        self.assertIn("contains_disallowed_content", result.safety_flags)

    def test_fallback_when_missing_text_field(self) -> None:
        client = _FakeLLMClient({"answer": "invalid"})
        service = LLMContentGenerationService(
            llm_client=client,
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.generate(
            mode="allow",
            question="宴请标准是什么",
            prompt_fields={"summary": "摘要"},
            fallback_text="fallback",
            conversation_id="conv-1",
            sender_id="u-1",
        )

        self.assertTrue(result.fallback_used)
        self.assertIn("missing_text", result.safety_flags)

    def test_disabled_mode_directly_uses_fallback(self) -> None:
        service = LLMContentGenerationService(
            llm_client=None,
            model="qwen-plus",
            enabled=False,
            rollout_percentage=100,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.generate(
            mode="allow",
            question="宴请标准是什么",
            prompt_fields={"summary": "摘要"},
            fallback_text="fallback-text",
            conversation_id="conv-1",
            sender_id="u-1",
        )

        self.assertTrue(result.fallback_used)
        self.assertEqual("fallback-text", result.text)
        self.assertIn("llm_disabled", result.safety_flags)


if __name__ == "__main__":
    unittest.main()

