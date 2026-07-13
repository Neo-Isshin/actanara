from datetime import date, datetime, timedelta

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import JSONResponse, StreamingResponse
from app.services import ai_assets, agents, foundation, skills
from app.services.dashboard_state import dashboard_failure
from app.services.tz import hkt_today
from data_foundation.refresh import HistoryBackfillAlreadyActiveError
import logging
import asyncio
import json

logger = logging.getLogger(__name__)
router = APIRouter()
events_router = APIRouter()

@router.get("/ai-assets")
async def api_ai_assets():
    try:
        data = ai_assets.get_ai_assets_cached()
        return data
    except Exception as e:
        logger.exception("GET /api/ai-assets failed")
        return dashboard_failure(
            "ai-assets",
            fallback={
                "tools": [],
                "totalTokens": 0,
                "totalMessages": 0,
                "totalSessions": 0,
                "agents": [],
                "agentCount": 0,
                "activeDayCount": 0,
            },
        )

@router.post("/ai-assets/tool-configs/discover")
async def api_discover_tool_configs():
    try:
        return ai_assets.refresh_tool_configs_with_metadata()
    except Exception as e:
        logger.exception("POST /api/ai-assets/tool-configs/discover failed")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.post("/ai-assets/refresh")
async def api_refresh_ai_assets(background_tasks: BackgroundTasks, payload: dict | None = None):
    try:
        raw_date = (payload or {}).get("businessDate")
        business_date = date.fromisoformat(raw_date) if raw_date else hkt_today()
        run_id = foundation.queue_refresh(business_date)
        background_tasks.add_task(foundation.execute_refresh, run_id)
        return JSONResponse({"runId": run_id, "status": "queued"}, status_code=202)
    except (TypeError, ValueError):
        return JSONResponse({"error": "Invalid businessDate; expected YYYY-MM-DD"}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/ai-assets/refresh failed")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/foundation/refresh-jobs")
async def api_refresh_jobs(limit: int = 20):
    try:
        if limit < 1 or limit > 100:
            raise ValueError
        return foundation.list_refresh_jobs(limit=limit)
    except ValueError:
        return JSONResponse({"error": "Invalid limit; expected 1..100"}, status_code=400)
    except Exception as e:
        logger.exception("GET /api/foundation/refresh-jobs failed")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/foundation/refresh-jobs/{run_id}")
async def api_refresh_status(run_id: int):
    try:
        status = foundation.get_refresh_status(run_id)
        if status is None:
            return JSONResponse({"error": "Refresh job not found"}, status_code=404)
        return status
    except Exception as e:
        logger.exception("GET /api/foundation/refresh-jobs/%s failed", run_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/foundation/backfill")
async def api_foundation_backfill(background_tasks: BackgroundTasks, payload: dict):
    try:
        period_start = date.fromisoformat(payload["start"])
        if payload.get("end"):
            period_end = date.fromisoformat(payload["end"])
            days = (period_end - period_start).days + 1
        else:
            days = max(1, int(payload.get("days", 1)))
            period_end = period_start + timedelta(days=days - 1)
        if days < 1 or days > 366:
            raise ValueError
        run_id = foundation.queue_refresh(period_end, period_start=period_start)
        background_tasks.add_task(
            foundation.execute_refresh,
            run_id,
            period_start=period_start,
            period_days=days,
        )
        return JSONResponse(
            {
                "runId": run_id,
                "status": "queued",
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
                "days": days,
            },
            status_code=202,
        )
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "Invalid backfill payload; expected start/end range up to 366 days"}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/foundation/backfill failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/foundation/history-backfill")
async def api_foundation_history_backfill(background_tasks: BackgroundTasks, payload: dict):
    try:
        selected_periods = payload.get("periods") if isinstance(payload.get("periods"), list) else None
        if selected_periods:
            starts = [date.fromisoformat(str(item["start"])) for item in selected_periods if isinstance(item, dict)]
            ends = [date.fromisoformat(str(item["end"])) for item in selected_periods if isinstance(item, dict)]
            if not starts or not ends:
                raise ValueError("periods must include start and end")
            period_start = min(starts)
            period_end = max(ends)
        else:
            period_start = date.fromisoformat(payload["start"])
            period_end = date.fromisoformat(payload["end"])
        grain = str(payload.get("grain") or "both")
        include_summaries = bool(payload.get("includeSummaries"))
        skip_ready = payload.get("skipReady", True) is not False
        overwrite_daily = bool(payload.get("overwriteDaily"))
        dry_run = bool(payload.get("dryRun"))
        scheduled_at = str(payload.get("scheduledAt") or "").strip() or None
        if scheduled_at:
            datetime.fromisoformat(scheduled_at)
        plan = foundation.plan_history_backfill_request(
            period_start,
            period_end,
            grain=grain,
            include_summaries=include_summaries,
            skip_ready=skip_ready,
            periods=selected_periods,
        )
        if not dry_run and int(plan.get("overwriteItemCount") or 0) > 0 and not overwrite_daily:
            raise ValueError("overwriteDaily confirmation is required when selected dates or summaries already have data")
        if dry_run:
            return plan
        run_id = foundation.queue_history_backfill_request(
            period_start,
            period_end,
            grain=grain,
            include_summaries=include_summaries,
            skip_ready=skip_ready,
            overwrite_daily=overwrite_daily,
            scheduled_at=scheduled_at,
            periods=selected_periods,
        )
        if scheduled_at is None:
            background_tasks.add_task(
                foundation.execute_history_backfill,
                run_id,
                start_date=period_start,
                end_date=period_end,
                grain=grain,
                include_summaries=include_summaries,
                skip_ready=skip_ready,
                overwrite_daily=overwrite_daily,
                periods=selected_periods,
            )
        return JSONResponse(
            {
                "runId": run_id,
                "status": "scheduled" if scheduled_at else "queued",
                "start": period_start.isoformat(),
                "end": period_end.isoformat(),
                "grain": grain,
                "includeSummaries": include_summaries,
                "overwriteDaily": overwrite_daily,
                "scheduledAt": scheduled_at,
                "periodCount": plan["periodCount"],
                "llmCallCount": plan["llmCallCount"],
                "dailyPipelineDays": plan.get("dailyPipelineDays", 0),
            },
            status_code=202,
        )
    except HistoryBackfillAlreadyActiveError as error:
        active = error.active_run
        return JSONResponse(
            {
                "error": "Historical backfill is already running or scheduled. Please use Background Tasks to view progress.",
                "activeRunId": active.get("id"),
                "activeStatus": active.get("status"),
            },
            status_code=409,
        )
    except (KeyError, TypeError, ValueError) as error:
        return JSONResponse({"error": f"Invalid history backfill payload: {error}"}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/foundation/history-backfill failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/foundation/history-backfill/{run_id}/retry-failed")
async def api_foundation_history_backfill_retry_failed(background_tasks: BackgroundTasks, run_id: int):
    try:
        retry_run_id = foundation.queue_failed_history_backfill_retry_request(run_id)
        status = foundation.get_refresh_status(retry_run_id)
        meta = status.get("metadata") if isinstance(status, dict) and isinstance(status.get("metadata"), dict) else {}
        period_start = date.fromisoformat(str(meta["periodStart"]))
        period_end = date.fromisoformat(str(meta["periodEnd"]))
        background_tasks.add_task(
            foundation.execute_history_backfill,
            retry_run_id,
            start_date=period_start,
            end_date=period_end,
            grain=str(meta.get("grain") or "both"),
            include_summaries=bool(meta.get("includeSummaries")),
            skip_ready=meta.get("skipReady", True) is not False,
            overwrite_daily=bool(meta.get("overwriteDaily")),
            periods=meta.get("periods") if isinstance(meta.get("periods"), list) else None,
        )
        return JSONResponse({"runId": retry_run_id, "status": "queued", "sourceRunId": run_id}, status_code=202)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/foundation/history-backfill/%s/retry-failed failed", run_id)
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/foundation/history-backfill/{run_id}/cancel")
async def api_foundation_history_backfill_cancel(run_id: int):
    try:
        result = foundation.cancel_history_backfill_request(run_id)
        return JSONResponse(result, status_code=202)
    except ValueError as error:
        return JSONResponse({"error": str(error)}, status_code=404)
    except Exception as e:
        logger.exception("POST /api/foundation/history-backfill/%s/cancel failed", run_id)
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/foundation/readiness")
async def api_foundation_readiness(start: str | None = None, days: int = 7):
    try:
        if start is None:
            return foundation.get_reader_readiness()
        if days < 1 or days > 62:
            raise ValueError
        return foundation.get_reader_readiness(period_start=date.fromisoformat(start), period_days=days)
    except ValueError:
        return JSONResponse({"error": "Invalid period; expected start=YYYY-MM-DD and days=1..62"}, status_code=400)
    except Exception as e:
        logger.exception("GET /api/foundation/readiness failed")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/file-content")
async def api_file_content(path: str):
    try:
        data = ai_assets.read_file_content(path)
        if "error" in data:
            return JSONResponse(data, status_code=data.get("status", 400))
        return data
    except Exception as e:
        logger.exception(f"GET /api/file-content failed for {path}")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.put("/file-content")
async def api_update_file_content(payload: dict):
    try:
        path = payload.get("path")
        content = payload.get("content")
        if not path or content is None:
            return JSONResponse({"error": "Missing path or content"}, status_code=400)
        data = ai_assets.update_file_content(
            path,
            content,
            confirmation_text=str(payload.get("confirmationText") or ""),
            dry_run=payload.get("dryRun") is True,
        )
        if "error" in data:
            return JSONResponse(data, status_code=data.get("status", 400))
        return data
    except Exception as e:
        logger.exception(f"PUT /api/file-content failed for {payload.get('path')}")
        return JSONResponse({"error": str(e)}, status_code=500)

@router.get("/agents")
async def api_agents():
    try:
        data = agents.get_agent_list()
        return data
    except Exception as e:
        logger.exception("GET /api/agents failed")
        return []

@router.get("/skills")
async def api_skills():
    try:
        data = skills.get_all_skills()
        return data
    except Exception as e:
        logger.exception("GET /api/skills failed")
        return []

# SSE Events endpoints
@router.get("/events/agents")
@events_router.get("/events/agents")
async def sse_agents(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected(): break
            try:
                data = agents.get_summary()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.error(f"SSE agents error: {e}")
            await asyncio.sleep(10)
    return StreamingResponse(event_generator(), media_type="text/event-stream")

@router.get("/events/skills")
@events_router.get("/events/skills")
async def sse_skills(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected(): break
            try:
                data = skills.get_all_skills()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.error(f"SSE skills error: {e}")
            await asyncio.sleep(10)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
@router.get("/events/tasks")
@events_router.get("/events/tasks")
async def sse_tasks(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected(): break
            try:
                from app.services import nova_task_review
                data = nova_task_review.task_board_payload(enabled=foundation.nova_task_enabled())
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.exception("SSE task source failed")
                failure = dashboard_failure(
                    "nova-task-board",
                    fallback={"tasks": [], "grouped": {}, "tree": [], "nodes": [], "lastModified": None},
                )
                yield f"data: {json.dumps(failure)}\n\n"
            await asyncio.sleep(10)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
