from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.services.llm_env import env_bool, env_int, env_int_alias, env_str, rollout_hit

_DRAFT_SYSTEM_PROMPT = (
    "你是企业内部文档申请草稿助手，只输出JSON对象。"
    "只能抽取或润色字段，不得决定审批状态。"
)


@dataclass(frozen=True)
class DraftLLMResult:
    requested_item: str
    request_purpose: str
    fallback_used: bool
    validation_passed: bool
    model: str
    raw_payload: dict[str, Any] = field(default_factory=dict)


class LLMDraftGenerationService:
    """LLM assistant for field extraction and wording polish only."""

    def __init__(
        self,
        *,
        llm_client: QwenChatClient | None,
        model: str,
        enabled: bool,
        rollout_percentage: int,
        timeout_seconds: int,
        max_retries: int,
        rollout_salt: str = "llm-draft",
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._enabled = enabled and llm_client is not None
        self._rollout_percentage = rollout_percentage
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._rollout_salt = rollout_salt

    def extract_initial(
        self,
        *,
        text: str,
        conversation_id: str,
        sender_id: str,
    ) -> DraftLLMResult:
        return self._extract(
            mode="initial",
            text=text,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    def extract_followup(
        self,
        *,
        text: str,
        conversation_id: str,
        sender_id: str,
    ) -> DraftLLMResult:
        return self._extract(
            mode="followup",
            text=text,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    def polish_purpose(
        self,
        *,
        purpose: str,
        conversation_id: str,
        sender_id: str,
    ) -> DraftLLMResult:
        return self._extract(
            mode="polish",
            text=purpose,
            conversation_id=conversation_id,
            sender_id=sender_id,
        )

    def _extract(
        self,
        *,
        mode: str,
        text: str,
        conversation_id: str,
        sender_id: str,
    ) -> DraftLLMResult:
        if not self._enabled:
            return self._fallback()
        if not rollout_hit(
            conversation_id=conversation_id,
            sender_id=sender_id,
            percentage=self._rollout_percentage,
            salt=self._rollout_salt,
        ):
            return self._fallback()
        payload: dict[str, Any]
        try:
            assert self._llm_client is not None
            payload = self._llm_client.generate_json(
                model=self._model,
                system_prompt=_DRAFT_SYSTEM_PROMPT,
                user_prompt=(
                    f"模式: {mode}\n"
                    f"文本: {text.strip()}\n"
                    "输出JSON字段: requested_item, request_purpose。"
                    "无法识别请返回空字符串。"
                ),
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
            )
        except Exception:
            return self._fallback(validation_passed=False)
        item = payload.get("requested_item")
        purpose = payload.get("request_purpose")
        if not isinstance(item, str):
            item = ""
        if not isinstance(purpose, str):
            purpose = ""
        item = item.strip("：:，,。.!！?？ ").strip()
        purpose = purpose.strip("：:，,。.!！?？ ").strip()
        if len(purpose) == 1:
            purpose = ""
        return DraftLLMResult(
            requested_item=item,
            request_purpose=purpose,
            fallback_used=False,
            validation_passed=True,
            model=self._model,
            raw_payload=payload,
        )

    @staticmethod
    def _fallback(validation_passed: bool = True) -> DraftLLMResult:
        return DraftLLMResult(
            requested_item="",
            request_purpose="",
            fallback_used=True,
            validation_passed=validation_passed,
            model="rule_fallback",
            raw_payload={},
        )


def build_default_llm_draft_generation_service() -> LLMDraftGenerationService:
    api_key = env_str(("LLM_API_KEY", "QWEN_API_KEY"), "")
    endpoint = env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), DEFAULT_QWEN_CHAT_ENDPOINT)
    model = env_str(("LLM_CHAT_MODEL", "QWEN_CHAT_MODEL"), "qwen-plus")
    enabled_flag = env_bool("LLM_DRAFT_ENABLED", False)
    enabled = enabled_flag and bool(api_key)
    llm_client: QwenChatClient | None = None
    if enabled:
        llm_client = HttpQwenChatClient(api_key=api_key, endpoint=endpoint)
    return LLMDraftGenerationService(
        llm_client=llm_client,
        model=model or "qwen-plus",
        enabled=enabled,
        rollout_percentage=env_int("LLM_DRAFT_ROLLOUT_PERCENT", 10, minimum=0, maximum=100),
        timeout_seconds=env_int_alias(("LLM_TIMEOUT_SECONDS", "QWEN_TIMEOUT_SECONDS"), 10, minimum=1),
        max_retries=env_int_alias(("LLM_MAX_RETRIES", "QWEN_MAX_RETRIES"), 2, minimum=0, maximum=2),
    )

