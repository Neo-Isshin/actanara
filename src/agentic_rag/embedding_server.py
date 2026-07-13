#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Agentic RAG Embedding + Search Server (v4.9 - Industrial Grade)
"""

import os
import sys
import json
import hmac
import time
import re
import math
import asyncio
import threading
from contextlib import asynccontextmanager
from typing import Any, List, Optional
from datetime import datetime, timezone, timedelta
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

import uvicorn
import numpy as np
from fastapi import FastAPI, HTTPException
try:
    from starlette.requests import Request
except Exception:  # pragma: no cover - lightweight unit-test dependency stub
    Request = Any  # type: ignore[misc,assignment]
try:
    from fastapi.responses import JSONResponse
except Exception:  # pragma: no cover - lightweight unit-test fastapi stub
    class JSONResponse(dict):
        def __init__(self, content=None, status_code=200):
            super().__init__(content or {})
            self.status_code = status_code
from pydantic import BaseModel, Field

# 引用全局配置
sys.path.insert(0, str(Path(__file__).parent))
import rag_config
from rag_active_source import resolve_active_rag_index
from query_embedding_provider import create_query_embedding_provider_from_config
from rag_retriever import build_query_plan, build_retrieval_passes, fuse_ranked_passes, rank_scored_chunks
from data_foundation.network import (
    RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
    is_loopback_host,
    require_loopback_host,
)

# ===================== 全局状态 =====================
embedding_provider = None
_emb_matrix_norm = None
_emb_ids = None
_emb_chunks = None
_emb_mtime = 0
_emb_index_path = None
_emb_lock = threading.Lock()
_inference_executor = ThreadPoolExecutor(max_workers=1)
_search_executor = ThreadPoolExecutor(max_workers=max(1, int(getattr(rag_config, "MAX_CONCURRENT_SEARCHES", 2))))
_search_semaphore = None
_batch_queue = None
MAX_EXTERNAL_TOP_K = 20
MAX_SERVER_SEARCH_BUDGET_SECONDS = 60.0


def _bounded_top_k(value) -> int:
    default = max(1, min(int(getattr(rag_config, "DEFAULT_TOP_K", 8) or 8), MAX_EXTERNAL_TOP_K))
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, MAX_EXTERNAL_TOP_K))

# ===================== 核心算法 =====================
def get_emb_matrix():
    global _emb_matrix_norm, _emb_ids, _emb_chunks, _emb_mtime, _emb_index_path
    index_state = _current_index_state()
    index_file = index_state["path"]
    if index_state["source"] != "v2" or not index_state["ready"] or not index_file or not index_file.exists():
        _emb_matrix_norm, _emb_ids, _emb_chunks, _emb_mtime, _emb_index_path = None, None, None, 0, index_file
        return None, None, None
    mtime = index_file.stat().st_mtime
    if _emb_matrix_norm is not None and _emb_mtime == mtime and _emb_index_path == index_file:
        return _emb_matrix_norm, _emb_ids, _emb_chunks
    with _emb_lock:
        index_state = _current_index_state()
        index_file = index_state["path"]
        if index_state["source"] != "v2" or not index_state["ready"] or not index_file or not index_file.exists():
            _emb_matrix_norm, _emb_ids, _emb_chunks, _emb_mtime, _emb_index_path = None, None, None, 0, index_file
            return None, None, None
        mtime = index_file.stat().st_mtime
        if _emb_matrix_norm is not None and _emb_mtime == mtime and _emb_index_path == index_file:
            return _emb_matrix_norm, _emb_ids, _emb_chunks
        ids, embs, chunks = [], [], []
        skipped_dimension = 0
        with open(index_file, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    embedding = d.get('embedding')
                    if embedding and len(embedding) == rag_config.EMBEDDING_DIM:
                        ids.append(d['id'])
                        embs.append(embedding)
                        d.pop("embedding", None)
                        chunks.append(d)
                    elif embedding:
                        skipped_dimension += 1
                except: pass
        if not embs:
            _emb_matrix_norm, _emb_ids, _emb_chunks, _emb_mtime, _emb_index_path = None, None, None, 0, index_file
            return None, None, None
        m = np.array(embs, dtype=np.float32)
        _emb_matrix_norm = m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)
        _emb_ids, _emb_chunks, _emb_mtime, _emb_index_path = ids, chunks, mtime, index_file
        if skipped_dimension:
            print(f"⚠️ Skipped {skipped_dimension} embeddings with mismatched dimensions.")
        return _emb_matrix_norm, _emb_ids, _emb_chunks


def _current_index_state():
    try:
        active = resolve_active_rag_index()
        return {
            "source": active.source,
            "path": active.index_path,
            "ready": active.ready,
            "reason": active.reason,
        }
    except Exception as exc:
        return {
            "source": "unavailable",
            "path": None,
            "ready": False,
            "reason": f"active-index-resolve-failed:{exc.__class__.__name__}",
        }


def _dense_scores_for_query(m_norm, query: str) -> tuple[object | None, dict | None]:
    q_emb = np.array(embedding_provider.encode_query(query), dtype=np.float32)
    return _dense_scores_for_embedding(m_norm, q_emb)


def _dense_scores_for_embedding(m_norm, query_embedding) -> tuple[object | None, dict | None]:
    q_emb = np.array(query_embedding, dtype=np.float32)
    if len(q_emb) != m_norm.shape[1]:
        return None, {
            "queryDimension": int(len(q_emb)),
            "indexDimension": int(m_norm.shape[1]),
        }
    q_norm = q_emb / (np.linalg.norm(q_emb) + 1e-12)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        cos_scores = m_norm @ q_norm
        cos_scores = np.nan_to_num(cos_scores, nan=0.0)
    return cos_scores, None


# ===================== Worker & Lifespan =====================

async def _batch_worker():
    print("👷 Worker active.")
    while True:
        try:
            item = await _batch_queue.get()
            batch = [item]
            start_wait = time.time()
            while len(batch) < 32 and (time.time() - start_wait) < 0.05:
                try:
                    item = _batch_queue.get_nowait(); batch.append(item)
                except asyncio.QueueEmpty:
                    await asyncio.sleep(0.01)

            # 推理 (显式参数)
            texts = [b[0] for b in batch]
            loop = asyncio.get_running_loop()
            # 使用 lambda 显式传参，防止 pos args 错位
            embeddings = await loop.run_in_executor(
                _inference_executor,
                lambda: embedding_provider.encode(texts, show_progress_bar=False)
            )

            for (t, future), emb in zip(batch, embeddings):
                if not future.done(): future.set_result(emb)
            for _ in range(len(batch)): _batch_queue.task_done()
        except Exception as e:
            print(f"❌ Worker Panic: {e}")
            await asyncio.sleep(1)

@asynccontextmanager
async def lifespan(app: FastAPI):
    global embedding_provider, _batch_queue, _search_semaphore
    require_loopback_host(getattr(rag_config, "SERVER_HOST", ""), field="NOVA_RAG_SERVER_HOST")
    print(f"🔄 Launching RAG...")
    _batch_queue = asyncio.Queue()
    _search_semaphore = asyncio.Semaphore(max(1, int(getattr(rag_config, "MAX_CONCURRENT_SEARCHES", 2))))
    asyncio.create_task(_batch_worker())

    device = _resolve_embedding_device()
    embedding_provider = create_query_embedding_provider_from_config(rag_config, device=device)
    print("🔄 Loading embedding provider...")
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(_inference_executor, embedding_provider.load)

    get_emb_matrix()
    print(f"✅ RAG Ready ({device})")
    yield
    print("💤 Shutdown.")

app = FastAPI(title="Agentic RAG Server v4.9", lifespan=lifespan)


async def enforce_loopback_client(request: Request, call_next):
    client_host = str(request.client.host if request.client else "")
    if not is_loopback_host(client_host):
        return JSONResponse(
            {
                "error": RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
                "message": "nova-RAG direct server accepts loopback clients only in macOS v1.",
            },
            status_code=403,
        )
    if str(request.url.path) == "/encode" and not _internal_encode_authorized(request):
        return JSONResponse(
            {
                "error": "rag-internal-authorization-required",
                "message": "The internal nova-RAG encode endpoint requires the managed Runtime token.",
            },
            status_code=403,
        )
    return await call_next(request)


if callable(getattr(app, "middleware", None)):
    app.middleware("http")(enforce_loopback_client)


def _resolve_embedding_device() -> str:
    configured = str(getattr(rag_config, "EMBEDDING_DEVICE", "auto") or "auto").strip()
    if configured and configured != "auto":
        return configured
    return "mps" if os.uname().sysname == "Darwin" else "cpu"


def _internal_encode_authorized(request: Request) -> bool:
    expected = _read_internal_token()
    supplied = str(request.headers.get("x-open-nova-rag-internal-token") or "")
    return bool(expected and supplied and hmac.compare_digest(expected, supplied))


def _read_internal_token() -> str:
    raw_path = str(os.getenv("NOVA_RAG_INTERNAL_TOKEN_FILE") or "").strip()
    if not raw_path:
        return ""
    path = Path(raw_path).expanduser()
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""

# ===================== API =====================

class EmbedRequest(BaseModel):
    texts: List[str]

@app.get("/health")
async def health():
    m_norm, ids, _chunks = get_emb_matrix()
    index_state = _current_index_state()
    index_file = index_state["path"]
    return {
        "status": "ok" if _embedding_provider_ready() else "booting",
        "model": rag_config.MODEL_NAME,
        "dimension": rag_config.EMBEDDING_DIM,
        "provider": rag_config.PRODUCTION_MODE,
        "providerId": getattr(rag_config, "PROVIDER_ID", rag_config.PRODUCTION_MODE),
        "embeddingProfile": getattr(rag_config, "EMBEDDING_PROFILE", {}),
        "embeddingProfileHash": getattr(rag_config, "EMBEDDING_PROFILE_HASH", None),
        "providerLoaded": _embedding_provider_ready(),
        "internalEncodeAuthorized": bool(_read_internal_token()),
        "indexPath": str(index_file) if index_file else None,
        "indexSource": index_state["source"],
        "indexReady": index_state["ready"],
        "indexUnavailableReason": index_state["reason"],
        "indexExists": bool(index_file and index_file.exists()),
        "indexLoaded": m_norm is not None,
        "entries": len(ids or []),
    }

@app.get("/stats")
async def stats():
    return stats_payload()


def stats_payload():
    m_norm, ids, chunks = get_emb_matrix()
    index_state = _current_index_state()
    index_file = index_state["path"]
    layer_counts = {}
    agent_counts = {}
    tag_counts = {}
    dates = []
    try:
        from rag_retriever import infer_tags

        for chunk in chunks or []:
            layer = chunk.get("layer") or "unknown"
            agent = chunk.get("agent") or "unknown"
            layer_counts[layer] = layer_counts.get(layer, 0) + 1
            agent_counts[agent] = agent_counts.get(agent, 0) + 1
            if chunk.get("date"):
                dates.append(str(chunk.get("date")))
            for tag in infer_tags(chunk):
                tag_counts[tag] = tag_counts.get(tag, 0) + 1
    except Exception:
        pass
    return {
        "status": "ready" if _embedding_provider_configured() and m_norm is not None else "booting",
        "server": {
            "title": app.title,
            "model": rag_config.MODEL_NAME,
            "dimension": rag_config.EMBEDDING_DIM,
            "provider": rag_config.PRODUCTION_MODE,
            "providerId": getattr(rag_config, "PROVIDER_ID", rag_config.PRODUCTION_MODE),
            "embeddingProfile": getattr(rag_config, "EMBEDDING_PROFILE", {}),
            "embeddingProfileHash": getattr(rag_config, "EMBEDDING_PROFILE_HASH", None),
            "providerLoaded": _embedding_provider_ready(),
        },
        "index": {
            "source": index_state["source"],
            "path": str(index_file) if index_file else None,
            "ready": index_state["ready"],
            "unavailableReason": index_state["reason"],
            "exists": bool(index_file and index_file.exists()),
            "loaded": m_norm is not None,
            "entries": len(ids or []),
            "layers": layer_counts,
            "agents": agent_counts,
            "tags": tag_counts,
            "dateRange": {
                "start": min(dates) if dates else None,
                "end": max(dates) if dates else None,
            },
        },
        "api": {
            "readOnly": True,
            "mutationAllowed": False,
            "endpoints": {
                "health": "GET /health",
                "stats": "GET /stats",
                "search": "POST /search",
            },
            "rejectedMutationStatus": 403,
        },
    }

@app.post("/encode")
async def encode(request: EmbedRequest):
    if not _embedding_provider_configured(): raise HTTPException(status_code=503, detail="Booting")
    results = []
    loop = asyncio.get_running_loop()
    for text in request.texts:
        future = loop.create_future()
        await _batch_queue.put((text, future))
        results.append(future)
    return await asyncio.gather(*results)

class SearchRequest(BaseModel):
    query: str
    top_k: int = Field(
        default=_bounded_top_k(getattr(rag_config, "DEFAULT_TOP_K", 8)),
        ge=1,
        le=MAX_EXTERNAL_TOP_K,
    )
    date: Optional[str] = None
    date_from: Optional[str] = None
    date_to: Optional[str] = None
    role: Optional[str] = None
    project: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    source_sets: List[str] = Field(default_factory=list)
    lifecycle: List[str] = Field(default_factory=list)
    work_type: List[str] = Field(default_factory=list)
    include_full_text: bool = True
    include_governance: bool = True
    latency_budget_ms: Optional[int] = None

@app.post("/search")
async def search(request: SearchRequest):
    started = time.monotonic()
    budget_seconds = _search_budget_seconds(request)
    deadline = started + budget_seconds
    if not _embedding_provider_configured():
        return JSONResponse(
            _external_search_response(request, available=False, reason="embedding-provider-booting", error="Booting"),
            status_code=503,
        )
    loop = asyncio.get_running_loop()
    semaphore = _search_semaphore
    permit_held = False
    if semaphore is not None:
        try:
            await asyncio.wait_for(
                semaphore.acquire(),
                timeout=max(0.001, min(0.25, deadline - time.monotonic())),
            )
            permit_held = True
        except asyncio.TimeoutError:
            payload = _external_search_response(
                request,
                available=False,
                reason="search-capacity-exhausted",
                error="busy",
            )
            _attach_worker_telemetry(
                payload,
                worker_state="not-started",
                started=started,
                budget_seconds=budget_seconds,
                capacity_permit_held=False,
            )
            return JSONResponse(payload, status_code=429)
    try:
        worker_future = loop.run_in_executor(_search_executor, perform_search, request)
    except BaseException:
        if permit_held:
            semaphore.release()
        raise
    # Shielding prevents coroutine cancellation from marking the asyncio Future
    # complete while its executor worker is still running. Capacity is returned
    # only by the real worker-completion callback.
    if permit_held:
        worker_future.add_done_callback(lambda _future: semaphore.release())
    remaining = max(0.001, deadline - time.monotonic())
    try:
        payload = await asyncio.wait_for(asyncio.shield(worker_future), timeout=remaining)
    except asyncio.TimeoutError:
        payload = _search_timeout_response(
            request,
            stage="local-worker",
            started=started,
            budget_seconds=budget_seconds,
        )
        _attach_worker_telemetry(
            payload,
            worker_state="running_after_timeout",
            started=started,
            budget_seconds=budget_seconds,
            capacity_permit_held=permit_held,
        )
        return JSONResponse(payload, status_code=503)
    except asyncio.CancelledError:
        payload = _external_search_response(
            request,
            available=False,
            reason="search-cancelled",
            error="cancelled",
        )
        _attach_worker_telemetry(
            payload,
            worker_state="running_after_cancel",
            started=started,
            budget_seconds=budget_seconds,
            capacity_permit_held=permit_held,
        )
        return JSONResponse(payload, status_code=503)
    if isinstance(payload, dict):
        _attach_worker_telemetry(
            payload,
            worker_state="finished",
            started=started,
            budget_seconds=budget_seconds,
            capacity_permit_held=False,
        )
    return payload


@app.post("/memory/write")
@app.post("/memories")
@app.post("/index/run")
@app.post("/index/rebuild")
async def reject_external_mutation():
    raise HTTPException(
        status_code=403,
        detail={
            "error": "rag-external-api-read-only",
            "message": "External RAG API is read-only; memory writes and index mutations are not allowed.",
        },
    )

def perform_search(request: SearchRequest):
    started = time.monotonic()
    budget_seconds = _search_budget_seconds(request)
    deadline = started + budget_seconds
    top_k = _bounded_top_k(request.top_k)
    m_norm, ids, chunks = get_emb_matrix()
    if m_norm is None:
        return _external_search_response(request, available=False, reason="active-v2-index-not-loaded")

    query_plan = build_query_plan(
        request.query,
        date_filter=request.date,
        date_from=request.date_from,
        date_to=request.date_to,
        role_filter=request.role,
        tag_filter=request.tags,
        project_filter=request.project,
        source_set_filter=request.source_sets,
        lifecycle_filter=request.lifecycle,
        work_type_filter=request.work_type,
    )
    passes = build_retrieval_passes(request.query, query_plan)
    if request.source_sets:
        passes = [item for item in passes if item.get("id") != "authoritative-source-pass"]
    pass_top_k = min(max(top_k * 4, 24), MAX_EXTERNAL_TOP_K * 4)
    ranked_passes = []
    pass_telemetry = []
    embedding_telemetry = []
    stop_reason = "passes-exhausted"
    optional_embeddings_batched = False

    # Baseline is deliberately evaluated first. Most queries should finish here
    # without paying for rewrites or authoritative-source recall.
    baseline = passes[0]
    baseline_started = time.monotonic()
    baseline_query = str(baseline.get("query") or request.query)
    embedding_started = time.monotonic()
    try:
        baseline_vectors = _encode_queries([baseline_query], timeout_seconds=max(deadline - time.monotonic(), 0.1))
    except TimeoutError:
        return _search_timeout_response(
            request,
            stage="baseline-embedding",
            started=started,
            budget_seconds=budget_seconds,
        )
    embedding_telemetry.append({
        "batch": "baseline",
        "queryCount": 1,
        "elapsedMs": round((time.monotonic() - embedding_started) * 1000, 3),
    })
    cos_scores, dimension_error = _dense_scores_for_embedding(m_norm, baseline_vectors[0] if baseline_vectors else [])
    if dimension_error:
        return _external_search_response(
            request, available=False, reason="dimension-mismatch", error="dimension-mismatch", extra=dimension_error
        )
    ranked_passes.append(_rank_retrieval_pass(request, baseline, baseline_query, cos_scores, chunks or [], pass_top_k))
    pass_telemetry.append(_pass_telemetry(baseline, baseline_started, ranked_passes[-1]))
    ranked = _fuse_search_passes(request, query_plan, ranked_passes, chunks or [])
    if (ranked.get("quality") or {}).get("status") == "strong":
        stop_reason = "quality-strong-after-baseline"
    elif time.monotonic() >= deadline:
        stop_reason = "latency-budget-exhausted-after-baseline"
    else:
        # One batch call supplies embeddings for all optional passes, avoiding
        # multiple cloud round trips. Passes are still scored/evaluated one by
        # one and stop as soon as the quality gate becomes strong.
        optional_passes = passes[1:]
        optional_queries = [str(item.get("query") or request.query) for item in optional_passes]
        embedding_started = time.monotonic()
        try:
            optional_vectors = _encode_queries(
                optional_queries, timeout_seconds=max(deadline - time.monotonic(), 0.1)
            )
        except TimeoutError:
            optional_vectors = []
            stop_reason = "optional-embedding-timeout"
            embedding_telemetry.append({
                "batch": "optional-passes",
                "queryCount": len(optional_queries),
                "elapsedMs": round((time.monotonic() - embedding_started) * 1000, 3),
                "status": "timeout",
            })
        else:
            embedding_telemetry.append({
                "batch": "optional-passes",
                "queryCount": len(optional_queries),
                "elapsedMs": round((time.monotonic() - embedding_started) * 1000, 3),
                "status": "ready",
            })
            optional_embeddings_batched = bool(optional_queries)
        for retrieval_pass, pass_query, vector in zip(optional_passes, optional_queries, optional_vectors):
            if time.monotonic() >= deadline:
                stop_reason = "latency-budget-exhausted"
                break
            pass_started = time.monotonic()
            cos_scores, dimension_error = _dense_scores_for_embedding(m_norm, vector)
            if dimension_error:
                return _external_search_response(
                    request,
                    available=False,
                    reason="dimension-mismatch",
                    error="dimension-mismatch",
                    extra=dimension_error,
                )
            ranked_pass = _rank_retrieval_pass(
                request, retrieval_pass, pass_query, cos_scores, chunks or [], pass_top_k
            )
            ranked_passes.append(ranked_pass)
            pass_telemetry.append(_pass_telemetry(retrieval_pass, pass_started, ranked_pass))
            ranked = _fuse_search_passes(request, query_plan, ranked_passes, chunks or [])
            if (ranked.get("quality") or {}).get("status") == "strong":
                stop_reason = f"quality-strong-after-{retrieval_pass.get('id')}"
                break

    controller = ranked.setdefault("retrievalController", {})
    controller["executionPolicy"] = "baseline-first-adaptive-bounded"
    controller["latencyBudgetMs"] = int(round(budget_seconds * 1000))
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    controller["elapsedMs"] = elapsed_ms
    controller["slowQuery"] = elapsed_ms > 30000
    controller["slowQueryThresholdMs"] = 30000
    controller["chunkCount"] = len(chunks or [])
    controller["stopReason"] = stop_reason
    controller["passTelemetry"] = pass_telemetry
    controller["embeddingTelemetry"] = embedding_telemetry
    controller["batchedOptionalEmbeddings"] = optional_embeddings_batched
    controller["embeddingDimension"] = int(m_norm.shape[1])
    if stop_reason == "optional-embedding-timeout":
        controller["degraded"] = True
        controller["timeoutStage"] = "optional-embedding"
    if not request.include_full_text or not request.include_governance:
        ranked = _shape_search_response(
            ranked,
            include_full_text=request.include_full_text,
            include_governance=request.include_governance,
        )
    return ranked


def _rank_retrieval_pass(request, retrieval_pass, pass_query, cos_scores, chunks, pass_top_k):
    pass_source_sets = list(request.source_sets or retrieval_pass.get("sourceSets") or [])
    ranked = rank_scored_chunks(
            query=pass_query,
            chunks=chunks or [],
            dense_scores=cos_scores if cos_scores is not None else [],
            top_k=pass_top_k,
            similarity_weight=rag_config.SIMILARITY_WEIGHT,
            keyword_weight=rag_config.KEYWORD_WEIGHT,
            recency_half_life_days=rag_config.TIME_DECAY_HALF_LIFE,
            date_filter=request.date,
            date_from=request.date_from,
            date_to=request.date_to,
            role_filter=request.role,
            tag_filter=request.tags,
            project_filter=request.project,
            source_set_filter=pass_source_sets,
            lifecycle_filter=request.lifecycle,
            work_type_filter=request.work_type,
            reranker_policy={"enabled": False, "provider": "none"},
            language_profile=getattr(rag_config, "LANGUAGE_PROFILE", "en"),
    )
    return {**retrieval_pass, "query": pass_query, "sourceSets": pass_source_sets, "ranked": ranked}


def _fuse_search_passes(request, query_plan, ranked_passes, chunks):
    return fuse_ranked_passes(
        query=request.query,
        query_plan=query_plan,
        ranked_passes=ranked_passes,
        total_indexed=len(chunks or []),
        top_k=_bounded_top_k(request.top_k),
        reranker_policy=rag_config.RERANKER_POLICY,
        language_profile=getattr(rag_config, "LANGUAGE_PROFILE", "en"),
    )


def _pass_telemetry(retrieval_pass, started, ranked_pass):
    ranked = ranked_pass.get("ranked") or {}
    return {
        "id": retrieval_pass.get("id"),
        "elapsedMs": round((time.monotonic() - started) * 1000, 3),
        "candidateCount": len(ranked.get("results") or []),
        "qualityStatus": (ranked.get("quality") or {}).get("status"),
    }


def _search_budget_seconds(request: SearchRequest) -> float:
    configured = max(float(getattr(rag_config, "SEARCH_LATENCY_BUDGET_SECONDS", 60)), 0.1)
    requested = float(request.latency_budget_ms or round(configured * 1000)) / 1000
    return max(0.1, min(requested, configured, MAX_SERVER_SEARCH_BUDGET_SECONDS))


def _attach_worker_telemetry(
    payload: dict,
    *,
    worker_state: str,
    started: float,
    budget_seconds: float,
    capacity_permit_held: bool,
) -> None:
    elapsed_ms = round((time.monotonic() - started) * 1000, 3)
    telemetry = {
        "workerState": worker_state,
        "hardCancelled": False,
        "capacityPermitHeld": bool(capacity_permit_held),
        "latencyBudgetMs": int(round(budget_seconds * 1000)),
        "elapsedMs": elapsed_ms,
    }
    payload["workerTelemetry"] = telemetry
    controller = payload.setdefault("retrievalController", {})
    controller["workerTelemetry"] = dict(telemetry)


def _search_timeout_response(
    request: SearchRequest,
    *,
    stage: str,
    started: float,
    budget_seconds: float,
) -> dict:
    payload = _external_search_response(
        request,
        available=False,
        reason="search-timeout",
        error="timeout",
    )
    controller = payload["retrievalController"]
    controller.update({
        "executionPolicy": "baseline-first-adaptive-bounded",
        "latencyBudgetMs": int(round(budget_seconds * 1000)),
        "elapsedMs": round((time.monotonic() - started) * 1000, 3),
        "stopReason": f"{stage}-timeout",
        "timeoutStage": stage,
        "degraded": True,
    })
    return payload


def _encode_queries(queries: list[str], *, timeout_seconds: float | None = None) -> list[list[float]]:
    if not queries:
        return []
    encode_many = getattr(embedding_provider, "encode", None)
    if callable(encode_many):
        try:
            return encode_many(queries, show_progress_bar=False, timeout_seconds=timeout_seconds)
        except TypeError as exc:
            if "timeout_seconds" not in str(exc):
                raise
            return encode_many(queries, show_progress_bar=False)
    vectors = []
    for query in queries:
        try:
            vectors.append(embedding_provider.encode_query(query, timeout_seconds=timeout_seconds))
        except TypeError as exc:
            if "timeout_seconds" not in str(exc):
                raise
            vectors.append(embedding_provider.encode_query(query))
    return vectors


def _external_search_response(
    request: SearchRequest,
    *,
    available: bool,
    reason: str,
    error: str | None = None,
    extra: dict | None = None,
) -> dict:
    query = str(request.query or "")
    top_k = _bounded_top_k(request.top_k)
    payload = {
        "schemaVersion": 2,
        "available": available,
        "reason": reason,
        "results": [],
        "query": query,
        "topK": top_k,
        "queryPlan": {
            "schemaVersion": 2,
            "query": query,
            "topK": top_k,
            "stages": [],
            "subQueries": [query] if query.strip() else [],
            "explicitFilters": {},
            "status": "unavailable" if not available else "ready",
        },
        "citationPack": [],
        "eventAggregation": {
            "schemaVersion": 2,
            "status": "unavailable" if not available else "no-events",
            "eventCount": 0,
            "events": [],
            "timeline": [],
            "mostSevereEvent": None,
            "resolutionCitations": [],
            "reason": reason,
        },
        "answerSynthesis": {
            "status": "unavailable" if not available else "no-results",
            "method": "extractive",
            "summary": "",
            "citationIds": [],
            "reason": reason,
        },
        "quality": {
            "schemaVersion": 1,
            "status": "insufficient" if not available else "weak",
            "needsMoreEvidence": True,
            "resultCount": 0,
            "keyTerms": [],
            "coveredTerms": [],
            "missingTerms": [],
            "coverage": 0.0,
            "flags": {},
            "recommendations": ["retry-when-rag-available"] if not available else ["expand-query-or-increase-top-k"],
        },
        "retrievalController": {
            "schemaVersion": 1,
            "mode": "bounded-deterministic-multi-pass",
            "serverSide": True,
            "executionPolicy": "unavailable before retrieval",
            "passesRun": ["quality-gate"],
            "passes": [
                {
                    "id": "quality-gate",
                    "status": "insufficient" if not available else "weak",
                    "needsMoreEvidence": True,
                }
            ],
            "qualityStatus": "insufficient" if not available else "weak",
            "needsMoreEvidence": True,
        },
        "agentic": {
            "schemaVersion": 2,
            "evidenceFieldsStable": True,
            "serverSidePlanning": True,
            "serverSideMultiPass": True,
            "serverSideQualityGate": True,
            "serverSideEventAggregation": True,
            "llmGenerated": False,
        },
    }
    if error:
        payload["error"] = error
    if extra:
        payload.update(extra)
    return payload


def _shape_search_response(ranked: dict, *, include_full_text: bool, include_governance: bool) -> dict:
    shaped = dict(ranked)
    results = []
    for item in ranked.get("results") or []:
        result = dict(item)
        if not include_full_text:
            result.pop("text", None)
        if not include_governance:
            result.pop("governance", None)
        results.append(result)
    shaped["results"] = results
    return shaped


def _embedding_provider_ready() -> bool:
    return embedding_provider is not None and embedding_provider.ready


def _embedding_provider_configured() -> bool:
    return embedding_provider is not None

if __name__ == "__main__":
    server_host = require_loopback_host(rag_config.SERVER_HOST, field="NOVA_RAG_SERVER_HOST")
    uvicorn.run(app, host=server_host, port=rag_config.SERVER_PORT, log_level="info")
