"""Background job records for RAG v2 candidate refresh."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from agentic_rag.rag_settings import is_rag_product_enabled, rag_product_disabled_reason, resolve_rag_settings
from agentic_rag.rag_profile import settings_embedding_profile
from agentic_rag.rag_server_lifecycle import start_rag_server
from agentic_rag.rag_v2_promote import promote_v2_candidate, required_v2_promotion_confirmation
from agentic_rag.rag_v2_sync import sync_v2_production_index
from data_foundation.paths import load_paths
from data_foundation.settings import write_settings


RAG_PROFILE_MIGRATION_CONFIRMATION = "MIGRATE RAG PROFILE"
RAG_PROFILE_INITIALIZATION_CONFIRMATION = "INITIALIZE OPEN NOVA RAG"
PROJECT_ROOT = Path(__file__).resolve().parents[4]
LOCAL_RAG_DEPENDENCY_TIMEOUT_SECONDS = 1800


def plan_profile_migration(payload: dict[str, Any], *, requested_by: str = "dashboard") -> dict[str, Any]:
    request = payload if isinstance(payload, dict) else {}
    init_mode = bool(request.get("initMode") or request.get("initialize"))
    settings = resolve_rag_settings()
    target = _target_profile(request)
    enabled = is_rag_product_enabled(settings)
    auto_promote = bool(request.get("autoPromote"))
    return {
        "accepted": True,
        "status": "planned",
        "requestedBy": requested_by,
        "checkedAt": _now_iso(),
        "initMode": init_mode,
        "currentProfile": settings_embedding_profile(settings),
        "targetProfile": target,
        "sourceSets": list(settings.indexing_source_sets),
        "productEnabled": enabled,
        "disabledReason": None if enabled else (rag_product_disabled_reason(settings) or "nova-RAG subsystem is disabled by settings."),
        "autoPromote": auto_promote,
        "confirmationTextRequired": (
            RAG_PROFILE_INITIALIZATION_CONFIRMATION if init_mode else RAG_PROFILE_MIGRATION_CONFIRMATION
        ),
        "steps": _profile_migration_steps(
            init_mode=init_mode,
            auto_promote=auto_promote,
            embedding_mode=str(target.get("mode") or ""),
        ),
        "sideEffects": _profile_migration_side_effects(
            init_mode=init_mode,
            auto_promote=auto_promote,
            embedding_mode=str(target.get("mode") or ""),
        ),
        "risk": _profile_migration_risk(auto_promote=auto_promote, settings_mutated=init_mode),
    }


def queue_candidate_refresh(*, requested_by: str = "dashboard") -> dict[str, Any]:
    settings = resolve_rag_settings()
    if not is_rag_product_enabled(settings):
        return {
            "accepted": False,
            "status": "rag-disabled",
            "reason": rag_product_disabled_reason(settings) or "nova-RAG subsystem is disabled by settings.",
        }
    job_id = f"rag-index-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}-{uuid4().hex[:8]}"
    record = {
        "id": job_id,
        "type": "rag-v2-candidate-refresh",
        "status": "queued",
        "progress": 5,
        "requestedBy": requested_by,
        "requestedAt": _now_iso(),
        "sourceSets": list(settings.indexing_source_sets),
        "embeddingProvider": settings.embedding_provider,
        "providerId": settings.embedding_provider_id,
    }
    _append_record(record)
    return {"accepted": True, "status": "queued", "job": record, "jobId": job_id}


def queue_production_sync(*, requested_by: str = "dashboard") -> dict[str, Any]:
    settings = resolve_rag_settings()
    if not is_rag_product_enabled(settings):
        return {
            "accepted": False,
            "status": "rag-disabled",
            "reason": rag_product_disabled_reason(settings) or "nova-RAG subsystem is disabled by settings.",
        }
    job_id = f"rag-sync-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}-{uuid4().hex[:8]}"
    record = {
        "id": job_id,
        "type": "rag-v2-production-sync",
        "status": "queued",
        "progress": 5,
        "requestedBy": requested_by,
        "requestedAt": _now_iso(),
        "sourceSets": list(settings.indexing_source_sets),
        "embeddingProvider": settings.embedding_provider,
        "providerId": settings.embedding_provider_id,
        "promotionRequired": False,
    }
    _append_record(record)
    return {"accepted": True, "status": "queued", "job": record, "jobId": job_id}


def queue_profile_migration(payload: dict[str, Any], *, requested_by: str = "dashboard") -> dict[str, Any]:
    request = payload if isinstance(payload, dict) else {}
    init_mode = bool(request.get("initMode") or request.get("initialize"))
    required_confirmation = RAG_PROFILE_INITIALIZATION_CONFIRMATION if init_mode else RAG_PROFILE_MIGRATION_CONFIRMATION
    if str(request.get("confirmationText") or "") != required_confirmation:
        raise ValueError(f"confirmationText must be exactly: {required_confirmation}")
    settings = resolve_rag_settings()
    if not init_mode and not is_rag_product_enabled(settings):
        return {
            "accepted": False,
            "status": "rag-disabled",
            "reason": rag_product_disabled_reason(settings) or "nova-RAG subsystem is disabled by settings.",
        }
    target = _target_profile(request)
    job_id = f"rag-profile-migration-{datetime.now().astimezone().strftime('%Y%m%dT%H%M%S%z')}-{uuid4().hex[:8]}"
    record = {
        "id": job_id,
        "type": "rag-profile-migration",
        "status": "queued",
        "progress": 5,
        "requestedBy": requested_by,
        "requestedAt": _now_iso(),
        "sourceSets": list(settings.indexing_source_sets),
        "currentProfile": settings_embedding_profile(settings),
        "targetProfile": target,
        "risk": _profile_migration_risk(auto_promote=bool(request.get("autoPromote")), settings_mutated=init_mode),
        "initMode": init_mode,
        "autoPromote": bool(request.get("autoPromote")),
    }
    _append_record(record)
    return {"accepted": True, "status": "queued", "job": record, "jobId": job_id}


def execute_candidate_refresh(job_id: str) -> None:
    _append_record({"id": job_id, "status": "running", "progress": 45, "startedAt": _now_iso()})
    try:
        result = sync_v2_production_index(
            resolve_rag_settings(),
            requested_by="dashboard-background",
            promote=False,
            server_wait_timeout_seconds=600,
        )
    except Exception as exc:
        _append_record(
            {
                "id": job_id,
                "status": "failed",
                "progress": 100,
                "completedAt": _now_iso(),
                "errorSummary": str(exc),
            }
        )
        raise
    build = result.get("build") if isinstance(result.get("build"), dict) else {}
    manifest = _sync_manifest(result)
    _append_record(
        {
            "id": job_id,
            "status": result.get("status") or "completed",
            "progress": 100,
            "completedAt": _now_iso(),
            "reason": result.get("reason"),
            "embeddingSource": result.get("embeddingSource"),
            "serverLifecycle": result.get("serverLifecycle"),
            "candidateManifest": build.get("candidateManifest"),
            "candidatePath": build.get("candidatePath"),
            "chunkCount": manifest.get("chunkCount"),
            "embeddingCount": manifest.get("embeddingCount"),
            "skippedCount": manifest.get("skippedCount"),
        }
    )


def execute_production_sync(job_id: str) -> None:
    _append_record({"id": job_id, "status": "running", "progress": 45, "startedAt": _now_iso()})
    try:
        result = sync_v2_production_index(
            resolve_rag_settings(),
            requested_by="dashboard-production-sync",
            promote=True,
            server_wait_timeout_seconds=600,
        )
    except Exception as exc:
        _append_record(
            {
                "id": job_id,
                "status": "failed",
                "progress": 100,
                "completedAt": _now_iso(),
                "errorSummary": str(exc),
            }
        )
        raise
    build = result.get("build") if isinstance(result.get("build"), dict) else {}
    manifest = _sync_manifest(result)
    promotion = result.get("promotion") if isinstance(result.get("promotion"), dict) else {}
    _append_record(
        {
            "id": job_id,
            "status": result.get("status") or "completed",
            "progress": 100,
            "completedAt": _now_iso(),
            "reason": result.get("reason"),
            "embeddingSource": result.get("embeddingSource"),
            "serverLifecycle": result.get("serverLifecycle"),
            "candidateManifest": build.get("candidateManifest"),
            "candidatePath": build.get("candidatePath"),
            "chunkCount": manifest.get("chunkCount"),
            "embeddingCount": manifest.get("embeddingCount"),
            "skippedCount": manifest.get("skippedCount"),
            "promotion": promotion,
            "activeRunId": promotion.get("activeRunId"),
            "activeIndexPath": promotion.get("activeIndexPath"),
        }
    )


def execute_profile_migration(job_id: str) -> None:
    job = _find_job(job_id)
    target = job.get("targetProfile") if isinstance(job.get("targetProfile"), dict) else {}
    init_mode = bool(job.get("initMode"))
    _append_record({"id": job_id, "status": "running", "progress": 20, "startedAt": _now_iso()})
    try:
        current = resolve_rag_settings()
        target_settings = replace(
            current,
            embedding_provider=str(target.get("mode") or current.embedding_provider),
            embedding_provider_id=str(target.get("providerId") or target.get("mode") or current.embedding_provider_id),
            embedding_model=str(target.get("model") or current.embedding_model),
            embedding_dimension=int(target.get("dimension") or current.embedding_dimension),
            embedding_endpoint=str(target.get("endpoint") or current.embedding_endpoint),
            embedding_api_key_env=str(target.get("apiKeyEnv") or current.embedding_api_key_env),
            language_profile=str(target.get("languageProfile") or current.language_profile),
        )
        if init_mode:
            _write_initialized_rag_settings(target_settings)
            target_settings = resolve_rag_settings()
        if init_mode and target_settings.embedding_provider == "local":
            _ensure_local_rag_dependencies(job_id)
        if init_mode:
            server_start = start_rag_server(
                target_settings,
                requested_by="dashboard-profile-initialization",
                wait_timeout_seconds=2.0,
            )
            if not server_start.get("accepted"):
                raise RuntimeError(
                    "nova-RAG server could not start after initialization: "
                    f"{server_start.get('reason') or server_start.get('status') or 'unknown error'}"
                )
            _append_record(
                {
                    "id": job_id,
                    "status": "starting-server",
                    "serverStartStatus": server_start.get("status"),
                    "progress": 42,
                }
            )
        _append_record({"id": job_id, "status": "building-candidate", "progress": 45})
        result = sync_v2_production_index(
            target_settings,
            requested_by="dashboard-profile-migration",
            promote=False,
            server_wait_timeout_seconds=600,
        )
    except Exception as exc:
        _append_record(
            {
                "id": job_id,
                "status": "failed",
                "progress": 100,
                "completedAt": _now_iso(),
                "errorSummary": str(exc),
            }
        )
        raise
    build = result.get("build") if isinstance(result.get("build"), dict) else {}
    manifest = _sync_manifest(result)
    auto_promote = bool(job.get("autoPromote"))
    promotion_result: dict[str, Any] | None = None
    final_status = result.get("status") or "completed"
    if auto_promote and final_status == "candidate-ready":
        run_id = str(manifest.get("lastBuildRunId") or build.get("runId") or "").strip()
        if not run_id:
            candidate_manifest = str(build.get("candidateManifest") or "")
            if candidate_manifest:
                run_id = Path(candidate_manifest).parent.name
        if not run_id:
            raise RuntimeError("candidate runId is missing; cannot auto-promote nova-RAG candidate")
        promotion_result = promote_v2_candidate(
            resolve_rag_settings(),
            run_id=run_id,
            confirm=True,
            confirmation_text=required_v2_promotion_confirmation(run_id),
            requested_by="dashboard-profile-migration",
            reason="auto-promote after nova-RAG initialization candidate build",
        )
        final_status = "promoted"
    _append_record(
        {
            "id": job_id,
            "status": final_status,
            "progress": 100,
            "completedAt": _now_iso(),
            "reason": result.get("reason"),
            "embeddingSource": result.get("embeddingSource"),
            "serverLifecycle": result.get("serverLifecycle"),
            "candidateManifest": build.get("candidateManifest"),
            "candidatePath": build.get("candidatePath"),
            "chunkCount": manifest.get("chunkCount"),
            "embeddingCount": manifest.get("embeddingCount"),
            "skippedCount": manifest.get("skippedCount"),
            "targetProfile": target,
            "promotionRequired": not auto_promote,
            "promotion": promotion_result,
        }
    )


def _write_initialized_rag_settings(settings) -> None:
    embedding = {
        "mode": settings.embedding_provider,
        "provider": settings.embedding_provider,
        "providerId": settings.embedding_provider_id,
        "model": settings.embedding_model,
        "dimension": settings.embedding_dimension,
        "device": settings.embedding_device,
    }
    if settings.embedding_provider == "cloud":
        embedding.update(
            {
                "endpoint": settings.embedding_endpoint,
                "apiKeyEnv": settings.embedding_api_key_env,
            }
        )
    write_settings(
        {
            "features": {
                "rag": True,
                "embeddingServer": True,
            },
            "rag": {
                "enabled": True,
                "mode": "v2",
                "embedding": embedding,
                "server": {"enabled": True},
                "indexing": {"enabled": True, "defaultFullRebuild": False},
            },
        }
    )


def _ensure_local_rag_dependencies(job_id: str) -> None:
    from agentic_rag.rag_server_lifecycle import REQUIRED_SERVER_MODULES, _python_has_modules

    if _python_has_modules(sys.executable, required_modules=REQUIRED_SERVER_MODULES):
        _append_record({"id": job_id, "dependencyStatus": "ready", "progress": 35})
        return
    if not (PROJECT_ROOT / "pyproject.toml").is_file():
        raise RuntimeError(f"Open Nova runtime source is missing pyproject.toml: {PROJECT_ROOT}")
    log_path = load_paths().state_dir / "logs" / "rag-local-dependency-install.log"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    timeout = max(60, int(os.getenv("NOVA_RAG_DEPENDENCY_INSTALL_TIMEOUT_SECONDS", LOCAL_RAG_DEPENDENCY_TIMEOUT_SECONDS)))
    _append_record(
        {
            "id": job_id,
            "status": "installing-dependencies",
            "dependencyStatus": "installing",
            "dependencyLogPath": str(log_path),
            "progress": 30,
        }
    )
    with log_path.open("ab") as log_handle:
        result = subprocess.run(
            [sys.executable, "-m", "pip", "install", f"{PROJECT_ROOT}[rag-local]"],
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    if result.returncode != 0 or not _python_has_modules(sys.executable, required_modules=REQUIRED_SERVER_MODULES):
        _append_record({"id": job_id, "dependencyStatus": "failed", "progress": 40})
        raise RuntimeError(
            "nova-RAG local dependency installation failed; review "
            f"{log_path} and retry initialization from Dashboard"
        )
    _append_record({"id": job_id, "dependencyStatus": "installed", "progress": 40})


def list_candidate_refresh_jobs(*, limit: int = 20) -> list[dict[str, Any]]:
    records = _read_records()
    merged: dict[str, dict[str, Any]] = {}
    for record in records:
        job_id = str(record.get("id") or "")
        if not job_id:
            continue
        merged[job_id] = {**merged.get(job_id, {}), **record}
    jobs = sorted(
        merged.values(),
        key=lambda item: item.get("completedAt") or item.get("startedAt") or item.get("requestedAt") or "",
        reverse=True,
    )
    return jobs[: max(1, min(int(limit), 100))]


def _sync_manifest(result: dict[str, Any]) -> dict[str, Any]:
    build = result.get("build") if isinstance(result.get("build"), dict) else {}
    manifest = build.get("manifest") if isinstance(build.get("manifest"), dict) else None
    if manifest is None:
        manifest = result.get("manifest") if isinstance(result.get("manifest"), dict) else {}
    return manifest


def _append_record(record: dict[str, Any]) -> None:
    path = _jobs_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _read_records() -> list[dict[str, Any]]:
    path = _jobs_path()
    if not path.exists():
        return []
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as handle:
        for line in handle:
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            if isinstance(payload, dict):
                records.append(payload)
    return records


def _find_job(job_id: str) -> dict[str, Any]:
    merged: dict[str, Any] = {}
    for record in _read_records():
        if str(record.get("id") or "") == str(job_id):
            merged = {**merged, **record}
    if not merged:
        raise ValueError(f"unknown RAG job: {job_id}")
    return merged


def _target_profile(request: dict[str, Any]) -> dict[str, Any]:
    target = request.get("targetProfile") if isinstance(request.get("targetProfile"), dict) else request
    mode = str(target.get("mode") or "").strip()
    if mode not in {"local", "cloud"}:
        raise ValueError("targetProfile.mode must be local or cloud")
    model = str(target.get("model") or "").strip()
    if not model:
        raise ValueError("targetProfile.model is required")
    dimension = int(target.get("dimension") or 0)
    if dimension <= 0:
        raise ValueError("targetProfile.dimension must be positive")
    provider_id = str(target.get("providerId") or mode).strip()
    return {
        "mode": mode,
        "providerId": provider_id,
        "model": model,
        "dimension": dimension,
        "endpoint": str(target.get("endpoint") or "").strip(),
        "apiKeyEnv": str(target.get("apiKeyEnv") or "NOVA_RAG_CLOUD_API_KEY").strip(),
        "languageProfile": str(target.get("languageProfile") or "zh").strip() or "zh",
    }


def _profile_migration_steps(*, init_mode: bool, auto_promote: bool, embedding_mode: str = "") -> list[dict[str, Any]]:
    steps = []
    if init_mode:
        steps.extend(
            [
                {
                    "id": "write-rag-settings",
                    "label": "Write nova-RAG settings",
                    "mutates": ["runtime-settings"],
                },
            ]
        )
        if embedding_mode == "local":
            steps.append(
                {
                    "id": "ensure-rag-local-dependencies",
                    "label": "Install missing local nova-RAG dependencies",
                    "mutates": ["runtime-python-environment", "rag-dependency-log"],
                }
            )
        steps.append(
            {
                "id": "start-rag-server",
                "label": "Start nova-RAG search server",
                "mutates": ["rag-server-process"],
            }
        )
    steps.append(
        {
            "id": "build-candidate-index",
            "label": "Build v2 candidate index with target embedding profile",
            "mutates": ["rag-candidate-index", "rag-job-log"],
        }
    )
    if auto_promote:
        steps.append(
            {
                "id": "promote-active-index",
                "label": "Promote successful candidate to active index",
                "mutates": ["rag-active-index-manifest"],
            }
        )
    return steps


def _profile_migration_side_effects(*, init_mode: bool, auto_promote: bool, embedding_mode: str = "") -> list[str]:
    effects = ["rag-job-log-write", "rag-candidate-index-write"]
    if init_mode:
        effects.extend(["runtime-settings-write", "rag-server-start"])
        if embedding_mode == "local":
            effects.extend(["runtime-python-dependency-install", "rag-dependency-log-write"])
    if auto_promote:
        effects.append("rag-active-index-promotion")
    return effects


def _profile_migration_risk(*, auto_promote: bool, settings_mutated: bool) -> dict[str, Any]:
    return {
        "requiresCandidateRebuild": True,
        "activeIndexUnchangedUntilPromotion": not auto_promote,
        "settingsMutated": settings_mutated,
        "requiresServerReady": True,
        "estimatedCost": "nova-RAG server may download/load a local embedding model; cloud mode may incur provider API cost",
    }


def _jobs_path() -> Path:
    return load_paths().state_dir / "rag" / "candidate-refresh-jobs.jsonl"


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
