from __future__ import annotations

from contextvars import ContextVar

_TRACE_ID: ContextVar[str] = ContextVar("trace_id", default="")


def set_trace_id(trace_id: str) -> object:
    return _TRACE_ID.set(trace_id)


def reset_trace_id(token: object) -> None:
    _TRACE_ID.reset(token)


def get_trace_id() -> str:
    return _TRACE_ID.get()
