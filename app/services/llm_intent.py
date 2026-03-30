from __future__ import annotations

from typing import Any

from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.schemas.dingtalk_chat import IntentType
from app.schemas.llm import IntentInferenceResult
from app.services.intent_classifier import IntentClassification, IntentClassifier
from app.services.llm_env import env_bool, env_float, env_int, env_int_alias, env_str, rollout_hit

_INTENT_VALUES: tuple[IntentType, ...] = (
    "policy_process",
    "document_request",
    "file_request",
    "reimbursement",
    "leave",
    "fixed_quote",
    "other",
)

_INTENT_SYSTEM_PROMPT = (
    "你是企业内部Agent的意图分类器，只输出JSON对象。"
    "字段必须包含 intent/confidence/reason。"
    "intent仅允许: policy_process, document_request, file_request, reimbursement, leave, fixed_quote, other。"
    "confidence范围0到1。"
    "规则：\n"
    "1) 规则解释/流程/入口/步骤/内容是什么 -> policy_process。\n"
    "2) 申请受控文档权限/提交申请 -> document_request。\n"
    "3) 明确要拿文件或下载链接（我想要/给我/发我/找+XX文件, XX文件在哪里看/下载）-> file_request。\n"
    "4) 报销相关 -> reimbursement。5) 请假相关 -> leave。6) 固定报价 -> fixed_quote。\n"
    "不确定时返回 other。"
)


class LLMIntentService:
    """LLM-first intent inference with strict validation and rule fallback."""

    def __init__(
        self,
        *,
        llm_client: QwenChatClient | None,
        fallback_classifier: IntentClassifier,
        model: str,
        enabled: bool,
        rollout_percentage: int,
        confidence_threshold: float,
        timeout_seconds: int,
        max_retries: int,
        rollout_salt: str = "llm-intent",
    ) -> None:
        self._llm_client = llm_client
        self._fallback_classifier = fallback_classifier
        self._model = model
        self._enabled = enabled and llm_client is not None
        self._rollout_percentage = rollout_percentage
        self._confidence_threshold = confidence_threshold
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._rollout_salt = rollout_salt

    def infer(
        self,
        *,
        text: str,
        conversation_id: str,
        sender_id: str,
    ) -> IntentInferenceResult:
        fallback = self._fallback_classifier.classify(text)

        if not self._enabled:
            return self._fallback_result(
                fallback=fallback,
                reason="llm_disabled",
                validation_passed=True,
            )
        if not rollout_hit(
            conversation_id=conversation_id,
            sender_id=sender_id,
            percentage=self._rollout_percentage,
            salt=self._rollout_salt,
        ):
            return self._fallback_result(
                fallback=fallback,
                reason="llm_rollout_not_hit",
                validation_passed=True,
            )

        payload: dict[str, Any]
        try:
            assert self._llm_client is not None
            payload = self._llm_client.generate_json(
                model=self._model,
                system_prompt=_INTENT_SYSTEM_PROMPT,
                user_prompt=f"用户输入：{text.strip()}",
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
            )
        except Exception as exc:
            return self._fallback_result(
                fallback=fallback,
                reason=f"llm_error:{type(exc).__name__}",
                validation_passed=False,
            )

        validated = self._validate_payload(payload)
        if validated is None:
            return self._fallback_result(
                fallback=fallback,
                reason="llm_output_invalid",
                validation_passed=False,
            )

        intent, confidence, reason = validated
        if confidence < self._confidence_threshold:
            return self._fallback_result(
                fallback=fallback,
                reason="llm_low_confidence",
                validation_passed=False,
            )

        # High-confidence semantic mismatch guard: keep rule outcome to avoid silent misroute.
        if self._is_high_conflict(fallback=fallback, llm_intent=intent, llm_confidence=confidence):
            return self._fallback_result(
                fallback=fallback,
                reason="llm_semantic_conflict",
                validation_passed=False,
            )

        return IntentInferenceResult(
            intent=intent,
            confidence=confidence,
            reason=reason,
            model=self._model,
            fallback_used=False,
            validation_passed=True,
            raw_payload=payload,
        )

    @staticmethod
    def _validate_payload(payload: dict[str, Any]) -> tuple[IntentType, float, str] | None:
        intent = payload.get("intent")
        confidence = payload.get("confidence")
        reason = payload.get("reason")
        if not isinstance(intent, str) or intent not in _INTENT_VALUES:
            return None
        if not isinstance(confidence, (int, float)):
            return None
        if confidence < 0 or confidence > 1:
            return None
        if not isinstance(reason, str) or not reason.strip():
            return None
        return intent, float(confidence), reason.strip()

    @staticmethod
    def _is_high_conflict(
        *,
        fallback: IntentClassification,
        llm_intent: IntentType,
        llm_confidence: float,
    ) -> bool:
        return (
            llm_intent != fallback.intent
            and llm_confidence >= 0.9
            and fallback.confidence >= 0.9
            and fallback.intent != "other"
        )

    def _fallback_result(
        self,
        *,
        fallback: IntentClassification,
        reason: str,
        validation_passed: bool,
    ) -> IntentInferenceResult:
        return IntentInferenceResult(
            intent=fallback.intent,
            confidence=fallback.confidence,
            reason=reason,
            model="rule_fallback",
            fallback_used=True,
            validation_passed=validation_passed,
            raw_payload={},
        )


def build_default_llm_intent_service(*, fallback_classifier: IntentClassifier) -> LLMIntentService:
    api_key = env_str(("LLM_API_KEY", "QWEN_API_KEY"), "")
    endpoint = env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), DEFAULT_QWEN_CHAT_ENDPOINT)
    model = env_str(("LLM_INTENT_MODEL", "QWEN_INTENT_MODEL"), "qwen-plus")
    enabled_flag = env_bool("LLM_INTENT_ENABLED", False)
    enabled = enabled_flag and bool(api_key)
    llm_client: QwenChatClient | None = None
    if enabled:
        llm_client = HttpQwenChatClient(api_key=api_key, endpoint=endpoint)
    return LLMIntentService(
        llm_client=llm_client,
        fallback_classifier=fallback_classifier,
        model=model or "qwen-plus",
        enabled=enabled,
        rollout_percentage=env_int("LLM_INTENT_ROLLOUT_PERCENT", 10, minimum=0, maximum=100),
        confidence_threshold=env_float("LLM_INTENT_CONFIDENCE_THRESHOLD", 0.75, minimum=0.0, maximum=1.0),
        timeout_seconds=env_int_alias(("LLM_TIMEOUT_SECONDS", "QWEN_TIMEOUT_SECONDS"), 10, minimum=1),
        max_retries=env_int_alias(("LLM_MAX_RETRIES", "QWEN_MAX_RETRIES"), 2, minimum=0, maximum=2),
    )
