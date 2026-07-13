#!/usr/bin/env python3
"""Dashboard 动态服务 — FastAPI + SSE"""
import json
import logging
from pathlib import Path
import sys, os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))
import config
from datetime import datetime

from fastapi import FastAPI
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from app.routers import diary, tasks, metrics, ai_assets, settings, foundation_ops
from app.services import scheduler
from app.services.dashboard_security import (
    DASHBOARD_CSRF_COOKIE,
    DASHBOARD_CSRF_HEADER,
    DASHBOARD_SESSION_COOKIE,
    DashboardSessionStore,
    SAFE_METHODS,
    dashboard_security_config,
    is_host_allowed,
    is_origin_allowed,
    is_protected_path,
    is_session_exempt_path,
    request_uses_secure_cookie,
    set_dashboard_session_cookies,
    should_bootstrap_session,
)
from data_foundation.paths import load_paths
from data_foundation.settings import resolve_dashboard_settings

logging.basicConfig(level=logging.INFO, format="%(levelname)s:%(name)s:%(message)s")
logger = logging.getLogger("dashboard")

app = FastAPI(title="Open Nova Dashboard", version="3.5")
_security_sessions = DashboardSessionStore()
_security_config = dashboard_security_config()


def _apply_dashboard_security_headers(response):
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("Referrer-Policy", "same-origin")
    response.headers.setdefault("X-Frame-Options", "DENY")
    response.headers.setdefault("Content-Security-Policy", "frame-ancestors 'none'")
    return response


class DashboardSecurityMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        path = request.url.path
        method = request.method.upper()
        protected = is_protected_path(path)
        session_id = request.cookies.get(DASHBOARD_SESSION_COOKIE)

        if protected:
            origin = request.headers.get("origin")
            if origin and not is_origin_allowed(origin):
                return _apply_dashboard_security_headers(
                    JSONResponse({"error": "Origin not allowed"}, status_code=403)
                )
            if not origin and not is_host_allowed(request.headers.get("host")):
                return _apply_dashboard_security_headers(
                    JSONResponse({"error": "Host not allowed"}, status_code=403)
                )
            if not is_session_exempt_path(path):
                if not _security_sessions.validate(session_id):
                    return _apply_dashboard_security_headers(
                        JSONResponse({"error": "Dashboard session required"}, status_code=401)
                    )
                if method not in SAFE_METHODS and not _security_sessions.validate_csrf(
                    session_id,
                    request.headers.get(DASHBOARD_CSRF_HEADER),
                    request.cookies.get(DASHBOARD_CSRF_COOKIE),
                ):
                    return _apply_dashboard_security_headers(
                        JSONResponse({"error": "CSRF token required"}, status_code=403)
                    )

        response = await call_next(request)
        _apply_dashboard_security_headers(response)
        if should_bootstrap_session(path, method) and not _security_sessions.validate(session_id):
            new_session, csrf_value = _security_sessions.create()
            set_dashboard_session_cookies(
                response,
                new_session,
                csrf_value,
                secure=request_uses_secure_cookie(request.headers, request.url.scheme),
            )
        return response

app.add_middleware(DashboardSecurityMiddleware)
app.add_middleware(
    CORSMiddleware,
    allow_origins=_security_config["allowedOrigins"],
    allow_credentials=True,
    allow_methods=["GET", "HEAD", "OPTIONS", "POST", "PUT", "PATCH", "DELETE"],
    allow_headers=["Content-Type", DASHBOARD_CSRF_HEADER],
    expose_headers=["X-Open-Nova-CSRF"],
)

APP_DIR = Path(__file__).parent
STATIC_DIR = APP_DIR / "static"
STATIC_DIR.mkdir(parents=True, exist_ok=True)
DIARY_DATA_DIR = APP_DIR / "diary-data"
DIARY_DATA_DIR.mkdir(parents=True, exist_ok=True)

# Include Routers
app.include_router(diary.router, prefix="/api", tags=["Diary"])
app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
app.include_router(metrics.router, prefix="/api", tags=["Metrics"])
app.include_router(ai_assets.router, prefix="/api", tags=["AI Assets"])
app.include_router(settings.router, prefix="/api", tags=["Settings"])
app.include_router(foundation_ops.router, prefix="/api", tags=["Foundation Ops"])
app.include_router(metrics.events_router, tags=["Events"])
app.include_router(ai_assets.events_router, tags=["Events"])

@app.get("/")
async def root():
    return RedirectResponse(url="/dashboard")

@app.get("/dashboard")
async def dashboard():
    return RedirectResponse(url="/static/index.html")

@app.get("/tasks")
async def tasks_page():
    return RedirectResponse(url="/static/tasks.html")

@app.get("/health")
async def health():
    return {"status": "ok", "time": datetime.now().strftime("%Y-%m-%d %H:%M:%S")}


# 静态文件强制 no-cache（防止浏览器缓存旧版）
class NoCacheStaticFiles(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/static") or request.url.path.startswith("/diary-data"):
            response.headers["Cache-Control"] = "no-cache, no-store, must-revalidate"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
        return response

app.add_middleware(NoCacheStaticFiles)

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")
app.mount("/diary-data", StaticFiles(directory=str(DIARY_DATA_DIR)), name="diary-data")

@app.on_event("startup")
async def startup_event():
    scheduler.start_scheduler_loop()
    try:
        dashboard_settings = resolve_dashboard_settings(load_paths())
        logger.info("Dashboard server started on %s", dashboard_settings["url"])
    except Exception:
        logger.info("Dashboard server started")


@app.on_event("shutdown")
async def shutdown_event():
    await scheduler.stop_scheduler_loop()
