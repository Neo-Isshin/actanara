"""Deterministic workspace attribution from tool-observed paths."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable

from .paths import RuntimePaths, load_paths

PROJECT_MARKERS = (
    ".git",
    "pyproject.toml",
    "package.json",
    "pnpm-workspace.yaml",
    "go.mod",
    "Cargo.toml",
    "deno.json",
    "deno.jsonc",
)

TRANSIENT_PARTS = {
    ".git",
    ".venv",
    "__pycache__",
    "node_modules",
    ".pytest_cache",
    ".mypy_cache",
    "dist",
    "build",
}

INFRASTRUCTURE_WORKSPACE_NAMES = {
    ".cache",
    ".claude",
    ".codex",
    ".config",
    ".gemini",
    ".local",
    ".npm",
    ".nvm",
    ".opencode",
    ".pnpm-store",
    ".pytest_cache",
    ".venv",
    "__pycache__",
    "application support",
    "build",
    "caches",
    "dist",
    "homebrew",
    "library",
    "logs",
    "memories",
    "node_modules",
    "nvm",
}

_ABSOLUTE_PATH_RE = re.compile(r"(?<![\w.])/(?:[^\s\"'`<>|\\]+)")

LEGACY_PROJECT_NAME_ALIASES = {
    "nova-diary-v2": "open-nova",
}

WORKSPACE_ATTRIBUTION_RULE_SCHEMA_VERSION = 1


@dataclass(frozen=True)
class WorkspaceAttribution:
    display_name: str
    root_path: str
    confidence: str
    evidence: str

    def to_dict(self) -> dict[str, str]:
        return asdict(self)


def workspace_display_name(path: str | Path) -> str:
    attribution = attribute_workspace_path(path)
    return canonical_workspace_name(attribution.display_name if attribution else _fallback_name(path))


def canonical_workspace_name(name: str | None) -> str:
    normalized = str(name or "").strip()
    if not normalized:
        return ""
    alias_rules = _workspace_alias_rules_tuple()
    return alias_rules.get(normalized, LEGACY_PROJECT_NAME_ALIASES.get(normalized, normalized))


def workspace_usage_display_allowed(name: str | None, *, project_marker_confirmed: bool = False) -> bool:
    """Return whether a workspace/agent bucket should be shown as a project row.

    Project-marker-confirmed names win over the infrastructure catalog. Name-only
    buckets are otherwise filtered when they are clearly tool config, package
    manager, cache, or hidden infrastructure directories.
    """
    normalized = str(name or "").strip()
    if not normalized:
        return False
    if project_marker_confirmed:
        return True
    lowered = normalized.lower()
    if lowered in _workspace_container_names_tuple():
        return False
    if lowered in INFRASTRUCTURE_WORKSPACE_NAMES:
        return False
    if lowered.startswith("."):
        return False
    return True


def infer_workspace_from_text(text: str) -> WorkspaceAttribution | None:
    cached = _infer_workspace_tuple_from_text(text or "")
    return WorkspaceAttribution(*cached) if cached else None


@lru_cache(maxsize=20000)
def _infer_workspace_tuple_from_text(text: str) -> tuple[str, str, str, str] | None:
    candidates: dict[str, dict[str, Any]] = {}
    for raw_path in _extract_absolute_paths_cached(text):
        attribution = attribute_workspace_path(raw_path)
        if attribution is None:
            continue
        if attribution.confidence != "high":
            continue
        item = candidates.setdefault(
            attribution.root_path,
            {
                "attribution": attribution,
                "count": 0,
            },
        )
        item["count"] += 1
    if not candidates:
        return None
    ranked = sorted(
        candidates.values(),
        key=lambda item: (-item["count"], -len(item["attribution"].root_path), item["attribution"].display_name),
    )
    attribution = ranked[0]["attribution"]
    return (attribution.display_name, attribution.root_path, attribution.confidence, attribution.evidence)


def infer_workspace_name_from_text(text: str) -> str | None:
    cached = _infer_workspace_tuple_from_text(text or "")
    return cached[0] if cached else None


def extract_absolute_paths(text: str) -> list[str]:
    return list(_extract_absolute_paths_cached(text or ""))


@lru_cache(maxsize=20000)
def _extract_absolute_paths_cached(text: str) -> tuple[str, ...]:
    paths = []
    for match in _ABSOLUTE_PATH_RE.finditer(text):
        value = match.group(0).rstrip(".,;:!?)]}'\"\\")
        if value and _looks_like_filesystem_path_candidate(value):
            paths.append(value)
    return tuple(paths)


def attribute_workspace_path(path: str | Path) -> WorkspaceAttribution | None:
    cached = _attribute_workspace_tuple(str(path))
    return WorkspaceAttribution(*cached) if cached else None


@lru_cache(maxsize=20000)
def _attribute_workspace_tuple(path: str) -> tuple[str, str, str, str] | None:
    candidate = Path(path).expanduser()
    if not candidate.is_absolute():
        return None
    candidate = candidate.absolute()
    manual = _manual_path_attribution(candidate)
    if manual is not None:
        return manual
    if _looks_transient(candidate):
        return None
    root = project_root_for_path(candidate)
    if root is None:
        try:
            fallback_root = candidate if candidate.exists() and candidate.is_dir() else candidate.parent
        except OSError:
            return None
        return (_fallback_name(candidate), str(fallback_root), "low", "absolute-path")
    return (project_display_name(root), str(root), "high", "project-marker")


def project_root_for_path(path: str | Path) -> Path | None:
    cached = _project_root_for_path_cached(str(path))
    return Path(cached) if cached else None


@lru_cache(maxsize=20000)
def _project_root_for_path_cached(path: str) -> str | None:
    candidate = Path(path).expanduser().absolute()
    base = _existing_directory(candidate)
    if base is None or _looks_transient(base):
        return None
    for current in [base, *base.parents]:
        if _has_project_marker(current):
            return str(current)
    return None


def project_display_name(root: Path) -> str:
    return canonical_workspace_name(_project_display_name_cached(str(root)))


def workspace_attribution_rules_path(paths: RuntimePaths | None = None) -> Path:
    selected = paths or load_paths()
    return selected.state_dir / "workspace-attribution" / "rules.json"


def default_workspace_attribution_rules() -> dict[str, Any]:
    return {
        "schemaVersion": WORKSPACE_ATTRIBUTION_RULE_SCHEMA_VERSION,
        "updatedAt": "",
        "rules": [],
    }


def read_workspace_attribution_rules(paths: RuntimePaths | None = None) -> dict[str, Any]:
    selected = paths or load_paths()
    path = workspace_attribution_rules_path(selected)
    if not path.exists():
        return default_workspace_attribution_rules()
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default_workspace_attribution_rules()
    rules = data.get("rules") if isinstance(data, dict) else []
    return {
        "schemaVersion": int(data.get("schemaVersion") or WORKSPACE_ATTRIBUTION_RULE_SCHEMA_VERSION) if isinstance(data, dict) else WORKSPACE_ATTRIBUTION_RULE_SCHEMA_VERSION,
        "updatedAt": str(data.get("updatedAt") or "") if isinstance(data, dict) else "",
        "rules": [rule for rule in rules if isinstance(rule, dict)] if isinstance(rules, list) else [],
    }


def write_workspace_attribution_rules(paths: RuntimePaths | None, rules: dict[str, Any]) -> dict[str, Any]:
    selected = paths or load_paths()
    payload = {
        "schemaVersion": WORKSPACE_ATTRIBUTION_RULE_SCHEMA_VERSION,
        "updatedAt": datetime.now().astimezone().isoformat(),
        "rules": [rule for rule in (rules.get("rules") if isinstance(rules, dict) else []) if isinstance(rule, dict)],
    }
    path = workspace_attribution_rules_path(selected)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    tmp.replace(path)
    clear_workspace_attribution_caches()
    return payload


def clear_workspace_attribution_caches() -> None:
    _workspace_rules_tuple.cache_clear()
    _workspace_alias_rules_tuple.cache_clear()
    _workspace_container_names_tuple.cache_clear()
    _attribute_workspace_tuple.cache_clear()
    _project_root_for_path_cached.cache_clear()
    _infer_workspace_tuple_from_text.cache_clear()


def validate_workspace_path(path: str | Path) -> dict[str, Any]:
    candidate = Path(str(path or "")).expanduser()
    result = {
        "input": str(path or ""),
        "path": str(candidate.absolute()) if str(path or "") else "",
        "exists": False,
        "isDirectory": False,
        "hasProjectMarker": False,
        "displayName": "",
        "rootPath": "",
        "valid": False,
        "reason": "",
    }
    if not str(path or "").strip():
        result["reason"] = "path is required"
        return result
    if not candidate.is_absolute():
        result["reason"] = "path must be absolute"
        return result
    try:
        result["exists"] = candidate.exists()
        result["isDirectory"] = candidate.is_dir()
    except OSError as exc:
        result["reason"] = str(exc)
        return result
    attribution = attribute_workspace_path(candidate)
    if attribution is not None:
        result["displayName"] = attribution.display_name
        result["rootPath"] = attribution.root_path
        result["hasProjectMarker"] = attribution.confidence == "high"
    if not result["exists"]:
        result["reason"] = "path does not exist"
        return result
    if not result["isDirectory"]:
        result["reason"] = "path must be a directory"
        return result
    if not result["hasProjectMarker"]:
        result["reason"] = "path has no project marker"
        return result
    result["valid"] = True
    result["reason"] = "ok"
    return result


def normalize_workspace_attribution_rule(rule: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(rule, dict):
        raise ValueError("rule must be an object")
    rule_type = str(rule.get("type") or "").strip()
    if rule_type not in {"alias", "path", "container", "source_session"}:
        raise ValueError("type must be one of alias, path, container, source_session")
    normalized = {
        "id": str(rule.get("id") or f"{rule_type}:{datetime.now().astimezone().timestamp()}"),
        "type": rule_type,
        "tool": str(rule.get("tool") or "").strip(),
        "createdBy": str(rule.get("createdBy") or "user"),
        "reason": str(rule.get("reason") or "manual-attribution-review"),
        "createdAt": str(rule.get("createdAt") or datetime.now().astimezone().isoformat()),
    }
    if rule_type == "alias":
        source = str(rule.get("source") or rule.get("from") or "").strip()
        target = str(rule.get("target") or rule.get("to") or "").strip()
        if not source or not target:
            raise ValueError("alias rule requires source and target")
        normalized.update({"source": source, "target": target})
    elif rule_type == "container":
        name = str(rule.get("name") or rule.get("workspace") or "").strip()
        if not name:
            raise ValueError("container rule requires name")
        normalized.update({"name": name})
    elif rule_type == "path":
        workspace_path = str(rule.get("workspacePath") or rule.get("path") or "").strip()
        validation = validate_workspace_path(workspace_path)
        if not validation["valid"]:
            raise ValueError(f"invalid workspacePath: {validation['reason']}")
        normalized.update({
            "match": str(rule.get("match") or (validation["rootPath"].rstrip("/") + "/**")),
            "workspacePath": validation["rootPath"],
            "workspace": str(rule.get("workspace") or validation["displayName"] or Path(validation["rootPath"]).name),
        })
    elif rule_type == "source_session":
        source_path = str(rule.get("sourcePath") or "").strip()
        workspace_path = str(rule.get("workspacePath") or "").strip()
        validation = validate_workspace_path(workspace_path)
        if not source_path:
            raise ValueError("source_session rule requires sourcePath")
        if not validation["valid"]:
            raise ValueError(f"invalid workspacePath: {validation['reason']}")
        normalized.update({
            "sourcePath": source_path,
            "workspacePath": validation["rootPath"],
            "workspace": str(rule.get("workspace") or validation["displayName"] or Path(validation["rootPath"]).name),
        })
    return normalized


def add_workspace_attribution_rule(
    rule: dict[str, Any],
    paths: RuntimePaths | None = None,
    *,
    dry_run: bool = True,
) -> dict[str, Any]:
    selected = paths or load_paths()
    normalized = normalize_workspace_attribution_rule(rule)
    current = read_workspace_attribution_rules(selected)
    existing = list(current.get("rules") or [])
    duplicate = _find_duplicate_rule(existing, normalized)
    result = {
        "dryRun": dry_run,
        "rule": normalized,
        "duplicate": duplicate,
        "ruleCountBefore": len(existing),
        "ruleCountAfter": len(existing) if duplicate else len(existing) + 1,
        "sideEffects": [] if dry_run else ["workspace-attribution-rules-write", "ai-assets-cache-reparse-required", "period-projection-refresh-recommended"],
    }
    if dry_run:
        return result
    if duplicate:
        return {**result, "rules": current}
    updated = {**current, "rules": [*existing, normalized]}
    written = write_workspace_attribution_rules(selected, updated)
    return {**result, "rules": written}


def source_session_workspace_attribution(raw_path: str | Path) -> WorkspaceAttribution | None:
    raw = str(raw_path or "")
    if not raw:
        return None
    for rule in _workspace_rules_tuple():
        if rule.get("type") != "source_session":
            continue
        if str(rule.get("sourcePath") or "") != raw:
            continue
        workspace_path = str(rule.get("workspacePath") or "")
        workspace = canonical_workspace_name(rule.get("workspace") or workspace_display_name(workspace_path))
        if workspace_path:
            return WorkspaceAttribution(workspace, workspace_path, "high", "user-source-session-rule")
    return None


def _find_duplicate_rule(rules: list[dict[str, Any]], candidate: dict[str, Any]) -> dict[str, Any] | None:
    keys = {
        "alias": ("type", "source", "target"),
        "container": ("type", "name"),
        "path": ("type", "tool", "match", "workspacePath"),
        "source_session": ("type", "tool", "sourcePath", "workspacePath"),
    }.get(candidate.get("type"), ("type",))
    for rule in rules:
        if all(str(rule.get(key) or "") == str(candidate.get(key) or "") for key in keys):
            return rule
    return None


@lru_cache(maxsize=1)
def _workspace_rules_tuple() -> tuple[dict[str, Any], ...]:
    return tuple(read_workspace_attribution_rules().get("rules") or [])


@lru_cache(maxsize=1)
def _workspace_alias_rules_tuple() -> dict[str, str]:
    aliases = {}
    for rule in _workspace_rules_tuple():
        if rule.get("type") == "alias":
            source = str(rule.get("source") or "").strip()
            target = str(rule.get("target") or "").strip()
            if source and target:
                aliases[source] = target
    return aliases


@lru_cache(maxsize=1)
def _workspace_container_names_tuple() -> tuple[str, ...]:
    names = []
    for rule in _workspace_rules_tuple():
        if rule.get("type") == "container":
            name = str(rule.get("name") or "").strip().lower()
            if name:
                names.append(name)
    return tuple(names)


def _manual_path_attribution(candidate: Path) -> tuple[str, str, str, str] | None:
    matched = []
    candidate_text = str(candidate)
    for rule in _workspace_rules_tuple():
        if rule.get("type") != "path":
            continue
        root = str(rule.get("workspacePath") or "").rstrip("/")
        if not root:
            continue
        if candidate_text == root or candidate_text.startswith(root + "/"):
            matched.append(rule)
    if not matched:
        return None
    rule = sorted(matched, key=lambda item: len(str(item.get("workspacePath") or "")), reverse=True)[0]
    root = str(rule.get("workspacePath") or "")
    workspace = canonical_workspace_name(rule.get("workspace") or _fallback_name(root))
    return (workspace, root, "high", "user-path-rule")


@lru_cache(maxsize=4096)
def _project_display_name_cached(root: str) -> str:
    root_path = Path(root)
    for name in (_pyproject_name(root_path), _package_name(root_path)):
        if name:
            return name
    return root_path.name or str(root_path)


def materialize_workspace_attribution_catalog(
    paths: RuntimePaths | None = None,
    *,
    observed_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    selected = paths or load_paths()
    catalog = build_workspace_attribution_catalog(selected, observed_paths=observed_paths)
    output = workspace_attribution_catalog_path(selected)
    output.parent.mkdir(parents=True, exist_ok=True)
    tmp = output.with_suffix(output.suffix + ".tmp")
    tmp.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(output)
    return catalog


def build_workspace_attribution_catalog(
    paths: RuntimePaths | None = None,
    *,
    observed_paths: Iterable[str | Path] = (),
) -> dict[str, Any]:
    selected = paths or load_paths()
    entries: dict[str, dict[str, Any]] = {}
    for source, raw_path in _observed_workspace_paths(selected, observed_paths=observed_paths):
        attribution = attribute_workspace_path(raw_path)
        if attribution is None:
            continue
        if attribution.confidence != "high":
            continue
        entry = entries.setdefault(
            attribution.root_path,
            {
                **attribution.to_dict(),
                "sources": set(),
                "observation_count": 0,
            },
        )
        entry["sources"].add(source)
        entry["observation_count"] += 1
    projects = []
    for entry in entries.values():
        entry["sources"] = sorted(entry["sources"])
        projects.append(entry)
    projects.sort(key=lambda item: (-item["observation_count"], item["display_name"], item["root_path"]))
    return {
        "schemaVersion": 1,
        "generatedAt": datetime.now().astimezone().isoformat(),
        "authority": "deterministic-tool-observed-paths",
        "projects": projects,
        "counts": {"projects": len(projects), "observations": sum(item["observation_count"] for item in projects)},
    }


def workspace_attribution_catalog_path(paths: RuntimePaths | None = None) -> Path:
    selected = paths or load_paths()
    return selected.state_dir / "workspace-attribution" / "catalog.json"


def _observed_workspace_paths(
    paths: RuntimePaths,
    *,
    observed_paths: Iterable[str | Path] = (),
) -> Iterable[tuple[str, str | Path]]:
    for raw_path in observed_paths:
        yield "caller", raw_path
    for rule in read_workspace_attribution_rules(paths).get("rules") or []:
        if rule.get("type") in {"path", "source_session"} and rule.get("workspacePath"):
            yield "user-rule", str(rule.get("workspacePath"))
    yield "runtime", paths.home
    yield "runtime", paths.diary_dir
    yield from _settings_workspace_paths(paths)
    yield from _db_initial_cwd_paths(paths)


def _settings_workspace_paths(paths: RuntimePaths) -> Iterable[tuple[str, str]]:
    try:
        from .settings import read_settings

        settings = read_settings(paths)
    except Exception:
        return
    candidates = [
        ((settings.get("general") or {}).get("workspaceRoot") if isinstance(settings.get("general"), dict) else None),
        (
            ((settings.get("paths") or {}).get("install") or {}).get("workspace")
            if isinstance(settings.get("paths"), dict) and isinstance((settings.get("paths") or {}).get("install"), dict)
            else None
        ),
    ]
    for candidate in candidates:
        if candidate:
            yield "settings", str(candidate)


def _db_initial_cwd_paths(paths: RuntimePaths) -> Iterable[tuple[str, str]]:
    try:
        from .db import connect

        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT tool_key, initial_cwd
                FROM sessions
                WHERE initial_cwd IS NOT NULL AND TRIM(initial_cwd) != ''
                """
            )
            for row in rows:
                yield str(row["tool_key"] or "session"), str(row["initial_cwd"])
    except Exception:
        return


def _existing_directory(path: Path) -> Path | None:
    try:
        current = path if path.exists() and path.is_dir() else path.parent
    except OSError:
        return None
    for candidate in [current, *current.parents]:
        try:
            if candidate.exists() and candidate.is_dir():
                return candidate
        except OSError:
            continue
    return None


def _has_project_marker(path: Path) -> bool:
    return any((path / marker).exists() for marker in PROJECT_MARKERS)


def _pyproject_name(root: Path) -> str | None:
    path = root / "pyproject.toml"
    if not path.exists():
        return None
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    match = re.search(r"(?m)^name\s*=\s*[\"']([^\"']+)[\"']", text)
    return match.group(1).strip() if match else None


def _package_name(root: Path) -> str | None:
    path = root / "package.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None
    name = data.get("name") if isinstance(data, dict) else None
    return name.strip() if isinstance(name, str) and name.strip() else None


def _fallback_name(path: str | Path) -> str:
    candidate = Path(path).expanduser()
    if candidate == Path.home():
        return "home"
    return candidate.name or str(candidate)


def _looks_transient(path: Path) -> bool:
    return any(part in TRANSIENT_PARTS for part in path.parts)


def _looks_like_url_path(path: str) -> bool:
    return path.startswith("//")


def _looks_like_filesystem_path_candidate(path: str) -> bool:
    if not path or len(path) > 4096 or _looks_like_url_path(path):
        return False
    try:
        return all(len(part) <= 255 for part in Path(path).parts)
    except (OSError, ValueError):
        return False
