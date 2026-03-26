from __future__ import annotations

import unittest

from app.integrations.dingtalk.openapi_identity import IdentityRecord
from app.schemas.dingtalk_chat import IncomingChatMessage
from app.services.user_context import UserContextResolver


class _Clock:
    def __init__(self) -> None:
        self._now = 0.0

    def time(self) -> float:
        return self._now

    def advance(self, seconds: float) -> None:
        self._now += seconds


class _FakeIdentityClient:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False
        self.records: dict[str, IdentityRecord] = {}

    def fetch_identity(self, user_id: str) -> IdentityRecord:
        self.calls += 1
        if self.fail:
            raise RuntimeError("openapi unavailable")
        return self.records[user_id]


def _make_message(
    *,
    sender_id: str = "user-001",
    sender_staff_id: str = "staff-001",
    sender_nick: str = "Alice",
) -> IncomingChatMessage:
    return IncomingChatMessage(
        event_id="evt-a06-001",
        conversation_id="conv-a06-001",
        conversation_type="single",
        sender_id=sender_id,
        message_type="text",
        text="你好",
        sender_staff_id=sender_staff_id,
        sender_nick=sender_nick,
    )


class UserContextResolverTests(unittest.TestCase):
    def test_openapi_success_returns_full_identity(self) -> None:
        clock = _Clock()
        client = _FakeIdentityClient()
        client.records["staff-001"] = IdentityRecord(
            user_id="staff-001",
            user_name="Alice",
            dept_id="dept-hr",
            dept_name="HR",
        )
        resolver = UserContextResolver(identity_client=client, clock=clock.time)

        context = resolver.resolve(_make_message())
        self.assertEqual("staff-001", context.user_id)
        self.assertEqual("Alice", context.user_name)
        self.assertEqual("dept-hr", context.dept_id)
        self.assertEqual("HR", context.dept_name)
        self.assertEqual("openapi", context.identity_source)
        self.assertFalse(context.is_degraded)

    def test_fresh_cache_avoids_repeated_openapi_calls(self) -> None:
        clock = _Clock()
        client = _FakeIdentityClient()
        client.records["staff-001"] = IdentityRecord(
            user_id="staff-001",
            user_name="Alice",
            dept_id="dept-finance",
            dept_name="Finance",
        )
        resolver = UserContextResolver(identity_client=client, clock=clock.time)

        first = resolver.resolve(_make_message())
        second = resolver.resolve(_make_message())
        self.assertEqual("openapi", first.identity_source)
        self.assertEqual("cache_fresh", second.identity_source)
        self.assertEqual(1, client.calls)

    def test_openapi_failure_uses_stale_cache_within_30_minutes(self) -> None:
        clock = _Clock()
        client = _FakeIdentityClient()
        client.records["staff-001"] = IdentityRecord(
            user_id="staff-001",
            user_name="Alice",
            dept_id="dept-finance",
            dept_name="Finance",
        )
        resolver = UserContextResolver(identity_client=client, clock=clock.time)

        resolver.resolve(_make_message())
        clock.advance(301)
        client.fail = True

        context = resolver.resolve(_make_message())
        self.assertEqual("cache_stale", context.identity_source)
        self.assertTrue(context.is_degraded)
        self.assertEqual(2, client.calls)

    def test_openapi_failure_without_cache_falls_back_to_event_fields(self) -> None:
        clock = _Clock()
        client = _FakeIdentityClient()
        client.fail = True
        resolver = UserContextResolver(identity_client=client, clock=clock.time)

        context = resolver.resolve(_make_message(sender_staff_id="staff-fallback", sender_nick="Bob"))
        self.assertEqual("event_fallback", context.identity_source)
        self.assertTrue(context.is_degraded)
        self.assertEqual("staff-fallback", context.user_id)
        self.assertEqual("Bob", context.user_name)
        self.assertEqual("unknown", context.dept_id)
        self.assertEqual("unknown", context.dept_name)


if __name__ == "__main__":
    unittest.main()

