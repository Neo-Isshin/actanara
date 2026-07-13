from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks
from fastapi.responses import JSONResponse
from app.services import diary, foundation
from app.services.dashboard_state import attach_dashboard_state, dashboard_failure
from data_foundation.paths import load_paths
from data_foundation.diary_markdown import read_diary_markdown_documents
from data_foundation.settings import read_settings
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _refresh_period_days(payload: dict) -> int:
    days = int(payload.get("days", 7))
    if days < 1 or days > 62:
        raise ValueError("days must be 1..62")
    return days

@router.get("/diary-list")
async def api_diary_list(envelope: bool = False):
    try:
        data = diary.get_diary_list(include_state=envelope)
        return data
    except Exception as e:
        logger.exception("GET /api/diary-list failed")
        return JSONResponse(
            dashboard_failure("diary-list", fallback={"items": []}),
            status_code=503,
        )

@router.get("/diary/{full_date}")
async def api_diary(full_date: str):
    try:
        result = diary.get_diary_page(full_date)
        state = result.get("dashboardState") if isinstance(result, dict) else {}
        if isinstance(state, dict) and state.get("status") == "error":
            return JSONResponse(result, status_code=503)
        return result
    except Exception as e:
        logger.exception(f"GET /api/diary/{full_date} failed")
        return JSONResponse(dashboard_failure("diary-page"), status_code=503)

@router.get("/weekly-report")
async def api_weekly_report(days: int = 7, start: str = None, include_assets: bool = False):
    try:
        data = diary.generate_weekly_report(days, start, include_assets=include_assets)
        return attach_dashboard_state(data, empty=not bool(data))
    except Exception as e:
        logger.exception("GET /api/weekly-report failed")
        return JSONResponse(dashboard_failure("period-report"), status_code=503)

@router.post("/weekly-report/refresh")
async def api_refresh_weekly_report(background_tasks: BackgroundTasks, payload: dict):
    try:
        days = _refresh_period_days(payload)
        period_start = date.fromisoformat(payload["start"])
        business_date = period_start + timedelta(days=days - 1)
        run_id = foundation.queue_refresh(business_date, period_start=period_start)
        background_tasks.add_task(
            foundation.execute_refresh,
            run_id,
            period_start=period_start,
            period_days=days,
        )
        return JSONResponse({"runId": run_id, "status": "queued"}, status_code=202)
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "Invalid refresh payload; expected start and optional days"}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/weekly-report/refresh failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.post("/weekly-report/summary/refresh")
async def api_refresh_weekly_report_summary(background_tasks: BackgroundTasks, payload: dict):
    try:
        days = _refresh_period_days(payload)
        period_start = date.fromisoformat(payload["start"])
        business_date = period_start + timedelta(days=days - 1)
        run_id = foundation.queue_period_summary(business_date, period_start=period_start)
        background_tasks.add_task(
            foundation.execute_period_summary,
            run_id,
            period_start=period_start,
            period_days=days,
        )
        return JSONResponse({"runId": run_id, "status": "queued"}, status_code=202)
    except (KeyError, TypeError, ValueError):
        return JSONResponse({"error": "Invalid summary refresh payload; expected start and optional days"}, status_code=400)
    except Exception as e:
        logger.exception("POST /api/weekly-report/summary/refresh failed")
        return JSONResponse({"error": str(e)}, status_code=500)


@router.get("/report-list")
async def api_report_list():
    try:
        paths = load_paths()
        reports = []
        documents = _read_report_documents(paths, date(1970, 1, 1), date(2999, 12, 31))
        for document in documents:
            if document.get("report_type") != "learning":
                continue
            try:
                date_str = str(document.get("business_date") or "")
                if not date_str:
                    continue
                reports.append({
                    "id": f"report-{date_str}",
                    "title": document.get("title") or _learning_report_title(paths, date_str),
                    "date": date_str,
                    "mtime": str(document.get("modified_at") or document.get("parsed_at") or "")[:10],
                    "path": document.get("relative_path") or "",
                    "source": "foundation-diary-markdown-documents",
                })
            except Exception:
                pass
        reports.sort(key=lambda x: x["date"], reverse=True)
        return reports
    except Exception as e:
        logger.exception("GET /api/report-list failed")
        return []

@router.get("/report/{report_id}")
async def api_report_detail(report_id: str):
    try:
        paths = load_paths()
        date_str = report_id.replace("report-", "")
        business_date = date.fromisoformat(date_str)
        documents = _read_report_documents(paths, business_date, business_date)
        document = next((item for item in documents if item.get("report_type") == "learning"), None)
        if document is None:
            return JSONResponse(
                {
                    "error": "Report not found",
                    "reason": "foundation-document-missing",
                    "source": "foundation-diary-markdown-documents",
                },
                status_code=404,
            )
        return {
            "id": report_id,
            "title": document.get("title") or _learning_report_title(paths, date_str),
            "date": date_str,
            "content": _document_markdown(document),
            "mtime": str(document.get("modified_at") or document.get("parsed_at") or "")[:10],
            "path": document.get("relative_path") or "",
            "source": "foundation-diary-markdown-documents",
        }
    except Exception as e:
        logger.exception(f"GET /api/report/{report_id} failed")
        return JSONResponse({"error": str(e)}, status_code=500)


def _document_markdown(document: dict) -> str:
    lines = []
    title = document.get("title")
    if title:
        lines.append(f"# {title}")
        lines.append("")
    for section in document.get("sections") or []:
        heading = section.get("heading")
        if heading:
            level = max(1, min(int(section.get("headingLevel") or 2), 6))
            lines.append(f"{'#' * level} {heading}")
        body = str(section.get("bodyMarkdown") or "").strip()
        if body:
            lines.append(body)
        if heading or body:
            lines.append("")
    return "\n".join(lines).rstrip() + ("\n" if lines else "")


def _read_report_documents(paths, start_date: date, end_date: date) -> list[dict]:
    if not paths.db_path.exists():
        return []
    return read_diary_markdown_documents(paths, start_date, end_date)


def _learning_report_title(paths, date_str: str) -> str:
    try:
        settings = read_settings(paths)
        pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
        language_profile = str(pipeline.get("languageProfile") or "zh").lower()
    except Exception:
        language_profile = "zh"
    if language_profile.startswith("en"):
        return f"{date_str} Learning and Infrastructure Audit"
    return f"{date_str} 智慧沉淀"
