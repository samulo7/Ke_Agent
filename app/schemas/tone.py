from __future__ import annotations

from typing import Literal

from app.schemas.dingtalk_chat import IntentType

ToneProfile = Literal["formal", "neutral", "conversational"]

_ALLOWED_TONES: set[str] = {"formal", "neutral", "conversational"}
_ALLOWED_INTENTS: set[str] = {
    "policy_process",
    "document_request",
    "reimbursement",
    "leave",
    "fixed_quote",
    "other",
}


def parse_tone_profile(value: str) -> ToneProfile:
    normalized = value.strip().lower()
    if normalized not in _ALLOWED_TONES:
        raise ValueError("must be one of: conversational, formal, neutral")
    return normalized  # type: ignore[return-value]


def parse_intent_tone_overrides(value: str) -> dict[IntentType, ToneProfile]:
    normalized = value.strip()
    if not normalized:
        return {}

    result: dict[IntentType, ToneProfile] = {}
    pairs = [item.strip() for item in normalized.split(",") if item.strip()]
    for pair in pairs:
        if ":" not in pair:
            raise ValueError("must use format intent:tone, separated by commas")

        intent_raw, tone_raw = pair.split(":", 1)
        intent = intent_raw.strip().lower()
        if intent not in _ALLOWED_INTENTS:
            raise ValueError(
                "intent must be one of: document_request, fixed_quote, leave, other, policy_process, reimbursement"
            )
        tone = parse_tone_profile(tone_raw)
        result[intent] = tone  # type: ignore[index]

    return result
