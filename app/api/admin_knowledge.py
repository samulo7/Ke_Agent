from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field

from app.services.admin_knowledge import (
    AdminKnowledgeForbiddenError,
    AdminKnowledgeNotFoundError,
    AdminKnowledgeService,
    AdminKnowledgeValidationError,
    KnowledgeInput,
    QuoteFieldsInput,
    build_default_admin_knowledge_service,
)

router = APIRouter(prefix="/admin", tags=["admin-knowledge"])


class QuoteFieldsPayload(BaseModel):
    quote_item_name: str = ""
    spec_model: str = ""
    quote_category: str = ""
    price_amount: float = 0
    unit: str = ""
    tax_included: bool = True
    effective_date: str = ""
    quote_version: str = ""
    non_standard_action: str = ""
    quote_item_code: str = ""
    price_currency: str = "CNY"
    expire_date: str = ""
    source_note: str = ""
    has_price_conflict: bool = False
    price_conflict_note: str = ""

    def to_input(self) -> QuoteFieldsInput:
        return QuoteFieldsInput(**self.model_dump())


class KnowledgePayload(BaseModel):
    knowledge_kind: str
    title: str
    summary: str
    applicability: str = ""
    next_step: str = ""
    source_uri: str = ""
    updated_at: str
    owner: str
    department: str = ""
    permission_scope: str = "public"
    permitted_depts: list[str] = Field(default_factory=list)
    keywords: list[str] = Field(default_factory=list)
    intents: list[str] = Field(default_factory=list)
    version_tag: str = ""
    category: str = ""
    quote_fields: QuoteFieldsPayload | None = None

    def to_input(self) -> KnowledgeInput:
        return KnowledgeInput(
            knowledge_kind=self.knowledge_kind,  # type: ignore[arg-type]
            title=self.title,
            summary=self.summary,
            applicability=self.applicability,
            next_step=self.next_step,
            source_uri=self.source_uri,
            updated_at=self.updated_at,
            owner=self.owner,
            department=self.department,
            permission_scope=self.permission_scope,
            permitted_depts=tuple(self.permitted_depts),
            keywords=tuple(self.keywords),
            intents=tuple(self.intents),
            version_tag=self.version_tag,
            category=self.category,
            quote_fields=self.quote_fields.to_input() if self.quote_fields is not None else None,
        )


class PublishRequest(BaseModel):
    publish_note: str = ""


class ValidationPreviewRequest(BaseModel):
    question: str
    doc_id: str = ""
    dept_context: str = ""


class RoleContextMixin:
    @staticmethod
    def resolve_role(request: Request) -> str:
        return str(request.headers.get("X-Admin-Role", "admin")).strip() or "admin"

    @staticmethod
    def resolve_user_id(request: Request) -> str:
        return str(request.headers.get("X-Admin-User-Id", "system-admin")).strip() or "system-admin"


def _service(request: Request) -> AdminKnowledgeService:
    service = getattr(request.app.state, "admin_knowledge_service", None)
    if service is None:
        service = build_default_admin_knowledge_service()
        request.app.state.admin_knowledge_service = service
    return service


def _error_response(exc: Exception) -> HTTPException:
    if isinstance(exc, AdminKnowledgeForbiddenError):
        return HTTPException(
            status_code=403,
            detail={
                "ok": False,
                "error": {
                    "code": "FORBIDDEN",
                    "message": str(exc),
                    "details": {},
                },
            },
        )
    if isinstance(exc, AdminKnowledgeNotFoundError):
        return HTTPException(
            status_code=404,
            detail={
                "ok": False,
                "error": {
                    "code": "NOT_FOUND",
                    "message": str(exc),
                    "details": {},
                },
            },
        )
    if isinstance(exc, AdminKnowledgeValidationError):
        return HTTPException(
            status_code=400,
            detail={
                "ok": False,
                "error": {
                    "code": "VALIDATION_ERROR",
                    "message": str(exc),
                    "details": {"field": exc.field} if exc.field else {},
                },
            },
        )
    return HTTPException(
        status_code=500,
        detail={
            "ok": False,
            "error": {
                "code": "INTERNAL_ERROR",
                "message": str(exc),
                "details": {},
            },
        },
    )


def _split_csv_form(raw_value: str) -> tuple[str, ...]:
    return tuple(item.strip() for item in raw_value.split(",") if item.strip())


def _build_upload_input(
    *,
    knowledge_kind: str,
    title: str,
    summary: str,
    applicability: str,
    next_step: str,
    source_uri: str,
    owner: str,
    department: str,
    permission_scope: str,
    permitted_depts: str,
    keywords: str,
    intents: str,
    version_tag: str,
    category: str,
) -> KnowledgeInput:
    return KnowledgeInput(
        knowledge_kind=knowledge_kind,  # type: ignore[arg-type]
        title=title,
        summary=summary,
        applicability=applicability,
        next_step=next_step,
        source_uri=source_uri,
        updated_at=datetime.now(timezone.utc).isoformat(),
        owner=owner,
        department=department,
        permission_scope=permission_scope,
        permitted_depts=_split_csv_form(permitted_depts),
        keywords=_split_csv_form(keywords),
        intents=_split_csv_form(intents),
        version_tag=version_tag,
        category=category,
        quote_fields=None,
    )


@router.get("/me/permissions")
def get_current_permissions(request: Request) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        data = service.get_permissions(user_id=user_id, role_code=role_code)  # type: ignore[arg-type]
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.get("/knowledge")
def list_knowledge(
    request: Request,
    knowledge_kind: str = "",
    review_status: str = "",
    keyword: str = "",
    page: int = 1,
    page_size: int = 20,
) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    try:
        data = service.list_knowledge(
            role_code=role_code,  # type: ignore[arg-type]
            knowledge_kind=knowledge_kind,
            review_status=review_status,
            keyword=keyword,
            page=page,
            page_size=page_size,
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.get("/knowledge/{doc_id}")
def get_knowledge_detail(request: Request, doc_id: str) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    try:
        data = service.get_knowledge_detail(role_code=role_code, doc_id=doc_id)  # type: ignore[arg-type]
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.post("/knowledge")
def create_knowledge(request: Request, payload: KnowledgePayload) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        data = service.create_knowledge(
            user_id=user_id,
            role_code=role_code,  # type: ignore[arg-type]
            payload=payload.to_input(),
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.post("/knowledge/upload")
async def upload_knowledge(
    request: Request,
    knowledge_kind: str = Form(...),
    title: str = Form(...),
    summary: str = Form(default=""),
    applicability: str = Form(default=""),
    next_step: str = Form(default=""),
    source_uri: str = Form(default=""),
    owner: str = Form(...),
    department: str = Form(default=""),
    permission_scope: str = Form(default="public"),
    permitted_depts: str = Form(default=""),
    keywords: str = Form(default=""),
    intents: str = Form(default=""),
    version_tag: str = Form(default=""),
    category: str = Form(default=""),
    file: UploadFile = File(...),
) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        payload = _build_upload_input(
            knowledge_kind=knowledge_kind,
            title=title,
            summary=summary,
            applicability=applicability,
            next_step=next_step,
            source_uri=source_uri,
            owner=owner,
            department=department,
            permission_scope=permission_scope,
            permitted_depts=permitted_depts,
            keywords=keywords,
            intents=intents,
            version_tag=version_tag,
            category=category,
        )
        data = service.upload_knowledge_document(
            user_id=user_id,
            role_code=role_code,  # type: ignore[arg-type]
            payload=payload,
            filename=file.filename or "",
            content=await file.read(),
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.put("/knowledge/{doc_id}")
def update_knowledge(request: Request, doc_id: str, payload: KnowledgePayload) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        data = service.update_knowledge(
            user_id=user_id,
            role_code=role_code,  # type: ignore[arg-type]
            doc_id=doc_id,
            payload=payload.to_input(),
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.post("/validation/dingtalk-preview")
def preview_dingtalk_reply(request: Request, payload: ValidationPreviewRequest) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        data = service.preview_dingtalk_reply(
            user_id=user_id,
            role_code=role_code,  # type: ignore[arg-type]
            question=payload.question,
            doc_id=payload.doc_id,
            dept_context=payload.dept_context,
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}


@router.post("/publish/{doc_id}")
def publish_knowledge(request: Request, doc_id: str, payload: PublishRequest) -> dict[str, Any]:
    service = _service(request)
    role_code = RoleContextMixin.resolve_role(request)
    user_id = RoleContextMixin.resolve_user_id(request)
    try:
        data = service.publish_knowledge(
            user_id=user_id,
            role_code=role_code,  # type: ignore[arg-type]
            doc_id=doc_id,
            publish_note=payload.publish_note,
        )
    except Exception as exc:
        raise _error_response(exc) from exc
    return {"ok": True, "data": data}
