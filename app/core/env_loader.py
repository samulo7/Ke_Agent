from __future__ import annotations

from functools import lru_cache
import os
from pathlib import Path


def _default_env_path() -> Path:
    return Path(__file__).resolve().parents[2] / ".env"


def _normalize_env_value(raw: str) -> str:
    value = raw.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
        return value[1:-1]
    return value


@lru_cache(maxsize=None)
def load_project_env(env_path: str | None = None) -> str | None:
    path = Path(env_path) if env_path else _default_env_path()
    if not path.exists():
        return None
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, raw_value = line.split("=", 1)
        key = key.strip()
        if not key or key in os.environ:
            continue
        os.environ[key] = _normalize_env_value(raw_value)
    return str(path)
