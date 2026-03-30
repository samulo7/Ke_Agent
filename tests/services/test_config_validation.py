from __future__ import annotations

import unittest

from app.schemas.config_catalog import REQUIRED_KEYS
from app.services.config_validation import validate_config


def make_valid_env() -> dict[str, str]:
    return {
        "APP_ENV": "dev",
        "DINGTALK_CLIENT_ID": "cid",
        "DINGTALK_CLIENT_SECRET": "secret",
        "DINGTALK_AGENT_ID": "agent",
        "LLM_API_KEY": "llm-key",
        "PG_HOST": "127.0.0.1",
        "PG_DATABASE": "keagent",
        "PG_USER": "keagent",
        "PG_PASSWORD": "pwd",
        "REDIS_HOST": "127.0.0.1",
    }


class ConfigValidationTests(unittest.TestCase):
    def test_valid_minimum_config_passes(self) -> None:
        result = validate_config(make_valid_env())
        self.assertTrue(result.ok)
        self.assertEqual(8000, result.resolved["APP_PORT"])
        self.assertEqual("INFO", result.resolved["LOG_LEVEL"])
        self.assertEqual("json", result.resolved["LOG_FORMAT"])

    def test_missing_required_keys_are_precise(self) -> None:
        for key in REQUIRED_KEYS:
            with self.subTest(missing_key=key):
                env = make_valid_env()
                env.pop(key, None)
                result = validate_config(env)
                self.assertFalse(result.ok)
                matches = [err for err in result.errors if err.key == key and err.code == "MISSING_REQUIRED"]
                self.assertEqual(1, len(matches), f"missing error for required key: {key}")
                self.assertTrue(matches[0].remediation.strip())

    def test_legacy_qwen_api_key_alias_still_passes(self) -> None:
        env = make_valid_env()
        env.pop("LLM_API_KEY", None)
        env["QWEN_API_KEY"] = "legacy-key"
        result = validate_config(env)
        self.assertTrue(result.ok)

    def test_missing_llm_and_legacy_api_key_is_rejected(self) -> None:
        env = make_valid_env()
        env.pop("LLM_API_KEY", None)
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "LLM_API_KEY" and err.code == "MISSING_REQUIRED"]
        self.assertEqual(1, len(matches))

    def test_invalid_integer_is_rejected(self) -> None:
        env = make_valid_env()
        env["PG_PORT"] = "abc"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "PG_PORT" and err.code == "INVALID_VALUE"]
        self.assertEqual(1, len(matches))

    def test_production_forbidden_switch_is_rejected(self) -> None:
        env = make_valid_env()
        env["APP_ENV"] = "prod"
        env["DEV_BYPASS_AUTH"] = "true"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "DEV_BYPASS_AUTH" and err.code == "PRODUCTION_FORBIDDEN"]
        self.assertEqual(1, len(matches))

    def test_tone_configuration_accepts_default_and_intent_overrides(self) -> None:
        env = make_valid_env()
        env["RESPONSE_TONE_DEFAULT"] = "formal"
        env["RESPONSE_TONE_BY_INTENT"] = "policy_process:neutral,fixed_quote:formal"
        result = validate_config(env)
        self.assertTrue(result.ok)
        self.assertEqual("formal", result.resolved["RESPONSE_TONE_DEFAULT"])
        self.assertEqual(
            {"policy_process": "neutral", "fixed_quote": "formal"},
            result.resolved["RESPONSE_TONE_BY_INTENT"],
        )

    def test_tone_configuration_rejects_invalid_values(self) -> None:
        env = make_valid_env()
        env["RESPONSE_TONE_BY_INTENT"] = "policy_process:loud"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "RESPONSE_TONE_BY_INTENT" and err.code == "INVALID_VALUE"]
        self.assertEqual(1, len(matches))

    def test_llm_rollout_configuration_defaults_are_resolved(self) -> None:
        result = validate_config(make_valid_env())
        self.assertTrue(result.ok)
        self.assertEqual(10, result.resolved["LLM_INTENT_ROLLOUT_PERCENT"])
        self.assertEqual(10, result.resolved["LLM_CONTENT_ROLLOUT_PERCENT"])
        self.assertEqual(10, result.resolved["LLM_DRAFT_ROLLOUT_PERCENT"])
        self.assertEqual(10, result.resolved["LLM_ORCHESTRATOR_SHADOW_ROLLOUT_PERCENT"])

    def test_llm_rollout_configuration_rejects_invalid_percentage(self) -> None:
        env = make_valid_env()
        env["LLM_INTENT_ROLLOUT_PERCENT"] = "101"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "LLM_INTENT_ROLLOUT_PERCENT" and err.code == "INVALID_VALUE"]
        self.assertEqual(1, len(matches))

    def test_streaming_card_defaults_are_resolved(self) -> None:
        result = validate_config(make_valid_env())
        self.assertTrue(result.ok)
        self.assertFalse(result.resolved["DINGTALK_AI_CARD_STREAMING_ENABLED"])
        self.assertEqual("content", result.resolved["DINGTALK_AI_CARD_CONTENT_KEY"])
        self.assertEqual(20, result.resolved["DINGTALK_AI_CARD_CHUNK_CHARS"])
        self.assertEqual(120, result.resolved["DINGTALK_AI_CARD_INTERVAL_MS"])

    def test_streaming_card_enabled_requires_template_id(self) -> None:
        env = make_valid_env()
        env["DINGTALK_AI_CARD_STREAMING_ENABLED"] = "true"
        env["DINGTALK_AI_CARD_TEMPLATE_ID"] = ""
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [
            err
            for err in result.errors
            if err.key == "DINGTALK_AI_CARD_TEMPLATE_ID" and err.code == "MISSING_REQUIRED_WHEN_ENABLED"
        ]
        self.assertEqual(1, len(matches))

    def test_interactive_and_streaming_card_templates_must_be_separated(self) -> None:
        env = make_valid_env()
        env["DINGTALK_CARD_TEMPLATE_ID"] = "tpl-same.schema"
        env["DINGTALK_AI_CARD_TEMPLATE_ID"] = "tpl-same.schema"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [err for err in result.errors if err.key == "DINGTALK_CARD_TEMPLATE_ID" and err.code == "TEMPLATE_ID_CONFLICT"]
        self.assertEqual(1, len(matches))

    def test_hr_approval_card_requires_user_and_template_together(self) -> None:
        env_missing_template = make_valid_env()
        env_missing_template["DINGTALK_HR_APPROVER_USER_ID"] = "hr-user-1"
        result_missing_template = validate_config(env_missing_template)
        self.assertFalse(result_missing_template.ok)
        matches_missing_template = [
            err
            for err in result_missing_template.errors
            if err.key == "DINGTALK_HR_CARD_TEMPLATE_ID" and err.code == "MISSING_REQUIRED_WHEN_ENABLED"
        ]
        self.assertEqual(1, len(matches_missing_template))

        env_missing_user = make_valid_env()
        env_missing_user["DINGTALK_HR_CARD_TEMPLATE_ID"] = "tpl-hr.schema"
        result_missing_user = validate_config(env_missing_user)
        self.assertFalse(result_missing_user.ok)
        matches_missing_user = [
            err
            for err in result_missing_user.errors
            if err.key == "DINGTALK_HR_APPROVER_USER_ID" and err.code == "MISSING_REQUIRED_WHEN_ENABLED"
        ]
        self.assertEqual(1, len(matches_missing_user))

    def test_hr_approval_card_template_must_be_separated_from_other_templates(self) -> None:
        env = make_valid_env()
        env["DINGTALK_HR_APPROVER_USER_ID"] = "hr-user-1"
        env["DINGTALK_HR_CARD_TEMPLATE_ID"] = "tpl-same.schema"
        env["DINGTALK_CARD_TEMPLATE_ID"] = "tpl-same.schema"
        result = validate_config(env)
        self.assertFalse(result.ok)
        matches = [
            err
            for err in result.errors
            if err.key == "DINGTALK_HR_CARD_TEMPLATE_ID" and err.code == "TEMPLATE_ID_CONFLICT"
        ]
        self.assertEqual(1, len(matches))

    def test_hr_approval_card_valid_combination_passes(self) -> None:
        env = make_valid_env()
        env["DINGTALK_HR_APPROVER_USER_ID"] = "hr-user-1"
        env["DINGTALK_HR_CARD_TEMPLATE_ID"] = "tpl-hr.schema"
        env["DINGTALK_CARD_TEMPLATE_ID"] = "tpl-requester.schema"
        env["DINGTALK_AI_CARD_TEMPLATE_ID"] = "tpl-ai.schema"
        result = validate_config(env)
        self.assertTrue(result.ok)


if __name__ == "__main__":
    unittest.main()
