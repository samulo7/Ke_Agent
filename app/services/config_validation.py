from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Mapping

from app.schemas.config_catalog import CONFIG_RULES


@dataclass(frozen=True)
class ConfigIssue:
    key: str
    code: str
    message: str
    remediation: str


@dataclass
class ValidationResult:
    resolved: dict[str, Any] = field(default_factory=dict)
    errors: list[ConfigIssue] = field(default_factory=list)
    warnings: list[ConfigIssue] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return len(self.errors) == 0


def _is_blank(value: str | None) -> bool:
    return value is None or value.strip() == ""


def validate_config(raw_env: Mapping[str, str]) -> ValidationResult:
    result = ValidationResult()

    for rule in CONFIG_RULES:
        original = raw_env.get(rule.key)
        used_default = False

        if _is_blank(original):
            if rule.required:
                result.errors.append(
                    ConfigIssue(
                        key=rule.key,
                        code="MISSING_REQUIRED",
                        message=f"{rule.key} is required but missing.",
                        remediation=rule.remediation,
                    )
                )
                continue

            if rule.default is None:
                result.resolved[rule.key] = None
                continue

            value_to_parse = rule.default
            used_default = True
        else:
            value_to_parse = original.strip()

        try:
            parsed = rule.parser(value_to_parse)
        except ValueError as exc:
            result.errors.append(
                ConfigIssue(
                    key=rule.key,
                    code="INVALID_VALUE",
                    message=f"{rule.key} invalid: {exc}",
                    remediation=rule.remediation,
                )
            )
            continue

        result.resolved[rule.key] = parsed

        if used_default:
            result.warnings.append(
                ConfigIssue(
                    key=rule.key,
                    code="DEFAULT_APPLIED",
                    message=f"{rule.key} missing, default applied: {rule.default}",
                    remediation=f"Set {rule.key} explicitly if the default is not suitable.",
                )
            )

    llm_api_key = str(result.resolved.get("LLM_API_KEY") or "").strip()
    legacy_llm_api_key = str(result.resolved.get("QWEN_API_KEY") or "").strip()
    if not llm_api_key and not legacy_llm_api_key:
        result.errors.append(
            ConfigIssue(
                key="LLM_API_KEY",
                code="MISSING_REQUIRED",
                message="LLM_API_KEY is required (or set legacy QWEN_API_KEY for compatibility).",
                remediation="Set LLM_API_KEY for your active model gateway, or set QWEN_API_KEY as legacy fallback.",
            )
        )

    runtime_env = str(result.resolved.get("APP_ENV", "dev")).lower()
    interactive_template_id = str(result.resolved.get("DINGTALK_CARD_TEMPLATE_ID", "") or "").strip()
    hr_approver_user_id = str(result.resolved.get("DINGTALK_HR_APPROVER_USER_ID", "") or "").strip()
    hr_template_id = str(result.resolved.get("DINGTALK_HR_CARD_TEMPLATE_ID", "") or "").strip()
    leave_approval_enabled = bool(result.resolved.get("DINGTALK_LEAVE_APPROVAL_ENABLED", False))
    leave_approval_process_code = str(result.resolved.get("DINGTALK_LEAVE_APPROVAL_PROCESS_CODE", "") or "").strip()
    leave_approval_type_field = str(result.resolved.get("DINGTALK_LEAVE_APPROVAL_TYPE_FIELD", "") or "").strip()
    leave_approval_start_time_field = str(result.resolved.get("DINGTALK_LEAVE_APPROVAL_START_TIME_FIELD", "") or "").strip()
    leave_approval_end_time_field = str(result.resolved.get("DINGTALK_LEAVE_APPROVAL_END_TIME_FIELD", "") or "").strip()
    reimburse_approval_enabled = bool(result.resolved.get("DINGTALK_REIMBURSE_APPROVAL_ENABLED", False))
    reimburse_approval_process_code = str(result.resolved.get("DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE", "") or "").strip()
    travel_lookup_enabled = bool(result.resolved.get("DINGTALK_REIMBURSE_TRAVEL_LOOKUP_ENABLED", False))
    travel_approval_process_code = str(result.resolved.get("DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE", "") or "").strip()
    ai_streaming_enabled = bool(result.resolved.get("DINGTALK_AI_CARD_STREAMING_ENABLED", False))
    ai_streaming_template_id = str(result.resolved.get("DINGTALK_AI_CARD_TEMPLATE_ID", "") or "").strip()
    ai_streaming_content_key = str(result.resolved.get("DINGTALK_AI_CARD_CONTENT_KEY", "") or "").strip()
    if ai_streaming_enabled and not ai_streaming_template_id:
        result.errors.append(
            ConfigIssue(
                key="DINGTALK_AI_CARD_TEMPLATE_ID",
                code="MISSING_REQUIRED_WHEN_ENABLED",
                message=(
                    "DINGTALK_AI_CARD_TEMPLATE_ID is required when "
                    "DINGTALK_AI_CARD_STREAMING_ENABLED=true."
                ),
                remediation=(
                    "Set DINGTALK_AI_CARD_TEMPLATE_ID to your streaming-enabled card template id."
                ),
            )
        )
    if ai_streaming_enabled and not ai_streaming_content_key:
        result.errors.append(
            ConfigIssue(
                key="DINGTALK_AI_CARD_CONTENT_KEY",
                code="MISSING_REQUIRED_WHEN_ENABLED",
                message=(
                    "DINGTALK_AI_CARD_CONTENT_KEY is required when "
                    "DINGTALK_AI_CARD_STREAMING_ENABLED=true."
                ),
                remediation="Set DINGTALK_AI_CARD_CONTENT_KEY to the template markdown variable key.",
            )
        )
    if interactive_template_id and ai_streaming_template_id and interactive_template_id == ai_streaming_template_id:
        result.errors.append(
            ConfigIssue(
                key="DINGTALK_CARD_TEMPLATE_ID",
                code="TEMPLATE_ID_CONFLICT",
                message=(
                    "DINGTALK_CARD_TEMPLATE_ID and DINGTALK_AI_CARD_TEMPLATE_ID must be different. "
                    "Interactive request-confirm card and AI streaming card cannot reuse one template."
                ),
                remediation=(
                    "Use two template ids: set DINGTALK_CARD_TEMPLATE_ID to interactive request button template, "
                    "and DINGTALK_AI_CARD_TEMPLATE_ID to AI streaming template."
                ),
            )
        )
    if bool(hr_approver_user_id) != bool(hr_template_id):
        missing_key = "DINGTALK_HR_CARD_TEMPLATE_ID" if hr_approver_user_id else "DINGTALK_HR_APPROVER_USER_ID"
        result.errors.append(
            ConfigIssue(
                key=missing_key,
                code="MISSING_REQUIRED_WHEN_ENABLED",
                message=(
                    "DINGTALK_HR_APPROVER_USER_ID and DINGTALK_HR_CARD_TEMPLATE_ID must be configured together "
                    "for HR approval card delivery."
                ),
                remediation=(
                    "Set both DINGTALK_HR_APPROVER_USER_ID and DINGTALK_HR_CARD_TEMPLATE_ID, "
                    "or leave both empty to disable HR approval card delivery."
                ),
            )
        )
    if leave_approval_enabled:
        required_leave_pairs = (
            ("DINGTALK_LEAVE_APPROVAL_PROCESS_CODE", leave_approval_process_code),
            ("DINGTALK_LEAVE_APPROVAL_TYPE_FIELD", leave_approval_type_field),
            ("DINGTALK_LEAVE_APPROVAL_START_TIME_FIELD", leave_approval_start_time_field),
            ("DINGTALK_LEAVE_APPROVAL_END_TIME_FIELD", leave_approval_end_time_field),
        )
        for key, value in required_leave_pairs:
            if value:
                continue
            result.errors.append(
                ConfigIssue(
                    key=key,
                    code="MISSING_REQUIRED_WHEN_ENABLED",
                    message=f"{key} is required when DINGTALK_LEAVE_APPROVAL_ENABLED=true.",
                    remediation=(
                        f"Set {key} to the mapped DingTalk leave approval value, "
                        "or disable DINGTALK_LEAVE_APPROVAL_ENABLED."
                    ),
                )
            )
    elif any((leave_approval_process_code, leave_approval_type_field, leave_approval_start_time_field, leave_approval_end_time_field)):
        result.warnings.append(
            ConfigIssue(
                key="DINGTALK_LEAVE_APPROVAL_ENABLED",
                code="DISABLED_FEATURE_CONFIG_PRESENT",
                message=(
                    "Leave approval config is present but DINGTALK_LEAVE_APPROVAL_ENABLED is false; "
                    "manual OA handoff will remain active."
                ),
                remediation="Set DINGTALK_LEAVE_APPROVAL_ENABLED=true to activate direct approval creation.",
            )
        )
    if reimburse_approval_enabled and not reimburse_approval_process_code:
        result.errors.append(
            ConfigIssue(
                key="DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE",
                code="MISSING_REQUIRED_WHEN_ENABLED",
                message="DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE is required when DINGTALK_REIMBURSE_APPROVAL_ENABLED=true.",
                remediation=(
                    "Set DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE to your reimbursement approval process code, "
                    "or disable DINGTALK_REIMBURSE_APPROVAL_ENABLED."
                ),
            )
        )
    elif reimburse_approval_process_code:
        result.warnings.append(
            ConfigIssue(
                key="DINGTALK_REIMBURSE_APPROVAL_ENABLED",
                code="DISABLED_FEATURE_CONFIG_PRESENT",
                message=(
                    "Reimbursement approval config is present but DINGTALK_REIMBURSE_APPROVAL_ENABLED is false; "
                    "auto submit remains disabled."
                ),
                remediation="Set DINGTALK_REIMBURSE_APPROVAL_ENABLED=true to activate reimbursement auto submission.",
            )
        )
    if travel_lookup_enabled and not travel_approval_process_code:
        result.errors.append(
            ConfigIssue(
                key="DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE",
                code="MISSING_REQUIRED_WHEN_ENABLED",
                message=(
                    "DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE is required when "
                    "DINGTALK_REIMBURSE_TRAVEL_LOOKUP_ENABLED=true."
                ),
                remediation=(
                    "Set DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE to your travel approval process code, "
                    "or disable DINGTALK_REIMBURSE_TRAVEL_LOOKUP_ENABLED."
                ),
            )
        )
    if hr_template_id:
        conflict_pairs = (
            ("DINGTALK_CARD_TEMPLATE_ID", interactive_template_id),
            ("DINGTALK_AI_CARD_TEMPLATE_ID", ai_streaming_template_id),
        )
        for conflict_key, conflict_value in conflict_pairs:
            if conflict_value and conflict_value == hr_template_id:
                result.errors.append(
                    ConfigIssue(
                        key="DINGTALK_HR_CARD_TEMPLATE_ID",
                        code="TEMPLATE_ID_CONFLICT",
                        message=(
                            "DINGTALK_HR_CARD_TEMPLATE_ID must be different from "
                            f"{conflict_key}."
                        ),
                        remediation=(
                            "Use separate template ids for requester confirmation, HR approval, "
                            "and AI streaming cards."
                        ),
                    )
                )

    if runtime_env == "prod":
        for rule in CONFIG_RULES:
            if not rule.production_forbidden:
                continue
            value = result.resolved.get(rule.key)
            if value in (None, "", False):
                continue
            result.errors.append(
                ConfigIssue(
                    key=rule.key,
                    code="PRODUCTION_FORBIDDEN",
                    message=f"{rule.key} must not be enabled in production.",
                    remediation=rule.remediation,
                )
            )

    return result


def summarize(result: ValidationResult) -> str:
    lines: list[str] = []
    if result.errors:
        lines.append(f"Errors: {len(result.errors)}")
        for issue in result.errors:
            lines.append(f"- [{issue.code}] {issue.key}: {issue.message}")
            lines.append(f"  Fix: {issue.remediation}")
    else:
        lines.append("Errors: 0")

    if result.warnings:
        lines.append(f"Warnings: {len(result.warnings)}")
        for issue in result.warnings:
            lines.append(f"- [{issue.code}] {issue.key}: {issue.message}")
    else:
        lines.append("Warnings: 0")

    return "\n".join(lines)
