from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from time import time
from typing import Any

import requests

from app.integrations.dingtalk.openapi_identity import LEGACY_DINGTALK_BASE
from app.integrations.dingtalk.stream_runtime import DEFAULT_OPENAPI_ENDPOINT
from app.services.leave_request import LeaveApprovalCreator, LeaveApprovalResult, LeaveApprovalSubmission

_DEFAULT_LEAVE_REASON_FALLBACK = "未填写"


@dataclass(frozen=True)
class LeaveApprovalSettings:
    enabled: bool
    process_code: str
    leave_type_field: str
    leave_start_time_field: str
    leave_end_time_field: str
    leave_reason_field: str
    applicant_field: str
    department_field: str
    openapi_endpoint: str
    legacy_openapi_endpoint: str


class DingTalkLeaveApprovalCreator(LeaveApprovalCreator):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: LeaveApprovalSettings,
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

    def submit(self, submission: LeaveApprovalSubmission) -> LeaveApprovalResult:
        originator_user_id = submission.originator_user_id.strip()
        if not originator_user_id or originator_user_id == "unknown":
            return LeaveApprovalResult(success=False, reason="missing_originator_user_id")

        form_component_values = self._build_form_component_values(submission)
        if not form_component_values:
            return LeaveApprovalResult(success=False, reason="missing_form_component_values")

        payload: dict[str, Any] = {
            "process_code": self._settings.process_code,
            "originator_user_id": originator_user_id,
            "form_component_values": form_component_values,
        }
        dept_id = self._normalize_dept_id(submission.department_id)
        if dept_id is None:
            return LeaveApprovalResult(success=False, reason="missing_dept_id")
        payload["dept_id"] = dept_id

        try:
            access_token = self._get_access_token()
            response = requests.post(
                f"{self._settings.legacy_openapi_endpoint}/topapi/processinstance/create",
                params={"access_token": access_token},
                headers={"Content-Type": "application/json", "Accept": "application/json"},
                json=payload,
                timeout=self._timeout_seconds,
            )
            response.raise_for_status()
            body = response.json()
        except requests.RequestException:
            self._logger.exception(
                "leave.approval.transport_error",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.leave_approval",
                        "event": "leave_approval_transport_error",
                        "originator_user_id": originator_user_id,
                        "process_code": self._settings.process_code,
                    }
                },
            )
            return LeaveApprovalResult(success=False, reason="transport_error")
        except ValueError:
            self._logger.exception(
                "leave.approval.invalid_json",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.leave_approval",
                        "event": "leave_approval_invalid_json",
                        "originator_user_id": originator_user_id,
                        "process_code": self._settings.process_code,
                    }
                },
            )
            return LeaveApprovalResult(success=False, reason="invalid_json")

        errcode = int(body.get("errcode", 0) or 0)
        if errcode != 0:
            self._logger.warning(
                "leave.approval.api_error",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.leave_approval",
                        "event": "leave_approval_api_error",
                        "originator_user_id": originator_user_id,
                        "process_code": self._settings.process_code,
                        "errcode": errcode,
                        "errmsg": str(body.get("errmsg") or ""),
                    }
                },
            )
            return LeaveApprovalResult(success=False, reason="api_error")

        process_instance_id = str(body.get("process_instance_id") or body.get("processInstanceId") or "").strip()
        if not process_instance_id:
            return LeaveApprovalResult(success=False, reason="missing_process_instance_id")
        return LeaveApprovalResult(
            success=True,
            reason="submitted",
            process_instance_id=process_instance_id,
        )

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

    def _build_form_component_values(self, submission: LeaveApprovalSubmission) -> list[dict[str, str]]:
        fields: list[tuple[str, str]] = [
            (self._settings.leave_type_field, submission.leave_type),
            (self._settings.leave_start_time_field, submission.leave_start_time),
            (self._settings.leave_end_time_field, submission.leave_end_time),
            (self._settings.leave_reason_field, submission.leave_reason),
            (self._settings.applicant_field, submission.applicant_name),
            (self._settings.department_field, submission.department),
        ]
        values: list[dict[str, str]] = []
        for name, value in fields:
            normalized_name = name.strip()
            normalized_value = value.strip()
            # Keep reason optional in dialogue while improving DingTalk form compatibility
            # for templates that mark reason as required.
            if normalized_name == self._settings.leave_reason_field and not normalized_value:
                normalized_value = _DEFAULT_LEAVE_REASON_FALLBACK
            if not normalized_name or not normalized_value or normalized_value == "unknown":
                continue
            values.append({"name": normalized_name, "value": normalized_value})
        return values

    @staticmethod
    def _normalize_dept_id(value: str) -> int | None:
        normalized = value.strip()
        if not normalized or normalized == "unknown" or not normalized.isdigit():
            return None
        return int(normalized)


def load_leave_approval_settings(raw_env: Mapping[str, str] | None = None) -> LeaveApprovalSettings:
    env = raw_env if raw_env is not None else os.environ
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    legacy_openapi_endpoint = str(env.get("DINGTALK_LEGACY_OPENAPI_ENDPOINT") or LEGACY_DINGTALK_BASE).strip()
    return LeaveApprovalSettings(
        enabled=str(env.get("DINGTALK_LEAVE_APPROVAL_ENABLED") or "").strip().lower() in {"1", "true", "yes", "y", "on"},
        process_code=str(env.get("DINGTALK_LEAVE_APPROVAL_PROCESS_CODE") or "").strip(),
        leave_type_field=str(env.get("DINGTALK_LEAVE_APPROVAL_TYPE_FIELD") or "").strip(),
        leave_start_time_field=str(env.get("DINGTALK_LEAVE_APPROVAL_START_TIME_FIELD") or "").strip(),
        leave_end_time_field=str(env.get("DINGTALK_LEAVE_APPROVAL_END_TIME_FIELD") or "").strip(),
        leave_reason_field=str(env.get("DINGTALK_LEAVE_APPROVAL_REASON_FIELD") or "").strip(),
        applicant_field=str(env.get("DINGTALK_LEAVE_APPROVAL_APPLICANT_FIELD") or "").strip(),
        department_field=str(env.get("DINGTALK_LEAVE_APPROVAL_DEPARTMENT_FIELD") or "").strip(),
        openapi_endpoint=openapi_endpoint.rstrip("/") or DEFAULT_OPENAPI_ENDPOINT,
        legacy_openapi_endpoint=legacy_openapi_endpoint.rstrip("/") or LEGACY_DINGTALK_BASE,
    )


def build_default_leave_approval_creator(
    raw_env: Mapping[str, str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> LeaveApprovalCreator | None:
    env = raw_env if raw_env is not None else os.environ
    settings = load_leave_approval_settings(env)
    if not settings.enabled:
        return None

    obs_logger = logger or logging.getLogger("keagent.observability")
    missing = [
        key
        for key, value in (
            ("DINGTALK_LEAVE_APPROVAL_PROCESS_CODE", settings.process_code),
            ("DINGTALK_LEAVE_APPROVAL_TYPE_FIELD", settings.leave_type_field),
            ("DINGTALK_LEAVE_APPROVAL_START_TIME_FIELD", settings.leave_start_time_field),
            ("DINGTALK_LEAVE_APPROVAL_END_TIME_FIELD", settings.leave_end_time_field),
            ("DINGTALK_CLIENT_ID", str(env.get("DINGTALK_CLIENT_ID") or "").strip()),
            ("DINGTALK_CLIENT_SECRET", str(env.get("DINGTALK_CLIENT_SECRET") or "").strip()),
        )
        if not value
    ]
    if missing:
        obs_logger.warning(
            "leave approval creator disabled: incomplete config (%s)",
            ", ".join(missing),
        )
        return None

    return DingTalkLeaveApprovalCreator(
        client_id=str(env.get("DINGTALK_CLIENT_ID") or ""),
        client_secret=str(env.get("DINGTALK_CLIENT_SECRET") or ""),
        settings=settings,
        logger=obs_logger,
    )
