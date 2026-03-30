from __future__ import annotations

import logging
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import Request
from starlette.middleware.base import BaseHTTPMiddleware

from app.core.trace_context import reset_trace_id, set_trace_id

TRACE_HEADER = "X-Trace-Id"
OBS_LOGGER_NAME = "keagent.observability"


def _derive_error_category(status_code: int) -> str | None:
    if 400 <= status_code < 500:
        return "client_error"
    if status_code >= 500:
        return "server_error"
    return None


class TraceMiddleware(BaseHTTPMiddleware):
    """Propagate trace_id across request context, headers, and logs."""

    def __init__(self, app: Any) -> None:
        super().__init__(app)
        self._logger = logging.getLogger(OBS_LOGGER_NAME)

    async def dispatch(self, request: Request, call_next: Any):  # type: ignore[override]
        incoming_trace = (request.headers.get(TRACE_HEADER) or "").strip()
        trace_id = incoming_trace or str(uuid4())
        token = set_trace_id(trace_id)
        request.state.trace_id = trace_id
        request.state.user_id = getattr(request.state, "user_id", "unknown")
        request.state.dept_id = getattr(request.state, "dept_id", "unknown")
        request.state.intent = getattr(request.state, "intent", "other")
        request.state.identity_source = getattr(request.state, "identity_source", "event_fallback")
        request.state.is_degraded = getattr(request.state, "is_degraded", True)
        request.state.source_ids = getattr(request.state, "source_ids", [])
        request.state.permission_decision = getattr(request.state, "permission_decision", "allow")
        request.state.knowledge_version = getattr(request.state, "knowledge_version", "")
        request.state.answered_at = getattr(request.state, "answered_at", "")
        request.state.llm_trace = getattr(request.state, "llm_trace", {})

        started = perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            elapsed_ms = round((perf_counter() - started) * 1000, 3)
            error_category = getattr(request.state, "error_category", "server_error")
            self._logger.error(
                "request.exception",
                extra={
                    "obs": {
                        "module": "api.middleware",
                        "trace_id": trace_id,
                        "user_id": getattr(request.state, "user_id", "unknown"),
                        "dept_id": getattr(request.state, "dept_id", "unknown"),
                        "intent": getattr(request.state, "intent", "other"),
                        "identity_source": getattr(request.state, "identity_source", "event_fallback"),
                        "is_degraded": getattr(request.state, "is_degraded", True),
                        "source_ids": list(getattr(request.state, "source_ids", [])),
                        "permission_decision": getattr(request.state, "permission_decision", "allow"),
                        "knowledge_version": getattr(request.state, "knowledge_version", ""),
                        "answered_at": getattr(request.state, "answered_at", ""),
                        "llm_trace": dict(getattr(request.state, "llm_trace", {})),
                        "event": "request_exception",
                        "path": str(request.url.path),
                        "method": request.method,
                        "status_code": 500,
                        "duration_ms": elapsed_ms,
                        "error_category": error_category,
                    }
                },
            )
            reset_trace_id(token)
            raise

        elapsed_ms = round((perf_counter() - started) * 1000, 3)
        status_code = int(response.status_code)
        default_category = _derive_error_category(status_code)
        error_category = getattr(request.state, "error_category", default_category)
        self._logger.info(
            "request.completed",
            extra={
                "obs": {
                    "module": "api.middleware",
                    "trace_id": trace_id,
                    "user_id": getattr(request.state, "user_id", "unknown"),
                    "dept_id": getattr(request.state, "dept_id", "unknown"),
                    "intent": getattr(request.state, "intent", "other"),
                    "identity_source": getattr(request.state, "identity_source", "event_fallback"),
                    "is_degraded": getattr(request.state, "is_degraded", True),
                    "source_ids": list(getattr(request.state, "source_ids", [])),
                    "permission_decision": getattr(request.state, "permission_decision", "allow"),
                    "knowledge_version": getattr(request.state, "knowledge_version", ""),
                    "answered_at": getattr(request.state, "answered_at", ""),
                    "llm_trace": dict(getattr(request.state, "llm_trace", {})),
                    "event": "request_completed",
                    "path": str(request.url.path),
                    "method": request.method,
                    "status_code": status_code,
                    "duration_ms": elapsed_ms,
                    "error_category": error_category,
                }
            },
        )
        response.headers[TRACE_HEADER] = trace_id
        reset_trace_id(token)
        return response
