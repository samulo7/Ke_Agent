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
        "QWEN_API_KEY": "qwen-key",
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


if __name__ == "__main__":
    unittest.main()
