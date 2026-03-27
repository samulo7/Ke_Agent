from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Mapping

from app.schemas.dingtalk_chat import IntentType
from app.schemas.tone import ToneProfile, parse_intent_tone_overrides, parse_tone_profile

DEFAULT_TONE_ENV_KEY = "RESPONSE_TONE_DEFAULT"
INTENT_TONE_ENV_KEY = "RESPONSE_TONE_BY_INTENT"


@dataclass(frozen=True)
class ToneResolver:
    default_tone: ToneProfile = "conversational"
    overrides: Mapping[IntentType, ToneProfile] = field(default_factory=dict)

    def resolve(self, *, intent: IntentType) -> ToneProfile:
        return self.overrides.get(intent, self.default_tone)


def build_tone_resolver_from_env(raw_env: Mapping[str, str] | None = None) -> ToneResolver:
    env = raw_env if raw_env is not None else os.environ
    default_tone = parse_tone_profile((env.get(DEFAULT_TONE_ENV_KEY) or "conversational"))
    overrides = parse_intent_tone_overrides((env.get(INTENT_TONE_ENV_KEY) or ""))
    return ToneResolver(default_tone=default_tone, overrides=overrides)
