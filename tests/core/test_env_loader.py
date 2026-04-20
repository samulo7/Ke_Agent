from __future__ import annotations

import os
import tempfile
import unittest

from app.core.env_loader import load_project_env


class EnvLoaderTests(unittest.TestCase):
    def tearDown(self) -> None:
        load_project_env.cache_clear()
        for key in ("TEST_ENV_ALPHA", "TEST_ENV_BETA", "TEST_ENV_GAMMA"):
            os.environ.pop(key, None)

    def test_load_project_env_sets_missing_values(self) -> None:
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("TEST_ENV_ALPHA=hello\nTEST_ENV_BETA='world'\n")
            path = handle.name
        try:
            load_project_env(path)
            self.assertEqual("hello", os.environ.get("TEST_ENV_ALPHA"))
            self.assertEqual("world", os.environ.get("TEST_ENV_BETA"))
        finally:
            os.unlink(path)

    def test_load_project_env_does_not_override_existing_values(self) -> None:
        os.environ["TEST_ENV_GAMMA"] = "from-process"
        with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as handle:
            handle.write("TEST_ENV_GAMMA=from-file\n")
            path = handle.name
        try:
            load_project_env(path)
            self.assertEqual("from-process", os.environ.get("TEST_ENV_GAMMA"))
        finally:
            os.unlink(path)


if __name__ == "__main__":
    unittest.main()
