from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.services.config_validation import summarize, validate_config  # noqa: E402


def parse_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.exists():
        raise FileNotFoundError(f"env file not found: {path}")

    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key.strip()] = value.strip()
    return values


def main() -> int:
    parser = argparse.ArgumentParser(description="Validate A-03 configuration and secrets.")
    parser.add_argument(
        "--env-file",
        type=Path,
        default=None,
        help="Optional env file path. Values can be overridden by process environment.",
    )
    parser.add_argument(
        "--show-resolved",
        action="store_true",
        help="Print resolved values (use only with non-sensitive test data).",
    )
    args = parser.parse_args()

    merged: dict[str, str] = {}
    if args.env_file is not None:
        merged.update(parse_env_file(args.env_file))
    merged.update({k: v for k, v in os.environ.items()})

    result = validate_config(merged)
    print(summarize(result))

    if args.show_resolved:
        print("Resolved values:")
        for key in sorted(result.resolved.keys()):
            print(f"- {key}={result.resolved[key]}")

    if result.ok:
        print("Config validation passed.")
        return 0

    print("Config validation failed.")
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
