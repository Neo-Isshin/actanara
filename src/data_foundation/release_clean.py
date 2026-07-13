"""Read-only repository clean checks for new-user deployment gates."""

from __future__ import annotations

import re
import json
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

from .business_day_audit import business_day_hardcode_inventory


ROOT = Path(__file__).resolve().parents[2]
MAX_TEXT_BYTES = 2 * 1024 * 1024
DENY_PATH_RE = re.compile(
    r"(^|/)(\.env|settings\.json|runtime\.json|nova_data\.sqlite3|nova_tasks\.db|.*\.(db|sqlite|sqlite3|log|pem|key))$"
    r"|(^|/)(logs?|cache|snapshots|state|runtime|backups?|__pycache__|test-results)(/|$)",
    re.IGNORECASE,
)
ASSIGNMENT_KEY_PATTERN = r"[A-Za-z][A-Za-z0-9_.-]{0,80}"
SECRET_VALUE_PATTERN = r"[A-Za-z0-9_\-./+=!@#$%^&*]{20,}"
SECRET_VALUE_ASSIGNMENT_PATTERN = (
    rf'(?:"(?P<double_quoted_value>[^"\r\n]{{20,}})"|'
    rf"'(?P<single_quoted_value>[^'\r\n]{{20,}})'|"
    rf"(?P<bare_value>{SECRET_VALUE_PATTERN}))"
)
SECRET_RE = re.compile(
    rf"(?<![A-Za-z0-9_])(?P<key_quote>['\"]?)(?P<key>{ASSIGNMENT_KEY_PATTERN})(?P=key_quote)"
    rf"(?![A-Za-z0-9_])[ \t]*[:=][ \t]*{SECRET_VALUE_ASSIGNMENT_PATTERN}",
    re.IGNORECASE,
)
BOUNDED_DATA_ASSIGNMENT_RE = re.compile(
    rf"^[ \t]*(?:-[ \t]+)?(?P<key_quote>['\"]?)(?P<key>{ASSIGNMENT_KEY_PATTERN})(?P=key_quote)"
    rf"(?![A-Za-z0-9_])[ \t]*(?:\r?\n[ \t]*)?"
    rf"(?P<delimiter>[:=])[ \t]*"
    rf"(?:\\[ \t]*\r?\n[ \t]*|(?:[>|][+-]?[ \t]*(?:#[^\r\n]*)?)?\r?\n[ \t]+)?"
    rf"{SECRET_VALUE_ASSIGNMENT_PATTERN}",
    re.IGNORECASE | re.MULTILINE,
)
RUNTIME_SOURCE_BASE_FIELDS = {
    "schemaVersion",
    "product",
    "sourceLocator",
    "deployedSourceLocator",
    "releaseLocator",
    "deploymentMode",
    "copiedAt",
    "pyprojectVersion",
    "git",
    "databaseCompatibility",
}
RUNTIME_SOURCE_FINAL_FIELDS = RUNTIME_SOURCE_BASE_FIELDS | {"payload", "cleanScan"}
SAFE_RELEASE_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")


def _safe_manifest_relative_path(value: Any) -> bool:
    if not isinstance(value, str) or not value or "\0" in value or value.startswith(("/", "~/", "file:")):
        return False
    if re.match(r"^[A-Za-z]:[\\/]", value):
        return False
    path = Path(value)
    return not path.is_absolute() and ".." not in path.parts and "." not in path.parts


def _runtime_manifest_nested_shape_valid(manifest: dict[str, Any]) -> bool:
    try:
        datetime.fromisoformat(str(manifest["copiedAt"]))
    except (KeyError, TypeError, ValueError):
        return False
    version = manifest.get("pyprojectVersion")
    if version is not None and (
        not isinstance(version, str)
        or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}", version)
    ):
        return False
    git = manifest.get("git")
    if not isinstance(git, dict) or set(git) != {"available", "commit", "branch", "remote", "dirty"}:
        return False
    if type(git.get("available")) is not bool:
        return False
    if git.get("dirty") is not None and type(git.get("dirty")) is not bool:
        return False
    commit = git.get("commit")
    branch = git.get("branch")
    if commit is not None and (not isinstance(commit, str) or not re.fullmatch(r"[0-9a-f]{7,64}", commit)):
        return False
    if branch is not None and (
        not isinstance(branch, str)
        or not branch
        or len(branch) > 255
        or branch.startswith(("/", "~/", "file:"))
        or "/Users/" in branch
        or any(character in branch for character in "\0\r\n")
    ):
        return False
    compatibility = manifest.get("databaseCompatibility")
    compatibility_fields = {
        "schemaVersion",
        "policy",
        "preCommitWriterContract",
        "minimumReadableSchema",
        "maximumReadableSchema",
        "migrationSetSha256",
        "migrations",
    }
    if not isinstance(compatibility, dict) or set(compatibility) != compatibility_fields:
        return False
    migrations = compatibility.get("migrations")
    if (
        type(compatibility.get("schemaVersion")) is not int
        or compatibility.get("schemaVersion") != 1
        or compatibility.get("policy") != "rollback-compatible-additive-only"
        or compatibility.get("preCommitWriterContract") != "prior-reader-compatible-v1"
        or compatibility.get("minimumReadableSchema") != "unversioned"
        or not isinstance(migrations, list)
        or not migrations
        or not re.fullmatch(r"[0-9a-f]{64}", str(compatibility.get("migrationSetSha256") or ""))
    ):
        return False
    versions: list[str] = []
    for record in migrations:
        if not isinstance(record, dict) or set(record) != {"version", "sha256", "rollbackClass"}:
            return False
        migration_version = record.get("version")
        if (
            not isinstance(migration_version, str)
            or not re.fullmatch(r"[0-9]{4}_[a-z0-9_]+", migration_version)
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
            or record.get("rollbackClass") not in {"rollback-compatible-additive", "breaking"}
        ):
            return False
        versions.append(migration_version)
    if len(set(versions)) != len(versions) or compatibility.get("maximumReadableSchema") != versions[-1]:
        return False
    if "payload" not in manifest:
        return True
    clean = manifest.get("cleanScan")
    payload = manifest.get("payload")
    if (
        not isinstance(clean, dict)
        or set(clean) != {"status", "scanner", "scannedFiles", "findingCount"}
        or clean.get("status") != "passed"
        or clean.get("scanner")
        != "data_foundation.release_clean.repository_clean_deployment_check"
        or type(clean.get("scannedFiles")) is not int
        or clean.get("scannedFiles") < 0
        or clean.get("findingCount") != 0
        or not isinstance(payload, dict)
        or set(payload) != {"fileCount", "files", "sha256"}
    ):
        return False
    records = payload.get("files")
    if (
        type(payload.get("fileCount")) is not int
        or not isinstance(records, list)
        or not records
        or payload.get("fileCount") != len(records)
        or not re.fullmatch(r"[0-9a-f]{64}", str(payload.get("sha256") or ""))
    ):
        return False
    paths: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {"path", "sha256", "size"}:
            return False
        relative = record.get("path")
        if (
            not _safe_manifest_relative_path(relative)
            or not re.fullmatch(r"[0-9a-f]{64}", str(record.get("sha256") or ""))
            or type(record.get("size")) is not int
            or record.get("size") < 0
        ):
            return False
        paths.append(relative)
    return len(set(paths)) == len(paths)


def repository_clean_deployment_check(root: Path | None = None) -> dict[str, Any]:
    """Scan tracked-source-like files for runtime artifacts and raw secrets."""
    base = (root or ROOT).resolve()
    findings: list[dict[str, Any]] = []
    scanned = 0
    for path in _iter_candidate_files(base):
        rel = path.relative_to(base).as_posix()
        scanned += 1
        if DENY_PATH_RE.search(rel):
            findings.append({"path": rel, "kind": "runtime-artifact", "severity": "blocker"})
            continue
        provenance = _runtime_source_manifest_privacy_finding(path, rel)
        if provenance:
            findings.append(provenance)
            continue
        secret = _secret_finding(path, rel)
        if secret:
            findings.append(secret)
    blockers = [item for item in findings if item.get("severity") == "blocker"]
    return {
        "status": "passed" if not blockers else "blocked",
        "root": str(base),
        "scannedFiles": scanned,
        "findings": findings[:100],
        "truncated": len(findings) > 100,
        "businessDayHardcodes": business_day_hardcode_inventory(base),
        "policy": {
            "forbidden": ["DB", "logs", "snapshots", "state", "cache", "settings", "runtime", "raw secrets"],
            "allowedSecretForms": ["secretRef", "apiKeyEnv", "environment variable name"],
        },
    }


def _iter_candidate_files(base: Path):
    git_files = _git_source_files(base)
    if git_files is not None:
        for rel in git_files:
            path = base / rel
            if path.is_file():
                yield path
        return
    skipped = {".git", ".venv", "node_modules", "__pycache__"}
    for path in base.rglob("*"):
        if any(part in skipped for part in path.parts):
            continue
        if path.is_file():
            yield path


def _git_source_files(base: Path) -> list[Path] | None:
    if not (base / ".git").exists():
        return None
    try:
        result = subprocess.run(
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=base,
            check=True,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    seen: set[Path] = set()
    files: list[Path] = []
    for line in result.stdout.splitlines():
        if not line.strip():
            continue
        path = Path(line)
        if path in seen:
            continue
        seen.add(path)
        files.append(path)
    return files


def _secret_finding(path: Path, rel: str) -> dict[str, Any] | None:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return {
                "path": rel,
                "kind": "unscanned-oversize",
                "severity": "blocker",
                "sizeBytes": path.stat().st_size,
                "limitBytes": MAX_TEXT_BYTES,
            }
        content = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return None
    structured = _structured_json_secret(content)
    if structured:
        key, line = structured
        return {
            "path": rel,
            "kind": "possible-raw-secret",
            "severity": "blocker",
            "line": line,
            "key": key,
        }
    matchers = [SECRET_RE.finditer(content)]
    if _bounded_data_format(path):
        matchers.append(BOUNDED_DATA_ASSIGNMENT_RE.finditer(content))
    for matches in matchers:
        for match in matches:
            if not _looks_like_secret_key(match.group("key")):
                continue
            candidate_value = _assignment_value(match)
            if _looks_like_code_reference(
                candidate_value,
                allow_expression=match.group("bare_value") is not None,
            ):
                continue
            line = content[: match.start()].count("\n") + 1
            return {
                "path": rel,
                "kind": "possible-raw-secret",
                "severity": "blocker",
                "line": line,
                "key": match.group("key"),
            }
    return None


def _runtime_source_manifest_privacy_finding(path: Path, rel: str) -> dict[str, Any] | None:
    if path.name != ".open-nova-runtime-source.json":
        return None
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError, TypeError):
        return {
            "path": rel,
            "kind": "invalid-source-manifest",
            "severity": "blocker",
        }
    if not isinstance(manifest, dict) or type(manifest.get("schemaVersion")) is not int or manifest.get("schemaVersion") != 2:
        return {
            "path": rel,
            "kind": "legacy-private-provenance-schema",
            "severity": "blocker",
        }
    fields = frozenset(manifest)
    if fields not in {frozenset(RUNTIME_SOURCE_BASE_FIELDS), frozenset(RUNTIME_SOURCE_FINAL_FIELDS)}:
        return {
            "path": rel,
            "kind": "invalid-source-manifest-shape",
            "severity": "blocker",
        }
    if manifest.get("product") != "open-nova" or manifest.get("deploymentMode") != "release-symlink":
        return {"path": rel, "kind": "invalid-source-manifest-semantics", "severity": "blocker"}
    locator = manifest.get("sourceLocator")
    if not isinstance(locator, dict):
        return {
            "path": rel,
            "kind": "invalid-source-locator",
            "severity": "blocker",
        }
    kind = locator.get("kind")
    if kind == "unavailable":
        if set(locator) != {"kind", "issue"} or locator.get("issue") not in {
            "outside-login-home",
            "invalid-relative-components",
        }:
            return {
                "path": rel,
                "kind": "invalid-source-locator",
                "severity": "blocker",
            }
    else:
        components = locator.get("pathComponents")
        if (
            set(locator) != {"kind", "pathComponents"}
            or kind != "login-home-relative"
            or not isinstance(components, list)
            or not components
        ):
            return {
                "path": rel,
                "kind": "invalid-source-locator",
                "severity": "blocker",
            }
        if any(
            not isinstance(item, str)
            or not item
            or item in {".", ".."}
            or "/" in item
            or "\\" in item
            for item in components
        ):
            return {
                "path": rel,
                "kind": "invalid-source-locator",
                "severity": "blocker",
            }
    for field in ("deployedSourceLocator", "releaseLocator"):
        runtime_locator = manifest.get(field)
        runtime_components = runtime_locator.get("pathComponents") if isinstance(runtime_locator, dict) else None
        if (
            not isinstance(runtime_locator, dict)
            or set(runtime_locator) != {"kind", "pathComponents"}
            or runtime_locator.get("kind") != "runtime-relative"
            or not isinstance(runtime_components, list)
            or not runtime_components
            or any(
                not isinstance(item, str)
                or not item
                or item in {".", ".."}
                or "/" in item
                or "\\" in item
                for item in runtime_components
            )
        ):
            return {
                "path": rel,
                "kind": "invalid-runtime-locator",
                "severity": "blocker",
                "field": field,
            }
    if manifest["deployedSourceLocator"]["pathComponents"] != ["app", "source"]:
        return {"path": rel, "kind": "invalid-deployed-source-locator", "severity": "blocker"}
    release_components = manifest["releaseLocator"]["pathComponents"]
    if (
        len(release_components) != 3
        or release_components[:2] != ["app", "releases"]
        or not SAFE_RELEASE_ID_RE.fullmatch(release_components[2])
    ):
        return {"path": rel, "kind": "invalid-release-locator", "severity": "blocker"}
    git = manifest.get("git")
    if not isinstance(git, dict) or set(git) != {"available", "commit", "branch", "remote", "dirty"}:
        return {"path": rel, "kind": "invalid-source-git-provenance", "severity": "blocker"}
    if not _runtime_manifest_nested_shape_valid(manifest):
        return {"path": rel, "kind": "invalid-source-manifest-shape", "severity": "blocker"}
    remote = git.get("remote")
    if remote is not None:
        if not isinstance(remote, str):
            return {"path": rel, "kind": "private-source-remote", "severity": "blocker"}
        try:
            parsed = urlsplit(remote)
        except (TypeError, ValueError):
            parsed = None
        if (
            parsed is None
            or parsed.scheme not in {"https", "ssh"}
            or not parsed.hostname
            or parsed.username is not None
            or parsed.password is not None
            or bool(parsed.query)
            or bool(parsed.fragment)
        ):
            return {"path": rel, "kind": "private-source-remote", "severity": "blocker"}
    return None


def _bounded_data_format(path: Path) -> bool:
    return path.name.startswith(".env") or path.suffix.lower() in {
        ".conf",
        ".env",
        ".ini",
        ".json",
        ".yaml",
        ".yml",
    }


def _structured_json_secret(content: str) -> tuple[str, int] | None:
    try:
        payload = json.loads(content.removeprefix("\ufeff"))
    except (json.JSONDecodeError, TypeError):
        return None

    def walk(value: Any, *, depth: int = 0) -> tuple[str, str] | None:
        if isinstance(value, dict):
            for key, child in value.items():
                key_text = str(key)
                if _looks_like_secret_key(key_text) and isinstance(child, str):
                    if _looks_like_secret_value(child) and not _looks_like_code_reference(child):
                        return key_text, child
                nested = walk(child, depth=depth + 1)
                if nested:
                    return nested
        elif isinstance(value, list):
            for child in value:
                nested = walk(child, depth=depth + 1)
                if nested:
                    return nested
        elif isinstance(value, str) and depth < 4:
            candidate = value.lstrip("\ufeff \t\r\n")
            if candidate.startswith(("{", "[", '"{', '"[')):
                try:
                    nested_payload = json.loads(candidate)
                except (json.JSONDecodeError, TypeError):
                    return None
                return walk(nested_payload, depth=depth + 1)
        return None

    finding = walk(payload)
    if not finding:
        return None
    key, value = finding
    value_literal = json.dumps(value, ensure_ascii=False)
    value_at = content.find(value_literal)
    key_at = content.rfind(f'"{key}"', 0, value_at) if value_at >= 0 else -1
    line_at = key_at if key_at >= 0 else value_at
    line = content[:line_at].count("\n") + 1 if line_at >= 0 else 1
    return key, line


def _assignment_value(match: re.Match[str]) -> str:
    return str(
        match.group("double_quoted_value")
        or match.group("single_quoted_value")
        or match.group("bare_value")
        or ""
    )


def _looks_like_secret_key(value: str) -> bool:
    normalized = re.sub(r"[^a-z0-9]", "", value.casefold())
    if normalized.startswith(("masked", "redacted")):
        return False
    return normalized.endswith(("apikey", "privatekey", "secret", "token", "password", "bearer"))


def _looks_like_secret_value(value: str) -> bool:
    return len(value) >= 20 and "\n" not in value and "\r" not in value


def _looks_like_code_reference(value: str, *, allow_expression: bool = False) -> bool:
    lowered = value.strip().lower()
    if lowered in {"none", "null", "true", "false", "redacted", "masked", "<redacted>", "***"}:
        return True
    if re.fullmatch(
        r"(?:\[redacted\]|\[masked\]|%5bredacted%5d|%5bmasked%5d)(?:[&#?][^\r\n]*)?",
        lowered,
    ):
        return True
    if not allow_expression:
        return False
    return bool(
        re.fullmatch(
            r"(?:document|process|os)(?:\.[a-z_][a-z0-9_]*)+|"
            r"secrets\.token_(?:bytes|hex|urlsafe)|getenv",
            lowered,
        )
    )
