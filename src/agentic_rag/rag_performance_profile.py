"""Disposable release-candidate performance profiles for nova-RAG."""

from __future__ import annotations

import hashlib
import json
import resource
import sys
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any


CANDIDATE_85K_PROFILE = "candidate-85k"
CANDIDATE_85K_CHUNKS = 85_000
CANDIDATE_85K_BUDGET_SECONDS = 60.0
CANDIDATE_85K_TARGET_ID = "candidate-85k-anchor"
CANDIDATE_85K_QUERY = "candidate85k rare-anchor V1-A-029 SQLite WAL"


class _CandidateProfileProvider:
    ready = True

    def encode(self, queries, **_kwargs):
        return [[1.0, 0.0] for _query in queries]

    def encode_query(self, _query, **_kwargs):
        return [1.0, 0.0]


def run_candidate_85k_profile(work_dir: Path, *, chunk_count: int = CANDIDATE_85K_CHUNKS) -> dict[str, Any]:
    """Build and search an actual 85k-chunk disposable index.

    The exact cardinality is part of the named release profile. Smaller unit
    fixtures must use a different profile name and cannot satisfy this gate.
    """
    if int(chunk_count) != CANDIDATE_85K_CHUNKS:
        raise ValueError(f"{CANDIDATE_85K_PROFILE} requires exactly {CANDIDATE_85K_CHUNKS} real chunks")

    from . import embedding_server

    root = Path(work_dir).expanduser().absolute()
    root.mkdir(parents=True, exist_ok=True)
    index_path = root / "candidate-85k-index.jsonl"
    total_started = time.monotonic()
    peak_before = _peak_rss_mb()
    generation_started = time.monotonic()
    digest = hashlib.sha256()
    with index_path.open("wb") as handle:
        for index in range(CANDIDATE_85K_CHUNKS):
            is_target = index == CANDIDATE_85K_CHUNKS - 1
            row = {
                "id": CANDIDATE_85K_TARGET_ID if is_target else f"candidate-85k-{index:05d}",
                "text": (
                    "candidate85k rare-anchor V1-A-029 SQLite WAL rollback compatibility verified"
                    if is_target
                    else f"synthetic release performance distractor chunk {index}"
                ),
                "date": "2026-07-11",
                "layer": "technical" if is_target else "episodic",
                "sourceSet": "lessons" if is_target else "filtered-dialogue-daily",
                "workType": "lesson" if is_target else "general",
                "governance": {
                    "lifecycle": "canonical" if is_target else "episodic",
                    "authorityRank": 100 if is_target else 10,
                    "provenanceScore": 1.0 if is_target else 0.2,
                },
                "embedding": [1.0, 0.0] if is_target else [0.0, 1.0],
            }
            encoded = (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8")
            handle.write(encoded)
            digest.update(encoded)
    generation_seconds = time.monotonic() - generation_started

    originals = {
        "resolver": embedding_server.resolve_active_rag_index,
        "provider": embedding_server.embedding_provider,
        "dimension": embedding_server.rag_config.EMBEDDING_DIM,
        "matrix": embedding_server._emb_matrix_norm,
        "ids": embedding_server._emb_ids,
        "chunks": embedding_server._emb_chunks,
        "mtime": embedding_server._emb_mtime,
        "index_path": embedding_server._emb_index_path,
    }
    try:
        embedding_server.resolve_active_rag_index = lambda: SimpleNamespace(
            source="v2",
            index_path=index_path,
            ready=True,
            reason=None,
        )
        embedding_server.rag_config.EMBEDDING_DIM = 2
        embedding_server.embedding_provider = _CandidateProfileProvider()
        embedding_server._emb_matrix_norm = None
        embedding_server._emb_ids = None
        embedding_server._emb_chunks = None
        embedding_server._emb_mtime = 0
        embedding_server._emb_index_path = None

        load_started = time.monotonic()
        matrix, ids, chunks = embedding_server.get_emb_matrix()
        load_seconds = time.monotonic() - load_started
        actual_chunks = len(chunks or [])

        search_started = time.monotonic()
        result = embedding_server.perform_search(
            embedding_server.SearchRequest(
                query=CANDIDATE_85K_QUERY,
                top_k=5,
                latency_budget_ms=int(CANDIDATE_85K_BUDGET_SECONDS * 1000),
            )
        )
        search_seconds = time.monotonic() - search_started
    finally:
        embedding_server.resolve_active_rag_index = originals["resolver"]
        embedding_server.embedding_provider = originals["provider"]
        embedding_server.rag_config.EMBEDDING_DIM = originals["dimension"]
        embedding_server._emb_matrix_norm = originals["matrix"]
        embedding_server._emb_ids = originals["ids"]
        embedding_server._emb_chunks = originals["chunks"]
        embedding_server._emb_mtime = originals["mtime"]
        embedding_server._emb_index_path = originals["index_path"]

    results = list(result.get("results") or [])
    top_result_id = str((results[0] if results else {}).get("id") or "")
    quality_status = str((result.get("quality") or {}).get("status") or "unknown")
    timed_out = result.get("reason") == "search-timeout" or bool(
        (result.get("retrievalController") or {}).get("timeoutStage")
    )
    total_seconds = time.monotonic() - total_started
    peak_after = _peak_rss_mb()
    passed = bool(
        matrix is not None
        and len(ids or []) == CANDIDATE_85K_CHUNKS
        and actual_chunks == CANDIDATE_85K_CHUNKS
        and top_result_id == CANDIDATE_85K_TARGET_ID
        and quality_status == "strong"
        and not timed_out
        and search_seconds <= CANDIDATE_85K_BUDGET_SECONDS
    )
    return {
        "schemaVersion": 1,
        "profile": CANDIDATE_85K_PROFILE,
        "validationClass": "real-index-candidate",
        "expectedChunks": CANDIDATE_85K_CHUNKS,
        "actualChunks": actual_chunks,
        "indexBytes": index_path.stat().st_size,
        "indexSha256": digest.hexdigest(),
        "generationSeconds": round(generation_seconds, 3),
        "loadSeconds": round(load_seconds, 3),
        "searchSeconds": round(search_seconds, 3),
        "totalSeconds": round(total_seconds, 3),
        "searchBudgetSeconds": CANDIDATE_85K_BUDGET_SECONDS,
        "peakRssMB": round(peak_after, 3),
        "peakRssDeltaMB": round(max(0.0, peak_after - peak_before), 3),
        "timedOut": timed_out,
        "qualityStatus": quality_status,
        "topResultId": top_result_id or None,
        "resultCount": len(results),
        "passed": passed,
    }


def _peak_rss_mb() -> float:
    raw = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return raw / (1024.0 * 1024.0) if sys.platform == "darwin" else raw / 1024.0
