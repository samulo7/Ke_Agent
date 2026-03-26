from __future__ import annotations

from io import StringIO
from typing import TextIO

from fastapi import FastAPI

from app.api.dingtalk import router as dingtalk_router
from app.api.health import router as health_router
from app.core.structured_logging import configure_structured_logging
from app.core.trace_middleware import TraceMiddleware
from app.services.health import HealthService
from app.services.single_chat import SingleChatService


def create_app(
    *,
    health_service: HealthService | None = None,
    single_chat_service: SingleChatService | None = None,
    log_level: str = "INFO",
    log_stream: TextIO | None = None,
) -> FastAPI:
    configure_structured_logging(level=log_level, stream=log_stream)
    app = FastAPI(title="keagent")
    app.add_middleware(TraceMiddleware)
    app.state.health_service = health_service or HealthService()
    app.state.single_chat_service = single_chat_service or SingleChatService()
    app.include_router(health_router)
    app.include_router(dingtalk_router)
    return app


app = create_app()


def create_test_log_stream() -> StringIO:
    return StringIO()
