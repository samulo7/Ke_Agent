from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import requests

DINGTALK_OPENAPI_BASE = "https://api.dingtalk.com"
LEGACY_DINGTALK_BASE = "https://oapi.dingtalk.com"


@dataclass(frozen=True)
class IdentityRecord:
    user_id: str
    user_name: str
    dept_id: str
    dept_name: str


class DingTalkOpenAPIIdentityClient:
    """Resolve DingTalk user identity through OpenAPI endpoints."""

    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        base_url: str = DINGTALK_OPENAPI_BASE,
        legacy_base_url: str = LEGACY_DINGTALK_BASE,
        timeout_seconds: float = 10.0,
    ) -> None:
        self._client_id = client_id.strip()
        self._client_secret = client_secret.strip()
        self._base_url = base_url.rstrip("/")
        self._legacy_base_url = legacy_base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._access_token = ""
        self._access_token_expires_at = 0.0

    def fetch_identity(self, user_id: str) -> IdentityRecord:
        normalized_user_id = user_id.strip()
        if not normalized_user_id:
            raise ValueError("user_id is required for OpenAPI lookup")

        access_token = self._get_access_token()
        try:
            user_payload = self._request_json(
                "GET",
                f"/v1.0/contact/users/{quote(normalized_user_id)}",
                access_token=access_token,
            )
            user_data = self._unwrap_payload(user_payload)
        except Exception:
            user_data = self._request_topapi_json(
                "/topapi/v2/user/get",
                access_token=access_token,
                json_body={"userid": normalized_user_id},
            )

        resolved_user_id = self._pick_first(user_data, "userid", "userId", "unionid", "unionId") or normalized_user_id
        resolved_user_name = self._pick_first(user_data, "name", "nick", "displayName") or "unknown"
        resolved_dept_id = self._extract_dept_id(user_data) or "unknown"
        resolved_dept_name = "unknown"

        if resolved_dept_id != "unknown":
            try:
                dept_data = self._request_topapi_json(
                    "/topapi/v2/department/get",
                    access_token=access_token,
                    json_body={"dept_id": self._normalize_dept_id(resolved_dept_id)},
                )
                resolved_dept_name = self._pick_first(dept_data, "name", "deptName", "title") or "unknown"
            except Exception:
                resolved_dept_name = "unknown"

        return IdentityRecord(
            user_id=resolved_user_id,
            user_name=resolved_user_name,
            dept_id=resolved_dept_id,
            dept_name=resolved_dept_name,
        )

    def _get_access_token(self) -> str:
        now = time.time()
        if self._access_token and now < self._access_token_expires_at:
            return self._access_token

        payload = self._request_json(
            "POST",
            "/v1.0/oauth2/accessToken",
            json_body={"appKey": self._client_id, "appSecret": self._client_secret},
            access_token=None,
        )
        data = self._unwrap_payload(payload)
        access_token = str(data.get("accessToken") or payload.get("accessToken") or "").strip()
        expire_in = int(data.get("expireIn") or payload.get("expireIn") or 7200)
        if not access_token:
            raise RuntimeError("OpenAPI access token is empty")

        self._access_token = access_token
        self._access_token_expires_at = time.time() + max(expire_in - 300, 60)
        return self._access_token

    def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        access_token: str | None,
    ) -> dict[str, Any]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
        }
        if access_token:
            headers["x-acs-dingtalk-access-token"] = access_token

        url = f"{self._base_url}{path}"
        response = requests.request(
            method=method.upper(),
            url=url,
            headers=headers,
            json=json_body,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        return response.json()

    def _request_topapi_json(
        self,
        path: str,
        *,
        access_token: str,
        json_body: dict[str, Any],
    ) -> dict[str, Any]:
        url = f"{self._legacy_base_url}{path}"
        response = requests.request(
            method="POST",
            url=url,
            params={"access_token": access_token},
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
            },
            json=json_body,
            timeout=self._timeout_seconds,
        )
        response.raise_for_status()
        payload = response.json()
        errcode = int(payload.get("errcode", 0))
        if errcode != 0:
            raise RuntimeError(payload.get("errmsg", f"topapi error: errcode={errcode}"))
        result = payload.get("result")
        if isinstance(result, dict):
            return result
        return payload

    @staticmethod
    def _unwrap_payload(payload: dict[str, Any]) -> dict[str, Any]:
        result = payload.get("result")
        if isinstance(result, dict):
            return result

        data = payload.get("data")
        if isinstance(data, dict):
            return data

        return payload

    @staticmethod
    def _pick_first(payload: dict[str, Any], *keys: str) -> str:
        for key in keys:
            value = payload.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return ""

    @staticmethod
    def _extract_dept_id(payload: dict[str, Any]) -> str:
        candidates = (
            payload.get("deptId"),
            payload.get("dept_id"),
            payload.get("departmentId"),
            payload.get("deptIds"),
            payload.get("dept_id_list"),
        )
        for value in candidates:
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int):
                return str(value)
            if isinstance(value, list) and value:
                first = value[0]
                if isinstance(first, str) and first.strip():
                    return first.strip()
                if isinstance(first, int):
                    return str(first)
        return ""

    @staticmethod
    def _normalize_dept_id(value: str) -> int | str:
        normalized = value.strip()
        if normalized.isdigit():
            return int(normalized)
        return normalized
