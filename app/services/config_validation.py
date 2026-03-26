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

    runtime_env = str(result.resolved.get("APP_ENV", "dev")).lower()
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
