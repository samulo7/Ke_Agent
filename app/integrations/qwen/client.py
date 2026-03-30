from __future__ import annotations

import json
import os
from typing import Any, Protocol
from urllib import error, request

DEFAULT_QWEN_CHAT_ENDPOINT = "https://dashscope.aliyuncs.com/compatible-mode/v1/chat/completions"


class QwenClientError(RuntimeError):
    """Raised when Qwen API invocation fails after retries."""


class QwenChatClient(Protocol):
    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict[str, Any]: ...


class HttpQwenChatClient:
    """OpenAI-compatible Qwen chat client with bounded retries."""

    def __init__(
        self,
        *,
        api_key: str,
        endpoint: str = DEFAULT_QWEN_CHAT_ENDPOINT,
        user_agent: str = "keagent/llm-runtime",
    ) -> None:
        self._api_key = api_key.strip()
        normalized_endpoint = endpoint.strip() or DEFAULT_QWEN_CHAT_ENDPOINT
        self._endpoint = self._normalize_endpoint(normalized_endpoint)
        self._user_agent = user_agent
        self._max_tokens = self._read_max_tokens_from_env()

    def generate_json(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
        max_retries: int,
    ) -> dict[str, Any]:
        if not self._api_key:
            raise QwenClientError("LLM_API_KEY is empty")

        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._send_request(
                    model=model,
                    system_prompt=system_prompt,
                    user_prompt=user_prompt,
                    timeout_seconds=timeout_seconds,
                )
                return self._extract_json_payload(response)
            except Exception as exc:
                if attempt > max_retries + 1:
                    raise QwenClientError(str(exc)) from exc

    def _send_request(
        self,
        *,
        model: str,
        system_prompt: str,
        user_prompt: str,
        timeout_seconds: int,
    ) -> dict[str, Any]:
        payload = {
            "model": model,
            "temperature": 0,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        }
        if self._max_tokens is not None:
            payload["max_tokens"] = self._max_tokens
        body = json.dumps(payload).encode("utf-8")
        req = request.Request(
            self._endpoint,
            data=body,
            method="POST",
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
                "User-Agent": self._user_agent,
            },
        )
        try:
            with request.urlopen(req, timeout=timeout_seconds) as resp:
                raw = resp.read().decode("utf-8")
        except error.HTTPError as exc:
            detail = ""
            try:
                detail = exc.read().decode("utf-8")
            except Exception:
                detail = ""
            raise QwenClientError(f"http_error status={exc.code} detail={detail}") from exc
        except error.URLError as exc:
            raise QwenClientError(f"url_error reason={exc.reason}") from exc
        except TimeoutError as exc:
            raise QwenClientError("timeout") from exc

        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise QwenClientError("invalid_json_response") from exc

    @staticmethod
    def _extract_json_payload(response: dict[str, Any]) -> dict[str, Any]:
        choices = response.get("choices")
        if not isinstance(choices, list) or not choices:
            raise QwenClientError("response_missing_choices")
        message = choices[0].get("message")
        if not isinstance(message, dict):
            raise QwenClientError("response_missing_message")
        content = message.get("content")
        if isinstance(content, list):
            fragments: list[str] = []
            for item in content:
                if isinstance(item, dict):
                    text = item.get("text")
                    if isinstance(text, str):
                        fragments.append(text)
                elif isinstance(item, str):
                    fragments.append(item)
            content = "".join(fragments)
        if isinstance(content, dict):
            return content
        if not isinstance(content, str):
            raise QwenClientError("response_missing_content")
        cleaned = content.strip()
        if cleaned.startswith("```"):
            cleaned = cleaned.strip("`")
            cleaned = cleaned.replace("json\n", "", 1).strip()
        try:
            parsed = json.loads(cleaned)
        except json.JSONDecodeError as exc:
            raise QwenClientError("response_content_not_json") from exc
        if not isinstance(parsed, dict):
            raise QwenClientError("response_json_not_object")
        return parsed

    @staticmethod
    def _read_max_tokens_from_env() -> int | None:
        raw = (os.getenv("LLM_MAX_TOKENS") or os.getenv("QWEN_MAX_TOKENS") or os.getenv("MAX_TOKENS") or "").strip()
        if not raw:
            return None
        try:
            parsed = int(raw)
        except ValueError:
            return None
        if parsed <= 0:
            return None
        return parsed

    @staticmethod
    def _normalize_endpoint(endpoint: str) -> str:
        normalized = endpoint.strip().rstrip("/")
        if not normalized:
            return DEFAULT_QWEN_CHAT_ENDPOINT
        if normalized.endswith("/chat/completions"):
            return normalized
        if normalized.endswith("/v1"):
            return f"{normalized}/chat/completions"
        return normalized
