from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.structured_logging import configure_structured_logging  # noqa: E402
from app.integrations.dingtalk.stream_runtime import (  # noqa: E402
    StreamRuntimeError,
    load_stream_credentials,
    run_stream_client_forever,
)


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def apply_env_defaults(values: dict[str, str]) -> None:
    """
    Inject env-file values into process environment without overriding explicit
    process env vars. This keeps "process env > env file" precedence while
    making `.env` values visible to runtime services that read `os.getenv(...)`.
    """
    for key, value in values.items():
        os.environ.setdefault(key, value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run DingTalk Stream long-connection client for A-05.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Optional env file. Process environment overrides file values.",
    )
    args = parser.parse_args()

    merged: dict[str, str] = {}
    file_values: dict[str, str] = {}
    if args.env_file is not None and args.env_file.exists():
        file_values = parse_env_file(args.env_file)
        merged.update(file_values)

    apply_env_defaults(file_values)
    merged.update({k: v for k, v in os.environ.items()})

    try:
        credentials = load_stream_credentials(merged)
    except StreamRuntimeError as exc:
        print(f"Failed to load DingTalk stream credentials: {exc}")
        return 1

    log_level = merged.get("LOG_LEVEL", "INFO")
    configure_structured_logging(level=log_level)
    print(f"Starting DingTalk stream client at endpoint: {credentials.stream_endpoint}")

    try:
        run_stream_client_forever(credentials)
    except StreamRuntimeError as exc:
        print(f"Failed to start DingTalk stream client: {exc}")
        return 1
    except KeyboardInterrupt:
        print("DingTalk stream client stopped by user.")
        return 0

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
