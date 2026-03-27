from __future__ import annotations

import unittest

from app.services.tone_resolver import ToneResolver, build_tone_resolver_from_env


class ToneResolverTests(unittest.TestCase):
    def test_resolve_uses_default_when_no_override(self) -> None:
        resolver = ToneResolver(default_tone="neutral")
        self.assertEqual("neutral", resolver.resolve(intent="policy_process"))

    def test_resolve_prefers_intent_override(self) -> None:
        resolver = ToneResolver(
            default_tone="conversational",
            overrides={"policy_process": "formal"},
        )
        self.assertEqual("formal", resolver.resolve(intent="policy_process"))
        self.assertEqual("conversational", resolver.resolve(intent="other"))

    def test_build_from_env_parses_default_and_overrides(self) -> None:
        resolver = build_tone_resolver_from_env(
            {
                "RESPONSE_TONE_DEFAULT": "neutral",
                "RESPONSE_TONE_BY_INTENT": "policy_process:formal,fixed_quote:neutral",
            }
        )
        self.assertEqual("neutral", resolver.default_tone)
        self.assertEqual("formal", resolver.resolve(intent="policy_process"))
        self.assertEqual("neutral", resolver.resolve(intent="fixed_quote"))

    def test_build_from_env_rejects_invalid_tone(self) -> None:
        with self.assertRaises(ValueError):
            build_tone_resolver_from_env({"RESPONSE_TONE_DEFAULT": "loud"})


if __name__ == "__main__":
    unittest.main()
