from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.services.admin_knowledge import AdminKnowledgeService, build_default_admin_knowledge_service

router = APIRouter(prefix="/admin/ui", tags=["admin-pages"])

_TEMPLATES = Jinja2Templates(directory=str(Path(__file__).resolve().parents[1] / "templates"))


def _service(request: Request) -> AdminKnowledgeService:
    service = getattr(request.app.state, "admin_knowledge_service", None)
    if service is None:
        service = build_default_admin_knowledge_service()
        request.app.state.admin_knowledge_service = service
    return service


def _role(request: Request) -> str:
    role = str(request.query_params.get("as_role", "")).strip()
    if role:
        return role
    return str(request.headers.get("X-Admin-Role", "admin")).strip() or "admin"


def _user_id(request: Request) -> str:
    return str(request.headers.get("X-Admin-User-Id", "system-admin")).strip() or "system-admin"


def _page_context(request: Request) -> dict[str, Any]:
    service = _service(request)
    role_code = _role(request)
    permissions = service.get_permissions(user_id=_user_id(request), role_code=role_code)  # type: ignore[arg-type]
    return {
        "request": request,
        "role_code": role_code,
        "role_query": f"?as_role={role_code}",
        "permissions": permissions,
    }


def _assert_menu_access(*, permissions: dict[str, Any], menu_key: str) -> None:
    menus = permissions.get("menus", {})
    if not menus.get(menu_key, False):
        raise HTTPException(status_code=403, detail="forbidden")


def _assert_kind_create_access(*, permissions: dict[str, Any], kind: str) -> None:
    kind_perms = permissions.get("knowledge_permissions", {}).get(kind, {})
    if not kind_perms.get("can_create", False):
        raise HTTPException(status_code=403, detail="forbidden")


def _uploadable_document_kinds(*, permissions: dict[str, Any]) -> list[str]:
    available: list[str] = []
    for kind in ("policy_doc", "restricted_doc"):
        kind_perms = permissions.get("knowledge_permissions", {}).get(kind, {})
        if kind_perms.get("can_create", False):
            available.append(kind)
    return available


@router.get("/knowledge", response_class=HTMLResponse)
def knowledge_list_page(
    request: Request,
    knowledge_kind: str = Query(default=""),
    review_status: str = Query(default=""),
    keyword: str = Query(default=""),
) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="knowledge")
    service = _service(request)
    data = service.list_knowledge(
        role_code=ctx["role_code"],  # type: ignore[arg-type]
        knowledge_kind=knowledge_kind,
        review_status=review_status,
        keyword=keyword,
    )
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/knowledge_list.html",
        {
            **ctx,
            "page_title": "机器人知识管理",
            "active_nav": "knowledge",
            "knowledge_kind": knowledge_kind,
            "review_status": review_status,
            "keyword": keyword,
            "items": data["items"],
            "pagination": data["pagination"],
        },
    )


@router.get("/knowledge/faq/new", response_class=HTMLResponse)
def faq_form_page(request: Request, doc_id: str = Query(default="")) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="knowledge")
    _assert_kind_create_access(permissions=ctx["permissions"], kind="faq")
    initial_data = None
    if doc_id:
        initial_data = _service(request).get_knowledge_detail(role_code=ctx["role_code"], doc_id=doc_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/faq_form.html",
        {
            **ctx,
            "page_title": "新增 FAQ" if not doc_id else "编辑 FAQ",
            "active_nav": "knowledge",
            "initial_data": initial_data,
        },
    )


@router.get("/knowledge/fixed-quote/new", response_class=HTMLResponse)
def fixed_quote_form_page(request: Request, doc_id: str = Query(default="")) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="knowledge")
    _assert_kind_create_access(permissions=ctx["permissions"], kind="fixed_quote")
    initial_data = None
    if doc_id:
        initial_data = _service(request).get_knowledge_detail(role_code=ctx["role_code"], doc_id=doc_id)
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/fixed_quote_form.html",
        {
            **ctx,
            "page_title": "新增固定报价" if not doc_id else "编辑固定报价",
            "active_nav": "knowledge",
            "initial_data": initial_data,
        },
    )


@router.get("/knowledge/upload", response_class=HTMLResponse)
def document_upload_page(request: Request, knowledge_kind: str = Query(default="policy_doc")) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="knowledge")
    available_kinds = _uploadable_document_kinds(permissions=ctx["permissions"])
    if not available_kinds:
        raise HTTPException(status_code=403, detail="forbidden")
    if knowledge_kind not in available_kinds:
        knowledge_kind = available_kinds[0]
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/document_upload.html",
        {
            **ctx,
            "page_title": "上传知识文档",
            "active_nav": "knowledge",
            "knowledge_kind": knowledge_kind,
            "available_kinds": available_kinds,
        },
    )


@router.get("/validation", response_class=HTMLResponse)
def validation_page(
    request: Request,
    doc_id: str = Query(default=""),
    title: str = Query(default=""),
    question: str = Query(default=""),
) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="validation")
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/dingtalk_preview.html",
        {
            **ctx,
            "page_title": "钉钉对话验证",
            "active_nav": "validation",
            "doc_id": doc_id,
            "title": title,
            "question": question,
        },
    )


@router.get("/publish", response_class=HTMLResponse)
def publish_page(request: Request) -> HTMLResponse:
    ctx = _page_context(request)
    _assert_menu_access(permissions=ctx["permissions"], menu_key="publish")
    service = _service(request)
    draft_items = service.list_knowledge(role_code=ctx["role_code"], review_status="draft")  # type: ignore[arg-type]
    ready_items = service.list_knowledge(role_code=ctx["role_code"], review_status="ready_to_publish")  # type: ignore[arg-type]
    items = [*draft_items["items"], *ready_items["items"]]
    return _TEMPLATES.TemplateResponse(
        request,
        "admin/publish_list.html",
        {
            **ctx,
            "page_title": "发布到机器人",
            "active_nav": "publish",
            "items": items,
        },
    )
