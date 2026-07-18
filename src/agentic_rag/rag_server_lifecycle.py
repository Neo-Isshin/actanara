"""Local nova-RAG server process lifecycle helpers.

The nova-RAG subsystem owns its serving process under the selected Actanara runtime
state directory. This module does not build, promote or mutate indexes.
"""

from __future__ import annotations

import json
import os
import secrets
import signal
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any

from .rag_settings import RagSettings, resolve_rag_settings
from data_foundation.network import RAG_SERVER_NON_LOOPBACK_ISSUE_CODE, host_for_url, is_loopback_host

ROOT = Path(__file__).resolve().parents[2]
SRC_ROOT = ROOT / "src"
SERVER_SCRIPT = Path(__file__).resolve().parent / "embedding_server.py"
BASE_SERVER_MODULES = ("fastapi", "uvicorn", "numpy", "pydantic")
LOCAL_EMBEDDING_MODULES = ("sentence_transformers",)
REQUIRED_SERVER_MODULES = LOCAL_EMBEDDING_MODULES + BASE_SERVER_MODULES
MODULE_IMPORT_PROBE_TIMEOUT_SECONDS = 120

try:
    from data_foundation.paths import load_paths
except ImportError:  # pragma: no cover - direct script fallback
    load_paths = None  # type: ignore


def read_server_process_state(
    settings: RagSettings | None = None,
    *,
    probe_health: bool = False,
    timeout_seconds: float = 0.5,
) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    state_path = _state_path(resolved)
    log_path = _log_path(resolved)
    state = _read_json(state_path)
    pid = _optional_int(state.get("pid"))
    health = _probe_health(resolved, timeout_seconds=timeout_seconds) if probe_health else None
    running = (_pid_running(pid) if pid else False) or bool(health and health.get("healthy"))
    internal_token_path = rag_internal_token_path(resolved)
    internal_token_ready = bool(read_rag_internal_token(resolved))
    return {
        "enabled": resolved.server_enabled,
        "statePath": str(state_path),
        "logPath": str(log_path),
        "pid": pid,
        "running": running,
        "status": _process_status(resolved, running, health),
        "health": health,
        "startedAt": state.get("startedAt"),
        "stoppedAt": state.get("stoppedAt"),
        "error": state.get("error"),
        "python": state.get("python"),
        "command": state.get("command"),
        "host": resolved.server_host,
        "port": resolved.server_port,
        "internalAuthorization": {
            "status": "ready" if internal_token_ready else "missing",
            "tokenFile": str(internal_token_path),
            "tokenPresent": internal_token_ready,
            "secretExposed": False,
        },
    }


def start_rag_server(
    settings: RagSettings | None = None,
    *,
    requested_by: str = "dashboard",
    wait_timeout_seconds: float = 2.0,
) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    state_path = _state_path(resolved)
    log_path = _log_path(resolved)
    if not is_loopback_host(resolved.server_host):
        lifecycle = read_server_process_state(resolved, probe_health=False)
        return {
            "accepted": False,
            "status": "blocked",
            "reason": "nova-RAG direct server is restricted to loopback in macOS v1.",
            "issueCode": RAG_SERVER_NON_LOOPBACK_ISSUE_CODE,
            "lifecycle": lifecycle,
        }
    existing = read_server_process_state(resolved, probe_health=True)
    if existing["health"] and existing["health"].get("healthy"):
        return {
            "accepted": True,
            "status": "already-running",
            "reason": "nova-RAG search server health endpoint is already healthy.",
            "lifecycle": existing,
        }
    if existing["running"]:
        return {
            "accepted": True,
            "status": "running-unhealthy",
            "reason": "nova-RAG search server process is running but health is not ready yet.",
            "lifecycle": existing,
        }
    if not resolved.server_enabled:
        return {
            "accepted": False,
            "status": "disabled",
            "reason": "RAG serving is disabled because the RAG product is disabled.",
            "lifecycle": existing,
        }
    if not resolved.enabled or resolved.mode == "disabled":
        return {
            "accepted": False,
            "status": "rag-disabled",
            "reason": "nova-RAG subsystem is disabled in runtime settings.",
            "lifecycle": existing,
        }

    required_modules = _required_server_modules(resolved)
    python = _select_server_python(required_modules=required_modules)
    if python is None:
        result = {
            "accepted": False,
            "status": "missing-runtime",
            "reason": "The Actanara runtime venv is missing the local nova-RAG server dependencies.",
            "requiredModules": list(required_modules),
            "requiredInstallGroup": "rag-local" if resolved.embedding_provider == "local" else "rag-server",
            "installHint": (
                "Initialize nova-RAG from Dashboard to install the optional rag-local dependencies in the background, "
                "or run the installer with --enable-rag."
                if resolved.embedding_provider == "local"
                else "Repair the Actanara rag-server runtime dependencies before starting nova-RAG."
            ),
            "lifecycle": existing,
        }
        _write_json_atomic(state_path, {**_read_json(state_path), "error": result["reason"], "updatedAt": _now()})
        return result

    command = [python, str(SERVER_SCRIPT)]
    token_path = _rotate_internal_token(resolved)
    env = _server_env(resolved)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)
    with log_path.open("ab") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=str(ROOT),
            env=env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    state = {
        "schemaVersion": 1,
        "pid": process.pid,
        "python": python,
        "command": command,
        "cwd": str(ROOT),
        "host": resolved.server_host,
        "port": resolved.server_port,
        "mode": resolved.mode,
        "indexPath": _active_index_path_hint(resolved),
        "startedAt": _now(),
        "requestedBy": requested_by,
        "stoppedAt": None,
        "error": None,
        "internalTokenFile": str(token_path),
    }
    _write_json_atomic(state_path, state)
    lifecycle = _wait_for_health(resolved, timeout_seconds=wait_timeout_seconds)
    return {
        "accepted": True,
        "status": "running" if lifecycle.get("health", {}).get("healthy") else "starting",
        "reason": "nova-RAG search server process started.",
        "lifecycle": read_server_process_state(resolved, probe_health=True),
    }


def stop_rag_server(
    settings: RagSettings | None = None,
    *,
    requested_by: str = "dashboard",
    wait_timeout_seconds: float = 5.0,
) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    state_path = _state_path(resolved)
    state = _read_json(state_path)
    pid = _optional_int(state.get("pid"))
    lifecycle = read_server_process_state(resolved, probe_health=False)
    if not pid or not lifecycle["running"]:
        updated = {**state, "stoppedAt": _now(), "requestedBy": requested_by}
        _write_json_atomic(state_path, updated)
        return {
            "accepted": True,
            "status": "not-running",
            "reason": "No nova-RAG search server process recorded as running.",
            "lifecycle": read_server_process_state(resolved, probe_health=False),
        }
    if not _state_matches_rag_server(state):
        return {
            "accepted": False,
            "status": "refused",
            "reason": "Recorded process does not match the nova-RAG search server command; refusing to terminate it.",
            "lifecycle": lifecycle,
        }

    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    except OSError as exc:
        _write_json_atomic(state_path, {**state, "error": str(exc), "updatedAt": _now()})
        return {
            "accepted": False,
            "status": "stop-failed",
            "reason": str(exc),
            "lifecycle": read_server_process_state(resolved, probe_health=False),
        }

    deadline = time.time() + max(0.1, wait_timeout_seconds)
    while time.time() < deadline:
        if not _pid_running(pid):
            break
        time.sleep(0.1)
    stopped = not _pid_running(pid)
    _write_json_atomic(
        state_path,
        {
            **state,
            "stoppedAt": _now(),
            "requestedBy": requested_by,
            "status": "stopped" if stopped else "stopping",
        },
    )
    return {
        "accepted": True,
        "status": "stopped" if stopped else "stopping",
        "reason": "nova-RAG search server stop requested.",
        "lifecycle": read_server_process_state(resolved, probe_health=False),
    }


def _state_path(settings: RagSettings | None = None) -> Path:
    return _runtime_state_dir(settings) / "rag" / "server-state.json"


def _log_path(settings: RagSettings | None = None) -> Path:
    return _runtime_state_dir(settings) / "logs" / "rag-server.log"


def rag_internal_token_path(settings: RagSettings | None = None) -> Path:
    return _runtime_state_dir(settings) / "rag" / "internal-token"


def read_rag_internal_token(settings: RagSettings | None = None) -> str:
    path = rag_internal_token_path(settings)
    try:
        stat = path.stat()
        if not path.is_file() or stat.st_uid != os.getuid() or stat.st_mode & 0o077:
            return ""
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _rotate_internal_token(settings: RagSettings) -> Path:
    path = rag_internal_token_path(settings)
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.{secrets.token_hex(4)}.tmp")
    internal_credential = secrets.token_urlsafe(32)
    descriptor = os.open(staging, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(internal_credential + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(staging, path)
        os.chmod(path, 0o600)
    finally:
        try:
            staging.unlink()
        except FileNotFoundError:
            pass
    return path


def _runtime_state_dir(settings: RagSettings | None = None) -> Path:
    if settings is not None:
        home = _home_from_v2_store(settings.v2_store_path)
        if home is not None:
            return home / "state"
    if load_paths is None:
        return Path(os.getenv("ACTANARA_HOME", str(ROOT))) / "state"
    return load_paths().state_dir


def _home_from_v2_store(v2_store_path: Path) -> Path | None:
    path = Path(v2_store_path).expanduser().absolute()
    try:
        if path.name == "v2" and path.parent.name == "rag" and path.parent.parent.name == "reserved":
            return path.parent.parent.parent
    except IndexError:
        return None
    return None


def _required_server_modules(settings: RagSettings) -> tuple[str, ...]:
    if settings.embedding_provider == "local":
        return REQUIRED_SERVER_MODULES
    return BASE_SERVER_MODULES


def _select_server_python(*, required_modules: tuple[str, ...] = REQUIRED_SERVER_MODULES) -> str | None:
    candidates = [
        os.getenv("NOVA_RAG_SERVER_PYTHON"),
        sys.executable,
        _runtime_venv_python(),
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not candidate:
            continue
        path = str(Path(candidate).expanduser())
        if path in seen or not Path(path).exists():
            continue
        seen.add(path)
        if _python_has_modules(path, required_modules=required_modules):
            return path
    return None


def _python_has_modules(
    python: str,
    *,
    required_modules: tuple[str, ...] = REQUIRED_SERVER_MODULES,
) -> bool:
    code = "import sys; assert sys.version_info >= (3, 10); " + "; ".join(
        f"import {module}" for module in required_modules
    )
    try:
        try:
            timeout_seconds = int(
                os.getenv(
                    "NOVA_RAG_MODULE_IMPORT_PROBE_TIMEOUT_SECONDS",
                    str(MODULE_IMPORT_PROBE_TIMEOUT_SECONDS),
                )
            )
        except (TypeError, ValueError):
            timeout_seconds = MODULE_IMPORT_PROBE_TIMEOUT_SECONDS
        result = subprocess.run(
            [python, "-c", code],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=max(15, timeout_seconds),
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _runtime_venv_python() -> str | None:
    try:
        home = load_paths().home if load_paths is not None else Path(os.getenv("ACTANARA_HOME", ""))
    except Exception:
        home = Path(os.getenv("ACTANARA_HOME", ""))
    if not home:
        return None
    path = Path(home).expanduser() / ".venv" / "bin" / "python"
    return str(path) if path.exists() else None


def _server_env(settings: RagSettings) -> dict[str, str]:
    if not is_loopback_host(settings.server_host):
        raise ValueError("nova-RAG server environment refuses a non-loopback host")
    env = os.environ.copy()
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    pythonpath = [
        str(ROOT),
        str(SRC_ROOT),
        str(SRC_ROOT / "agentic_rag"),
        env.get("PYTHONPATH", ""),
    ]
    env["PYTHONPATH"] = os.pathsep.join([item for item in pythonpath if item])
    if "ACTANARA_HOME" not in env and load_paths is not None:
        env["ACTANARA_HOME"] = str(load_paths().home)
    env.update(
        {
            "NOVA_RAG_ENABLED": "true" if settings.enabled else "false",
            "NOVA_RAG_MODE": settings.mode,
            "NOVA_RAG_SERVER_ENABLED": "true" if settings.server_enabled else "false",
            "NOVA_RAG_SERVER_HOST": settings.server_host,
            "NOVA_RAG_SERVER_PORT": str(settings.server_port),
            "NOVA_RAG_SERVER_HEALTH_PATH": settings.server_health_path,
            "NOVA_RAG_INTERNAL_TOKEN_FILE": str(rag_internal_token_path(settings)),
            "NOVA_RAG_EMBEDDING_MODEL": settings.embedding_model,
            "NOVA_RAG_EMBEDDING_DIMENSION": str(settings.embedding_dimension),
            "NOVA_RAG_V2_STORE": str(settings.v2_store_path),
            "NOVA_RAG_LEGACY_INDEX": str(settings.legacy_index_path),
        }
    )
    return env


def _probe_health(settings: RagSettings, *, timeout_seconds: float) -> dict[str, Any]:
    url = f"http://{host_for_url(settings.server_host)}:{settings.server_port}{settings.server_health_path}"
    result: dict[str, Any] = {
        "url": url,
        "healthy": False,
        "statusCode": None,
        "error": None,
        "payload": None,
    }
    if not is_loopback_host(settings.server_host):
        result["error"] = RAG_SERVER_NON_LOOPBACK_ISSUE_CODE
        return result
    try:
        with urllib.request.urlopen(url, timeout=timeout_seconds) as response:
            result["statusCode"] = response.status
            result["healthy"] = 200 <= response.status < 300
            try:
                payload = json.loads(response.read().decode("utf-8"))
                result["payload"] = payload if isinstance(payload, dict) else None
            except (json.JSONDecodeError, UnicodeDecodeError):
                result["payload"] = None
    except (OSError, urllib.error.URLError) as exc:
        result["error"] = exc.__class__.__name__
    return result


def _wait_for_health(settings: RagSettings, *, timeout_seconds: float) -> dict[str, Any]:
    deadline = time.time() + max(0.0, timeout_seconds)
    health = _probe_health(settings, timeout_seconds=0.5)
    while not health["healthy"] and time.time() < deadline:
        time.sleep(0.2)
        health = _probe_health(settings, timeout_seconds=0.5)
    return {"health": health}


def _process_status(settings: RagSettings, running: bool, health: dict[str, Any] | None) -> str:
    if not settings.server_enabled:
        return "disabled"
    if health and health.get("healthy"):
        return "healthy"
    if running:
        return "running"
    return "stopped"


def _pid_running(pid: int | None) -> bool:
    if not pid or pid <= 0:
        return False
    try:
        reaped_pid, _status = os.waitpid(pid, os.WNOHANG)
        if reaped_pid == pid:
            return False
    except ChildProcessError:
        # The process is not our child (for example, launchd owns it); fall
        # through to the portable existence probe below.
        pass
    except OSError:
        pass
    try:
        os.kill(pid, 0)
        return True
    except PermissionError:
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _state_matches_rag_server(state: dict[str, Any]) -> bool:
    command = state.get("command")
    if not isinstance(command, list):
        return False
    command_text = " ".join(str(item) for item in command)
    return str(SERVER_SCRIPT) in command_text and str(ROOT) == str(state.get("cwd") or ROOT)


def _active_index_path_hint(settings: RagSettings) -> str | None:
    try:
        from .rag_active_source import resolve_active_rag_index

        active = resolve_active_rag_index(settings)
        return str(active.index_path) if active.index_path else None
    except Exception:
        return None


def _read_json(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return {}


def _write_json_atomic(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def _optional_int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _now() -> str:
    return datetime.now().astimezone().isoformat()
