from __future__ import annotations

from typing import Any

from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.schemas.dingtalk_chat import IntentType
from app.schemas.llm import OrchestratorAction, OrchestratorShadowResult
from app.services.llm_env import env_bool, env_int, env_int_alias, env_str, rollout_hit

_ACTION_VALUES: tuple[OrchestratorAction, ...] = (
    "knowledge_answer",
    "file_request",
    "document_request",
    "flow_guidance",
    "fallback",
)

_ORCHESTRATOR_SYSTEM_PROMPT = (
    "你是企业Agent编排建议器（影子模式），只输出JSON对象。"
    "字段必须包含 suggested_action/reason。"
    "suggested_action只能是 knowledge_answer/file_request/document_request/flow_guidance/fallback。"
    "注意：权限、审批状态机、数据库写入不由你决定。"
)


class LLMOrchestratorShadowService:
    """Shadow-only orchestrator inference, never drives execution."""

    def __init__(
        self,
        *,
        llm_client: QwenChatClient | None,
        model: str,
        enabled: bool,
        rollout_percentage: int,
        timeout_seconds: int,
        max_retries: int,
        rollout_salt: str = "llm-orchestrator-shadow",
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._enabled = enabled and llm_client is not None
        self._rollout_percentage = rollout_percentage
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._rollout_salt = rollout_salt

    def suggest(
        self,
        *,
        question: str,
        intent: IntentType,
        rule_action: OrchestratorAction,
        conversation_id: str,
        sender_id: str,
    ) -> OrchestratorShadowResult:
        if not self._enabled:
            return self._fallback(rule_action=rule_action, reason="shadow_disabled", validation_passed=True)
        if not rollout_hit(
            conversation_id=conversation_id,
            sender_id=sender_id,
            percentage=self._rollout_percentage,
            salt=self._rollout_salt,
        ):
            return self._fallback(rule_action=rule_action, reason="shadow_rollout_not_hit", validation_passed=True)

        payload: dict[str, Any]
        try:
            assert self._llm_client is not None
            payload = self._llm_client.generate_json(
                model=self._model,
                system_prompt=_ORCHESTRATOR_SYSTEM_PROMPT,
                user_prompt=(
                    f"问题: {question.strip()}\n"
                    f"已识别意图: {intent}\n"
                    f"规则动作: {rule_action}\n"
                    "请只给动作建议，不要做权限和审批决策。"
                ),
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
            )
        except Exception as exc:
            return self._fallback(
                rule_action=rule_action,
                reason=f"shadow_error:{type(exc).__name__}",
                validation_passed=False,
            )

        action = payload.get("suggested_action")
        reason = payload.get("reason")
        if not isinstance(action, str) or action not in _ACTION_VALUES:
            return self._fallback(rule_action=rule_action, reason="shadow_invalid_action", validation_passed=False)
        if not isinstance(reason, str) or not reason.strip():
            return self._fallback(rule_action=rule_action, reason="shadow_invalid_reason", validation_passed=False)
        risk_tags: tuple[str, ...] = ()
        if action != rule_action:
            risk_tags = ("action_mismatch",)
        return OrchestratorShadowResult(
            suggested_action=action,
            rule_action=rule_action,
            reason=reason.strip(),
            model=self._model,
            fallback_used=False,
            validation_passed=True,
            risk_tags=risk_tags,
            raw_payload=payload,
        )

    def _fallback(self, *, rule_action: OrchestratorAction, reason: str, validation_passed: bool) -> OrchestratorShadowResult:
        return OrchestratorShadowResult(
            suggested_action=rule_action,
            rule_action=rule_action,
            reason=reason,
            model="shadow_fallback",
            fallback_used=True,
            validation_passed=validation_passed,
            risk_tags=(),
            raw_payload={},
        )


def build_default_orchestrator_shadow_service() -> LLMOrchestratorShadowService:
    api_key = env_str(("LLM_API_KEY", "QWEN_API_KEY"), "")
    endpoint = env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), DEFAULT_QWEN_CHAT_ENDPOINT)
    model = env_str(("LLM_CHAT_MODEL", "QWEN_CHAT_MODEL"), "qwen-plus")
    enabled_flag = env_bool("LLM_ORCHESTRATOR_SHADOW_ENABLED", False)
    enabled = enabled_flag and bool(api_key)
    llm_client: QwenChatClient | None = None
    if enabled:
        llm_client = HttpQwenChatClient(api_key=api_key, endpoint=endpoint)
    return LLMOrchestratorShadowService(
        llm_client=llm_client,
        model=model or "qwen-plus",
        enabled=enabled,
        rollout_percentage=env_int("LLM_ORCHESTRATOR_SHADOW_ROLLOUT_PERCENT", 10, minimum=0, maximum=100),
        timeout_seconds=env_int_alias(("LLM_TIMEOUT_SECONDS", "QWEN_TIMEOUT_SECONDS"), 10, minimum=1),
        max_retries=env_int_alias(("LLM_MAX_RETRIES", "QWEN_MAX_RETRIES"), 2, minimum=0, maximum=2),
    )
