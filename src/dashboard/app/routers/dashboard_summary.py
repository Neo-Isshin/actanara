"""Asset-first summary, protected by the shared Dashboard session middleware."""

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.services import dashboard_summary
from app.services.dashboard_state import dashboard_failure


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/dashboard")


@router.get("/summary")
async def api_dashboard_summary():
    try:
        payload = await run_in_threadpool(dashboard_summary.get_summary)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except Exception:
        logger.exception("Dashboard asset summary failed")
        return JSONResponse(
            dashboard_failure("dashboard-summary", fallback={"status": "error"}),
            status_code=500,
            headers={"Cache-Control": "no-store"},
        )


@router.get("/document")
async def api_dashboard_document(businessDate: str, document_type: str = Query(alias="type")):
    headers = {"Cache-Control": "no-store"}
    try:
        payload = await run_in_threadpool(dashboard_summary.get_document, businessDate, document_type)
        return JSONResponse(payload, headers=headers)
    except dashboard_summary.DashboardDocumentQueryError as error:
        return JSONResponse({"status": "error", "code": "invalid-query", "error": str(error)}, status_code=400, headers=headers)
    except dashboard_summary.DashboardDocumentNotFound as error:
        return JSONResponse({"status": "empty", "code": "document-not-found", "error": str(error)}, status_code=404, headers=headers)
    except dashboard_summary.DashboardDocumentAmbiguous as error:
        return JSONResponse({"status": "degraded", "code": "document-ambiguous", "error": str(error)}, status_code=409, headers=headers)
    except Exception:
        logger.exception("Dashboard document read failed")
        return JSONResponse(dashboard_failure("dashboard-document", fallback={"status": "error"}), status_code=503, headers=headers)


@router.get("/skills")
async def api_dashboard_skills():
    try:
        payload = await run_in_threadpool(dashboard_summary.get_skills)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except Exception:
        logger.warning("Dashboard canonical Skill library read failed")
        return JSONResponse(
            dashboard_failure("canonical-skill-library", status="unavailable",
                              fallback={"status": "unavailable", "count": None, "items": []}),
            status_code=503, headers={"Cache-Control": "no-store"},
        )


@router.get("/skills/{asset_id}")
async def api_dashboard_skill(asset_id: str):
    try:
        payload = await run_in_threadpool(dashboard_summary.get_skill, asset_id)
        return JSONResponse(payload, headers={"Cache-Control": "no-store"})
    except dashboard_summary.DashboardSkillNotFound as error:
        return JSONResponse(
            {"status": "empty", "code": "skill-not-found", "error": str(error)},
            status_code=404, headers={"Cache-Control": "no-store"},
        )
    except Exception:
        logger.warning("Dashboard canonical Skill detail read failed")
        return JSONResponse(
            dashboard_failure("canonical-skill-library", status="unavailable", fallback={"status": "unavailable"}),
            status_code=503, headers={"Cache-Control": "no-store"},
        )
