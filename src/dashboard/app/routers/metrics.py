from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse
from app.services import tokens, ai_assets
from app.services.dashboard_state import attach_dashboard_state, dashboard_failure
from app.services.ui_text import is_english_profile, dashboard_language_profile
import logging
import asyncio
import json

logger = logging.getLogger(__name__)
router = APIRouter()
events_router = APIRouter()

@router.get("/tokens")
async def api_tokens():
    try:
        data = tokens.compute_summary()
        summary = data.get("summary") if isinstance(data.get("summary"), dict) else {}
        return attach_dashboard_state(data, empty=int(summary.get("total") or 0) == 0 and int(summary.get("count") or 0) == 0)
    except Exception as e:
        logger.exception("GET /api/tokens failed")
        return dashboard_failure("token-summary", fallback={"today": {}, "week": {}, "summary": {}})

@router.get("/cron-status")
async def api_cron_status():
    try:
        from pathlib import Path
        import config
        table_path = config.TMP_WORKSPACE.parent / "速查表" / "定时任务速查表.md"
        if not table_path.exists():
            return {"status": "unknown", "msg": _cron_status_message("missing")}
        content = table_path.read_text(encoding="utf-8")
        failed = "🔴" in content or "Fail" in content
        return {
            "status": "error" if failed else "ok",
            "msg": _cron_status_message("failed" if failed else "ok")
        }
    except Exception as e:
        return {"status": "unknown", "msg": str(e)}


def _cron_status_message(state: str, profile: str | None = None) -> str:
    text = _CRON_STATUS_TEXT["en" if is_english_profile(profile or dashboard_language_profile()) else "zh"]
    return text.get(state, text["missing"])


_CRON_STATUS_TEXT = {
    "zh": {
        "missing": "速查表不存在",
        "failed": "有任务失败",
        "ok": "全部正常",
    },
    "en": {
        "missing": "Quick-reference table not found",
        "failed": "Some scheduled jobs failed",
        "ok": "All scheduled jobs normal",
    },
}

@router.get("/token-clock")
async def api_token_clock():
    try:
        from app.services import token_clock
        # The live scanner walks real agent history and can take long enough to
        # starve this single-worker ASGI event loop (including /health).  Keep
        # that blocking file/SQLite work in a worker thread.
        data = await asyncio.to_thread(token_clock.get_token_clock_data)
        return data
    except Exception as e:
        logger.exception("GET /api/token-clock failed")
        return dashboard_failure(
            "token-clock",
            fallback={"tools": [], "totalTokens": 0, "totalMessages": 0, "hourlyTimeline": [], "workspaceUsage": []},
        )

# SSE Events endpoint for tokens
@router.get("/events/tokens")
@events_router.get("/events/tokens")
async def sse_tokens(request: Request):
    async def event_generator():
        while True:
            if await request.is_disconnected():
                break
            try:
                data = tokens.compute_summary()
                yield f"data: {json.dumps(data)}\n\n"
            except Exception as e:
                logger.exception("SSE token source failed")
                failure = dashboard_failure("token-summary", fallback={"today": {}, "week": {}, "summary": {}})
                yield f"data: {json.dumps(failure)}\n\n"
            await asyncio.sleep(5)
    return StreamingResponse(event_generator(), media_type="text/event-stream")
