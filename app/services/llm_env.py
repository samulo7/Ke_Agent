from __future__ import annotations

import hashlib
import os

_TRUTHY = {"1", "true", "yes", "y", "on"}
_FALSY = {"0", "false", "no", "n", "off", ""}


def env_str(keys: tuple[str, ...], default: str = "") -> str:
    for key in keys:
        raw = os.getenv(key)
        if raw is None:
            continue
        value = raw.strip()
        if value:
            return value
    return default


def env_bool(key: str, default: bool) -> bool:
    raw = os.getenv(key)
    if raw is None:
        return default
    normalized = raw.strip().lower()
    if normalized in _TRUTHY:
        return True
    if normalized in _FALSY:
        return False
    return default


def env_bool_alias(keys: tuple[str, ...], default: bool) -> bool:
    for key in keys:
        raw = os.getenv(key)
        if raw is None:
            continue
        normalized = raw.strip().lower()
        if normalized in _TRUTHY:
            return True
        if normalized in _FALSY:
            return False
    return default


def env_int(key: str, default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = int(raw.strip())
        except ValueError:
            value = default
    if value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def env_int_alias(keys: tuple[str, ...], default: int, *, minimum: int = 0, maximum: int | None = None) -> int:
    value = default
    for key in keys:
        raw = os.getenv(key)
        if raw is None or raw.strip() == "":
            continue
        try:
            value = int(raw.strip())
        except ValueError:
            value = default
        break
    if value < minimum:
        value = minimum
    if maximum is not None and value > maximum:
        value = maximum
    return value


def env_float(key: str, default: float, *, minimum: float = 0.0, maximum: float = 1.0) -> float:
    raw = os.getenv(key)
    if raw is None or raw.strip() == "":
        value = default
    else:
        try:
            value = float(raw.strip())
        except ValueError:
            value = default
    if value < minimum:
        value = minimum
    if value > maximum:
        value = maximum
    return value


def rollout_hit(*, conversation_id: str, sender_id: str, percentage: int, salt: str) -> bool:
    if percentage >= 100:
        return True
    if percentage <= 0:
        return False
    key = f"{conversation_id}::{sender_id}::{salt}".encode("utf-8")
    bucket = int.from_bytes(hashlib.sha256(key).digest()[:2], byteorder="big") % 100
    return bucket < percentage
