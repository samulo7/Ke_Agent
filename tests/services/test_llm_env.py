from __future__ import annotations

import os
import unittest
from unittest.mock import patch

from app.services.llm_env import env_bool_alias, env_int_alias, env_str


class LLMEnvAliasTests(unittest.TestCase):
    def test_env_str_prefers_first_non_empty_alias(self) -> None:
        with patch.dict(
            os.environ,
            {
                "LLM_BASE_URL": "https://llm.example/v1",
                "QWEN_BASE_URL": "https://legacy.example/v1",
            },
            clear=True,
        ):
            self.assertEqual("https://llm.example/v1", env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), "default"))

    def test_env_str_falls_back_to_legacy_alias(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_BASE_URL": "https://legacy.example/v1",
            },
            clear=True,
        ):
            self.assertEqual("https://legacy.example/v1", env_str(("LLM_BASE_URL", "QWEN_BASE_URL"), "default"))

    def test_env_int_alias_applies_bounds(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_MAX_RETRIES": "9",
            },
            clear=True,
        ):
            self.assertEqual(2, env_int_alias(("LLM_MAX_RETRIES", "QWEN_MAX_RETRIES"), 0, minimum=0, maximum=2))

    def test_env_bool_alias_uses_first_present_value(self) -> None:
        with patch.dict(
            os.environ,
            {
                "QWEN_FLAG": "true",
            },
            clear=True,
        ):
            self.assertTrue(env_bool_alias(("LLM_FLAG", "QWEN_FLAG"), False))


if __name__ == "__main__":
    unittest.main()
