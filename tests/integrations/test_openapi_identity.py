from __future__ import annotations

import unittest
from unittest.mock import patch

from app.integrations.dingtalk.openapi_identity import DingTalkOpenAPIIdentityClient


class _FakeResponse:
    def __init__(self, *, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self._payload = payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import requests

            raise requests.HTTPError(f"{self.status_code} error")

    def json(self) -> dict[str, object]:
        return self._payload


class OpenAPIIdentityClientTests(unittest.TestCase):
    @patch("app.integrations.dingtalk.openapi_identity.requests.request")
    def test_fetch_identity_uses_v1_user_and_topapi_department(self, mock_request) -> None:  # type: ignore[no-untyped-def]
        def side_effect(*, method, url, **kwargs):  # type: ignore[no-untyped-def]
            if url.endswith("/v1.0/oauth2/accessToken"):
                return _FakeResponse(
                    status_code=200,
                    payload={"accessToken": "token-a06", "expireIn": 7200},
                )
            if "/v1.0/contact/users/" in url:
                return _FakeResponse(
                    status_code=200,
                    payload={"result": {"userid": "staff-001", "name": "Alice", "dept_id_list": [1]}},
                )
            if url.endswith("/topapi/v2/department/get"):
                return _FakeResponse(
                    status_code=200,
                    payload={"errcode": 0, "result": {"dept_id": 1, "name": "Finance"}},
                )
            raise AssertionError(f"unexpected request: {method} {url}")

        mock_request.side_effect = side_effect
        client = DingTalkOpenAPIIdentityClient(client_id="cid", client_secret="secret")
        record = client.fetch_identity("staff-001")

        self.assertEqual("staff-001", record.user_id)
        self.assertEqual("Alice", record.user_name)
        self.assertEqual("1", record.dept_id)
        self.assertEqual("Finance", record.dept_name)

    @patch("app.integrations.dingtalk.openapi_identity.requests.request")
    def test_fetch_identity_falls_back_to_topapi_user_on_v1_failure(self, mock_request) -> None:  # type: ignore[no-untyped-def]
        def side_effect(*, method, url, **kwargs):  # type: ignore[no-untyped-def]
            if url.endswith("/v1.0/oauth2/accessToken"):
                return _FakeResponse(
                    status_code=200,
                    payload={"accessToken": "token-a06", "expireIn": 7200},
                )
            if "/v1.0/contact/users/" in url:
                return _FakeResponse(
                    status_code=403,
                    payload={"code": "Forbidden.AccessDenied.AccessTokenPermissionDenied"},
                )
            if url.endswith("/topapi/v2/user/get"):
                return _FakeResponse(
                    status_code=200,
                    payload={
                        "errcode": 0,
                        "result": {"userid": "staff-002", "name": "Bob", "dept_id_list": [2]},
                    },
                )
            if url.endswith("/topapi/v2/department/get"):
                return _FakeResponse(
                    status_code=200,
                    payload={"errcode": 0, "result": {"dept_id": 2, "name": "HR"}},
                )
            raise AssertionError(f"unexpected request: {method} {url}")

        mock_request.side_effect = side_effect
        client = DingTalkOpenAPIIdentityClient(client_id="cid", client_secret="secret")
        record = client.fetch_identity("staff-002")

        self.assertEqual("staff-002", record.user_id)
        self.assertEqual("Bob", record.user_name)
        self.assertEqual("2", record.dept_id)
        self.assertEqual("HR", record.dept_name)


if __name__ == "__main__":
    unittest.main()

