from __future__ import annotations

import unittest
from typing import Any

from app.services.intent_classifier import IntentClassifier
from app.services.llm_intent import LLMIntentService


class _FakeLLMClient:
    def __init__(self, payloads: list[dict[str, Any]] | None = None, *, raises: Exception | None = None) -> None:
        self._payloads = payloads or []
        self._raises = raises
        self.calls = 0

    def generate_json(  # type: ignore[no-untyped-def]
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict[str, Any]:
        self.calls += 1
        if self._raises is not None:
            raise self._raises
        if not self._payloads:
            raise RuntimeError("no payload configured")
        return self._payloads.pop(0)


class LLMIntentServiceTests(unittest.TestCase):
    def test_uses_llm_result_when_output_valid(self) -> None:
        client = _FakeLLMClient(payloads=[{"intent": "policy_process", "confidence": 0.91, "reason": "policy question"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="宴请标准是什么", conversation_id="conv-1", sender_id="u-1")

        self.assertEqual("policy_process", result.intent)
        self.assertFalse(result.fallback_used)
        self.assertTrue(result.validation_passed)
        self.assertEqual("qwen-plus", result.model)
        self.assertEqual(1, client.calls)

    def test_falls_back_when_confidence_too_low(self) -> None:
        client = _FakeLLMClient(payloads=[{"intent": "policy_process", "confidence": 0.2, "reason": "low"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="宴请标准是什么", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertEqual("rule_fallback", result.model)
        self.assertEqual("llm_low_confidence", result.reason)

    def test_falls_back_when_output_invalid(self) -> None:
        client = _FakeLLMClient(payloads=[{"intent": "policy_process", "reason": "missing confidence"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="宴请标准是什么", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertEqual("llm_output_invalid", result.reason)

    def test_falls_back_when_llm_raises(self) -> None:
        client = _FakeLLMClient(raises=TimeoutError("timeout"))
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="宴请标准是什么", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertIn("llm_error", result.reason)

    def test_explicit_fixed_quote_rule_guard_overrides_llm_other(self) -> None:
        client = _FakeLLMClient(payloads=[{"intent": "other", "confidence": 0.8, "reason": "uncertain"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="Z9特殊组件成本核算", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertEqual("llm_explicit_rule_guard", result.reason)
        self.assertEqual("fixed_quote", result.intent)

    def test_explicit_fixed_quote_rule_guard_overrides_medium_confidence_policy_process(self) -> None:
        client = _FakeLLMClient(payloads=[{"intent": "policy_process", "confidence": 0.8, "reason": "policy-like"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="Z9特殊组件成本核算", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertEqual("llm_explicit_rule_guard", result.reason)
        self.assertEqual("fixed_quote", result.intent)

        client = _FakeLLMClient(payloads=[{"intent": "policy_process", "confidence": 0.95, "reason": "wrong"}])
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        result = service.infer(text="定影器采购合同在哪里下载", conversation_id="conv-1", sender_id="u-1")

        self.assertTrue(result.fallback_used)
        self.assertEqual("llm_semantic_conflict", result.reason)
        self.assertEqual("file_request", result.intent)

    def test_high_confusion_regression_set_has_20_samples(self) -> None:
        high_confusion_samples: list[tuple[str, str]] = [
            ("定影器采购合同在哪里下载", "file_request"),
            ("我想要定影器采购合同", "file_request"),
            ("帮我找一下定影器采购合同扫描件", "file_request"),
            ("给我发采购合同文件链接", "file_request"),
            ("定影器采购合同内容是什么", "policy_process"),
            ("采购合同制度流程是什么", "policy_process"),
            ("我要申请采购制度文件", "file_request"),
            ("请帮我申请合同正文查看权限", "document_request"),
            ("报销流程入口在哪", "reimbursement"),
            ("出差报销要哪些材料", "reimbursement"),
            ("我要请假", "leave"),
            ("病假流程怎么走", "leave"),
            ("XX定影器多少钱", "fixed_quote"),
            ("定影器标准报价多少", "fixed_quote"),
            ("请问财务制度在哪里看", "policy_process"),
            ("发我劳动合同扫描版", "file_request"),
            ("帮我申请劳动合同调阅权限", "document_request"),
            ("采购合同在哪里看下载", "file_request"),
            ("采购合同审批流程是什么", "policy_process"),
            ("我要找采购合同原件", "file_request"),
        ]
        self.assertEqual(20, len(high_confusion_samples))

        payloads = [
            {"intent": intent, "confidence": 0.93, "reason": "test fixture"}
            for _, intent in high_confusion_samples
        ]
        client = _FakeLLMClient(payloads=payloads)
        service = LLMIntentService(
            llm_client=client,
            fallback_classifier=IntentClassifier(),
            model="qwen-plus",
            enabled=True,
            rollout_percentage=100,
            confidence_threshold=0.75,
            timeout_seconds=10,
            max_retries=2,
        )

        predictions = [
            service.infer(text=text, conversation_id=f"conv-{i}", sender_id="u-1").intent
            for i, (text, _) in enumerate(high_confusion_samples)
        ]
        expected = [intent for _, intent in high_confusion_samples]
        correct = sum(1 for pred, exp in zip(predictions, expected, strict=True) if pred == exp)
        self.assertGreaterEqual(correct / len(high_confusion_samples), 0.9)


if __name__ == "__main__":
    unittest.main()
