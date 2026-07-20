"""Project registry status and operator-confirmed candidate workflow."""

from __future__ import annotations

import hashlib
import json
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any

from .db import connect
from .paths import RuntimePaths, load_paths

ATTRIBUTION_AUTHORITY = (
    "confirmed enabled registry roots matched only against structured session initial_cwd; "
    "text mentions and model output remain unattributed"
)


def project_registry_status(paths: RuntimePaths | None = None) -> dict[str, Any]:
    """Return read-only project registry health and DB comparison state."""
    selected = paths or load_paths()
    registry_path = selected.config_dir / "projects-registry.json"
    parse_status, data, error = _read_registry(registry_path)
    raw_projects = data.get("projects", []) if isinstance(data, dict) else []
    projects = [_project_payload(project) for project in raw_projects if isinstance(project, dict)]
    enabled = [project for project in projects if project["enabled"] and project["canonicalName"] and project["canonicalRoot"]]
    issues = _registry_issues(parse_status, projects, error)
    issues.extend(_overlap_issues(enabled))
    db_projects = _db_projects(selected)
    return {
        "actanaraHome": str(selected.home),
        "registryPath": str(registry_path),
        "exists": registry_path.exists(),
        "status": "ok" if not issues else "attention",
        "parseStatus": parse_status,
        "projects": projects,
        "enabledProjects": enabled,
        "counts": {
            "projects": len(projects),
            "enabledProjects": len(enabled),
            "candidates": len([candidate for candidate in data.get("candidates", []) if isinstance(candidate, dict)]),
            "dbProjects": len(db_projects),
        },
        "candidates": [candidate for candidate in data.get("candidates", []) if isinstance(candidate, dict)],
        "dbProjects": db_projects,
        "issues": issues,
        "authority": {
            "mode": "operator-confirmed",
            "attribution": ATTRIBUTION_AUTHORITY,
            "writesAllowed": "confirmation-required",
            "candidateCreationAllowed": "structured-cwd-only",
        },
    }


def discover_project_candidates(paths: RuntimePaths | None = None, *, limit: int = 50) -> list[dict[str, Any]]:
    """Discover pending project candidates from structured session cwd evidence."""
    selected = paths or load_paths()
    registry_path = selected.config_dir / "projects-registry.json"
    _, data, _ = _read_registry(registry_path)
    projects = [_project_payload(project) for project in data.get("projects", []) if isinstance(project, dict)]
    enabled_roots = [Path(project["expandedRoot"]) for project in projects if project["enabled"] and project["expandedRoot"]]
    existing_roots = {
        str(Path(candidate.get("proposed_canonical_root", "")).expanduser().absolute())
        for candidate in data.get("candidates", [])
        if isinstance(candidate, dict) and candidate.get("proposed_canonical_root")
    }
    grouped: dict[str, dict[str, Any]] = {}
    for row in _structured_cwd_rows(selected):
        raw = str(row.get("initial_cwd") or "")
        if not raw:
            continue
        cwd = Path(raw).expanduser().absolute()
        if _under_any(cwd, enabled_roots) or _looks_transient(cwd):
            continue
        root = _candidate_root(cwd)
        if _under_any(root, enabled_roots) or _looks_transient(root):
            continue
        root_key = str(root)
        entry = grouped.setdefault(
            root_key,
            {
                "candidate_id": _candidate_id(root_key),
                "proposed_canonical_name": root.name,
                "proposed_canonical_root": root_key,
                "status": "pending",
                "trust": "high",
                "source": "structured_cwd",
                "observation_count": 0,
                "tools": set(),
                "representative_paths": [],
                "alreadyInRegistry": root_key in existing_roots,
            },
        )
        entry["observation_count"] += int(row.get("session_count") or 0)
        for tool in str(row.get("tools") or "").split(","):
            if tool:
                entry["tools"].add(tool)
        if len(entry["representative_paths"]) < 5:
            entry["representative_paths"].append(raw)
    candidates = []
    for entry in grouped.values():
        entry["tools"] = sorted(entry["tools"])
        entry["suggestedAction"] = "review-existing-candidate" if entry["alreadyInRegistry"] else "confirm-or-reject"
        entry["evidence"] = [
            {
                "type": "structured_cwd",
                "trust": "high",
                "paths": entry["representative_paths"],
                "tools": entry["tools"],
                "observation_count": entry["observation_count"],
            }
        ]
        candidates.append(entry)
    candidates.sort(key=lambda item: (-item["observation_count"], item["proposed_canonical_root"]))
    return candidates[: max(1, min(int(limit), 200))]


def write_project_candidates(
    paths: RuntimePaths,
    candidates: list[dict[str, Any]],
    *,
    operator: str,
    reason: str = "operator candidate discovery",
) -> dict[str, Any]:
    data = _registry_data(paths)
    existing_ids = {
        str(candidate.get("candidate_id"))
        for candidate in data.setdefault("candidates", [])
        if isinstance(candidate, dict)
    }
    created = []
    now = _now_iso()
    for candidate in candidates:
        if candidate.get("candidate_id") in existing_ids or candidate.get("alreadyInRegistry"):
            continue
        record = {
            **candidate,
            "created_at": now,
            "created_by": operator,
            "status": "pending",
        }
        data["candidates"].append(record)
        created.append(record)
    if created:
        _audit(data, operator=operator, action="candidates-created", reason=reason, after={"candidate_ids": [c["candidate_id"] for c in created]})
        _write_registry(paths, data)
    return {"created": len(created), "candidates": created}


def confirm_project_candidate(
    paths: RuntimePaths,
    candidate_id: str,
    *,
    operator: str,
    confirmation: str,
    canonical_name: str | None = None,
    aliases: list[str] | None = None,
    reason: str = "",
) -> dict[str, Any]:
    if confirmation != f"confirm {candidate_id}":
        raise ValueError(f"confirmation must equal: confirm {candidate_id}")
    data = _registry_data(paths)
    candidate = _find_candidate(data, candidate_id)
    if candidate.get("status") != "pending":
        raise ValueError("candidate is not pending")
    project = {
        "canonical_name": canonical_name or candidate["proposed_canonical_name"],
        "canonical_root": candidate["proposed_canonical_root"],
        "enabled": True,
        "aliases": aliases or [],
        "status": "confirmed",
        "confirmed_at": _now_iso(),
        "confirmed_by": operator,
        "evidence": candidate.get("evidence", []),
        "notes": reason,
    }
    _ensure_no_confirmed_root_conflict(data, project["canonical_root"])
    data.setdefault("projects", []).append(project)
    candidate["status"] = "confirmed"
    candidate["confirmed_at"] = project["confirmed_at"]
    candidate["confirmed_by"] = operator
    _audit(data, operator=operator, action="candidate-confirmed", reason=reason, after={"candidate_id": candidate_id, "project": project})
    _write_registry(paths, data)
    return {"project": project, "candidate": candidate}


def reject_project_candidate(
    paths: RuntimePaths,
    candidate_id: str,
    *,
    operator: str,
    confirmation: str,
    reason: str,
) -> dict[str, Any]:
    if confirmation != f"reject {candidate_id}":
        raise ValueError(f"confirmation must equal: reject {candidate_id}")
    data = _registry_data(paths)
    candidate = _find_candidate(data, candidate_id)
    if candidate.get("status") != "pending":
        raise ValueError("candidate is not pending")
    candidate["status"] = "rejected"
    candidate["rejected_at"] = _now_iso()
    candidate["rejected_by"] = operator
    candidate["rejection_reason"] = reason
    _audit(data, operator=operator, action="candidate-rejected", reason=reason, after={"candidate_id": candidate_id})
    _write_registry(paths, data)
    return candidate


def _read_registry(path: Path) -> tuple[str, dict[str, Any], str | None]:
    try:
        return "ok", json.loads(path.read_text(encoding="utf-8")), None
    except FileNotFoundError:
        return "missing", {}, None
    except json.JSONDecodeError as error:
        return "invalid-json", {}, str(error)
    except OSError as error:
        return "unreadable", {}, str(error)


def _registry_data(paths: RuntimePaths) -> dict[str, Any]:
    path = paths.config_dir / "projects-registry.json"
    _, data, _ = _read_registry(path)
    result = data if isinstance(data, dict) else {}
    result.setdefault("version", 1)
    result.setdefault("projects", [])
    result.setdefault("candidates", [])
    result.setdefault("audit", [])
    return result


def _write_registry(paths: RuntimePaths, data: dict[str, Any]) -> None:
    path = paths.config_dir / "projects-registry.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _project_payload(project: dict[str, Any]) -> dict[str, Any]:
    root_raw = str(project.get("canonical_root") or "")
    expanded = str(Path(root_raw).expanduser().absolute()) if root_raw else ""
    return {
        "canonicalName": str(project.get("canonical_name") or ""),
        "canonicalRoot": root_raw,
        "expandedRoot": expanded,
        "enabled": bool(project.get("enabled", True)),
        "aliases": [str(alias) for alias in project.get("aliases", []) if alias],
        "status": str(project.get("status") or ""),
        "absolute": bool(root_raw and Path(root_raw).expanduser().is_absolute()),
        "exists": bool(expanded and Path(expanded).exists()),
    }


def _registry_issues(parse_status: str, projects: list[dict[str, Any]], error: str | None) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if parse_status != "ok":
        issues.append({"severity": "warning", "code": parse_status, "message": error or parse_status})
    for project in projects:
        label = project["canonicalName"] or project["canonicalRoot"] or "unknown"
        if not project["canonicalName"]:
            issues.append({"severity": "warning", "code": "missing-name", "message": f"{label}: missing canonical_name"})
        if not project["canonicalRoot"]:
            issues.append({"severity": "warning", "code": "missing-root", "message": f"{label}: missing canonical_root"})
        elif not project["absolute"]:
            issues.append({"severity": "warning", "code": "relative-root", "message": f"{label}: root is not absolute"})
    return issues


def _overlap_issues(projects: list[dict[str, Any]]) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    roots = [(project["canonicalName"], Path(project["expandedRoot"])) for project in projects if project["expandedRoot"]]
    for index, (name, root) in enumerate(roots):
        for other_name, other_root in roots[index + 1 :]:
            if root == other_root or root in other_root.parents or other_root in root.parents:
                issues.append(
                    {
                        "severity": "warning",
                        "code": "overlapping-roots",
                        "message": f"{name} ({root}) overlaps {other_name} ({other_root})",
                    }
                )
    return issues


def _db_projects(paths: RuntimePaths) -> list[dict[str, Any]]:
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT p.id, p.canonical_name, p.canonical_root, p.enabled,
                       GROUP_CONCAT(pa.alias, '||') AS aliases
                FROM projects p
                LEFT JOIN project_aliases pa ON pa.project_id = p.id
                GROUP BY p.id
                ORDER BY p.canonical_name
                """
            ).fetchall()
    except Exception:
        return []
    return [
        {
            "id": row["id"],
            "canonicalName": row["canonical_name"],
            "canonicalRoot": row["canonical_root"],
            "enabled": bool(row["enabled"]),
            "aliases": [alias for alias in (row["aliases"] or "").split("||") if alias],
        }
        for row in rows
    ]


def _structured_cwd_rows(paths: RuntimePaths) -> list[dict[str, Any]]:
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT initial_cwd, COUNT(*) AS session_count,
                       GROUP_CONCAT(DISTINCT tool_key) AS tools
                FROM sessions
                WHERE initial_cwd IS NOT NULL AND initial_cwd != ''
                GROUP BY initial_cwd
                ORDER BY session_count DESC
                """
            ).fetchall()
    except Exception:
        return []
    return [dict(row) for row in rows]


def _candidate_root(cwd: Path) -> Path:
    markers = {".git", "pyproject.toml", "package.json", "go.mod", "Cargo.toml", "Gemfile"}
    for current in (cwd, *cwd.parents):
        if any((current / marker).exists() for marker in markers):
            return current
    return cwd


def _under_any(path: Path, roots: list[Path]) -> bool:
    return any(path == root or root in path.parents for root in roots)


def _looks_transient(path: Path) -> bool:
    transient = {"node_modules", ".venv", "venv", "dist", "build", "__pycache__", ".cache", "tmp", "temp"}
    absolute = path.expanduser().absolute()
    temporary_root = Path(tempfile.gettempdir()).expanduser().absolute()
    try:
        candidate_parts = absolute.relative_to(temporary_root).parts
    except ValueError:
        candidate_parts = absolute.parts
    return any(part in transient for part in candidate_parts)


def _candidate_id(root: str) -> str:
    return "cand-" + hashlib.sha256(root.encode("utf-8")).hexdigest()[:12]


def _find_candidate(data: dict[str, Any], candidate_id: str) -> dict[str, Any]:
    for candidate in data.get("candidates", []):
        if isinstance(candidate, dict) and candidate.get("candidate_id") == candidate_id:
            return candidate
    raise ValueError(f"unknown candidate: {candidate_id}")


def _ensure_no_confirmed_root_conflict(data: dict[str, Any], root: str) -> None:
    candidate_root = str(Path(root).expanduser().absolute())
    for project in data.get("projects", []):
        if not isinstance(project, dict) or not project.get("enabled", True):
            continue
        existing = str(Path(str(project.get("canonical_root") or "")).expanduser().absolute())
        if existing == candidate_root:
            raise ValueError(f"confirmed project root already exists: {candidate_root}")


def _audit(data: dict[str, Any], *, operator: str, action: str, reason: str, after: dict[str, Any]) -> None:
    data.setdefault("audit", []).append(
        {
            "timestamp": _now_iso(),
            "operator": operator,
            "action": action,
            "reason": reason,
            "after": after,
        }
    )


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat()
