from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import logging
import os
from time import time
from typing import Any

import requests

from app.integrations.dingtalk.openapi_identity import LEGACY_DINGTALK_BASE
from app.schemas.reimbursement import TravelApplication
from app.services.reimbursement_request import TravelApplicationProvider

DEFAULT_OPENAPI_ENDPOINT = "https://api.dingtalk.com"

@dataclass(frozen=True)
class TravelApprovalLookupSettings:
    enabled: bool
    process_code: str
    page_size: int
    openapi_endpoint: str
    legacy_openapi_endpoint: str


class DingTalkTravelApplicationProvider(TravelApplicationProvider):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: TravelApprovalLookupSettings,
        timeout_seconds: float = 10.0,
        logger: logging.Logger | None = None,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._settings = settings
        self._timeout_seconds = timeout_seconds
        self._logger = logger or logging.getLogger("keagent.observability")
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def list_recent_approved(
        self,
        *,
        originator_user_id: str,
        lookback_days: int,
        now: datetime,
    ) -> list[TravelApplication]:
        if not self._settings.enabled:
            return []
        start_time = now - timedelta(days=max(1, lookback_days))
        try:
            access_token = self._get_access_token()
            instance_ids = self._list_process_instance_ids(
                access_token=access_token,
                start_time=start_time,
                end_time=now,
            )
        except Exception:
            self._logger.exception("travel.approval.list_ids_failed")
            return []

        results: list[TravelApplication] = []
        for instance_id in instance_ids:
            detail = self._get_process_instance_detail(access_token=access_token, process_instance_id=instance_id)
            if not detail:
                continue
            if not _is_completed_and_approved(detail):
                continue
            if not _is_originator_match(detail, originator_user_id=originator_user_id):
                continue
            parsed = _parse_travel_application(detail=detail)
            if parsed is not None:
                results.append(parsed)

        # Keep latest first and avoid overlong candidate lists.
        results.sort(key=lambda item: item.start_date, reverse=True)
        return results[:10]

    def _list_process_instance_ids(
        self,
        *,
        access_token: str,
        start_time: datetime,
        end_time: datetime,
    ) -> list[str]:
        start_ms = int(start_time.astimezone(timezone.utc).timestamp() * 1000)
        end_ms = int(end_time.astimezone(timezone.utc).timestamp() * 1000)
        payload = {
            "process_code": self._settings.process_code,
            "start_time": start_ms,
            "end_time": end_ms,
            "size": self._settings.page_size,
            "cursor": 0,
        }
        response = requests.post(
            f"{self._settings.legacy_openapi_endpoint}/topapi/processinstance/listids",
            params={"access_token": access_token},
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json=payload,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        body = response.json()
        errcode = int(body.get("errcode", 0) or 0)
        if errcode != 0:
            return []
        result = body.get("result")
        if not isinstance(result, Mapping):
            return []
        ids = result.get("list")
        if not isinstance(ids, list):
            return []
        return [str(item).strip() for item in ids if str(item).strip()]

    def _get_process_instance_detail(self, *, access_token: str, process_instance_id: str) -> Mapping[str, Any] | None:
        payload = {"process_instance_id": process_instance_id}
        try:
            response = requests.post(
                f"{self._settings.legacy_openapi_endpoint}/topapi/processinstance/get",
                params={"access_token": access_token},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except Exception:
            return None
        errcode = int(body.get("errcode", 0) or 0)
        if errcode != 0:
            return None
        detail = body.get("process_instance")
        if isinstance(detail, Mapping):
            return detail
        return None

    def _get_access_token(self) -> str:
        now = time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        response = requests.post(
            f"{self._settings.openapi_endpoint}/v1.0/oauth2/accessToken",
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            json={"appKey": self._client_id, "appSecret": self._client_secret},
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        access_token = str(payload.get("accessToken") or "").strip()
        expire_in = int(payload.get("expireIn") or 7200)
        if not access_token:
            raise RuntimeError("OpenAPI access token is empty")
        self._access_token = access_token
        self._access_token_expires_at = time() + max(expire_in - 300, 60)
        return self._access_token


def _is_completed_and_approved(detail: Mapping[str, Any]) -> bool:
    status = str(detail.get("status") or detail.get("process_status") or "").strip().lower()
    result = str(detail.get("result") or detail.get("process_result") or "").strip().lower()
    completed = status in {"completed", "finish", "finished"}
    approved = result in {"agree", "approved", "pass", "passed"}
    return completed and approved


def _is_originator_match(detail: Mapping[str, Any], *, originator_user_id: str) -> bool:
    if not originator_user_id:
        return False
    current = str(
        detail.get("originator_userid")
        or detail.get("originator_user_id")
        or detail.get("originatorUserid")
        or ""
    ).strip()
    if current:
        return current == originator_user_id
    return True


def _parse_travel_application(*, detail: Mapping[str, Any]) -> TravelApplication | None:
    process_instance_id = str(detail.get("process_instance_id") or detail.get("processInstanceId") or "").strip()
    if not process_instance_id:
        return None
    form_values = detail.get("form_component_values")
    destination = ""
    purpose = ""
    start_date = ""
    if isinstance(form_values, list):
        for item in form_values:
            if not isinstance(item, Mapping):
                continue
            name = str(item.get("name") or "").strip()
            value = str(item.get("value") or "").strip()
            if not destination and any(token in name for token in ("地点", "目的地", "城市")):
                destination = value
            if not purpose and any(token in name for token in ("事由", "目的")):
                purpose = value
            if not start_date and any(token in name for token in ("开始", "出发", "日期")):
                start_date = _normalize_date_text(value)

    if not destination:
        destination = str(detail.get("title") or "出差").strip() or "出差"
    if not start_date:
        created_time = str(detail.get("create_time") or detail.get("created_time") or "").strip()
        start_date = _normalize_date_text(created_time) or datetime.now().strftime("%Y-%m-%d")

    return TravelApplication(
        process_instance_id=process_instance_id,
        start_date=start_date,
        destination=destination,
        purpose=purpose,
    )


def _normalize_date_text(raw: str) -> str:
    text = (raw or "").strip()
    if not text:
        return ""
    if len(text) >= 10 and text[4] in {"-", "/"}:
        return text[:10].replace("/", "-")
    if text.isdigit():
        try:
            timestamp_ms = int(text)
            if timestamp_ms > 10_000_000_000:
                dt = datetime.fromtimestamp(timestamp_ms / 1000, tz=timezone.utc)
            else:
                dt = datetime.fromtimestamp(timestamp_ms, tz=timezone.utc)
            return dt.strftime("%Y-%m-%d")
        except Exception:
            return ""
    return ""


def load_travel_approval_lookup_settings(raw_env: Mapping[str, str] | None = None) -> TravelApprovalLookupSettings:
    env = raw_env if raw_env is not None else os.environ
    enabled = str(env.get("DINGTALK_REIMBURSE_TRAVEL_LOOKUP_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    legacy_openapi_endpoint = str(env.get("DINGTALK_LEGACY_OPENAPI_ENDPOINT") or LEGACY_DINGTALK_BASE).strip()
    page_size_raw = str(env.get("DINGTALK_REIMBURSE_TRAVEL_LOOKUP_PAGE_SIZE") or "20").strip()
    try:
        page_size = max(1, int(page_size_raw))
    except ValueError:
        page_size = 20
    return TravelApprovalLookupSettings(
        enabled=enabled,
        process_code=str(env.get("DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE") or "").strip(),
        page_size=page_size,
        openapi_endpoint=openapi_endpoint.rstrip("/") or DEFAULT_OPENAPI_ENDPOINT,
        legacy_openapi_endpoint=legacy_openapi_endpoint.rstrip("/") or LEGACY_DINGTALK_BASE,
    )


def build_default_travel_application_provider(
    raw_env: Mapping[str, str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> TravelApplicationProvider:
    env = raw_env if raw_env is not None else os.environ
    settings = load_travel_approval_lookup_settings(env)
    if not settings.enabled:
        return _DisabledTravelApplicationProvider()

    client_id = str(env.get("DINGTALK_CLIENT_ID") or "").strip()
    client_secret = str(env.get("DINGTALK_CLIENT_SECRET") or "").strip()
    if not client_id or not client_secret or not settings.process_code:
        obs_logger = logger or logging.getLogger("keagent.observability")
        obs_logger.warning(
            "travel approval provider disabled: missing DINGTALK_CLIENT_ID / DINGTALK_CLIENT_SECRET / DINGTALK_TRAVEL_APPROVAL_PROCESS_CODE"
        )
        return _DisabledTravelApplicationProvider()

    return DingTalkTravelApplicationProvider(
        client_id=client_id,
        client_secret=client_secret,
        settings=settings,
        logger=logger,
    )


class _DisabledTravelApplicationProvider(TravelApplicationProvider):
    def list_recent_approved(
        self,
        *,
        originator_user_id: str,
        lookback_days: int,
        now: datetime,
    ) -> list[TravelApplication]:
        del originator_user_id, lookback_days, now
        return []
