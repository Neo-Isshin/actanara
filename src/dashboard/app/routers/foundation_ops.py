from datetime import date as date_cls

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from app.services import background_tasks, foundation_ops
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/background-tasks")
async def api_background_tasks(limit: int = 30):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        return background_tasks.get_background_tasks(limit=limit)
    except ValueError:
        return JSONResponse({"error": "Invalid limit; expected 1..100"}, status_code=400)
    except Exception as e:
        logger.exception("GET /api/background-tasks failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/snapshot")
async def api_foundation_snapshot_operations(start: str | None = None, days: int | None = None, limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        if start is None:
            return foundation_ops.get_snapshot_operations(limit=limit)
        period_days = 7 if days is None else days
        if period_days < 1 or period_days > 62:
            raise ValueError
        return foundation_ops.get_snapshot_operations(
            period_start=date_cls.fromisoformat(start),
            period_days=period_days,
            limit=limit,
        )
    except ValueError:
        return JSONResponse(
            {"error": "Invalid request; expected start=YYYY-MM-DD, days=1..62 and limit=1..100"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("GET /api/foundation/ops/snapshot failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/production-readiness")
async def api_foundation_production_readiness(start: str | None = None, days: int | None = None, limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        if start is None:
            return foundation_ops.get_foundation_production_readiness(limit=limit)
        period_days = 7 if days is None else days
        if period_days < 1 or period_days > 62:
            raise ValueError
        return foundation_ops.get_foundation_production_readiness(
            period_start=date_cls.fromisoformat(start),
            period_days=period_days,
            limit=limit,
        )
    except ValueError:
        return JSONResponse(
            {"error": "Invalid request; expected start=YYYY-MM-DD, days=1..62 and limit=1..100"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("GET /api/foundation/ops/production-readiness failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/daily-qa")
async def api_foundation_daily_qa(date: str, limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        return foundation_ops.get_foundation_daily_qa(
            business_date=date_cls.fromisoformat(date),
            limit=limit,
        )
    except ValueError:
        return JSONResponse(
            {"error": "Invalid request; expected date=YYYY-MM-DD and limit=1..100"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("GET /api/foundation/ops/daily-qa failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/daily-qa/overview")
async def api_foundation_daily_qa_overview(end: str, days: int = 7, limit: int = 20):
    try:
        if days < 1 or days > 31 or limit < 1 or limit > 100:
            raise ValueError
        return foundation_ops.get_foundation_daily_qa_overview(
            end_date=date_cls.fromisoformat(end),
            days=days,
            limit=limit,
        )
    except ValueError:
        return JSONResponse(
            {"error": "Invalid request; expected end=YYYY-MM-DD, days=1..31 and limit=1..100"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("GET /api/foundation/ops/daily-qa/overview failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/daily-pipeline-summary")
async def api_foundation_daily_pipeline_summary(date: str, limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        return foundation_ops.get_foundation_daily_pipeline_summary(
            business_date=date_cls.fromisoformat(date),
            limit=limit,
        )
    except ValueError:
        return JSONResponse(
            {"error": "Invalid request; expected date=YYYY-MM-DD and limit=1..100"},
            status_code=400,
        )
    except Exception as e:
        logger.exception("GET /api/foundation/ops/daily-pipeline-summary failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/foundation/ops/daily-qa/repair-runs")
async def api_foundation_daily_qa_repair_run(background_tasks: BackgroundTasks, payload: dict):
    try:
        action_id = str(payload.get("actionId") or "")
        business_date = date_cls.fromisoformat(str(payload.get("businessDate") or ""))
        confirmation_text = str(payload.get("confirmationText") or "")
        result = foundation_ops.queue_foundation_daily_qa_repair(
            action_id=action_id,
            business_date=business_date,
            confirmation_text=confirmation_text,
            limit=30,
        )
        run = result.get("run") or {}
        if result.get("status") == "already_running":
            return JSONResponse({"error": "Repair run already active", "run": run}, status_code=409)
        background_tasks.add_task(foundation_ops.execute_foundation_daily_qa_repair, run["id"])
        return JSONResponse({"run": run, "status": "queued"}, status_code=202)
    except ValueError as e:
        return JSONResponse({"error": str(e)}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/foundation/ops/daily-qa/repair-runs failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/daily-qa/repair-runs")
async def api_foundation_daily_qa_repair_runs(limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        return foundation_ops.list_foundation_repair_runs(limit=limit)
    except ValueError:
        return JSONResponse({"error": "Invalid limit; expected 1..100"}, status_code=400)
    except Exception as e:
        logger.exception("GET /api/foundation/ops/daily-qa/repair-runs failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/daily-qa/repair-runs/{run_id}")
async def api_foundation_daily_qa_repair_run_status(run_id: int):
    try:
        run = foundation_ops.get_foundation_repair_run(run_id)
        if run is None:
            return JSONResponse({"error": "Repair run not found"}, status_code=404)
        return run
    except Exception as e:
        logger.exception("GET /api/foundation/ops/daily-qa/repair-runs/%s failed", run_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/project-registry")
async def api_foundation_project_registry_status():
    try:
        return foundation_ops.get_project_registry_status()
    except Exception as e:
        logger.exception("GET /api/foundation/ops/project-registry failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/foundation/ops/system-registry")
async def api_foundation_system_registry_status():
    try:
        return foundation_ops.get_system_registry_status()
    except Exception as e:
        logger.exception("GET /api/foundation/ops/system-registry failed")
        return JSONResponse({"error": str(e)}, status_code=500)
