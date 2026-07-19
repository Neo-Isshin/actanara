"""Production nova-RAG v2 sync entrypoint.

Builds a v2 candidate index and validates deterministic completeness gates.
Passing production candidates are promoted to the active nova-RAG snapshot by
default; explicit candidate-only callers can pass promote=False.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

try:
    from .rag_settings import RagSettings, effective_indexing_source_sets, rag_product_disabled_reason, resolve_rag_settings
    from .rag_server_lifecycle import read_rag_internal_token, read_server_process_state
    from .rag_v2_indexer import EmbeddingFn, build_v2_candidate_index
    from .rag_v2_promote import promote_v2_candidate, required_v2_promotion_confirmation
    from .rag_v2_store import RagV2OperationLockError, rag_v2_operation_lock, rag_v2_operation_lock_path
except ImportError:  # pragma: no cover - direct script fallback
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from agentic_rag.rag_settings import RagSettings, effective_indexing_source_sets, rag_product_disabled_reason, resolve_rag_settings  # type: ignore
    from agentic_rag.rag_server_lifecycle import read_rag_internal_token, read_server_process_state  # type: ignore
    from agentic_rag.rag_v2_indexer import EmbeddingFn, build_v2_candidate_index  # type: ignore
    from agentic_rag.rag_v2_promote import promote_v2_candidate, required_v2_promotion_confirmation  # type: ignore
    from agentic_rag.rag_v2_store import RagV2OperationLockError, rag_v2_operation_lock, rag_v2_operation_lock_path  # type: ignore

from data_foundation.network import (
    RAG_INTERNAL_AUTHORIZATION_ISSUE_CODE,
    RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
    host_for_url,
    is_loopback_host,
    require_loopback_host,
)


def sync_v2_production_index(
    settings: RagSettings | None = None,
    *,
    requested_by: str = "production-pipeline",
    embedding_fn: EmbeddingFn | None = None,
    promote: bool = True,
    server_wait_timeout_seconds: float = 10.0,
) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    disabled_reason = rag_product_disabled_reason(settings=resolved)
    if disabled_reason:
        return {
            "status": "skipped",
            "reason": disabled_reason,
            "build": None,
            "gates": {
                "status": "skipped",
                "checks": [],
                "failed": [],
            },
            "promotion": None,
            "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        }
    if not resolved.indexing_enabled:
        return {
            "status": "skipped",
            "reason": "nova-RAG indexing is disabled by settings.",
            "build": None,
            "gates": {
                "status": "skipped",
                "checks": [],
                "failed": [],
            },
            "promotion": None,
            "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        }
    if not is_loopback_host(resolved.server_host):
        return {
            "status": "blocked",
            "reason": RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
            "build": None,
            "gates": {"status": "blocked", "checks": [], "failed": []},
            "promotion": None,
            "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        }
    try:
        with rag_v2_operation_lock(resolved, operation="sync-promote" if promote else "sync-candidate") as lock:
            return _sync_v2_production_index_locked(
                resolved,
                requested_by=requested_by,
                embedding_fn=embedding_fn,
                promote=promote,
                server_wait_timeout_seconds=server_wait_timeout_seconds,
                lock=lock,
            )
    except RagV2OperationLockError as error:
        return {
            "status": "blocked",
            "reason": str(error),
            "build": None,
            "gates": {
                "status": "blocked",
                "checks": [],
                "failed": [],
            },
            "promotion": None,
            "singleFlight": {
                "locked": True,
                "lockPath": str(error.lock_path),
                "operation": error.operation,
            },
            "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        }


def _sync_v2_production_index_locked(
    resolved: RagSettings,
    *,
    requested_by: str,
    embedding_fn: EmbeddingFn | None,
    promote: bool,
    server_wait_timeout_seconds: float,
    lock: dict[str, Any],
) -> dict[str, Any]:
    selected_embedding_fn = embedding_fn
    embedding_source = "injected" if embedding_fn is not None else "server"
    server_lifecycle = None
    if selected_embedding_fn is None:
        server_lifecycle = _wait_for_server_ready(resolved, timeout_seconds=server_wait_timeout_seconds)
        health = server_lifecycle.get("health") if isinstance(server_lifecycle, dict) else None
        if not (isinstance(health, dict) and health.get("healthy")):
            return {
                "status": "blocked",
                "reason": "nova-RAG server is not ready for candidate indexing.",
                "build": None,
                "gates": {
                    "status": "blocked",
                    "checks": [],
                    "failed": [],
                },
                "promotion": None,
                "serverLifecycle": server_lifecycle,
                "embeddingSource": embedding_source,
                "singleFlight": {"locked": False, **lock},
                "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
            }
        mismatch_reason = _server_profile_mismatch_reason(resolved, health)
        if mismatch_reason:
            return {
                "status": "blocked",
                "reason": mismatch_reason,
                "build": None,
                "gates": {
                    "status": "blocked",
                    "checks": [],
                    "failed": [],
                },
                "promotion": None,
                "serverLifecycle": server_lifecycle,
                "embeddingSource": embedding_source,
                "singleFlight": {"locked": False, **lock},
                "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
            }
        try:
            selected_embedding_fn = _server_embedding_fn(resolved)
        except RuntimeError:
            return {
                "status": "blocked",
                "reason": RAG_INTERNAL_AUTHORIZATION_ISSUE_CODE,
                "build": None,
                "gates": {"status": "blocked", "checks": [], "failed": []},
                "promotion": None,
                "serverLifecycle": server_lifecycle,
                "embeddingSource": embedding_source,
                "singleFlight": {"locked": False, **lock},
                "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
            }
    try:
        build = build_v2_candidate_index(
            resolved,
            requested_by=requested_by,
            embedding_fn=selected_embedding_fn,
        )
    except ModuleNotFoundError as error:
        if error.name != "sentence_transformers":
            raise
        return {
            "status": "skipped",
            "reason": "missing-local-embedding-dependency",
            "missingModule": error.name,
            "build": None,
            "gates": {
                "status": "skipped",
                "checks": [],
                "failed": [],
            },
            "promotion": None,
            "serverLifecycle": server_lifecycle,
            "embeddingSource": embedding_source,
            "singleFlight": {"locked": False, **lock},
            "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        }
    gates = _candidate_gates(build, resolved)
    if gates["status"] != "passed":
        return {
            "status": "blocked",
            "build": build,
            "gates": gates,
            "promotion": None,
            "serverLifecycle": server_lifecycle,
            "embeddingSource": embedding_source,
            "singleFlight": {"locked": False, **lock},
            "mutationPolicy": _mutation_policy(promote_attempted=False),
        }
    if not promote:
        return {
            "status": "candidate-ready",
            "build": build,
            "gates": gates,
            "promotion": None,
            "serverLifecycle": server_lifecycle,
            "embeddingSource": embedding_source,
            "singleFlight": {"locked": False, **lock},
            "mutationPolicy": _mutation_policy(promote_attempted=False),
        }

    run_id = str(build["run"]["runId"])
    confirmation = required_v2_promotion_confirmation(run_id)
    promotion = promote_v2_candidate(
        resolved,
        run_id=run_id,
        confirm=True,
        confirmation_text=confirmation,
        requested_by=requested_by,
        reason="production v2 RAG sync",
        acquire_lock=False,
    )
    return {
        "status": "promoted",
        "build": build,
        "gates": gates,
        "promotion": promotion,
        "serverLifecycle": server_lifecycle,
        "embeddingSource": embedding_source,
        "singleFlight": {"locked": False, **lock},
        "mutationPolicy": _mutation_policy(promote_attempted=True),
    }


def plan_v2_production_sync(
    settings: RagSettings | None = None,
    *,
    action: str = "rag-sync",
    requested_by: str = "production-pipeline",
    promote: bool = True,
    confirmation_text: str | None = None,
    server_wait_timeout_seconds: float = 10.0,
    probe_server: bool = True,
) -> dict[str, Any]:
    """Return the read-only execution plan for the production v2 sync path."""
    resolved = settings or resolve_rag_settings()
    disabled_reason = rag_product_disabled_reason(settings=resolved)
    blockers: list[dict[str, Any]] = []
    if disabled_reason:
        blockers.append({"code": "rag-disabled", "reason": disabled_reason})
    if not resolved.indexing_enabled:
        blockers.append({"code": "indexing-disabled", "reason": "nova-RAG indexing is disabled by settings."})
    if not is_loopback_host(resolved.server_host):
        blockers.append(
            {
                "code": RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
                "reason": "nova-RAG sync refuses non-loopback server settings in macOS v1.",
            }
        )

    server_lifecycle = None
    server_health = None
    if probe_server and not blockers:
        server_lifecycle = read_server_process_state(resolved, probe_health=True, timeout_seconds=2.0)
        server_health = server_lifecycle.get("health") if isinstance(server_lifecycle, dict) else None
        if not (isinstance(server_health, dict) and server_health.get("healthy")):
            blockers.append({"code": "server-not-ready", "reason": "nova-RAG server is not ready for candidate indexing."})
        else:
            mismatch_reason = _server_profile_mismatch_reason(resolved, server_health)
            if mismatch_reason:
                blockers.append({"code": "server-profile-mismatch", "reason": mismatch_reason})
            elif not read_rag_internal_token(resolved):
                blockers.append(
                    {
                        "code": RAG_INTERNAL_AUTHORIZATION_ISSUE_CODE,
                        "reason": "Managed nova-RAG internal encode token is unavailable.",
                    }
                )

    return {
        "schemaVersion": 1,
        "action": action,
        "dryRun": True,
        "status": "plan",
        "canExecute": not blockers,
        "reason": _plan_reason(promote=promote),
        "requestedBy": requested_by,
        "confirmationTextRequired": confirmation_text,
        "backend": "agentic_rag.rag_v2_sync.sync_v2_production_index",
        "executionModel": "candidate-build-validate-promote" if promote else "candidate-build-validate",
        "plannedCall": {
            "requestedBy": requested_by,
            "promote": promote,
            "serverWaitTimeoutSeconds": server_wait_timeout_seconds,
            "embeddingSource": "server",
        },
        "indexing": {
            "buildScope": "full-candidate-snapshot",
            "embeddingReuse": "active-embedding-reuse",
            "sourceSets": list(effective_indexing_source_sets(resolved)),
        },
        "settings": {
            "enabled": resolved.enabled,
            "mode": resolved.mode,
            "indexingEnabled": resolved.indexing_enabled,
            "v2StorePath": str(resolved.v2_store_path),
            "embeddingProvider": resolved.embedding_provider,
            "embeddingModel": resolved.embedding_model,
            "embeddingDimension": resolved.embedding_dimension,
        },
        "server": {
            "probeHealth": bool(probe_server),
            "enabled": resolved.server_enabled,
            "url": f"http://{host_for_url(resolved.server_host)}:{resolved.server_port}{resolved.server_health_path}",
            "lifecycle": server_lifecycle,
        },
        "singleFlight": {
            "lockPath": str(rag_v2_operation_lock_path(resolved)),
            "blocking": False,
        },
        "blockers": blockers,
        "mutationPolicy": _mutation_policy(promote_attempted=False, candidate_built=False),
        "wouldMutateOnConfirm": {
            "candidateBuilt": not blockers,
            "activeSnapshotPromoted": promote and not blockers,
            "legacyMutated": False,
            "settingsMutated": False,
        },
    }


def _plan_reason(*, promote: bool) -> str:
    if promote:
        return (
            "Build a nova-RAG v2 candidate snapshot, reuse active embeddings for unchanged chunks when possible, "
            "validate deterministic gates, then promote the passing candidate to active."
        )
    return (
        "Build a nova-RAG v2 candidate snapshot, reuse active embeddings for unchanged chunks when possible, "
        "and validate deterministic gates without promoting."
    )


def _candidate_gates(build: dict[str, Any], settings: RagSettings) -> dict[str, Any]:
    manifest = build.get("manifest") if isinstance(build.get("manifest"), dict) else {}
    expected_source_sets = set(effective_indexing_source_sets(settings))
    actual_source_sets = {str(item) for item in manifest.get("sourceSets", [])}
    checks = [
        _check("candidate-ready", manifest.get("status") == "ready", {"status": manifest.get("status")}),
        _check(
            "all-chunks-embedded",
            int(manifest.get("chunkCount") or 0) == int(manifest.get("embeddingCount") or 0),
            {"chunkCount": manifest.get("chunkCount"), "embeddingCount": manifest.get("embeddingCount")},
        ),
        _check(
            "no-dimension-mismatch",
            int(manifest.get("dimensionMismatchCount") or 0) == 0,
            {"dimensionMismatchCount": manifest.get("dimensionMismatchCount")},
        ),
        _check(
            "configured-source-sets-present",
            expected_source_sets.issubset(actual_source_sets),
            {
                "expected": sorted(expected_source_sets),
                "actual": sorted(actual_source_sets),
                "missing": sorted(expected_source_sets - actual_source_sets),
            },
        ),
    ]
    failed = [item for item in checks if not item["passed"]]
    return {
        "status": "failed" if failed else "passed",
        "checks": checks,
        "failed": failed,
    }


def _check(name: str, passed: bool, data: dict[str, Any]) -> dict[str, Any]:
    return {"name": name, "passed": bool(passed), "data": data}


def _server_embedding_fn(settings: RagSettings, *, timeout_seconds: float = 600.0) -> EmbeddingFn:
    server_host = require_loopback_host(settings.server_host)
    internal_credential = read_rag_internal_token(settings)
    if not internal_credential:
        raise RuntimeError("nova-RAG internal encode authorization token is unavailable")
    url = f"http://{host_for_url(server_host)}:{settings.server_port}/encode"

    def embed(texts: list[str]) -> list[list[float]]:
        payload = json.dumps({"texts": [text if str(text).strip() else "empty" for text in texts]}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            url,
            data=payload,
            headers={
                "Content-Type": "application/json",
                "X-Actanara-RAG-Internal-Token": internal_credential,
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=timeout_seconds) as response:
            result = json.loads(response.read().decode("utf-8"))
        if not isinstance(result, list):
            raise RuntimeError("nova-RAG server /encode returned a non-list payload")
        return [_vector_to_float_list(vector) for vector in result]

    return embed


def _wait_for_server_ready(settings: RagSettings, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.0, float(timeout_seconds or 0.0))
    lifecycle = read_server_process_state(settings, probe_health=True, timeout_seconds=2.0)
    while not ((lifecycle.get("health") or {}).get("healthy")) and time.time() < deadline:
        time.sleep(0.5)
        lifecycle = read_server_process_state(settings, probe_health=True, timeout_seconds=2.0)
    return lifecycle


def _server_profile_mismatch_reason(settings: RagSettings, health: dict[str, Any]) -> str | None:
    payload = health.get("payload") if isinstance(health.get("payload"), dict) else None
    if not payload:
        return None
    model = str(payload.get("model") or "")
    if model and model != settings.embedding_model:
        return f"nova-RAG server model mismatch: server={model}, target={settings.embedding_model}"
    try:
        dimension = int(payload.get("dimension") or 0)
    except (TypeError, ValueError):
        dimension = 0
    if dimension and dimension != settings.embedding_dimension:
        return f"nova-RAG server dimension mismatch: server={dimension}, target={settings.embedding_dimension}"
    provider = str(payload.get("provider") or "")
    if provider and provider != settings.embedding_provider:
        return f"nova-RAG server provider mismatch: server={provider}, target={settings.embedding_provider}"
    return None


def _vector_to_float_list(value: Any) -> list[float]:
    if not isinstance(value, list):
        return []
    return [float(item) for item in value if isinstance(item, (int, float))]


def _mutation_policy(*, promote_attempted: bool, candidate_built: bool = True) -> dict[str, Any]:
    return {
        "legacyMutated": False,
        "legacyReadRequired": False,
        "candidateBuilt": candidate_built,
        "activeSnapshotPromoted": promote_attempted,
        "settingsMutated": False,
        "serverLifecycleChanged": False,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build, validate, and promote the production nova-RAG v2 index.")
    parser.add_argument(
        "--promote",
        action="store_true",
        help="Deprecated compatibility flag. Passing candidates are promoted by default.",
    )
    parser.add_argument(
        "--no-promote",
        action="store_true",
        help="Build and validate a candidate without promoting it.",
    )
    parser.add_argument("--requested-by", default="production-pipeline")
    parser.add_argument(
        "--server-wait-timeout-seconds",
        type=float,
        default=10.0,
        help="Seconds to wait for an already-running nova-RAG server health endpoint before skipping.",
    )
    args = parser.parse_args(argv)
    result = sync_v2_production_index(
        requested_by=args.requested_by,
        promote=not bool(args.no_promote),
        server_wait_timeout_seconds=args.server_wait_timeout_seconds,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if result.get("status") in {"promoted", "candidate-ready"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
