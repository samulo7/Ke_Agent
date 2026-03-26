from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.services.health import HealthService

router = APIRouter()


@router.get("/healthz")
async def healthz(request: Request) -> JSONResponse:
    health_service: HealthService = request.app.state.health_service
    trace_id = getattr(request.state, "trace_id", "")
    report = await health_service.run(trace_id=trace_id)

    payload = report.to_dict()
    status_code = 200 if report.status == "ok" else 503
    if status_code >= 500:
        request.state.error_category = "dependency_error"
    return JSONResponse(status_code=status_code, content=payload)
