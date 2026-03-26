from __future__ import annotations

import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Callable, Protocol

from app.integrations.dingtalk.openapi_identity import DingTalkOpenAPIIdentityClient, IdentityRecord
from app.schemas.dingtalk_chat import IncomingChatMessage
from app.schemas.user_context import UserContext

FRESH_TTL_SECONDS = 5 * 60
STALE_TTL_SECONDS = 30 * 60


class IdentityClient(Protocol):
    def fetch_identity(self, user_id: str) -> IdentityRecord: ...


@dataclass(frozen=True)
class _CacheEntry:
    record: IdentityRecord
    cached_at_epoch: float


class _InMemoryIdentityCache:
    def __init__(self) -> None:
        self._items: dict[str, _CacheEntry] = {}

    def get_fresh(self, key: str, *, now_epoch: float, fresh_ttl: int) -> IdentityRecord | None:
        entry = self._items.get(key)
        if not entry:
            return None
        if now_epoch - entry.cached_at_epoch <= fresh_ttl:
            return entry.record
        return None

    def get_stale(self, key: str, *, now_epoch: float, stale_ttl: int) -> IdentityRecord | None:
        entry = self._items.get(key)
        if not entry:
            return None
        age_seconds = now_epoch - entry.cached_at_epoch
        if age_seconds <= stale_ttl:
            return entry.record
        self._items.pop(key, None)
        return None

    def set(self, key: str, record: IdentityRecord, *, now_epoch: float) -> None:
        self._items[key] = _CacheEntry(record=record, cached_at_epoch=now_epoch)


class UserContextResolver:
    """Resolve user context via OpenAPI first, then cache/fallback."""

    def __init__(
        self,
        *,
        identity_client: IdentityClient | None,
        fresh_ttl_seconds: int = FRESH_TTL_SECONDS,
        stale_ttl_seconds: int = STALE_TTL_SECONDS,
        clock: Callable[[], float] | None = None,
    ) -> None:
        self._identity_client = identity_client
        self._fresh_ttl_seconds = fresh_ttl_seconds
        self._stale_ttl_seconds = stale_ttl_seconds
        self._clock = clock or time.time
        self._cache = _InMemoryIdentityCache()

    def resolve(self, message: IncomingChatMessage) -> UserContext:
        lookup_user_id = self._normalize_id(message.sender_staff_id or message.sender_id)
        now_epoch = self._clock()

        fresh = self._cache.get_fresh(
            lookup_user_id,
            now_epoch=now_epoch,
            fresh_ttl=self._fresh_ttl_seconds,
        )
        if fresh is not None:
            return self._build_context(
                record=fresh,
                identity_source="cache_fresh",
                is_degraded=False,
            )

        if self._identity_client is not None:
            try:
                record = self._identity_client.fetch_identity(lookup_user_id)
                self._cache.set(lookup_user_id, record, now_epoch=now_epoch)
                return self._build_context(
                    record=record,
                    identity_source="openapi",
                    is_degraded=False,
                )
            except Exception:
                stale = self._cache.get_stale(
                    lookup_user_id,
                    now_epoch=now_epoch,
                    stale_ttl=self._stale_ttl_seconds,
                )
                if stale is not None:
                    return self._build_context(
                        record=stale,
                        identity_source="cache_stale",
                        is_degraded=True,
                    )

        return self._build_event_fallback(message)

    @staticmethod
    def _normalize_id(raw_id: str | None) -> str:
        value = (raw_id or "").strip()
        return value or "unknown"

    @staticmethod
    def _normalize_name(raw_name: str | None) -> str:
        value = (raw_name or "").strip()
        return value or "unknown"

    def _build_context(self, *, record: IdentityRecord, identity_source: str, is_degraded: bool) -> UserContext:
        return UserContext(
            user_id=self._normalize_id(record.user_id),
            user_name=self._normalize_name(record.user_name),
            dept_id=self._normalize_id(record.dept_id),
            dept_name=self._normalize_name(record.dept_name),
            identity_source=identity_source,
            is_degraded=is_degraded,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )

    def _build_event_fallback(self, message: IncomingChatMessage) -> UserContext:
        return UserContext(
            user_id=self._normalize_id(message.sender_staff_id or message.sender_id),
            user_name=self._normalize_name(message.sender_nick),
            dept_id="unknown",
            dept_name="unknown",
            identity_source="event_fallback",
            is_degraded=True,
            resolved_at=datetime.now(timezone.utc).isoformat(),
        )


def build_default_user_context_resolver() -> UserContextResolver:
    client_id = (os.getenv("DINGTALK_CLIENT_ID") or "").strip()
    client_secret = (os.getenv("DINGTALK_CLIENT_SECRET") or "").strip()
    unusable_values = {"", "replace-me"}

    if client_id not in unusable_values and client_secret not in unusable_values:
        identity_client: IdentityClient | None = DingTalkOpenAPIIdentityClient(
            client_id=client_id,
            client_secret=client_secret,
        )
    else:
        identity_client = None

    return UserContextResolver(identity_client=identity_client)
