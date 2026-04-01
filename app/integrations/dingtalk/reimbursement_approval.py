from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import json
import logging
import os
from time import time
from typing import Any

import requests

from app.integrations.dingtalk.openapi_identity import LEGACY_DINGTALK_BASE
from app.schemas.reimbursement import ReimbursementApprovalResult, ReimbursementApprovalSubmission
from app.services.reimbursement_request import ReimbursementApprovalCreator

DEFAULT_OPENAPI_ENDPOINT = "https://api.dingtalk.com"
_DEFAULT_FIELD_TRAVEL_INSTANCE = "关联出差申请"
_DEFAULT_FIELD_COMPANY = "公司"
_DEFAULT_FIELD_DEPARTMENT = "部门"
_DEFAULT_FIELD_COST_COMPANY = "费用归属公司"
_DEFAULT_FIELD_DATE = "日期"
_DEFAULT_FIELD_AMOUNT = "金额(元)"
_DEFAULT_FIELD_OVER_5000 = "是否超过5千"
_DEFAULT_ATTACHMENT_FIELD = "附件"


@dataclass(frozen=True)
class ReimbursementApprovalSettings:
    enabled: bool
    process_code: str
    field_travel_instance: str
    field_company: str
    field_department: str
    field_cost_company: str
    field_date: str
    field_amount: str
    field_over_5000: str
    field_attachment: str
    openapi_endpoint: str
    legacy_openapi_endpoint: str


class DingTalkReimbursementApprovalCreator(ReimbursementApprovalCreator):
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        settings: ReimbursementApprovalSettings,
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

    def submit(self, submission: ReimbursementApprovalSubmission) -> ReimbursementApprovalResult:
        originator_user_id = submission.originator_user_id.strip()
        if not originator_user_id or originator_user_id == "unknown":
            return ReimbursementApprovalResult(success=False, reason="missing_originator_user_id")
        if not submission.travel_process_instance_id.strip():
            return ReimbursementApprovalResult(success=False, reason="missing_travel_process_instance_id")
        if not submission.department.strip():
            return ReimbursementApprovalResult(success=False, reason="missing_department")
        if not submission.cost_company.strip():
            return ReimbursementApprovalResult(success=False, reason="missing_cost_company")
        if not submission.amount.strip():
            return ReimbursementApprovalResult(success=False, reason="missing_amount")
        if not submission.attachment_media_id.strip():
            return ReimbursementApprovalResult(success=False, reason="missing_attachment_media_id")

        payload: dict[str, Any] = {
            "process_code": self._settings.process_code,
            "originator_user_id": originator_user_id,
            "form_component_values": self._build_form_component_values(submission=submission),
        }
        if not payload["form_component_values"]:
            return ReimbursementApprovalResult(success=False, reason="missing_form_component_values")

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
                "reimbursement.approval.transport_error",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.reimbursement_approval",
                        "event": "reimbursement_approval_transport_error",
                        "originator_user_id": originator_user_id,
                    }
                },
            )
            return ReimbursementApprovalResult(success=False, reason="transport_error")
        except ValueError:
            self._logger.exception(
                "reimbursement.approval.invalid_json",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.reimbursement_approval",
                        "event": "reimbursement_approval_invalid_json",
                        "originator_user_id": originator_user_id,
                    }
                },
            )
            return ReimbursementApprovalResult(success=False, reason="invalid_json")

        errcode = int(body.get("errcode", 0) or 0)
        if errcode != 0:
            self._logger.warning(
                "reimbursement.approval.api_error",
                extra={
                    "obs": {
                        "module": "integrations.dingtalk.reimbursement_approval",
                        "event": "reimbursement_approval_api_error",
                        "originator_user_id": originator_user_id,
                        "errcode": errcode,
                        "errmsg": str(body.get("errmsg") or ""),
                    }
                },
            )
            return ReimbursementApprovalResult(success=False, reason="api_error")

        process_instance_id = str(body.get("process_instance_id") or body.get("processInstanceId") or "").strip()
        if not process_instance_id:
            return ReimbursementApprovalResult(success=False, reason="missing_process_instance_id")
        return ReimbursementApprovalResult(success=True, reason="submitted", process_instance_id=process_instance_id)

    def _build_form_component_values(self, *, submission: ReimbursementApprovalSubmission) -> list[dict[str, str]]:
        attachment_value = json.dumps([{"mediaId": submission.attachment_media_id}], ensure_ascii=False)
        fields: list[tuple[str, str]] = [
            (self._settings.field_travel_instance, submission.travel_process_instance_id),
            (self._settings.field_company, submission.fixed_company),
            (self._settings.field_department, submission.department),
            (self._settings.field_cost_company, submission.cost_company),
            (self._settings.field_date, submission.date),
            (self._settings.field_amount, submission.amount),
            (self._settings.field_over_5000, submission.over_five_thousand),
            (self._settings.field_attachment, attachment_value),
        ]
        values: list[dict[str, str]] = []
        for name, value in fields:
            normalized_name = name.strip()
            normalized_value = value.strip()
            if not normalized_name or not normalized_value:
                continue
            values.append({"name": normalized_name, "value": normalized_value})
        return values

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


def load_reimbursement_approval_settings(raw_env: Mapping[str, str] | None = None) -> ReimbursementApprovalSettings:
    env = raw_env if raw_env is not None else os.environ
    openapi_endpoint = str(env.get("DINGTALK_OPENAPI_ENDPOINT") or DEFAULT_OPENAPI_ENDPOINT).strip()
    legacy_openapi_endpoint = str(env.get("DINGTALK_LEGACY_OPENAPI_ENDPOINT") or LEGACY_DINGTALK_BASE).strip()
    enabled = str(env.get("DINGTALK_REIMBURSE_APPROVAL_ENABLED") or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "y",
        "on",
    }
    return ReimbursementApprovalSettings(
        enabled=enabled,
        process_code=str(env.get("DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE") or "").strip(),
        field_travel_instance=str(env.get("DINGTALK_REIMBURSE_FIELD_TRAVEL_INSTANCE") or _DEFAULT_FIELD_TRAVEL_INSTANCE).strip()
        or _DEFAULT_FIELD_TRAVEL_INSTANCE,
        field_company=str(env.get("DINGTALK_REIMBURSE_FIELD_COMPANY") or _DEFAULT_FIELD_COMPANY).strip()
        or _DEFAULT_FIELD_COMPANY,
        field_department=str(env.get("DINGTALK_REIMBURSE_FIELD_DEPARTMENT") or _DEFAULT_FIELD_DEPARTMENT).strip()
        or _DEFAULT_FIELD_DEPARTMENT,
        field_cost_company=str(env.get("DINGTALK_REIMBURSE_FIELD_COST_COMPANY") or _DEFAULT_FIELD_COST_COMPANY).strip()
        or _DEFAULT_FIELD_COST_COMPANY,
        field_date=str(env.get("DINGTALK_REIMBURSE_FIELD_DATE") or _DEFAULT_FIELD_DATE).strip() or _DEFAULT_FIELD_DATE,
        field_amount=str(env.get("DINGTALK_REIMBURSE_FIELD_AMOUNT") or _DEFAULT_FIELD_AMOUNT).strip() or _DEFAULT_FIELD_AMOUNT,
        field_over_5000=str(env.get("DINGTALK_REIMBURSE_FIELD_OVER_5000") or _DEFAULT_FIELD_OVER_5000).strip()
        or _DEFAULT_FIELD_OVER_5000,
        field_attachment=str(env.get("DINGTALK_REIMBURSE_ATTACHMENT_FIELD") or _DEFAULT_ATTACHMENT_FIELD).strip()
        or _DEFAULT_ATTACHMENT_FIELD,
        openapi_endpoint=openapi_endpoint.rstrip("/") or DEFAULT_OPENAPI_ENDPOINT,
        legacy_openapi_endpoint=legacy_openapi_endpoint.rstrip("/") or LEGACY_DINGTALK_BASE,
    )


def build_default_reimbursement_approval_creator(
    raw_env: Mapping[str, str] | None = None,
    *,
    logger: logging.Logger | None = None,
) -> ReimbursementApprovalCreator | None:
    env = raw_env if raw_env is not None else os.environ
    settings = load_reimbursement_approval_settings(env)
    if not settings.enabled:
        return None

    obs_logger = logger or logging.getLogger("keagent.observability")
    missing = [
        key
        for key, value in (
            ("DINGTALK_REIMBURSE_APPROVAL_PROCESS_CODE", settings.process_code),
            ("DINGTALK_CLIENT_ID", str(env.get("DINGTALK_CLIENT_ID") or "").strip()),
            ("DINGTALK_CLIENT_SECRET", str(env.get("DINGTALK_CLIENT_SECRET") or "").strip()),
        )
        if not value
    ]
    if missing:
        obs_logger.warning(
            "reimbursement approval creator disabled: incomplete config (%s)",
            ", ".join(missing),
        )
        return None

    return DingTalkReimbursementApprovalCreator(
        client_id=str(env.get("DINGTALK_CLIENT_ID") or ""),
        client_secret=str(env.get("DINGTALK_CLIENT_SECRET") or ""),
        settings=settings,
        logger=obs_logger,
    )
