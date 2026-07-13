import hashlib
import http.server
import json
import os
import plistlib
import shutil
import signal
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import threading
import time
import unittest
import uuid
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install" / "install.sh"
UPDATE_HELPER = ROOT / "install" / "update_transaction.py"


class InstallerFullUpgradeTests(unittest.TestCase):
    maxDiff = None

    def _tree_digest(self, root: Path) -> str:
        digest = hashlib.sha256()
        for path in sorted(root.rglob("*")):
            if not (path.is_file() or path.is_symlink()):
                continue
            relative = path.relative_to(root).as_posix()
            content = (
                ("symlink:" + os.readlink(path)).encode("utf-8")
                if path.is_symlink()
                else path.read_bytes()
            )
            digest.update(relative.encode("utf-8"))
            digest.update(b"\0")
            digest.update(hashlib.sha256(content).digest())
            digest.update(b"\n")
        return digest.hexdigest()

    def _start_health_server(self) -> tuple[http.server.ThreadingHTTPServer, threading.Thread, int]:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                body = json.dumps(
                    {"sourceCommit": source_commit, "status": "ok"},
                    sort_keys=True,
                ).encode("utf-8") + b"\n"
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), HealthHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        return server, thread, int(server.server_address[1])

    def _write_fake_python(self, path: Path, log_path: Path) -> None:
        candidate_program = textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import signal
            import sqlite3
            import sys
            import time
            from pathlib import Path

            LOG_PATH = Path({str(log_path)!r})
            FAULT_CONFIG_PATH = Path({str(log_path.with_name("fake-python-fault.json"))!r})


            def fixture_value(name, default=None):
                try:
                    payload = json.loads(FAULT_CONFIG_PATH.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    payload = dict()
                value = payload.get(name) if isinstance(payload, dict) else None
                return os.environ.get(name, default) if value is None else str(value)


            def record(actor, phase):
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with LOG_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({{
                        "actor": actor,
                        "phase": phase,
                        "argv": sys.argv[1:],
                        "novaHome": os.environ.get("NOVA_HOME"),
                        "locationFile": os.environ.get("NOVA_LOCATION_FILE"),
                        "home": os.environ.get("HOME"),
                        "tmpDir": os.environ.get("TMPDIR"),
                        "xdgConfigHome": os.environ.get("XDG_CONFIG_HOME"),
                        "pipConfigFile": os.environ.get("PIP_CONFIG_FILE"),
                        "pipCacheDir": os.environ.get("PIP_CACHE_DIR"),
                        "pythonNoUserSite": os.environ.get("PYTHONNOUSERSITE"),
                        "secretBackend": os.environ.get("OPEN_NOVA_SECRET_BACKEND"),
                        "syntheticSentinelPresent": (
                            "OPEN_NOVA_SYNTHETIC_SECRET_SENTINEL" in os.environ
                        ),
                    }}) + "\\n")


            def maybe_fault(phase):
                if fixture_value("NOVA_FULL_UPGRADE_FAULT_PHASE") != phase:
                    return
                marker = fixture_value("NOVA_FULL_UPGRADE_FAULT_MARKER")
                if marker:
                    Path(marker).write_text(phase + "\\n", encoding="utf-8")
                kind = fixture_value("NOVA_FULL_UPGRADE_FAULT_KIND")
                if kind == "return":
                    raise SystemExit(86)
                if kind == "term":
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(0.05)
                    raise SystemExit(143)
                if kind == "kill":
                    os.kill(os.getpid(), signal.SIGKILL)
                    time.sleep(0.05)
                    raise SystemExit(137)
                if kind == "orphan-kill":
                    pid_path = Path(fixture_value("NOVA_FULL_UPGRADE_ORPHAN_PID"))
                    late_path = Path(fixture_value("NOVA_FULL_UPGRADE_LATE_MARKER"))
                    owner_pid_path = Path(fixture_value("NOVA_FULL_UPGRADE_OWNER_PID"))
                    pid_path.write_text(str(os.getpid()) + "\\n", encoding="utf-8")
                    os.kill(int(owner_pid_path.read_text(encoding="utf-8").strip()), signal.SIGKILL)
                    for descriptor in (0, 1, 2):
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    time.sleep(1.0)
                    late_path.parent.mkdir(parents=True, exist_ok=True)
                    late_path.write_text(phase + "\\n", encoding="utf-8")
                    raise SystemExit(137)
                raise SystemExit("unsupported full-upgrade fault kind")


            args = sys.argv[1:]
            if args[:3] == ["-m", "pip", "install"]:
                record("candidate", "pip")
                maybe_fault("pip")
                raise SystemExit(0)
            if args == ["-"]:
                record("candidate", "dependency")
                maybe_fault("dependency")
                print("dependency gate ok: isolated full-upgrade candidate")
                raise SystemExit(0)
            if args[:3] == ["-m", "data_foundation.cli", "doctor"]:
                record("candidate", "doctor")
                migration_database = fixture_value(
                    "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_DATABASE", ""
                )
                migration_version = fixture_value(
                    "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_VERSION", ""
                )
                if bool(migration_database) != bool(migration_version):
                    raise SystemExit("candidate doctor additive fixture is incomplete")
                if migration_database:
                    with sqlite3.connect(migration_database) as connection:
                        connection.execute(
                            "CREATE TABLE candidate_additive_probe "
                            "(id INTEGER PRIMARY KEY, value TEXT NOT NULL)"
                        )
                        connection.execute(
                            "INSERT INTO candidate_additive_probe(id, value) VALUES (1, ?)",
                            ("candidate-touch",),
                        )
                        connection.execute(
                            "INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                            (migration_version, "candidate-doctor"),
                        )
                        connection.commit()
                    record("candidate", "doctor-additive-migration")
                maybe_fault("doctor")
                print('{{"status":"ok","fixture":"candidate-doctor"}}')
                raise SystemExit(0)
            record("candidate", "other")
            raise SystemExit(0)
            """
        )
        wrapper_program = textwrap.dedent(
            f"""\
            #!{sys.executable}
            import json
            import os
            import signal
            import sys
            import time
            from pathlib import Path

            REAL_PYTHON = {sys.executable!r}
            LOG_PATH = Path({str(log_path)!r})
            FAULT_CONFIG_PATH = Path({str(log_path.with_name("fake-python-fault.json"))!r})
            CANDIDATE_PROGRAM = {candidate_program!r}


            def fixture_value(name, default=None):
                try:
                    payload = json.loads(FAULT_CONFIG_PATH.read_text(encoding="utf-8"))
                except (FileNotFoundError, json.JSONDecodeError, OSError):
                    payload = dict()
                value = payload.get(name) if isinstance(payload, dict) else None
                return os.environ.get(name, default) if value is None else str(value)


            def record(actor, phase):
                LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
                with LOG_PATH.open("a", encoding="utf-8") as handle:
                    handle.write(json.dumps({{
                        "actor": actor,
                        "phase": phase,
                        "argv": sys.argv[1:],
                        "novaHome": os.environ.get("NOVA_HOME"),
                        "locationFile": os.environ.get("NOVA_LOCATION_FILE"),
                        "home": os.environ.get("HOME"),
                        "tmpDir": os.environ.get("TMPDIR"),
                        "xdgConfigHome": os.environ.get("XDG_CONFIG_HOME"),
                        "pipConfigFile": os.environ.get("PIP_CONFIG_FILE"),
                        "pipCacheDir": os.environ.get("PIP_CACHE_DIR"),
                        "pythonNoUserSite": os.environ.get("PYTHONNOUSERSITE"),
                        "secretBackend": os.environ.get("OPEN_NOVA_SECRET_BACKEND"),
                        "syntheticSentinelPresent": (
                            "OPEN_NOVA_SYNTHETIC_SECRET_SENTINEL" in os.environ
                        ),
                    }}) + "\\n")


            def maybe_fault(phase):
                if fixture_value("NOVA_FULL_UPGRADE_FAULT_PHASE") != phase:
                    return
                marker = fixture_value("NOVA_FULL_UPGRADE_FAULT_MARKER")
                if marker:
                    Path(marker).write_text(phase + "\\n", encoding="utf-8")
                kind = fixture_value("NOVA_FULL_UPGRADE_FAULT_KIND")
                if kind == "return":
                    raise SystemExit(85)
                if kind == "term":
                    os.kill(os.getpid(), signal.SIGTERM)
                    time.sleep(0.05)
                    raise SystemExit(143)
                if kind == "kill":
                    os.kill(os.getpid(), signal.SIGKILL)
                    time.sleep(0.05)
                    raise SystemExit(137)
                if kind == "orphan-kill":
                    pid_path = Path(fixture_value("NOVA_FULL_UPGRADE_ORPHAN_PID"))
                    late_path = Path(fixture_value("NOVA_FULL_UPGRADE_LATE_MARKER"))
                    owner_pid_path = Path(fixture_value("NOVA_FULL_UPGRADE_OWNER_PID"))
                    pid_path.write_text(str(os.getpid()) + "\\n", encoding="utf-8")
                    os.kill(int(owner_pid_path.read_text(encoding="utf-8").strip()), signal.SIGKILL)
                    for descriptor in (0, 1, 2):
                        try:
                            os.close(descriptor)
                        except OSError:
                            pass
                    time.sleep(1.0)
                    late_path.parent.mkdir(parents=True, exist_ok=True)
                    late_path.write_text(phase + "\\n", encoding="utf-8")
                    raise SystemExit(137)
                raise SystemExit("unsupported full-upgrade fault kind")


            args = sys.argv[1:]
            if args[:2] == ["-m", "venv"] and len(args) == 3:
                record("builder", "venv-build")
                maybe_fault("venv-build")
                candidate_python = Path(args[2]) / "bin" / "python"
                candidate_python.parent.mkdir(parents=True, exist_ok=True)
                candidate_python.write_text(CANDIDATE_PROGRAM, encoding="utf-8")
                candidate_python.chmod(0o755)
                raise SystemExit(0)
            os.execv(REAL_PYTHON, [REAL_PYTHON, *args])
            """
        )
        path.write_text(wrapper_program, encoding="utf-8")
        path.chmod(0o755)

    def _write_fake_launchctl(self, path: Path) -> None:
        path.write_text(
            textwrap.dedent(
                f"""\
                #!{sys.executable}
                import os
                import sys
                from pathlib import Path

                prefix = os.environ["NOVA_TEST_LABEL_PREFIX"]
                state_dir = Path(os.environ["NOVA_TEST_LAUNCHCTL_STATE"])
                calls_path = Path(os.environ["NOVA_TEST_LAUNCHCTL_CALLS"])
                args = sys.argv[1:]
                calls_path.parent.mkdir(parents=True, exist_ok=True)
                with calls_path.open("a", encoding="utf-8") as handle:
                    handle.write(" ".join(args) + "\\n")
                if not args:
                    raise SystemExit(64)

                command = args[0]
                if command == "print":
                    target = args[1] if len(args) > 1 else ""
                    if target.startswith("gui/") and target.count("/") == 1:
                        print("state = running")
                        raise SystemExit(0)
                    label = target.rsplit("/", 1)[-1]
                    if not label.startswith(prefix):
                        raise SystemExit(113)
                    state_file = state_dir / label
                    if not state_file.is_file():
                        raise SystemExit(113)
                    print("state = " + state_file.read_text(encoding="utf-8").strip())
                    raise SystemExit(0)

                if command == "bootstrap":
                    label = Path(args[-1]).stem
                else:
                    label = args[-1].rsplit("/", 1)[-1]
                if not label.startswith(prefix):
                    raise SystemExit(77)
                state_dir.mkdir(parents=True, exist_ok=True)
                state_file = state_dir / label
                if command == "bootout":
                    state_file.unlink(missing_ok=True)
                elif command == "bootstrap":
                    fail_label = os.environ.get("NOVA_TEST_BOOTSTRAP_FAIL_ONCE_LABEL", "")
                    fail_marker_text = os.environ.get("NOVA_TEST_BOOTSTRAP_FAIL_ONCE_MARKER", "")
                    fail_marker = Path(fail_marker_text) if fail_marker_text else None
                    if label == fail_label and fail_marker is not None and not fail_marker.exists():
                        fail_marker.write_text(label + "\\n", encoding="utf-8")
                        raise SystemExit(78)
                    state_file.write_text(
                        ("running" if label.endswith(".rag-server") else "waiting") + "\\n",
                        encoding="utf-8",
                    )
                elif command == "kickstart":
                    state_file.write_text("running\\n", encoding="utf-8")
                else:
                    raise SystemExit(64)
                """
            ),
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_plist(self, path: Path, *, label: str, runtime: Path) -> None:
        source = runtime / "app" / "releases" / "old-release"
        python = runtime / ".venv" / "bin" / "python"
        environment = {
            "NOVA_HOME": str(runtime),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if label.endswith(".dashboard.watchdog"):
            arguments = [
                str(python),
                str(source / "advanced" / "dashboard" / "dashboard_launch_agent.py"),
                "check",
                "--url",
                "http://127.0.0.1:49151/health",
                "--label",
                label.removesuffix(".watchdog"),
                "--restart",
            ]
            payload = {
                "Label": label,
                "ProgramArguments": arguments,
                "EnvironmentVariables": environment,
            }
        elif label.endswith(".rag-server"):
            environment["PYTHONPATH"] = f"{source}:{source / 'src'}"
            payload = {
                "Label": label,
                "ProgramArguments": [
                    str(python),
                    str(source / "advanced" / "dashboard" / "rag_server_launch_agent.py"),
                    "run",
                    "--project-root",
                    str(source),
                    "--nova-home",
                    str(runtime),
                ],
                "EnvironmentVariables": environment,
            }
        elif label.endswith((".pipeline", ".dashboard-aggregation")):
            script = (
                "run_daily_pipeline.py"
                if label.endswith(".pipeline")
                else "run_dashboard_foundation_refresh.py"
            )
            environment["PYTHONPATH"] = f"{source}:{source / 'src'}:{source / 'src' / 'dashboard'}"
            payload = {
                "Label": label,
                "ProgramArguments": [
                    str(python),
                    str(source / "advanced" / "pipeline" / script),
                ],
                "WorkingDirectory": str(source),
                "EnvironmentVariables": environment,
            }
        else:
            environment.update(
                {
                    "NOVA_DASHBOARD_PROJECT_ROOT": str(source),
                    "NOVA_DASHBOARD_PYTHON": str(python),
                    "PYTHONPATH": f"{source}:{source / 'src'}:{source / 'src' / 'dashboard'}",
                }
            )
            payload = {
                "Label": label,
                "ProgramArguments": [
                    "/bin/zsh",
                    "-lc",
                    f"cd {source} && exec {python} -m uvicorn app.main:app "
                    f"--app-dir {source / 'src' / 'dashboard'} --host 127.0.0.1 --port 49151",
                ],
                "EnvironmentVariables": environment,
            }
        with path.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)

    def _fixture(self, root: Path) -> dict[str, object]:
        home = root / "Home"
        runtime = home / ".open-nova"
        app = runtime / "app"
        old_source = app / "releases" / "old-release"
        old_venv = runtime / ".venv"
        launch_agents = home / "Library" / "LaunchAgents"
        state_dir = root / "launchctl-state"
        for directory in (
            old_source,
            old_venv / "bin",
            runtime / "config",
            runtime / "data",
            runtime / "bin",
            launch_agents,
            state_dir,
            root / "config",
        ):
            directory.mkdir(parents=True, exist_ok=True)

        (app / "source").symlink_to("releases/old-release")
        (old_source / "pyproject.toml").write_text(
            '[project]\nname = "open-nova-old-fixture"\nversion = "0"\n',
            encoding="utf-8",
        )
        (old_source / ".open-nova-runtime-source.json").write_text(
            '{"fixture":"old-source"}\n',
            encoding="utf-8",
        )
        shutil.copytree(
            ROOT / "src" / "data_foundation" / "migrations",
            old_source / "src" / "data_foundation" / "migrations",
        )
        old_python = old_venv / "bin" / "python"
        old_python.write_text("#!/bin/sh\n# old-venv-fixture\nexit 0\n", encoding="utf-8")
        old_python.chmod(0o755)

        prefix = "com.open-nova.session-d-full-upgrade-" + uuid.uuid4().hex[:12]
        labels = {
            "dashboard": f"{prefix}.dashboard",
            "watchdog": f"{prefix}.dashboard.watchdog",
            "rag": f"{prefix}.rag-server",
            "pipeline": f"{prefix}.pipeline",
            "aggregation": f"{prefix}.dashboard-aggregation",
        }
        plist_paths = {key: launch_agents / f"{label}.plist" for key, label in labels.items()}
        for key, label in labels.items():
            self._write_plist(plist_paths[key], label=label, runtime=runtime)

        settings = runtime / "config" / "settings.json"
        settings.write_text(
            json.dumps(
                {
                    "dashboard": {
                        "host": "127.0.0.1",
                        "port": 49151,
                        "healthPath": "/health",
                        "serviceLabel": labels["dashboard"],
                        "watchdogLabel": labels["watchdog"],
                    },
                    "rag": {
                        "server": {
                            "host": "127.0.0.1",
                            "port": 49152,
                            "healthPath": "/health",
                            "launchAgent": {
                                "jobs": [
                                    {"label": labels["rag"], "plistPath": str(plist_paths["rag"])}
                                ]
                            },
                        }
                    },
                    "schedule": {
                        "systemTimer": {
                            "jobs": [
                                {
                                    "label": labels["pipeline"],
                                    "plistPath": str(plist_paths["pipeline"]),
                                },
                                {
                                    "label": labels["aggregation"],
                                    "plistPath": str(plist_paths["aggregation"]),
                                },
                            ]
                        }
                    },
                },
                sort_keys=True,
                separators=(",", ":"),
            )
            + "\n",
            encoding="utf-8",
        )
        runtime_manifest = runtime / "config" / "runtime.json"
        runtime_manifest.write_text('{"fixture":"runtime-before"}\n', encoding="utf-8")
        location = root / "config" / "location.json"
        location.write_text(json.dumps({"runtime": str(runtime)}, sort_keys=True) + "\n", encoding="utf-8")

        database = runtime / "data" / "nova_data.sqlite3"
        with closing(sqlite3.connect(database)) as connection:
            self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone(), ("wal",))
            connection.execute("CREATE TABLE evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL)")
            connection.execute(
                "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
            )
            connection.execute(
                "INSERT INTO schema_migrations(version, applied_at) VALUES ('0001_initial', 'fixture')"
            )
            connection.execute("INSERT INTO evidence(value) VALUES ('before-full-upgrade')")
            connection.commit()
            connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")

        initial_state = {labels["pipeline"]: "waiting"}
        for label, value in initial_state.items():
            (state_dir / label).write_text(value + "\n", encoding="utf-8")

        calls = root / "launchctl-calls.log"
        fake_launchctl = root / "launchctl"
        self._write_fake_launchctl(fake_launchctl)
        python_log = root / "fake-python.jsonl"
        python_fault_config = root / "fake-python-fault.json"
        fake_python = root / "python"
        self._write_fake_python(fake_python, python_log)
        marker = root / "fault-reached"

        protected_paths = [settings, runtime_manifest, location, database, *plist_paths.values()]
        protected_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected_paths}
        protected_bytes = {path: path.read_bytes() for path in protected_paths}
        env = os.environ.copy()
        for key in (
            "NOVA_INSTALL_TEST_FAIL_PHASE",
            "NOVA_INSTALL_TEST_HOOK",
            "NOVA_FULL_UPGRADE_FAULT_PHASE",
            "NOVA_FULL_UPGRADE_FAULT_KIND",
            "NOVA_FULL_UPGRADE_FAULT_MARKER",
            "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_DATABASE",
            "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_VERSION",
            "NOVA_INSTALL_LLM_API_KEY_VALUE",
            "OPEN_NOVA_SYNTHETIC_SECRET_SENTINEL",
        ):
            env.pop(key, None)
        env.update(
            {
                "HOME": str(home),
                "NOVA_HOME": str(runtime),
                "NOVA_LOCATION_FILE": str(location),
                "OPEN_NOVA_SECRET_BACKEND": "memory",
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_INSTALL_LAUNCHCTL": str(fake_launchctl),
                "NOVA_TEST_LAUNCHCTL_CALLS": str(calls),
                "NOVA_TEST_LAUNCHCTL_STATE": str(state_dir),
                "NOVA_TEST_LABEL_PREFIX": prefix,
                "NOVA_INSTALL_TEST_MODE": "1",
                "NOVA_INSTALL_TEST_HEALTH_TIMEOUT_SECONDS": "0.2",
                "NOVA_INSTALL_WIZARD": "0",
                "OPEN_NOVA_SYNTHETIC_SECRET_SENTINEL": "synthetic-test-only-not-a-secret",
                "PYTHONDONTWRITEBYTECODE": "1",
            }
        )
        command = [
            "zsh",
            str(INSTALLER),
            "--upgrade",
            "--runtime",
            str(runtime),
            "--source-root",
            str(ROOT),
            "--python",
            str(fake_python),
            "--no-python-auto-install",
            "--yes",
            "--no-scheduler",
            "--no-dashboard-server",
            "--no-desktop-diary-link",
            "--no-shell-path",
        ]
        return {
            "root": root,
            "home": home,
            "runtime": runtime,
            "app": app,
            "old_source": old_source,
            "old_venv": old_venv,
            "labels": labels,
            "plist_paths": plist_paths,
            "initial_state": initial_state,
            "state_dir": state_dir,
            "calls": calls,
            "fake_launchctl": fake_launchctl,
            "python_log": python_log,
            "python_fault_config": python_fault_config,
            "marker": marker,
            "protected_hashes": protected_hashes,
            "protected_bytes": protected_bytes,
            "env": env,
            "command": command,
        }

    def _run_update(
        self,
        fixture: dict[str, object],
        *,
        env_overrides: dict[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        env = dict(fixture["env"])
        env.update(env_overrides or {})
        fault_config = Path(fixture["python_fault_config"])
        fault_payload = {
            key: value
            for key, value in (env_overrides or {}).items()
            if key.startswith("NOVA_FULL_UPGRADE_")
        }
        if fault_payload:
            fault_config.write_text(
                json.dumps(fault_payload, sort_keys=True) + "\n",
                encoding="utf-8",
            )
        else:
            fault_config.unlink(missing_ok=True)
        return subprocess.run(
            fixture["command"],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
            timeout=120,
        )

    def _run_helper(self, fixture: dict[str, object], *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(UPDATE_HELPER), *args],
            cwd=ROOT,
            env=fixture["env"],
            text=True,
            capture_output=True,
            check=False,
            timeout=30,
        )

    def _write_outer_hook(self, fixture: dict[str, object], *, phase: str, kind: str) -> Path:
        hook = Path(fixture["root"]) / f"outer-hook-{phase}-{kind}"
        hook.write_text(
            "#!/bin/zsh\n"
            f'if [[ "$1" == {phase!r} ]]; then\n'
            f'  print -r -- "$1" > {str(fixture["marker"])!r}\n'
            + ('  kill -TERM "$PPID"\n' if kind == "term" else '  kill -KILL "$PPID"\n')
            + "fi\n",
            encoding="utf-8",
        )
        hook.chmod(0o755)
        return hook

    def _journal_paths(self, fixture: dict[str, object]) -> list[Path]:
        return sorted(
            path.resolve()
            for path in (Path(fixture["app"]) / "update-transactions").glob("*/journal.json")
        )

    def _launchctl_calls(self, fixture: dict[str, object]) -> list[str]:
        path = Path(fixture["calls"])
        return path.read_text(encoding="utf-8").splitlines() if path.exists() else []

    def _service_state(self, fixture: dict[str, object]) -> dict[str, str]:
        prefix = str(fixture["env"]["NOVA_TEST_LABEL_PREFIX"])
        return {
            path.name: path.read_text(encoding="utf-8").strip()
            for path in Path(fixture["state_dir"]).iterdir()
            if path.is_file() and path.name.startswith(prefix)
        }

    def _pid_alive(self, pid: int) -> bool:
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        return True

    def _wait_pid_dead(self, pid: int, *, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not self._pid_alive(pid):
                return True
            time.sleep(0.05)
        return not self._pid_alive(pid)

    def _assert_no_launchctl_mutation(self, fixture: dict[str, object]) -> None:
        mutations = [
            call
            for call in self._launchctl_calls(fixture)
            if call.split(" ", 1)[0] in {"bootout", "bootstrap", "kickstart"}
        ]
        self.assertEqual(mutations, [])

    def _assert_protected_unchanged(self, fixture: dict[str, object]) -> None:
        expected = fixture["protected_hashes"]
        actual = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in expected}
        self.assertEqual(actual, expected)

    def _assert_protected_after_success(self, fixture: dict[str, object]) -> None:
        plist_paths = set(fixture["plist_paths"].values())
        runtime = Path(fixture["runtime"]).resolve()
        stable_source = str(runtime / "app" / "source")
        stable_venv = str(runtime / ".venv")
        for path, expected_hash in fixture["protected_hashes"].items():
            if path not in plist_paths:
                self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)
                continue
            before = plistlib.loads(fixture["protected_bytes"][path])
            after = plistlib.loads(path.read_bytes())
            self.assertEqual(after["Label"], before["Label"])
            self.assertEqual(after["EnvironmentVariables"]["NOVA_HOME"], str(runtime))
            self.assertEqual(after["EnvironmentVariables"]["PYTHONDONTWRITEBYTECODE"], "1")
            serialized = json.dumps(after, sort_keys=True)
            self.assertIn(stable_source, serialized)
            self.assertIn(stable_venv, serialized)
            self.assertNotIn("/app/releases/old-release", serialized)

    def _assert_prior_runtime(self, fixture: dict[str, object]) -> None:
        app = Path(fixture["app"])
        old_source = Path(fixture["old_source"])
        old_venv = Path(fixture["old_venv"])
        source = app / "source"
        self.assertTrue(source.is_symlink())
        self.assertEqual(source.resolve(), old_source.resolve())
        self.assertTrue(old_venv.is_dir())
        self.assertFalse(old_venv.is_symlink())
        self.assertIn("old-venv-fixture", (old_venv / "bin" / "python").read_text(encoding="utf-8"))
        self.assertEqual(self._service_state(fixture), fixture["initial_state"])
        self._assert_protected_unchanged(fixture)

    def _journal_events(self, journal: Path) -> list[str]:
        events_path = journal.parent / "events.jsonl"
        return [
            json.loads(line)["event"]
            for line in events_path.read_text(encoding="utf-8").splitlines()
        ]

    def _assert_rolled_back_artifacts_cleaned(self, journal: Path) -> None:
        state = json.loads(journal.read_text(encoding="utf-8"))
        self.assertEqual(state["status"], "rolled-back")
        artifacts = {
            artifact["kind"]: artifact
            for artifact in state.get("candidateArtifacts") or []
        }
        reserved = {
            event.rsplit(":", 1)[-1]
            for event in self._journal_events(journal)
            if event.startswith("candidate-artifact-reserved:")
        }
        for kind, artifact in artifacts.items():
            path = Path(artifact["path"])
            self.assertFalse(artifact["created"], artifact)
            self.assertFalse(path.exists() or path.is_symlink(), artifact)
            self.assertIsNone(artifact["cleanupPath"], artifact)
            if kind not in reserved:
                continue
            if artifact["transferred"]:
                self.assertEqual(kind, "source-temp", artifact)
            else:
                self.assertTrue(artifact["cleaned"], artifact)

    def _assert_successful_full_upgrade(self, fixture: dict[str, object]) -> None:
        runtime = Path(fixture["runtime"])
        app = Path(fixture["app"])
        source = app / "source"
        venv = runtime / ".venv"
        self.assertTrue(source.is_symlink())
        self.assertNotEqual(source.resolve(), Path(fixture["old_source"]).resolve())
        self.assertEqual(os.readlink(source), f"releases/{source.resolve().name}")
        self.assertTrue((source / ".open-nova-runtime-source.json").is_file())
        self.assertTrue(venv.is_symlink())
        self.assertEqual(os.readlink(venv), f"app/venvs/{venv.resolve().name}")
        self.assertEqual(source.resolve().name, venv.resolve().name)
        self.assertTrue((venv / "bin" / "python").is_file())
        self.assertEqual(self._service_state(fixture), fixture["initial_state"])
        self._assert_protected_after_success(fixture)
        self.assertFalse((app / ".update-transaction.lock").exists())
        committed = [
            (journal, json.loads(journal.read_text(encoding="utf-8")))
            for journal in self._journal_paths(fixture)
            if json.loads(journal.read_text(encoding="utf-8"))["status"] == "committed"
        ]
        self.assertEqual(len(committed), 1, committed)
        committed_journal, committed_state = committed[0]
        events = set(self._journal_events(committed_journal))
        self.assertTrue(
            {
                "candidate-artifact-reserved:source-temp",
                "candidate-artifact-reserved:venv",
                "candidate-artifact-reserved:validation-runtime",
            }.issubset(events),
            events,
        )
        artifacts = {
            artifact["kind"]: artifact
            for artifact in committed_state["candidateArtifacts"]
        }
        validation = artifacts["validation-runtime"]
        self.assertFalse(validation["created"], validation)
        self.assertTrue(validation["cleaned"], validation)
        self.assertFalse(Path(validation["path"]).exists(), validation)
        temporary_source = artifacts["source-temp"]
        self.assertFalse(temporary_source["created"], temporary_source)
        self.assertTrue(temporary_source["transferred"], temporary_source)
        self.assertFalse(Path(temporary_source["path"]).exists(), temporary_source)
        for kind, active_path in (("source", source.resolve()), ("venv", venv.resolve())):
            artifact = artifacts[kind]
            self.assertTrue(artifact["created"], artifact)
            self.assertTrue(artifact["markerRemoved"], artifact)
            self.assertFalse(artifact["cleaned"], artifact)
            self.assertEqual(Path(artifact["path"]).resolve(), active_path, artifact)

        records = [
            json.loads(line)
            for line in Path(fixture["python_log"]).read_text(encoding="utf-8").splitlines()
        ]
        phases = [record["phase"] for record in records]
        self.assertIn("venv-build", phases)
        self.assertIn("pip", phases)
        self.assertIn("dependency", phases)
        self.assertIn("doctor", phases)

        live_runtime = runtime.resolve()
        transaction_root = (app / "update-transactions").resolve()
        isolated_phases = {"venv-build", "pip", "dependency"}
        isolated_records = [record for record in records if record["phase"] in isolated_phases]
        self.assertEqual({record["phase"] for record in isolated_records}, isolated_phases)
        for record in isolated_records:
            self.assertTrue(record["novaHome"], record)
            self.assertTrue(record["locationFile"], record)
            validation_runtime = Path(record["novaHome"]).resolve(strict=False)
            validation_location = Path(record["locationFile"]).resolve(strict=False)
            self.assertNotEqual(validation_runtime, live_runtime, record)
            self.assertEqual(validation_runtime.name, "candidate-runtime", record)
            self.assertEqual(validation_runtime.parent.parent, transaction_root, record)
            self.assertEqual(
                validation_location,
                (validation_runtime / "location.json").resolve(strict=False),
                record,
            )
            self.assertEqual(
                Path(record["home"]).resolve(strict=False),
                (validation_runtime / "home").resolve(strict=False),
                record,
            )
            self.assertEqual(
                Path(record["tmpDir"]).resolve(strict=False),
                (validation_runtime / "tmp").resolve(strict=False),
                record,
            )
            self.assertEqual(
                Path(record["xdgConfigHome"]).resolve(strict=False),
                (validation_runtime / "xdg").resolve(strict=False),
                record,
            )
            self.assertEqual(record["pipConfigFile"], "/dev/null", record)
            self.assertEqual(
                Path(record["pipCacheDir"]).resolve(strict=False),
                (validation_runtime / "pip-cache").resolve(strict=False),
                record,
            )
            self.assertEqual(record["pythonNoUserSite"], "1", record)
            self.assertEqual(record["secretBackend"], "memory", record)
            self.assertFalse(record["syntheticSentinelPresent"], record)

        doctor_records = [record for record in records if record["phase"] == "doctor"]
        self.assertTrue(doctor_records)
        live_location = Path(str(fixture["env"]["NOVA_LOCATION_FILE"])).resolve(strict=False)
        committed_doctor_sandbox = Path(validation["path"]).resolve(strict=False)
        committed_doctor_seen = False
        for record in doctor_records:
            self.assertEqual(
                Path(record["novaHome"]).resolve(strict=False),
                live_runtime,
                record,
            )
            self.assertEqual(
                Path(record["locationFile"]).resolve(strict=False),
                live_location,
                record,
            )
            doctor_sandbox = Path(record["home"]).resolve(strict=False).parent
            self.assertEqual(doctor_sandbox.name, "candidate-runtime", record)
            self.assertEqual(doctor_sandbox.parent.parent, transaction_root, record)
            self.assertEqual(
                Path(record["home"]).resolve(strict=False),
                (doctor_sandbox / "home").resolve(strict=False),
                record,
            )
            self.assertEqual(
                Path(record["tmpDir"]).resolve(strict=False),
                (doctor_sandbox / "tmp").resolve(strict=False),
                record,
            )
            self.assertEqual(
                Path(record["xdgConfigHome"]).resolve(strict=False),
                (doctor_sandbox / "xdg").resolve(strict=False),
                record,
            )
            self.assertEqual(record["pipConfigFile"], "/dev/null", record)
            self.assertEqual(
                Path(record["pipCacheDir"]).resolve(strict=False),
                (doctor_sandbox / "pip-cache").resolve(strict=False),
                record,
            )
            self.assertEqual(record["pythonNoUserSite"], "1", record)
            self.assertEqual(record["secretBackend"], "memory", record)
            self.assertFalse(record["syntheticSentinelPresent"], record)
            committed_doctor_seen = committed_doctor_seen or (
                doctor_sandbox == committed_doctor_sandbox
            )
        self.assertTrue(committed_doctor_seen, doctor_records)

    def _recover_idempotently(self, fixture: dict[str, object], journal: Path) -> None:
        for _ in range(2):
            result = self._run_helper(
                fixture,
                "recover",
                "--runtime",
                str(fixture["runtime"]),
            )
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self._assert_prior_runtime(fixture)
            self.assertFalse((Path(fixture["app"]) / ".update-transaction.lock").exists())
            state = json.loads(journal.read_text(encoding="utf-8"))
            for artifact in state.get("candidateArtifacts") or []:
                self.assertFalse(Path(artifact["path"]).exists())
            self._assert_rolled_back_artifacts_cleaned(journal)

    def _assert_failure_then_recovery(
        self,
        fixture: dict[str, object],
        result: subprocess.CompletedProcess[str],
        *,
        kind: str,
        pre_stop: bool,
        retry_after: bool | None = None,
        outer_sigkill: bool | None = None,
    ) -> None:
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertNotIn("atomic upgrade complete", output)
        journals = self._journal_paths(fixture)
        self.assertEqual(len(journals), 1, journals)
        journal_path = journals[0]
        state = json.loads(journal_path.read_text(encoding="utf-8"))
        lock = Path(fixture["app"]) / ".update-transaction.lock"
        if outer_sigkill is None:
            outer_sigkill = kind == "kill"
        if outer_sigkill:
            self.assertEqual(result.returncode, -signal.SIGKILL, output)
            self.assertNotIn(state["status"], {"committed", "rolled-back"})
            self.assertTrue(lock.exists())
        else:
            self.assertEqual(
                state["status"],
                "rolled-back",
                output + "\nrollbackErrors=" + repr(state.get("rollbackErrors")),
            )
            self.assertEqual(state["rollbackErrors"], [], output)
            self.assertFalse(lock.exists())
            self._assert_prior_runtime(fixture)
        if pre_stop:
            self._assert_no_launchctl_mutation(fixture)

        self._recover_idempotently(fixture, journal_path)
        recovered = json.loads(journal_path.read_text(encoding="utf-8"))
        self.assertEqual(recovered["status"], "rolled-back")
        self.assertEqual(recovered["rollbackErrors"], [])

        if retry_after is None:
            retry_after = kind == "kill"
        if retry_after:
            retry = self._run_update(fixture)
            self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
            self._assert_successful_full_upgrade(fixture)
            statuses = sorted(
                json.loads(path.read_text(encoding="utf-8"))["status"]
                for path in self._journal_paths(fixture)
            )
            self.assertEqual(statuses, ["committed", "rolled-back"])

    def test_fake_launchctl_rejects_production_labels_and_mutations_fail_closed(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp))
            fake = str(fixture["fake_launchctl"])
            env = fixture["env"]
            domain = f"gui/{os.getuid()}"
            probe = subprocess.run(
                [fake, "print", f"{domain}/com.open-nova.dashboard"],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(probe.returncode, 113)
            for command in (
                [fake, "bootout", f"{domain}/com.open-nova.dashboard"],
                [fake, "bootstrap", domain, str(Path(fixture["home"]) / "production.plist")],
                [fake, "kickstart", "-k", f"{domain}/com.open-nova.dashboard"],
            ):
                with self.subTest(command=command[1]):
                    result = subprocess.run(
                        command,
                        env=env,
                        text=True,
                        capture_output=True,
                        check=False,
                    )
                    self.assertEqual(result.returncode, 77)
            self.assertEqual(self._service_state(fixture), fixture["initial_state"])

    def test_candidate_venv_build_pip_and_dependency_failures_are_pre_stop(self):
        for phase in ("venv-build", "pip", "dependency"):
            for kind in ("return", "term", "kill"):
                with self.subTest(phase=phase, kind=kind), tempfile.TemporaryDirectory() as tmp:
                    fixture = self._fixture(Path(tmp))
                    overrides = {
                        "NOVA_FULL_UPGRADE_FAULT_PHASE": phase,
                        "NOVA_FULL_UPGRADE_FAULT_KIND": kind,
                        "NOVA_FULL_UPGRADE_FAULT_MARKER": str(fixture["marker"]),
                    }
                    result = self._run_update(fixture, env_overrides=overrides)
                    self.assertEqual(Path(fixture["marker"]).read_text(encoding="utf-8").strip(), phase)
                    self._assert_failure_then_recovery(
                        fixture,
                        result,
                        kind=kind,
                        pre_stop=True,
                        outer_sigkill=False,
                    )

    def test_pre_stop_sigkill_does_not_leave_orphan_candidate_children_or_late_writes(self):
        for phase in ("venv-build", "pip"):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as tmp:
                fixture = self._fixture(Path(tmp))
                pid_path = Path(fixture["root"]) / f"{phase}-orphan.pid"
                owner_pid_path = Path(fixture["root"]) / f"{phase}-outer-zsh.pid"
                late_path = Path(fixture["runtime"]) / "app" / "venvs" / f"{phase}-orphan-late-write"
                owner_hook = Path(fixture["root"]) / f"{phase}-owner-hook"
                owner_hook.write_text(
                    "#!/bin/zsh\n"
                    f'if [[ "$1" == "prior-captured" ]]; then print -r -- "$PPID" > {str(owner_pid_path)!r}; fi\n',
                    encoding="utf-8",
                )
                owner_hook.chmod(0o755)
                result = self._run_update(
                    fixture,
                    env_overrides={
                        "NOVA_FULL_UPGRADE_FAULT_PHASE": phase,
                        "NOVA_FULL_UPGRADE_FAULT_KIND": "orphan-kill",
                        "NOVA_FULL_UPGRADE_FAULT_MARKER": str(fixture["marker"]),
                        "NOVA_FULL_UPGRADE_ORPHAN_PID": str(pid_path),
                        "NOVA_FULL_UPGRADE_OWNER_PID": str(owner_pid_path),
                        "NOVA_FULL_UPGRADE_LATE_MARKER": str(late_path),
                        "NOVA_INSTALL_TEST_HOOK": str(owner_hook),
                    },
                )
                self.assertEqual(result.returncode, -signal.SIGKILL, result.stdout + result.stderr)
                self.assertTrue(pid_path.is_file())
                child_pid = int(pid_path.read_text(encoding="utf-8").strip())
                # The supervisor runs outside the killed zsh and needs a bounded
                # scheduling/reap window.  This remains shorter than the fake
                # child's one-second late write, so an unsupervised orphan is not
                # hidden by the grace period.
                alive_before_recovery = not self._wait_pid_dead(child_pid, timeout=0.8)
                journals = self._journal_paths(fixture)
                self.assertEqual(len(journals), 1)
                self.assertTrue((Path(fixture["app"]) / ".update-transaction.lock").exists())
                recovery = self._run_helper(
                    fixture,
                    "recover",
                    "--runtime",
                    str(fixture["runtime"]),
                )
                self.assertEqual(recovery.returncode, 0, recovery.stdout + recovery.stderr)
                alive_after_recovery = self._pid_alive(child_pid)
                # A surviving orphan proves continued execution by writing under
                # the transaction-owned candidate venv root after rollback.
                time.sleep(1.2)
                late_write = late_path.exists()
                still_alive = self._pid_alive(child_pid)
                if still_alive:
                    try:
                        os.kill(child_pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                self._assert_prior_runtime(fixture)
                self._assert_no_launchctl_mutation(fixture)
                observed = {
                    "aliveBeforeRecovery": alive_before_recovery,
                    "aliveAfterRecovery": alive_after_recovery,
                    "lateCandidateWrite": late_write,
                }
                self.assertEqual(
                    observed,
                    {
                        "aliveBeforeRecovery": False,
                        "aliveAfterRecovery": False,
                        "lateCandidateWrite": False,
                    },
                    "SIGKILL left an untracked candidate child running beyond outer transaction recovery",
                )

    def test_outer_full_upgrade_phase_failure_matrix_rolls_back_and_kill_retries(self):
        phases = (
            "prior-captured",
            "migration-compatibility-verified",
            "candidate-venv",
            "source-staged",
            "payload-scanned",
            "services-stopped",
            "source-promoted",
            "venv-promoted",
            "services-restored",
            "candidate-doctor-started",
            "candidate-doctor-passed",
            "candidate-verified",
        )
        for phase in phases:
            for kind in ("return", "term", "kill"):
                with self.subTest(phase=phase, kind=kind), tempfile.TemporaryDirectory() as tmp:
                    fixture = self._fixture(Path(tmp))
                    if kind == "return":
                        overrides = {"NOVA_INSTALL_TEST_FAIL_PHASE": phase}
                    else:
                        hook = self._write_outer_hook(fixture, phase=phase, kind=kind)
                        overrides = {"NOVA_INSTALL_TEST_HOOK": str(hook)}
                    result = self._run_update(fixture, env_overrides=overrides)
                    if kind != "return":
                        self.assertEqual(
                            Path(fixture["marker"]).read_text(encoding="utf-8").strip(),
                            phase,
                        )
                    self._assert_failure_then_recovery(
                        fixture,
                        result,
                        kind=kind,
                        pre_stop=phase in {
                            "migration-compatibility-verified",
                            "candidate-venv",
                            "source-staged",
                        },
                        retry_after=(
                            kind == "kill"
                            or (kind == "term" and phase.startswith("candidate-doctor-"))
                        ),
                    )

    def test_candidate_doctor_return_term_and_sigkill_are_fatal_and_roll_back(self):
        for kind in ("return", "term", "kill"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory() as tmp:
                fixture = self._fixture(Path(tmp))
                result = self._run_update(
                    fixture,
                    env_overrides={
                        "NOVA_FULL_UPGRADE_FAULT_PHASE": "doctor",
                        "NOVA_FULL_UPGRADE_FAULT_KIND": kind,
                        "NOVA_FULL_UPGRADE_FAULT_MARKER": str(fixture["marker"]),
                    },
                )
                self.assertEqual(
                    Path(fixture["marker"]).read_text(encoding="utf-8").strip(),
                    "doctor",
                )
                output = result.stdout + result.stderr
                self.assertIn("Candidate installer doctor", output)
                self.assertIn("failed", output)
                self._assert_failure_then_recovery(
                    fixture,
                    result,
                    kind=kind,
                    pre_stop=False,
                    retry_after=False,
                    outer_sigkill=False,
                )
                records = [
                    json.loads(line)
                    for line in Path(fixture["python_log"]).read_text(encoding="utf-8").splitlines()
                ]
                doctor = [record for record in records if record["phase"] == "doctor"]
                self.assertEqual(len(doctor), 1)
                self.assertEqual(
                    doctor[0]["argv"][:3],
                    ["-m", "data_foundation.cli", "doctor"],
                )

    def test_additive_candidate_migration_survives_rollback_and_prior_source_can_write(self):
        migration_version = "0019_session_d_additive"
        migration_body = textwrap.dedent(
            """\
            CREATE TABLE candidate_additive_probe (
                id INTEGER PRIMARY KEY,
                value TEXT NOT NULL
            );
            """
        )
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp))
            root = Path(fixture["root"])
            runtime = Path(fixture["runtime"])
            database = runtime / "data" / "nova_data.sqlite3"
            old_source = Path(fixture["old_source"])

            # The rollback reader is the actual prior-source implementation, not
            # a test double.  Only the package files needed for this compatibility
            # probe are added to the fixture's already-captured old source tree.
            prior_package = old_source / "src" / "data_foundation"
            for name in ("__init__.py", "db.py", "paths.py"):
                shutil.copy2(ROOT / "src" / "data_foundation" / name, prior_package / name)

            candidate_checkout = root / "candidate-checkout"
            for name in ("advanced", "install", "src"):
                shutil.copytree(
                    ROOT / name,
                    candidate_checkout / name,
                    symlinks=True,
                )
            for name in ("config.py", "LICENSE", "MANIFEST.in", "pyproject.toml"):
                shutil.copy2(ROOT / name, candidate_checkout / name)

            migration_path = (
                candidate_checkout
                / "src"
                / "data_foundation"
                / "migrations"
                / f"{migration_version}.sql"
            )
            migration_path.write_text(migration_body, encoding="utf-8")
            contract_path = (
                candidate_checkout
                / "src"
                / "data_foundation"
                / "migration_compatibility.json"
            )
            contract = json.loads(contract_path.read_text(encoding="utf-8"))
            contract["migrations"].append(
                {
                    "version": migration_version,
                    "sha256": hashlib.sha256(migration_body.encode("utf-8")).hexdigest(),
                    "rollbackClass": "rollback-compatible-additive",
                }
            )
            contract["maximumReadableSchema"] = migration_version
            contract_path.write_text(
                json.dumps(contract, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8",
            )
            command = list(fixture["command"])
            command[command.index("--source-root") + 1] = str(candidate_checkout)
            fixture["command"] = command

            result = self._run_update(
                fixture,
                env_overrides={
                    "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_DATABASE": str(database),
                    "NOVA_FULL_UPGRADE_DOCTOR_ADDITIVE_VERSION": migration_version,
                    "NOVA_INSTALL_TEST_FAIL_PHASE": "candidate-doctor-passed",
                },
            )

            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, output)
            self.assertNotIn("atomic upgrade complete", output)
            journals = self._journal_paths(fixture)
            self.assertEqual(len(journals), 1, journals)
            state = json.loads(journals[0].read_text(encoding="utf-8"))
            self.assertEqual(state["status"], "rolled-back", output)
            self.assertEqual(state["rollbackErrors"], [], output)
            self.assertEqual(
                state["databaseCompatibility"]["newMigrations"],
                [migration_version],
            )
            self.assertFalse((Path(fixture["app"]) / ".update-transaction.lock").exists())
            source_pointer = Path(fixture["app"]) / "source"
            self.assertTrue(source_pointer.is_symlink())
            self.assertEqual(source_pointer.resolve(), old_source.resolve())
            self.assertEqual(self._service_state(fixture), fixture["initial_state"])
            for path, expected_hash in fixture["protected_hashes"].items():
                if path != database:
                    self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), expected_hash)

            with closing(sqlite3.connect(database)) as connection:
                self.assertEqual(
                    connection.execute(
                        "SELECT applied_at FROM schema_migrations WHERE version = ?",
                        (migration_version,),
                    ).fetchone(),
                    ("candidate-doctor",),
                )
                self.assertEqual(
                    connection.execute(
                        "SELECT value FROM candidate_additive_probe WHERE id = 1"
                    ).fetchone(),
                    ("candidate-touch",),
                )
                self.assertEqual(
                    connection.execute("SELECT value FROM evidence ORDER BY id").fetchall(),
                    [("before-full-upgrade",)],
                )

            pipeline_label = fixture["labels"]["pipeline"]
            calls = self._launchctl_calls(fixture)
            self.assertTrue(
                any(call.endswith(f"/{pipeline_label}") and call.startswith("bootout ") for call in calls),
                calls,
            )
            self.assertTrue(
                any(
                    call.startswith("bootstrap ")
                    and call.endswith(f"/{pipeline_label}.plist")
                    for call in calls
                ),
                calls,
            )

            prior_probe = textwrap.dedent(
                """\
                import json
                import os
                from pathlib import Path

                import data_foundation.db as db
                from data_foundation.paths import runtime_paths_for_home

                prior_root = Path(os.environ["NOVA_PRIOR_SOURCE_ROOT"]).resolve()
                module_path = Path(db.__file__).resolve()
                module_path.relative_to(prior_root)
                paths = runtime_paths_for_home(Path(os.environ["NOVA_HOME"]))
                with db.connect(paths, read_only=True) as connection:
                    before = [row[0] for row in connection.execute(
                        "SELECT value FROM evidence ORDER BY id"
                    )]
                    additive = connection.execute(
                        "SELECT value FROM candidate_additive_probe WHERE id = 1"
                    ).fetchone()[0]
                with db.connect(paths) as connection:
                    connection.execute(
                        "INSERT INTO evidence(value) VALUES (?)",
                        ("prior-source-after-rollback",),
                    )
                with db.connect(paths, read_only=True) as connection:
                    after = [row[0] for row in connection.execute(
                        "SELECT value FROM evidence ORDER BY id"
                    )]
                print(json.dumps({
                    "module": str(module_path),
                    "before": before,
                    "additive": additive,
                    "after": after,
                }, sort_keys=True))
                """
            )
            prior_env = dict(fixture["env"])
            prior_env.update(
                {
                    "PYTHONPATH": str(old_source / "src"),
                    "NOVA_PRIOR_SOURCE_ROOT": str(old_source / "src"),
                    "PYTHONDONTWRITEBYTECODE": "1",
                }
            )
            prior_result = subprocess.run(
                [sys.executable, "-c", prior_probe],
                cwd=root,
                env=prior_env,
                text=True,
                capture_output=True,
                check=False,
                timeout=30,
            )
            self.assertEqual(
                prior_result.returncode,
                0,
                prior_result.stdout + prior_result.stderr,
            )
            prior_evidence = json.loads(prior_result.stdout)
            self.assertEqual(prior_evidence["before"], ["before-full-upgrade"])
            self.assertEqual(prior_evidence["additive"], "candidate-touch")
            self.assertEqual(
                prior_evidence["after"],
                ["before-full-upgrade", "prior-source-after-rollback"],
            )
            self.assertTrue(
                Path(prior_evidence["module"]).resolve().is_relative_to(
                    (old_source / "src").resolve()
                )
            )
            self.assertEqual(source_pointer.resolve(), old_source.resolve())
            self.assertEqual(self._service_state(fixture), fixture["initial_state"])

    def test_source_stage_helper_sigkill_is_recovered_without_candidate_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp))
            result = self._run_update(
                fixture,
                env_overrides={
                    "NOVA_INSTALL_TEST_KILL_PHASE": "candidate-command-released",
                },
            )
            self._assert_failure_then_recovery(
                fixture,
                result,
                kind="kill",
                pre_stop=True,
                retry_after=True,
                outer_sigkill=False,
            )
            time.sleep(0.5)
            states = [json.loads(path.read_text(encoding="utf-8")) for path in self._journal_paths(fixture)]
            rolled_back = next(state for state in states if state["status"] == "rolled-back")
            for artifact in rolled_back.get("candidateArtifacts") or []:
                self.assertFalse(Path(artifact["path"]).exists())

    def test_upgrade_from_active_source_copies_candidate_without_cleaning_prior_release(self):
        with tempfile.TemporaryDirectory() as tmp:
            fixture = self._fixture(Path(tmp))
            old_source = Path(fixture["old_source"])
            for name in ("advanced", "install", "src"):
                shutil.copytree(ROOT / name, old_source / name, dirs_exist_ok=True, symlinks=True)
            for name in ("config.py", "LICENSE", "MANIFEST.in", "pyproject.toml"):
                shutil.copy2(ROOT / name, old_source / name)
            egg_info = old_source / "src" / "open_nova.egg-info"
            egg_info.mkdir(exist_ok=True)
            marker = egg_info / "SESSION_D_ACTIVE_SOURCE_MARKER"
            marker.write_text("must-survive\n", encoding="utf-8")
            marker_mtime = marker.stat().st_mtime_ns
            prior_digest = self._tree_digest(old_source)
            command = list(fixture["command"])
            command[command.index("--source-root") + 1] = str(Path(fixture["app"]) / "source")
            fixture["command"] = command
            path_hijack_dir = Path(fixture["root"]) / "path-hijack"
            path_hijack_dir.mkdir()
            path_hijack_marker = Path(fixture["root"]) / "bare-env-was-executed"
            path_hijack = path_hijack_dir / "env"
            path_hijack.write_text(
                textwrap.dedent(
                    f"""\
                    #!{sys.executable}
                    import os
                    import sys
                    from pathlib import Path

                    Path({str(path_hijack_marker)!r}).write_text("called\\n", encoding="utf-8")
                    os.execv("/usr/bin/env", ["/usr/bin/env", *sys.argv[1:]])
                    """
                ),
                encoding="utf-8",
            )
            path_hijack.chmod(0o755)
            fixture["env"]["PATH"] = (
                str(path_hijack_dir)
                + os.pathsep
                + str(fixture["env"].get("PATH") or "")
            )

            result = self._run_update(fixture)

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertFalse(path_hijack_marker.exists())
            self._assert_successful_full_upgrade(fixture)
            self.assertEqual(self._tree_digest(old_source), prior_digest)
            self.assertEqual(marker.read_text(encoding="utf-8"), "must-survive\n")
            self.assertEqual(marker.stat().st_mtime_ns, marker_mtime)
            self.assertNotEqual((Path(fixture["app"]) / "source").resolve(), old_source.resolve())

    def test_dashboard_and_rag_bootstrap_fail_once_restore_prior_vector_and_retry(self):
        for failed_kind in ("dashboard", "rag"):
            with self.subTest(failed_kind=failed_kind), tempfile.TemporaryDirectory() as tmp:
                fixture = self._fixture(Path(tmp))
                dashboard_server, dashboard_thread, dashboard_port = self._start_health_server()
                rag_server, rag_thread, rag_port = self._start_health_server()
                try:
                    settings_path = Path(fixture["runtime"]) / "config" / "settings.json"
                    settings = json.loads(settings_path.read_text(encoding="utf-8"))
                    settings["dashboard"].update(
                        {"host": "127.0.0.1", "port": dashboard_port, "healthPath": "/health"}
                    )
                    settings["rag"]["server"].update(
                        {"host": "127.0.0.1", "port": rag_port, "healthPath": "/health"}
                    )
                    settings_path.write_text(
                        json.dumps(settings, sort_keys=True, separators=(",", ":")) + "\n",
                        encoding="utf-8",
                    )
                    fixture["protected_hashes"][settings_path] = hashlib.sha256(
                        settings_path.read_bytes()
                    ).hexdigest()
                    for kind in ("dashboard", "rag"):
                        label = fixture["labels"][kind]
                        (Path(fixture["state_dir"]) / label).write_text("running\n", encoding="utf-8")
                        fixture["initial_state"][label] = "running"
                    fail_marker = Path(fixture["root"]) / f"bootstrap-failed-{failed_kind}"
                    result = self._run_update(
                        fixture,
                        env_overrides={
                            "NOVA_TEST_BOOTSTRAP_FAIL_ONCE_LABEL": fixture["labels"][failed_kind],
                            "NOVA_TEST_BOOTSTRAP_FAIL_ONCE_MARKER": str(fail_marker),
                        },
                    )
                    self.assertTrue(fail_marker.is_file())
                    self._assert_failure_then_recovery(
                        fixture,
                        result,
                        kind="return",
                        pre_stop=False,
                        retry_after=True,
                        outer_sigkill=False,
                    )
                    calls = self._launchctl_calls(fixture)
                    for kind in ("dashboard", "rag"):
                        label = fixture["labels"][kind]
                        self.assertGreaterEqual(
                            sum(
                                call.startswith("bootstrap ")
                                and call.endswith(f"/{label}.plist")
                                for call in calls
                            ),
                            2,
                        )
                finally:
                    for server, thread in (
                        (dashboard_server, dashboard_thread),
                        (rag_server, rag_thread),
                    ):
                        server.shutdown()
                        server.server_close()
                        thread.join(timeout=5)


if __name__ == "__main__":
    unittest.main()
