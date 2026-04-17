from __future__ import annotations

from io import StringIO
from typing import TextIO

from fastapi import FastAPI

from app.api.admin_knowledge import router as admin_knowledge_router
from app.api.admin_pages import router as admin_pages_router
from app.api.dingtalk import router as dingtalk_router
from app.api.health import router as health_router
from app.core.structured_logging import configure_structured_logging
from app.core.trace_middleware import TraceMiddleware
from app.rag.knowledge_retriever import KnowledgeRetriever
from app.repos.sql_knowledge_repository import SQLKnowledgeRepository
from app.services.admin_knowledge import (
    AdminKnowledgeService,
    build_default_admin_knowledge_service,
    build_shared_admin_runtime_services,
)
from app.services.health import HealthService
from app.services.knowledge_answering import KnowledgeAnswerService
from app.services.single_chat import SingleChatService
from app.services.user_context import UserContextResolver, build_default_user_context_resolver


def create_app(
    *,
    health_service: HealthService | None = None,
    single_chat_service: SingleChatService | None = None,
    user_context_resolver: UserContextResolver | None = None,
    admin_knowledge_service: AdminKnowledgeService | None = None,
    log_level: str = "INFO",
    log_stream: TextIO | None = None,
) -> FastAPI:
    configure_structured_logging(level=log_level, stream=log_stream)
    app = FastAPI(title="keagent")
    app.add_middleware(TraceMiddleware)
    app.state.health_service = health_service or HealthService()

    if single_chat_service is None and admin_knowledge_service is None:
        admin_knowledge_service, knowledge_answer_service = build_shared_admin_runtime_services()
        single_chat_service = SingleChatService(knowledge_answer_service=knowledge_answer_service)
    elif single_chat_service is None and admin_knowledge_service is not None:
        repository = SQLKnowledgeRepository(connection=admin_knowledge_service.connection)
        knowledge_answer_service = KnowledgeAnswerService(
            retriever=KnowledgeRetriever(repository=repository),
            repository=repository,
        )
        single_chat_service = SingleChatService(knowledge_answer_service=knowledge_answer_service)
    elif single_chat_service is not None and admin_knowledge_service is None:
        admin_knowledge_service = build_default_admin_knowledge_service()

    app.state.single_chat_service = single_chat_service or SingleChatService()
    app.state.user_context_resolver = user_context_resolver or build_default_user_context_resolver()
    app.state.admin_knowledge_service = admin_knowledge_service or build_default_admin_knowledge_service()
    app.include_router(health_router)
    app.include_router(dingtalk_router)
    app.include_router(admin_knowledge_router)
    app.include_router(admin_pages_router)
    return app


app = create_app()


def create_test_log_stream() -> StringIO:
    return StringIO()
