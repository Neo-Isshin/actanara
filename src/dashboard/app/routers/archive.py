"""Read-only HTTP routes for the Living Archive Dashboard."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Query
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool

from app.services import archive


logger = logging.getLogger(__name__)
router = APIRouter(prefix="/archive/v1")


def _query_error(error: Exception) -> JSONResponse:
    return JSONResponse(
        {"status": "error", "code": "invalid-query", "error": str(error)},
        status_code=400,
    )


def _unexpected(endpoint: str) -> JSONResponse:
    logger.exception("GET /api/archive/v1/%s failed", endpoint)
    return JSONResponse(
        {
            "status": "error",
            "code": "archive-unavailable",
            "error": "The Archive projection is unavailable.",
        },
        status_code=500,
    )


@router.get("/bootstrap")
async def api_archive_bootstrap():
    try:
        return await run_in_threadpool(archive.get_bootstrap)
    except Exception:
        return _unexpected("bootstrap")


@router.get("/assets")
async def api_archive_assets(
    asset_type: str | None = Query(default=None, alias="type"),
    status: str | None = None,
    business_date: str | None = Query(default=None, alias="businessDate"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await run_in_threadpool(
            archive.get_assets,
            asset_type=asset_type,
            status=status,
            business_date=business_date,
            limit=limit,
            offset=offset,
        )
    except archive.ArchiveQueryError as error:
        return _query_error(error)
    except Exception:
        return _unexpected("assets")


@router.get("/assets/{asset_id}")
async def api_archive_asset(asset_id: str):
    try:
        return await run_in_threadpool(archive.get_asset, asset_id)
    except archive.ArchiveAssetNotFound:
        return JSONResponse(
            {"status": "error", "code": "asset-not-found", "error": "Archive asset was not found."},
            status_code=404,
        )
    except Exception:
        return _unexpected("asset")


@router.get("/skill-pass/runs")
async def api_archive_skill_pass_runs(
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0),
):
    try:
        return await run_in_threadpool(archive.get_skill_pass_runs, limit=limit, offset=offset)
    except archive.ArchiveQueryError as error:
        return _query_error(error)
    except Exception:
        return _unexpected("skill-pass/runs")


@router.get("/activity")
async def api_archive_activity():
    try:
        return await run_in_threadpool(archive.get_activity)
    except Exception:
        return _unexpected("activity")
