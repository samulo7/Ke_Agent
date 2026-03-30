from __future__ import annotations

from typing import Any, Mapping

from app.integrations.qwen.client import DEFAULT_QWEN_CHAT_ENDPOINT, HttpQwenChatClient, QwenChatClient
from app.schemas.llm import ContentGenerationResult
from app.services.llm_constants import SUMMARY_ONLY_ALLOWLIST
from app.services.llm_env import env_bool, env_int, env_int_alias, env_str, rollout_hit

_CONTENT_SYSTEM_PROMPT = (
    "你是企业Agent回复生成器，只输出JSON对象，字段必须是 text。"
    "不能输出Markdown代码块。"
    "不能新增输入中不存在的敏感事实。"
    "要保持可执行、简洁、中文。"
)


class LLMContentGenerationService:
    """Language-only generation layer with strict guardrails and deterministic fallback."""

    def __init__(
        self,
        *,
        llm_client: QwenChatClient | None,
        model: str,
        enabled: bool,
        rollout_percentage: int,
        timeout_seconds: int,
        max_retries: int,
        rollout_salt: str = "llm-content",
    ) -> None:
        self._llm_client = llm_client
        self._model = model
        self._enabled = enabled and llm_client is not None
        self._rollout_percentage = rollout_percentage
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._rollout_salt = rollout_salt

    def generate(
        self,
        *,
        mode: str,
        question: str,
        prompt_fields: Mapping[str, str],
        fallback_text: str,
        conversation_id: str,
        sender_id: str,
        disallowed_values: tuple[str, ...] = (),
    ) -> ContentGenerationResult:
        if not self._enabled:
            return self._fallback_result(
                fallback_text=fallback_text,
                safety_flags=("llm_disabled",),
                validation_passed=True,
            )
        if not rollout_hit(
            conversation_id=conversation_id,
            sender_id=sender_id,
            percentage=self._rollout_percentage,
            salt=self._rollout_salt,
        ):
            return self._fallback_result(
                fallback_text=fallback_text,
                safety_flags=("llm_rollout_not_hit",),
                validation_passed=True,
            )

        allowed_fields = self._filter_prompt_fields(mode=mode, prompt_fields=prompt_fields)
        user_prompt = self._build_user_prompt(mode=mode, question=question, prompt_fields=allowed_fields)
        payload: dict[str, Any]
        try:
            assert self._llm_client is not None
            payload = self._llm_client.generate_json(
                model=self._model,
                system_prompt=_CONTENT_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                timeout_seconds=self._timeout_seconds,
                max_retries=self._max_retries,
            )
        except Exception as exc:
            return self._fallback_result(
                fallback_text=fallback_text,
                safety_flags=(f"llm_error:{type(exc).__name__}",),
                validation_passed=False,
            )

        text = payload.get("text")
        if not isinstance(text, str):
            return self._fallback_result(
                fallback_text=fallback_text,
                safety_flags=("missing_text",),
                validation_passed=False,
            )
        normalized = text.strip()
        flags = self._validate_output(mode=mode, text=normalized, disallowed_values=disallowed_values)
        if flags:
            return self._fallback_result(
                fallback_text=fallback_text,
                safety_flags=tuple(flags),
                validation_passed=False,
            )
        return ContentGenerationResult(
            text=normalized,
            safety_flags=(),
            validation_passed=True,
            fallback_used=False,
            model=self._model,
            raw_payload=payload,
        )

    @staticmethod
    def _filter_prompt_fields(*, mode: str, prompt_fields: Mapping[str, str]) -> dict[str, str]:
        sanitized = {str(key): str(value).strip() for key, value in prompt_fields.items() if str(value).strip()}
        if mode == "summary_only":
            return {key: value for key, value in sanitized.items() if key in SUMMARY_ONLY_ALLOWLIST}
        return sanitized

    @staticmethod
    def _build_user_prompt(*, mode: str, question: str, prompt_fields: Mapping[str, str]) -> str:
        lines = [
            f"模式: {mode}",
            f"用户问题: {question.strip()}",
            "可用字段:",
        ]
        for key, value in prompt_fields.items():
            lines.append(f"- {key}: {value}")
        lines.append("输出JSON示例: {\"text\":\"...\"}")
        return "\n".join(lines)

    @staticmethod
    def _validate_output(*, mode: str, text: str, disallowed_values: tuple[str, ...]) -> list[str]:
        flags: list[str] = []
        if not text:
            flags.append("empty_text")
            return flags
        if len(text) > 1200:
            flags.append("too_long")
        if "```" in text:
            flags.append("contains_code_block")
        if mode == "summary_only":
            if "申请路径" not in text:
                flags.append("summary_only_missing_next_step")
            if "联系人" not in text:
                flags.append("summary_only_missing_contact")
        if mode == "deny":
            if "不可查看" not in text and "不可直接查看" not in text:
                flags.append("deny_missing_restriction_notice")
            if "申请路径" not in text:
                flags.append("deny_missing_next_step")
        for value in disallowed_values:
            cleaned = value.strip()
            if cleaned and cleaned in text:
                flags.append("contains_disallowed_content")
                break
        return flags

    def _fallback_result(
        self,
        *,
        fallback_text: str,
        safety_flags: tuple[str, ...],
        validation_passed: bool,
    ) -> ContentGenerationResult:
        return ContentGenerationResult(
            text=fallback_text,
            safety_flags=safety_flags,
            validation_passed=validation_passed,
            fallback_used=True,
            model="template_fallback",
            raw_payload={},
        )


def build_default_llm_content_generation_service() -> LLMContentGenerationService:
    api_key = env_str(("LLM_API_KEY", "QWEN_API_KEY"), "")
    endpoint = env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), DEFAULT_QWEN_CHAT_ENDPOINT)
    model = env_str(("LLM_CHAT_MODEL", "QWEN_CHAT_MODEL"), "qwen-plus")
    enabled_flag = env_bool("LLM_CONTENT_ENABLED", False)
    enabled = enabled_flag and bool(api_key)
    llm_client: QwenChatClient | None = None
    if enabled:
        llm_client = HttpQwenChatClient(api_key=api_key, endpoint=endpoint)
    return LLMContentGenerationService(
        llm_client=llm_client,
        model=model or "qwen-plus",
        enabled=enabled,
        rollout_percentage=env_int("LLM_CONTENT_ROLLOUT_PERCENT", 10, minimum=0, maximum=100),
        timeout_seconds=env_int_alias(("LLM_TIMEOUT_SECONDS", "QWEN_TIMEOUT_SECONDS"), 10, minimum=1),
        max_retries=env_int_alias(("LLM_MAX_RETRIES", "QWEN_MAX_RETRIES"), 2, minimum=0, maximum=2),
    )

