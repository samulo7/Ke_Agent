from __future__ import annotations

import re
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
_REIMBURSEMENT_FORBIDDEN_LABELS = (
    "办理入口：",
    "办理入口:",
    "准备材料：",
    "准备材料:",
    "流程路径：",
    "流程路径:",
    "下一步：",
    "下一步:",
)
_REIMBURSEMENT_MATERIAL_KEYWORDS = ("发票", "行程单", "金额")
_REIMBURSEMENT_FOLLOWUP_HINTS = (
    "告诉我",
    "我帮你",
    "我来帮你",
    "不确定",
    "要不要",
    "你可以",
    "可以说",
)
_SENTENCE_SPLIT_PATTERN = re.compile(r"[。！？!?]+")


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
        if mode == "flow_guidance_reimbursement":
            user_input = prompt_fields.get("user_input", question).strip()
            canonical_block = prompt_fields.get("canonical_block", "")
            return (
                "你是企业内部助手，用户询问了报销相关流程。\n\n"
                "请用自然对话语气回复，不要使用\"字段名：值\"的格式，\n"
                "不要出现\"办理入口：\"、\"准备材料：\"、\"流程路径：\"、\"下一步：\"等标签。\n\n"
                "回复要求：\n"
                "- 2-4句话说清楚怎么报销\n"
                "- 自然提到需要准备什么\n"
                "- 自然提到常见注意事项\n"
                "- 结尾留一个追问引导\n\n"
                "参考风格：\n"
                "\"出差报销需要准备发票、行程单和金额说明，在钉钉工作台的审批里选对应模板提交就行。"
                "注意出差后30天内要报，超时财务会退回。不确定选哪个报销类型可以告诉我，我帮你找。\"\n\n"
                f"用户问题：{user_input}\n"
                f"已知报销规则：{canonical_block}\n\n"
                "只输出JSON示例: {\"text\":\"...\"}"
            )

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
        if mode == "flow_guidance_reimbursement":
            flags.extend(LLMContentGenerationService._validate_reimbursement_guidance_text(text))
        for value in disallowed_values:
            cleaned = value.strip()
            if cleaned and cleaned in text:
                flags.append("contains_disallowed_content")
                break
        return flags

    @staticmethod
    def _validate_reimbursement_guidance_text(text: str) -> list[str]:
        flags: list[str] = []
        sentence_count = LLMContentGenerationService._count_sentences(text)
        if sentence_count < 2 or sentence_count > 4:
            flags.append("flow_guidance_reimbursement_sentence_count_invalid")

        if any(label in text for label in _REIMBURSEMENT_FORBIDDEN_LABELS):
            flags.append("flow_guidance_reimbursement_contains_field_labels")

        material_hits = sum(1 for token in _REIMBURSEMENT_MATERIAL_KEYWORDS if token in text)
        if material_hits < 2:
            flags.append("flow_guidance_reimbursement_missing_materials")

        if "30天" not in text and "三十天" not in text:
            flags.append("flow_guidance_reimbursement_missing_time_limit")

        has_invoice_mismatch_notice = "金额与发票不符" in text or (
            "金额" in text and "发票" in text and ("不符" in text or "不一致" in text)
        )
        if not has_invoice_mismatch_notice:
            flags.append("flow_guidance_reimbursement_missing_mismatch_notice")

        if not LLMContentGenerationService._ends_with_followup_prompt(text):
            flags.append("flow_guidance_reimbursement_missing_followup_prompt")
        return flags

    @staticmethod
    def _count_sentences(text: str) -> int:
        normalized = re.sub(r"\s+", " ", text).strip()
        if not normalized:
            return 0
        parts = [part.strip() for part in _SENTENCE_SPLIT_PATTERN.split(normalized) if part.strip()]
        return len(parts)

    @staticmethod
    def _ends_with_followup_prompt(text: str) -> bool:
        parts = [part.strip() for part in _SENTENCE_SPLIT_PATTERN.split(text.strip()) if part.strip()]
        if not parts:
            return False
        last_sentence = parts[-1]
        if text.strip().endswith(("？", "?")):
            return True
        return any(token in last_sentence for token in _REIMBURSEMENT_FOLLOWUP_HINTS)

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

