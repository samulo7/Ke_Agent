from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import IO, Any


class JsonObservabilityFormatter(logging.Formatter):
    """Format request observability events as single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        obs: dict[str, Any] = getattr(record, "obs", {})
        reserved_keys = {
            "module",
            "trace_id",
            "user_id",
            "dept_id",
            "intent",
            "identity_source",
            "is_degraded",
            "source_ids",
            "permission_decision",
            "knowledge_version",
            "answered_at",
            "llm_trace",
            "event",
            "path",
            "method",
            "status_code",
            "duration_ms",
            "error_category",
        }
        extra_obs = {
            key: value
            for key, value in obs.items()
            if key not in reserved_keys
        }
        payload = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "module": obs.get("module", record.name),
            "trace_id": obs.get("trace_id"),
            "user_id": obs.get("user_id"),
            "dept_id": obs.get("dept_id"),
            "intent": obs.get("intent"),
            "identity_source": obs.get("identity_source"),
            "is_degraded": obs.get("is_degraded"),
            "source_ids": obs.get("source_ids"),
            "permission_decision": obs.get("permission_decision"),
            "knowledge_version": obs.get("knowledge_version"),
            "answered_at": obs.get("answered_at"),
            "llm_trace": obs.get("llm_trace", {}),
            "event": obs.get("event", record.getMessage()),
            "path": obs.get("path"),
            "method": obs.get("method"),
            "status_code": obs.get("status_code"),
            "duration_ms": obs.get("duration_ms"),
            "error_category": obs.get("error_category"),
        }
        payload.update(extra_obs)
        return json.dumps(payload, ensure_ascii=False)


def configure_structured_logging(level: str = "INFO", stream: IO[str] | None = None) -> logging.Logger:
    logger = logging.getLogger("keagent.observability")
    resolved_level = getattr(logging, level.upper(), logging.INFO)
    logger.setLevel(resolved_level)
    logger.handlers.clear()
    logger.propagate = False

    handler = logging.StreamHandler(stream)
    handler.setFormatter(JsonObservabilityFormatter())
    logger.addHandler(handler)
    return logger
