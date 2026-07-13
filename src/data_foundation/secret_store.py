"""Runtime secret storage helpers.

Settings persist only logical references.  The default backend stores values in
the selected runtime's private ``state/secrets`` directory so unattended
pipeline processes do not depend on an interactive macOS Keychain session.
Legacy Keychain references remain readable for non-destructive migration.
"""

from __future__ import annotations

import hashlib
import os
import re
import secrets
import stat
import subprocess
from dataclasses import dataclass
from pathlib import Path


SERVICE = "open-nova"
_MEMORY_SECRETS: dict[tuple[str, str], str] = {}


@dataclass(frozen=True)
class SecretRef:
    backend: str
    service: str
    account: str

    def as_dict(self) -> dict[str, str]:
        return {"backend": self.backend, "service": self.service, "account": self.account}


def llm_api_key_ref(runtime_home: str, *, name: str = "llm-provider-api-key") -> SecretRef:
    del runtime_home  # Runtime location is deliberately absent from persisted refs.
    return SecretRef(backend=_default_backend(), service=SERVICE, account=_safe_account(name))


def rag_embedding_api_key_ref(
    runtime_home: str,
    *,
    provider_id: str = "cloud",
) -> SecretRef:
    """Return the runtime-local reference for one cloud embedding provider."""

    del runtime_home
    safe_provider = _safe_account(provider_id)
    return SecretRef(
        backend=_default_backend(),
        service=SERVICE,
        account=f"rag-embedding-api-key-{safe_provider}",
    )


def settings_transaction_secret_ref(
    runtime_home: str,
    transaction_id: str,
    *,
    provider_id: str,
) -> SecretRef:
    """Return a unique transaction-owned ref without embedding a private path."""

    normalized_transaction_id = str(transaction_id or "").strip().lower()
    if not re.fullmatch(r"[a-f0-9]{32}", normalized_transaction_id):
        raise ValueError("settings transaction id must be a 32-character hexadecimal value")
    runtime_id = hashlib.sha256(str(Path(runtime_home).expanduser().absolute()).encode("utf-8")).hexdigest()[:12]
    provider_resource_id = hashlib.sha256(str(provider_id or "custom").encode("utf-8")).hexdigest()[:12]
    account = f"settings-tx-{runtime_id}-{provider_resource_id}-{normalized_transaction_id}"
    return SecretRef(backend=_default_backend(), service=SERVICE, account=account)


def store_secret(
    ref: SecretRef | dict,
    value: str,
    *,
    runtime_home: str | os.PathLike[str] | None = None,
) -> dict[str, str]:
    normalized = _coerce_ref(ref)
    if not value:
        raise ValueError("secret value must be non-empty")
    if normalized.backend == "memory":
        _MEMORY_SECRETS[(normalized.service, normalized.account)] = value
        return normalized.as_dict()
    if normalized.backend == "process-env":
        raise RuntimeError("process-env secret backend is read-only; choose a writable secret backend")
    if normalized.backend == "runtime-file":
        _store_runtime_file_secret(normalized, value, runtime_home=runtime_home)
        return normalized.as_dict()
    if normalized.backend != "macos-keychain":
        raise RuntimeError(f"unsupported secret backend: {normalized.backend}")
    _store_macos_keychain_secret(normalized.service, normalized.account, value)
    return normalized.as_dict()


def read_secret(
    ref: SecretRef | dict,
    *,
    runtime_home: str | os.PathLike[str] | None = None,
) -> str:
    normalized = _coerce_ref(ref)
    if normalized.backend == "memory":
        return _MEMORY_SECRETS.get((normalized.service, normalized.account), "")
    if normalized.backend == "runtime-file":
        return _read_runtime_file_secret(normalized, runtime_home=runtime_home)
    if normalized.backend != "macos-keychain":
        return ""
    result = _security(
        "find-generic-password",
        "-a",
        normalized.account,
        "-s",
        normalized.service,
        "-w",
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.rstrip("\n")


def delete_secret(
    ref: SecretRef | dict,
    *,
    runtime_home: str | os.PathLike[str] | None = None,
) -> bool:
    normalized = _coerce_ref(ref)
    if normalized.backend == "memory":
        return _MEMORY_SECRETS.pop((normalized.service, normalized.account), None) is not None
    if normalized.backend == "runtime-file":
        return _delete_runtime_file_secret(normalized, runtime_home=runtime_home)
    if normalized.backend != "macos-keychain":
        return False
    result = _security(
        "delete-generic-password",
        "-a",
        normalized.account,
        "-s",
        normalized.service,
        check=False,
    )
    return result.returncode == 0


def _default_backend() -> str:
    forced = os.getenv("OPEN_NOVA_SECRET_BACKEND")
    if forced:
        return forced
    return "runtime-file"


def default_secret_backend() -> str:
    return _default_backend()


def _coerce_ref(ref: SecretRef | dict) -> SecretRef:
    if isinstance(ref, SecretRef):
        return ref
    if not isinstance(ref, dict):
        raise ValueError("secret reference must be an object")
    normalized = SecretRef(
        backend=str(ref.get("backend") or ""),
        service=str(ref.get("service") or SERVICE),
        account=str(ref.get("account") or ""),
    )
    if normalized.backend == "runtime-file":
        _validate_runtime_file_ref(normalized)
    return normalized


def _safe_account(value: str) -> str:
    raw = str(value or "")
    normalized = re.sub(r"[^A-Za-z0-9_.-]+", "-", raw).strip("-.")
    if not normalized:
        raise ValueError("secret account must contain a safe identifier")
    if normalized != raw or len(normalized) > 160:
        digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]
        normalized = f"{normalized[:140]}-{digest}"
    return normalized


def _validate_runtime_file_ref(ref: SecretRef) -> None:
    if not ref.service or not ref.account:
        raise ValueError("runtime-file secret service/account must be non-empty")
    for value in (ref.service, ref.account):
        if len(value) > 200 or value in {".", ".."} or not re.fullmatch(r"[A-Za-z0-9_.-]+", value):
            raise ValueError("runtime-file secret reference contains an invalid identifier")


def _runtime_home(runtime_home: str | os.PathLike[str] | None) -> Path:
    if runtime_home is not None:
        return Path(runtime_home).expanduser().absolute()
    # Imported lazily to avoid the paths -> secret_store import cycle.
    from .paths import load_paths

    return load_paths().home


def _runtime_secret_filename(ref: SecretRef) -> str:
    identifier = f"{ref.service}\0{ref.account}".encode("utf-8")
    return hashlib.sha256(identifier).hexdigest() + ".secret"


def _runtime_secret_root(
    runtime_home: str | os.PathLike[str] | None,
    *,
    create: bool,
) -> tuple[Path, int] | None:
    home = _runtime_home(runtime_home)
    state_root = home / "state"
    secret_root = state_root / "secrets"
    current_uid = os.getuid()

    if create:
        try:
            state_root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RuntimeError("runtime secret state directory could not be created") from exc
    try:
        state_info = state_root.lstat()
    except FileNotFoundError:
        return None
    if stat.S_ISLNK(state_info.st_mode) or not stat.S_ISDIR(state_info.st_mode) or state_info.st_uid != current_uid:
        raise RuntimeError("runtime secret state directory failed ownership or link validation")

    if create:
        try:
            secret_root.mkdir(mode=0o700, exist_ok=True)
        except OSError as exc:
            raise RuntimeError("runtime secret directory could not be created") from exc
    try:
        root_info = secret_root.lstat()
    except FileNotFoundError:
        return None
    if (
        stat.S_ISLNK(root_info.st_mode)
        or not stat.S_ISDIR(root_info.st_mode)
        or root_info.st_uid != current_uid
        or stat.S_IMODE(root_info.st_mode) != 0o700
    ):
        raise RuntimeError("runtime secret directory failed mode, ownership, or link validation")

    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(secret_root, flags)
    except OSError as exc:
        raise RuntimeError("runtime secret directory could not be opened safely") from exc
    descriptor_info = os.fstat(descriptor)
    if (
        not stat.S_ISDIR(descriptor_info.st_mode)
        or descriptor_info.st_uid != current_uid
        or stat.S_IMODE(descriptor_info.st_mode) != 0o700
        or (descriptor_info.st_dev, descriptor_info.st_ino) != (root_info.st_dev, root_info.st_ino)
    ):
        os.close(descriptor)
        raise RuntimeError("runtime secret directory changed during validation")
    return secret_root, descriptor


def _validate_runtime_secret_file(descriptor: int) -> None:
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        # An atomically replaced file can legitimately reach link count zero
        # after it has been opened. Multiple live links remain forbidden.
        or info.st_nlink > 1
    ):
        raise RuntimeError("runtime secret file failed mode, ownership, or link validation")


def _open_runtime_secret(root_descriptor: int, filename: str) -> int:
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(filename, flags, dir_fd=root_descriptor)
    except FileNotFoundError:
        raise
    except OSError as exc:
        raise RuntimeError("runtime secret file could not be opened safely") from exc
    try:
        _validate_runtime_secret_file(descriptor)
    except Exception:
        os.close(descriptor)
        raise
    return descriptor


def _store_runtime_file_secret(
    ref: SecretRef,
    value: str,
    *,
    runtime_home: str | os.PathLike[str] | None,
) -> None:
    _validate_runtime_file_ref(ref)
    encoded = value.encode("utf-8")
    if len(encoded) > 64 * 1024:
        raise ValueError("secret value is too large for runtime-file storage")
    opened = _runtime_secret_root(runtime_home, create=True)
    if opened is None:  # pragma: no cover - create=True contract guard
        raise RuntimeError("runtime secret directory is unavailable")
    _root, root_descriptor = opened
    filename = _runtime_secret_filename(ref)
    temporary = f".{filename}.{os.getpid()}.{secrets.token_hex(8)}.tmp"
    temporary_descriptor: int | None = None
    try:
        try:
            existing_descriptor = _open_runtime_secret(root_descriptor, filename)
        except FileNotFoundError:
            existing_descriptor = None
        if existing_descriptor is not None:
            os.close(existing_descriptor)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
        temporary_descriptor = os.open(temporary, flags, 0o600, dir_fd=root_descriptor)
        os.fchmod(temporary_descriptor, 0o600)
        offset = 0
        while offset < len(encoded):
            offset += os.write(temporary_descriptor, encoded[offset:])
        os.fsync(temporary_descriptor)
        descriptor_to_close = temporary_descriptor
        temporary_descriptor = None
        os.close(descriptor_to_close)
        os.replace(temporary, filename, src_dir_fd=root_descriptor, dst_dir_fd=root_descriptor)
        os.fsync(root_descriptor)
    except Exception as exc:
        if isinstance(exc, (ValueError, RuntimeError)):
            raise
        raise RuntimeError("runtime secret could not be stored safely") from exc
    finally:
        if temporary_descriptor is not None:
            os.close(temporary_descriptor)
        try:
            os.unlink(temporary, dir_fd=root_descriptor)
        except FileNotFoundError:
            pass
        os.close(root_descriptor)


def _read_runtime_file_secret(
    ref: SecretRef,
    *,
    runtime_home: str | os.PathLike[str] | None,
) -> str:
    _validate_runtime_file_ref(ref)
    opened = _runtime_secret_root(runtime_home, create=False)
    if opened is None:
        return ""
    _root, root_descriptor = opened
    descriptor: int | None = None
    try:
        try:
            descriptor = _open_runtime_secret(root_descriptor, _runtime_secret_filename(ref))
        except FileNotFoundError:
            return ""
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(descriptor, 8192)
            if not chunk:
                break
            total += len(chunk)
            if total > 64 * 1024:
                raise RuntimeError("runtime secret file exceeds the supported size")
            chunks.append(chunk)
        try:
            return b"".join(chunks).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise RuntimeError("runtime secret file is not valid UTF-8") from exc
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_descriptor)


def _delete_runtime_file_secret(
    ref: SecretRef,
    *,
    runtime_home: str | os.PathLike[str] | None,
) -> bool:
    _validate_runtime_file_ref(ref)
    opened = _runtime_secret_root(runtime_home, create=False)
    if opened is None:
        return False
    _root, root_descriptor = opened
    descriptor: int | None = None
    filename = _runtime_secret_filename(ref)
    try:
        try:
            descriptor = _open_runtime_secret(root_descriptor, filename)
        except FileNotFoundError:
            return False
        os.close(descriptor)
        descriptor = None
        try:
            os.unlink(filename, dir_fd=root_descriptor)
        except FileNotFoundError:
            return False
        os.fsync(root_descriptor)
        return True
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(root_descriptor)


def _store_macos_keychain_secret(
    service: str,
    account: str,
    value: str,
    *,
    executable: str = "/usr/bin/security",
    script_executable: str = "/usr/bin/script",
    timeout_seconds: float = 30.0,
) -> None:
    """Store a generic password without placing the secret in process arguments.

    The `security` CLI only prompts for a password when `-w` is attached to an
    interactive terminal. A pipe can therefore exit successfully while storing
    an empty value. Run that documented prompt on a private pseudo-terminal so
    existing Keychain ACL ownership remains with `/usr/bin/security`, while the
    value stays out of argv, logs, and temporary files. `/usr/bin/script`
    supplies the controlling terminal without forking a live Python process.
    """
    import errno
    import os
    import re
    import select
    import signal
    import time

    if not service or not account:
        raise ValueError("keychain service/account must be non-empty")
    if any(ord(character) < 32 or ord(character) == 127 for item in (service, account, value) for character in item):
        raise ValueError("keychain service/account/value must not contain terminal control characters")
    if timeout_seconds <= 0:
        raise ValueError("keychain store timeout must be positive")

    payload = value.encode("utf-8") + b"\n"
    if len(payload) > 1024:
        raise ValueError("keychain value is too large for the interactive store path")
    command = [
        script_executable,
        "-q",
        "-e",
        "/dev/null",
        "/bin/sh",
        "-c",
        'printf "OPEN_NOVA_SECURITY_PID=%s\\n" "$$"; exec "$@"',
        "open-nova-keychain-store",
        executable,
        "add-generic-password",
        "-a",
        account,
        "-s",
        service,
        "-U",
        "-w",
    ]
    process = subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        bufsize=0,
        start_new_session=True,
    )
    if process.stdin is None or process.stdout is None:  # pragma: no cover - Popen contract guard
        process.kill()
        process.wait()
        raise RuntimeError("macOS Keychain store could not allocate private pipes")

    deadline = time.monotonic() + float(timeout_seconds)
    stdout_fd = process.stdout.fileno()
    stdin_fd = process.stdin.fileno()
    prompt_output = bytearray()
    prompt_marker = b"password data for new item:"
    retype_prompt_marker = b"retype password for new item:"
    pid_pattern = re.compile(rb"OPEN_NOVA_SECURITY_PID=(\d+)")
    command_pid: int | None = None
    try:
        os.set_blocking(stdin_fd, False)

        def write_payload() -> None:
            offset = 0
            while offset < len(payload):
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("macOS Keychain store password write timed out")
                _, writable, _ = select.select([], [stdin_fd], [], min(0.1, remaining))
                if writable:
                    offset += os.write(stdin_fd, payload[offset:])

        prompt_seen = False
        while not (prompt_seen and command_pid is not None):
            result = process.poll()
            if result is not None:
                raise RuntimeError(f"macOS Keychain store command exited before password prompt with status {result}")
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("macOS Keychain store password prompt timed out")
            readable, _, _ = select.select([stdout_fd], [], [], min(0.1, remaining))
            if not readable:
                continue
            try:
                prompt_output.extend(os.read(stdout_fd, 4096))
                if len(prompt_output) > 8192:
                    del prompt_output[:-8192]
                lowered = bytes(prompt_output).lower()
                prompt_seen = prompt_marker in lowered
                pid_match = pid_pattern.search(prompt_output)
                if pid_match:
                    command_pid = int(pid_match.group(1))
            except OSError as exc:
                if exc.errno != errno.EIO:
                    raise

        write_payload()

        retype_sent = False
        while True:
            result = process.poll()
            if result is not None:
                break
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("macOS Keychain store timed out")
            readable, _, _ = select.select([stdout_fd], [], [], min(0.1, remaining))
            if readable:
                try:
                    prompt_output.extend(os.read(stdout_fd, 4096))
                    if len(prompt_output) > 8192:
                        del prompt_output[:-8192]
                    if not retype_sent and retype_prompt_marker in bytes(prompt_output).lower():
                        write_payload()
                        retype_sent = True
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise

        if result != 0:
            raise RuntimeError(f"macOS Keychain store command failed with status {result}")
    finally:
        try:
            process.stdin.close()
        except (BrokenPipeError, OSError):
            pass
        if process.poll() is None:
            if command_pid is not None:
                _kill_process_group(command_pid)
                try:
                    process.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        if process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        process.wait()
        process.stdout.close()


def _kill_process_group(pid: int) -> None:
    import os
    import signal

    try:
        group_id = os.getpgid(pid)
    except ProcessLookupError:
        return
    try:
        if group_id != os.getpgrp():
            os.killpg(group_id, signal.SIGKILL)
        else:
            os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _security(*args: str, check: bool = True, timeout_seconds: float = 30.0) -> subprocess.CompletedProcess[str]:
    command = ["/usr/bin/security", *args]
    try:
        return subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=check,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        if check:
            raise RuntimeError("macOS Keychain command timed out") from None
        return subprocess.CompletedProcess(command, 124, "", "")
