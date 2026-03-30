from __future__ import annotations

import unittest
from typing import Any

from app.services.llm_orchestrator_shadow import LLMOrchestratorShadowService


class _FakeClient:
    def __init__(self, payload: dict[str, Any]) -> None:
        self.payload = payload

    def generate_json(  # type: ignore[no-untyped-def]
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict[str, Any]:
        return dict(self.payload)


class LLMOrchestratorShadowTests(unittest.TestCase):
    def test_shadow_returns_mismatch_risk_tag(self) -> None:
        service = LLMOrchestratorShadowService(
            llm_client=_FakeClient({"suggested_action": "file_request", "reason": "need file"}),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.suggest(
            question="宴请标准是什么",
            intent="policy_process",
            rule_action="knowledge_answer",
            conversation_id="conv-1",
            sender_id="u-1",
        )

        self.assertFalse(result.fallback_used)
        self.assertIn("action_mismatch", result.risk_tags)
        self.assertEqual("file_request", result.suggested_action)
        self.assertEqual("knowledge_answer", result.rule_action)


if __name__ == "__main__":
    unittest.main()

