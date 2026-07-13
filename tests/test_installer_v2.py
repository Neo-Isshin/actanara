import hashlib
import http.server
import json
import os
import plistlib
import signal
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
import tomllib
import unittest
from contextlib import closing
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
INSTALLER = ROOT / "install" / "install.sh"
BOOTSTRAP = ROOT / "install" / "bootstrap.sh"
UPDATE_HELPER = ROOT / "install" / "update_transaction.py"
IMMUTABLE_TEST_COMMIT = "a" * 40

from data_foundation.paths import initialize_home
from data_foundation.settings import write_settings


class InstallerV2Tests(unittest.TestCase):
    def _fresh_bootstrap_env(self, home: Path) -> dict[str, str]:
        env = {
            **os.environ,
            "HOME": str(home),
            "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            "NOVA_INSTALL_PLATFORM": "Darwin",
        }
        env.pop("NOVA_HOME", None)
        env.pop("NOVA_INSTALL_RUNTIME", None)
        return env

    def _start_health_server(self) -> int:
        source_commit = subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=ROOT,
            text=True,
        ).strip()

        class HealthHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:
                if self.path != "/health":
                    self.send_error(404)
                    return
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
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        return int(server.server_address[1])

    def _write_stateful_fake_launchctl(self, path: Path) -> None:
        path.write_text(
            """#!/bin/zsh
set -eu
command_name="${1:-}"
print -r -- "$*" >> "$NOVA_TEST_LAUNCHCTL_CALLS"
case "$command_name" in
  print)
    target="${2:-}"
    if [[ "$target" == gui/<-> ]]; then
      print -r -- "state = running"
      exit 0
    fi
    label="${target##*/}"
    state_file="$NOVA_TEST_LAUNCHCTL_STATE/$label"
    [[ -f "$state_file" ]] || exit 113
    print -r -- "state = $(<\"$state_file\")"
    ;;
  bootout)
    target="${2:-}"
    label="${target##*/}"
    rm -f "$NOVA_TEST_LAUNCHCTL_STATE/$label"
    ;;
  bootstrap)
    plist="${3:-}"
    label="${plist:t:r}"
    state="waiting"
    if [[ "$label" == *dashboard* || "$label" == *rag* ]]; then
      state="running"
    fi
    print -r -- "$state" > "$NOVA_TEST_LAUNCHCTL_STATE/$label"
    ;;
  kickstart)
    target="${@: -1}"
    label="${target##*/}"
    print -r -- "running" > "$NOVA_TEST_LAUNCHCTL_STATE/$label"
    ;;
  *)
    exit 64
    ;;
esac
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_runtime_plist(self, path: Path, *, runtime: Path) -> None:
        label = path.stem
        source = runtime / "app" / "releases" / "old-release"
        python = runtime / ".venv" / "bin" / "python"
        environment = {
            "NOVA_HOME": str(runtime),
            "PYTHONDONTWRITEBYTECODE": "1",
        }
        if label.endswith("dashboard.watchdog"):
            payload = {
                "Label": label,
                "ProgramArguments": [
                    str(python),
                    str(source / "advanced" / "dashboard" / "dashboard_launch_agent.py"),
                    "check",
                    "--url",
                    "http://127.0.0.1:3036/health",
                    "--label",
                    label.removesuffix(".watchdog"),
                    "--restart",
                ],
                "EnvironmentVariables": environment,
            }
        elif label.endswith("rag-server"):
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
                    f"--app-dir {source / 'src' / 'dashboard'} --host 127.0.0.1 --port 3036",
                ],
                "EnvironmentVariables": environment,
            }
        with path.open("wb") as handle:
            plistlib.dump(payload, handle, sort_keys=False)

    def _write_prior_runtime_source(self, runtime: Path) -> Path:
        release = runtime / "app" / "releases" / "old-release"
        release.mkdir(parents=True, exist_ok=True)
        (release / "pyproject.toml").write_text(
            '[project]\nname="open-nova-old-fixture"\nversion="0"\n',
            encoding="utf-8",
        )
        (release / ".open-nova-runtime-source.json").write_text(
            '{"fixture":"old-source"}\n',
            encoding="utf-8",
        )
        shutil.copytree(
            ROOT / "src" / "data_foundation" / "migrations",
            release / "src" / "data_foundation" / "migrations",
        )
        (runtime / "app" / "source").symlink_to("releases/old-release")
        return release

    def _write_fake_python(self, path: Path, log_path: Path) -> None:
        path.write_text(
            f"""#!/bin/zsh
set -eu
print -r -- "$0 $*" >> "{log_path}"
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/python" <<'PYEOF'
#!/usr/bin/env zsh
print -r -- "$0 $*" >> "{log_path}"
exit 0
PYEOF
  chmod +x "$3/bin/python"
elif [[ "${{1:-}}" == "-" && -n "${{3:-}}" ]]; then
  if [[ "${{2:-}}" == /* && "${{2:h:t}}" == "releases" && "${{3:-}}" == /* && "${{3:t}}" == "source" ]]; then
    release_target="$2"
    link_path="$3"
    mkdir -p "${{link_path:h}}"
    rm -f "$link_path"
    ln -s "$release_target" "$link_path"
  else
    exec {sys.executable!r} "$@"
  fi
fi
exit 0
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_fake_python_with_dependency_remediation(self, path: Path, log_path: Path, marker_path: Path) -> None:
        path.write_text(
            f"""#!/bin/zsh
set -eu
print -r -- "$0 $*" >> "{log_path}"
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
  mkdir -p "$3/bin"
  cat > "$3/bin/python" <<'PYEOF'
#!/usr/bin/env zsh
set -eu
print -r -- "$0 $*" >> "{log_path}"
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "pip" && "${{3:-}}" == "install" ]]; then
  for arg in "$@"; do
    if [[ "$arg" == "fastapi>=0.110,<1" ]]; then
      print -r -- "installed" > "{marker_path}"
    fi
  done
  exit 0
fi
if [[ "${{1:-}}" == "-" ]]; then
  missing="${{NOVA_INSTALL_MISSING_DEPENDENCIES_FILE:-}}"
  if [[ ! -f "{marker_path}" ]]; then
    mkdir -p "${{missing:h}}"
    print -r -- "fastapi>=0.110,<1" > "$missing"
    print -r -- "dependency gate error: Dashboard API dependency import failed: fastapi: fake missing" >&2
    exit 1
  fi
  print -r -- "dependency gate ok: fake remediation passed"
  exit 0
fi
exit 0
PYEOF
  chmod +x "$3/bin/python"
elif [[ "${{1:-}}" == "-" && -n "${{3:-}}" ]]; then
  if [[ "${{2:-}}" == /* && "${{2:h:t}}" == "releases" && "${{3:-}}" == /* && "${{3:t}}" == "source" ]]; then
    release_target="$2"
    link_path="$3"
    mkdir -p "${{link_path:h}}"
    rm -f "$link_path"
    ln -s "$release_target" "$link_path"
  else
    exec {sys.executable!r} "$@"
  fi
fi
exit 0
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_fake_versioned_python(self, path: Path, version: str) -> None:
        major, minor, patch = version.split(".")
        path.write_text(
            f"""#!/bin/zsh
set -eu
if [[ "${{1:-}}" == "-" ]]; then
  print -r -- "{version}"
  if (( {major} > 3 || ( {major} == 3 && {minor} >= 11 ) )); then
    exit 0
  fi
  exit 3
fi
if [[ "${{1:-}}" == "-c" && "${{2:-}}" == "import venv" ]]; then
  exit 0
fi
if [[ "${{1:-}}" == "-m" && "${{2:-}}" == "venv" ]]; then
  mkdir -p "$3/bin"
  cp "$0" "$3/bin/python"
  chmod +x "$3/bin/python"
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_fake_git(self, path: Path, log_path: Path) -> None:
        path.write_text(
            f"""#!/usr/bin/env zsh
set -eu
print -r -- "$0 $*" >> "{log_path}"
if [[ "${{1:-}}" == "clone" ]]; then
  target=""
  for arg in "$@"; do
    target="$arg"
  done
  mkdir -p "$target/install" "$target/.git" "$target/advanced/cli" "$target/advanced/dashboard" "$target/advanced/pipeline" "$target/src/dashboard/app/static" "$target/src/data_foundation/migrations"
  cp "{INSTALLER}" "$target/install/install.sh"
  cp "{ROOT / 'pyproject.toml'}" "$target/pyproject.toml"
  cp "{ROOT / 'MANIFEST.in'}" "$target/MANIFEST.in"
  cp "{ROOT / 'LICENSE'}" "$target/LICENSE"
  cp "{ROOT / 'config.py'}" "$target/config.py"
  cp -R "{ROOT / 'advanced'}"/. "$target/advanced/"
  cp -R "{ROOT / 'src'}"/. "$target/src/"
  chmod +x "$target/install/install.sh"
fi
if [[ "${{1:-}}" == "-C" && "${{3:-}}" == "rev-parse" ]]; then
  print -r -- "{IMMUTABLE_TEST_COMMIT}"
fi
exit 0
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def _write_fake_lsof(self, path: Path) -> None:
        path.write_text(
            """#!/usr/bin/env zsh
set -eu
for arg in "$@"; do
  if [[ "$arg" == *3036* ]]; then
    exit 0
  fi
done
exit 1
""",
            encoding="utf-8",
        )
        path.chmod(0o755)

    def test_installer_script_has_valid_zsh_syntax(self):
        for script in (INSTALLER, BOOTSTRAP):
            with self.subTest(script=script.name):
                result = subprocess.run(
                    ["zsh", "-n", str(script)],
                    cwd=ROOT,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(result.returncode, 0, result.stderr)

    def test_wizard_renders_product_header_with_version(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("render_installer_header", script)
        self.assertIn("installer_version", script)
        self.assertIn("Open Nova ${version}", script)
        self.assertIn("installer v2", script)
        self.assertIn("████", script)
        self.assertIn("TTY_BLUE", script)
        self.assertIn('version = ', script)

    def test_wizard_uses_english_until_language_is_selected(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("LANGUAGE_SELECTED=0", script)
        self.assertIn('text_language="en-US"', script)
        self.assertIn('if [[ "$LANGUAGE_SET" != "1" && "$LANGUAGE_SELECTED" != "1" ]]; then', script)
        self.assertIn("LANGUAGE_SELECTED=1", script)
        self.assertIn("Choose Open Nova language profile", script)
        self.assertIn("Welcome to Open Nova. Core pipeline, Dashboard, and Nova-Task are installed by default.", script)

    def test_installer_declares_input_data_sensitivity_notice(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("print_installer_data_notice", script)
        self.assertIn("agent/tool history", script)
        self.assertIn("may preserve sensitive information", script)
        self.assertIn("not secret values", script)

    def test_wizard_exposes_only_rag_as_product_choice(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("prompt_rag_choice", script)
        self.assertIn("Core pipeline, Dashboard, and Nova-Task are installed by default", script)
        self.assertIn("installer_text rag_choice_prompt", script)
        self.assertIn("rag_not_now_label", script)
        self.assertIn("rag_local_label", script)
        self.assertIn("rag_cloud_label", script)
        self.assertNotIn("Enable nova-RAG memory/search subsystem?", script)
        self.assertIn("--enable-dev-test", script)
        self.assertNotIn("prompt_subsystems", script)
        self.assertNotIn("selected_subsystems", script)
        self.assertNotIn("Select subsystems", script)
        self.assertNotIn("Use Up/Down or j/k, Space to toggle optional items", script)
        self.assertNotIn('local ids=("dashboard" "dashboard-server" "scheduler"', script)
        self.assertNotIn('"Dashboard server service"', script)
        self.assertNotIn('"macOS scheduler"', script)
        self.assertNotIn('"LLM diary generation"', script)

    def test_wizard_does_not_prompt_for_runtime_or_generated_diary_paths(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertNotIn('prompt_line "Runtime/install target"', script)
        self.assertNotIn('prompt_line "Generated diary output path"', script)
        self.assertNotIn('prompt_line "Reports output path"', script)
        self.assertNotIn('prompt_line "Dashboard/report snapshots path"', script)
        self.assertNotIn('prompt_line "Archives/intermediate output path"', script)
        self.assertNotIn('prompt_line "Python executable for the runtime venv"', script)
        self.assertNotIn("Create a Desktop shortcut to the generated diary folder?", script)

    def test_wizard_llm_selection_uses_provider_catalog_before_model_and_key(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("llm_provider_catalog_rows", script)
        self.assertIn("llm_model_catalog_rows", script)
        self.assertIn("installer_text llm_provider_prompt", script)
        self.assertIn("installer_text llm_provider_help", script)
        self.assertIn("installer_text llm_model_prompt", script)
        self.assertIn("installer_text custom_input", script)
        self.assertIn("installer_text custom_llm_endpoint", script)
        self.assertIn("installer_text custom_llm_model", script)
        self.assertIn("LLM API key environment variable name", script)
        self.assertIn("installer_text yes_recommended", script)

    def test_wizard_detects_and_selects_external_tools_before_settings_overlay(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("installer_text detecting_tools", script)
        self.assertIn("installer_text detected_tools", script)
        self.assertIn("OpenClaw", script)
        self.assertIn("Claude Code", script)
        self.assertIn("Codex", script)
        self.assertIn("Gemini CLI", script)
        self.assertIn("Hermes", script)
        self.assertIn("manual", script)
        self.assertIn("installer_text manual_tool_name", script)
        self.assertIn("installer_text manual_tool_path", script)
        self.assertIn("installerSelectedTools", script)
        self.assertIn("run_external_rag_skill_registration_apply", script)
        self.assertIn("selected external tools", script)
        self.assertIn('if [[ -z "$row" ]]; then', script)
        self.assertIn('if [[ "${#fields[@]}" -lt 4 ]]; then', script)

    def test_wizard_skill_registration_is_rag_gated_after_rag_choices(self):
        script = INSTALLER.read_text(encoding="utf-8")

        rag_choice = script.index("prompt_rag_choice")
        skill_registration = script.index("ENABLE_SKILL_REGISTRATION=1")
        self.assertGreater(skill_registration, rag_choice)
        self.assertNotIn("Enable Dashboard-controlled nova-RAG memory skill registration for selected tools?", script)
        self.assertIn('if [[ "$ENABLE_RAG" == "1" ]]; then', script)
        self.assertIn('if [[ -n "$SELECTED_EXTERNAL_TOOLS" ]]; then', script)
        self.assertIn("ENABLE_SKILL_REGISTRATION=1", script)
        self.assertIn("installerV2SkillRegistration", script)
        self.assertIn("RAG辅助记忆系统", script)
        self.assertIn('"status": "installer-applied"', script)
        self.assertIn('"supportedNow": True', script)
        self.assertIn('"applyEndpoint": "POST /api/settings/external-tools/rag-skill-registration"', script)
        self.assertIn('"confirmationTextRequired": "INSTALL OPEN NOVA RAG SKILL"', script)
        self.assertIn("exact unmodified generated versions are backed up and upgraded", script)
        self.assertIn("customized files are preserved unless Dashboard overwrite is explicitly confirmed", script)
        self.assertIn("installer writes missing nova-RAG skills for selected external tools", script)
        self.assertIn("--register-rag-skills", script)
        self.assertIn("queue_rag_skill_registration", script)
        self.assertLess(script.index("apply_installer_settings_overlay\n"), script.index("run_external_rag_skill_registration_apply\n"))
        self.assertIn("enable_rag and enable_skill_registration and selected_external_tools", script)

    def test_wizard_dry_run_uses_summary_only_output(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("SUMMARY_ONLY=0", script)
        self.assertIn('if [[ "$DRY_RUN" == "1" ]]; then\n    SUMMARY_ONLY=1', script)
        self.assertIn('if [[ "$SUMMARY_ONLY" == "1" && "$DRY_RUN" == "1" ]]; then', script)
        self.assertIn('if [[ "$SUMMARY_ONLY" == "1" && -t 1 && -r /dev/tty ]]; then', script)
        self.assertIn("print_install_summary", script)
        self.assertIn("print_useful_commands", script)

    def test_wizard_presents_core_and_rag_dependency_gates(self):
        script = INSTALLER.read_text(encoding="utf-8")
        wizard = script.split("run_wizard() {", 1)[1].split("\n}\n\nwhile [[ $# -gt 0", 1)[0]

        self.assertIn("wizard_core_dependency_gate", script)
        self.assertIn("installer_text core_dependency_title", script)
        self.assertIn("Dashboard runtime packages", script)
        self.assertIn("fastapi, uvicorn, PyYAML, and croniter", script)
        self.assertIn("wizard_rag_dependency_gate", script)
        self.assertIn("installer_text rag_dependency_title", script)
        self.assertIn("sentence-transformers, torch, numpy, and pydantic", script)
        self.assertIn("missing allowlisted RAG packages", script)
        self.assertGreater(wizard.index("wizard_core_dependency_gate"), wizard.index('if [[ "$LANGUAGE_SET" != "1" ]]'))
        self.assertGreater(wizard.index("wizard_rag_dependency_gate"), wizard.index("prompt_rag_local_model"))

    def test_installation_guide_documents_current_stable_workflow(self):
        runbook = (ROOT / "docs" / "new-user-onboarding-runbook.md").read_text(encoding="utf-8")

        for token in (
            "preflight",
            "post-install doctor",
            "open-nova update",
            "pyproject.toml",
            "~/.open-nova/bin/open-nova",
            "--upgrade",
            "https://github.com/Neo-Isshin/open-nova",
            "https://raw.githubusercontent.com/Neo-Isshin/open-nova/v1.0.1/install/bootstrap.sh",
        ):
            with self.subTest(token=token):
                self.assertIn(token, runbook)
        for private_process_term in (
            "Remaining Installer Milestones",
            "LaunchAgent Write Audit",
            "current phase",
            "publication remains",
        ):
            with self.subTest(private_process_term=private_process_term):
                self.assertNotIn(private_process_term, runbook)

    def test_readmes_document_shell_path_controls(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        for content in (readme, readme_zh):
            with self.subTest():
                self.assertIn("~/.open-nova/bin/open-nova", content)
                self.assertIn("~/.local/bin/open-nova", content)
                self.assertIn("--no-shell-path", content)
                self.assertIn("--shell-path-file /path/to/profile", content)

    def test_readmes_use_isolated_release_suite_command(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        readme_zh = (ROOT / "README.zh-CN.md").read_text(encoding="utf-8")

        for content in (readme, readme_zh):
            with self.subTest():
                self.assertIn("python tests/run_isolated_release_suite.py", content)
                self.assertNotIn("python -m pytest", content)

    def test_installer_runs_preflight_before_writes(self):
        script = INSTALLER.read_text(encoding="utf-8")

        preflight = script.index("run_installer_preflight")
        mkdir_runtime = script.index('run_cmd mkdir -p "${RUNTIME_HOME}"')
        self.assertLess(preflight, mkdir_runtime)
        self.assertIn("Installer preflight/doctor", script)
        self.assertIn("python-version", script)
        self.assertIn("writable-target", script)
        self.assertIn("launchagent-domain", script)
        self.assertIn("dashboard-port", script)
        self.assertIn("pip-network", script)

    def test_update_stops_managed_services_after_preflight_and_confirmation(self):
        script = INSTALLER.read_text(encoding="utf-8")

        entry = script.index('LOCATION_FILE="${NOVA_LOCATION_FILE:-$HOME/.config/open-nova/location.json}"')
        port_select = script.index("select_dashboard_port", entry)
        preflight = script.index("run_installer_preflight", entry)
        confirmation = script.index('prompt_yes_no "$(installer_text proceed_upgrade)"', preflight)
        transaction = script.index("run_guarded_update_transaction", confirmation)
        self.assertLess(port_select, preflight)
        self.assertLess(preflight, transaction)
        self.assertLess(confirmation, transaction)
        driver = script.split("run_guarded_update_transaction() {", 1)[1].split("print_useful_commands()", 1)[0]
        self.assertLess(driver.index("stage_runtime_source"), driver.index("update_transaction_command stop"))
        self.assertLess(driver.index('record_update_candidate source'), driver.index("stage_update_candidate_venv"))
        self.assertLess(driver.index("verify-migration-compatibility"), driver.index("stage_update_candidate_venv"))
        self.assertLess(driver.index("stage_update_candidate_venv"), driver.index("update_transaction_command stop"))
        self.assertLess(driver.index("update_transaction_command stop"), driver.index("update_transaction_command promote"))
        self.assertLess(driver.index("update_transaction_command promote"), driver.index("update_transaction_command restore-services"))
        self.assertIn("update_transaction.py", script)
        helper = (ROOT / "install" / "update_transaction.py").read_text(encoding="utf-8")
        for label in (
            "com.open-nova.dashboard",
            "com.open-nova.dashboard.watchdog",
            "com.open-nova.rag-server",
        ):
            self.assertIn(label, helper)
        self.assertIn('timer.get("label") or "open-nova.daily"', helper)
        self.assertIn('("scheduler-pipeline", "pipeline")', helper)
        self.assertIn('("scheduler-aggregation", "dashboard-aggregation")', helper)
        self.assertNotIn("defaults = [", helper)

    def test_upgrade_preflight_failure_does_not_stop_managed_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            launch_agents = home / "Library" / "LaunchAgents"
            source = root / "incomplete-source"
            launch_agents.mkdir(parents=True)
            runtime.mkdir(parents=True)
            self._write_prior_runtime_source(runtime)
            source.mkdir()
            (source / "pyproject.toml").write_text(
                '[project]\nname = "audit-fixture"\nversion = "0"\n',
                encoding="utf-8",
            )
            calls = root / "launchctl-calls.log"
            fake_launchctl = root / "launchctl"
            fake_launchctl.write_text(
                '#!/bin/zsh\nprint -r -- "$*" >> "$NOVA_TEST_LAUNCHCTL_CALLS"\n',
                encoding="utf-8",
            )
            fake_launchctl.chmod(0o755)
            for name in (
                "com.open-nova.dashboard.plist",
                "com.open-nova.dashboard.watchdog.plist",
                "com.open-nova.rag-server.plist",
            ):
                (launch_agents / name).write_text("placeholder", encoding="utf-8")

            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--upgrade",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(source),
                    "--yes",
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "NOVA_INSTALL_PLATFORM": "Darwin",
                    "NOVA_INSTALL_LAUNCHCTL": str(fake_launchctl),
                    "NOVA_INSTALL_TEST_MODE": "1",
                    "NOVA_TEST_LAUNCHCTL_CALLS": str(calls),
                    "NOVA_LOCATION_FILE": str(root / "location.json"),
                    "NOVA_INSTALL_PYTHON": sys.executable,
                },
                text=True,
                capture_output=True,
                timeout=30,
            )

            self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
            self.assertIn("Installer preflight failed", result.stdout + result.stderr)
            operations = calls.read_text(encoding="utf-8").splitlines() if calls.exists() else []
            self.assertFalse(any(line.startswith("bootout ") for line in operations), operations)
            self.assertFalse(any(line.startswith("bootstrap ") for line in operations), operations)

    def test_upgrade_requires_managed_service_registration_success(self):
        script = INSTALLER.read_text(encoding="utf-8")
        dashboard = script.split("run_dashboard_service_launch_agent_apply() {", 1)[1].split(
            "run_rag_service_launch_agent_apply() {", 1
        )[0]
        rag = script.split("run_rag_service_launch_agent_apply() {", 1)[1].split(
            "run_external_rag_skill_registration_apply() {", 1
        )[0]
        scheduler = script.split('log "Registering managed Open Nova scheduler LaunchAgents"', 1)[1].split(
            'elif [[ "$NO_SCHEDULER" == "1" ]]', 1
        )[0]

        for block in (dashboard, rag, scheduler):
            self.assertIn('if [[ "$UPGRADE" == "1" ]]', block)
            self.assertIn("run_json_cmd", block)
            self.assertIn("run_optional_json_cmd", block)

    def test_source_update_restores_mixed_actual_managed_service_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            launch_agents = home / "Library" / "LaunchAgents"
            state_dir = root / "launchctl-state"
            launch_agents.mkdir(parents=True)
            state_dir.mkdir()
            runtime.mkdir(parents=True)
            self._write_prior_runtime_source(runtime)
            health_port = self._start_health_server()
            (runtime / "config").mkdir()
            (runtime / "config" / "settings.json").write_text(
                json.dumps(
                    {
                        "dashboard": {
                            "host": "127.0.0.1",
                            "port": health_port,
                            "healthPath": "/health",
                        }
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            (runtime / ".venv" / "bin").mkdir(parents=True)
            existing_python = runtime / ".venv" / "bin" / "python"
            existing_python.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
            existing_python.chmod(0o755)
            calls = root / "launchctl-calls.log"
            fake_launchctl = root / "launchctl"
            self._write_stateful_fake_launchctl(fake_launchctl)
            for name in (
                "com.open-nova.dashboard.plist",
                "com.open-nova.dashboard.watchdog.plist",
                "com.open-nova.rag-server.plist",
                "open-nova.daily.pipeline.plist",
                "open-nova.daily.dashboard-aggregation.plist",
            ):
                self._write_runtime_plist(launch_agents / name, runtime=runtime)
            initial_state = {
                "com.open-nova.dashboard": "running",
                "com.open-nova.dashboard.watchdog": "running",
                "open-nova.daily.pipeline": "waiting",
            }
            for label, service_state in initial_state.items():
                (state_dir / label).write_text(service_state + "\n", encoding="utf-8")

            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--source-only",
                    "--runtime",
                    str(runtime),
                    "--yes",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "NOVA_INSTALL_PLATFORM": "Darwin",
                    "NOVA_INSTALL_LAUNCHCTL": str(fake_launchctl),
                    "NOVA_INSTALL_TEST_MODE": "1",
                    "NOVA_TEST_LAUNCHCTL_CALLS": str(calls),
                    "NOVA_TEST_LAUNCHCTL_STATE": str(state_dir),
                    "NOVA_LOCATION_FILE": str(root / "location.json"),
                    "NOVA_INSTALL_SOURCE_ROOT": str(ROOT),
                },
                text=True,
                capture_output=True,
                timeout=60,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            final_state = {
                path.name: path.read_text(encoding="utf-8").strip()
                for path in state_dir.iterdir()
                if path.is_file()
            }
            self.assertEqual(final_state, initial_state)
            source_manifest = json.loads(
                (runtime / "app" / "source" / ".open-nova-runtime-source.json").read_text(encoding="utf-8")
            )
            self.assertEqual(source_manifest["cleanScan"]["status"], "passed")
            self.assertEqual(source_manifest["cleanScan"]["findingCount"], 0)
            self.assertEqual(source_manifest["schemaVersion"], 2)
            self.assertIn(source_manifest["sourceLocator"]["kind"], {"login-home-relative", "unavailable"})
            self.assertNotIn("sourceRoot", source_manifest)
            self.assertNotIn("deployedSourceRoot", source_manifest)
            self.assertNotIn("releaseRoot", source_manifest)
            self.assertNotIn(str(Path.home()), json.dumps(source_manifest))
            self.assertGreater(source_manifest["payload"]["fileCount"], 0)
            self.assertEqual(source_manifest["payload"]["fileCount"], len(source_manifest["payload"]["files"]))
            operations = calls.read_text(encoding="utf-8").splitlines()
            self.assertTrue(any(line.startswith("bootout gui/") and line.endswith("dashboard.watchdog") for line in operations))
            self.assertTrue(any(line.startswith("bootstrap gui/") and line.endswith("open-nova.daily.pipeline.plist") for line in operations))
            self.assertFalse(any(line.startswith("bootstrap gui/") and line.endswith("rag-server.plist") for line in operations))
            self.assertFalse(any(line.startswith("bootstrap gui/") and line.endswith("dashboard-aggregation.plist") for line in operations))

    def test_source_update_post_stop_failure_matrix_restores_prior_state(self):
        cases = (
            ("services-stopped", "return"),
            ("source-promoted", "return"),
            ("services-restored", "return"),
            ("candidate-verified", "return"),
            ("services-stopped", "term"),
            ("prior-captured", "kill"),
            ("migration-compatibility-verified", "kill"),
            ("source-staged", "kill"),
            ("payload-scanned", "kill"),
            ("services-stopped", "kill"),
            ("source-promoted", "kill"),
            ("services-restored", "kill"),
            ("candidate-verified", "kill"),
        )
        for phase, failure_kind in cases:
            with self.subTest(phase=phase, failure=failure_kind), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                home = root / "Home"
                runtime = home / ".open-nova"
                app = runtime / "app"
                old_release = app / "releases" / "old-release"
                launch_agents = home / "Library" / "LaunchAgents"
                state_dir = root / "launchctl-state"
                for path in (
                    old_release,
                    runtime / ".venv" / "bin",
                    runtime / "config",
                    runtime / "data",
                    launch_agents,
                    state_dir,
                ):
                    path.mkdir(parents=True, exist_ok=True)
                (old_release / "pyproject.toml").write_text('[project]\nname="old"\nversion="0"\n', encoding="utf-8")
                (old_release / ".open-nova-runtime-source.json").write_text('{"old": true}\n', encoding="utf-8")
                shutil.copytree(
                    ROOT / "src" / "data_foundation" / "migrations",
                    old_release / "src" / "data_foundation" / "migrations",
                )
                (app / "source").symlink_to("releases/old-release")
                existing_python = runtime / ".venv" / "bin" / "python"
                existing_python.write_text("#!/bin/zsh\nexit 0\n", encoding="utf-8")
                existing_python.chmod(0o755)
                settings = runtime / "config" / "settings.json"
                runtime_manifest = runtime / "config" / "runtime.json"
                database = runtime / "data" / "nova_data.sqlite3"
                health_port = self._start_health_server()
                settings.write_text(
                    json.dumps(
                        {
                            "dashboard": {
                                "host": "127.0.0.1",
                                "port": health_port,
                                "healthPath": "/health",
                            }
                        },
                        separators=(",", ":"),
                    )
                    + "\n",
                    encoding="utf-8",
                )
                runtime_manifest.write_text('{"sentinel":"runtime"}\n', encoding="utf-8")
                with closing(sqlite3.connect(database)) as connection:
                    self.assertEqual(connection.execute("PRAGMA journal_mode = WAL").fetchone(), ("wal",))
                    connection.execute(
                        "CREATE TABLE update_evidence (id INTEGER PRIMARY KEY, value TEXT NOT NULL UNIQUE)"
                    )
                    connection.execute(
                        "CREATE TABLE schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
                    )
                    connection.execute(
                        "INSERT INTO schema_migrations(version, applied_at) VALUES ('0001_initial', 'fixture')"
                    )
                    connection.execute("INSERT INTO update_evidence(value) VALUES ('before-update')")
                    connection.commit()
                    connection.execute("PRAGMA wal_checkpoint(TRUNCATE)")
                protected = (settings, runtime_manifest, database)
                before_hashes = {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected}

                plist_names = (
                    "com.open-nova.dashboard.plist",
                    "com.open-nova.dashboard.watchdog.plist",
                    "com.open-nova.rag-server.plist",
                    "open-nova.daily.pipeline.plist",
                    "open-nova.daily.dashboard-aggregation.plist",
                )
                for name in plist_names:
                    self._write_runtime_plist(launch_agents / name, runtime=runtime)
                initial_state = {
                    "com.open-nova.dashboard": "running",
                    "com.open-nova.dashboard.watchdog": "running",
                    "open-nova.daily.pipeline": "waiting",
                }
                for label, service_state in initial_state.items():
                    (state_dir / label).write_text(service_state + "\n", encoding="utf-8")
                calls = root / "launchctl-calls.log"
                fake_launchctl = root / "launchctl"
                self._write_stateful_fake_launchctl(fake_launchctl)
                fault_env = {
                    "NOVA_INSTALL_TEST_MODE": "1",
                    "NOVA_INSTALL_TEST_FAIL_PHASE": phase,
                }
                if failure_kind == "term":
                    hook = root / "update-hook"
                    hook.write_text(
                        "#!/bin/zsh\n"
                        f'if [[ "$1" == "{phase}" ]]; then kill -TERM "$PPID"; fi\n',
                        encoding="utf-8",
                    )
                    hook.chmod(0o755)
                    fault_env = {
                        "NOVA_INSTALL_TEST_MODE": "1",
                        "NOVA_INSTALL_TEST_HOOK": str(hook),
                    }
                elif failure_kind == "kill":
                    hook = root / "update-hook"
                    hook_reached = root / "update-hook-reached"
                    hook.write_text(
                        "#!/bin/zsh\n"
                        f'if [[ "$1" == "{phase}" ]]; then print -r -- "$1" > "{hook_reached}"; kill -KILL "$PPID"; fi\n',
                        encoding="utf-8",
                    )
                    hook.chmod(0o755)
                    fault_env = {
                        "NOVA_INSTALL_TEST_MODE": "1",
                        "NOVA_INSTALL_TEST_HOOK": str(hook),
                    }

                command = [
                    "zsh",
                    str(INSTALLER),
                    "--source-only",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--yes",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ]
                base_env = {
                    **os.environ,
                    "HOME": str(home),
                    "NOVA_INSTALL_PLATFORM": "Darwin",
                    "NOVA_INSTALL_LAUNCHCTL": str(fake_launchctl),
                    "NOVA_INSTALL_TEST_MODE": "1",
                    "NOVA_TEST_LAUNCHCTL_CALLS": str(calls),
                    "NOVA_TEST_LAUNCHCTL_STATE": str(state_dir),
                    "NOVA_LOCATION_FILE": str(root / "location.json"),
                }
                result = subprocess.run(
                    command,
                    cwd=ROOT,
                    env={**base_env, **fault_env},
                    text=True,
                    capture_output=True,
                    timeout=120,
                )

                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertNotIn("source-only sync complete", result.stdout + result.stderr)
                journals = list((app / "update-transactions").glob("*/journal.json"))
                self.assertEqual(len(journals), 1)
                if failure_kind == "kill":
                    self.assertEqual(result.returncode, -signal.SIGKILL, result.stdout + result.stderr)
                    self.assertEqual(hook_reached.read_text(encoding="utf-8").strip(), phase)
                    interrupted = json.loads(journals[0].read_text(encoding="utf-8"))
                    self.assertNotIn(interrupted["status"], {"committed", "rolled-back"})
                    self.assertTrue((app / ".update-transaction.lock").exists())

                    recovery = subprocess.run(
                        [sys.executable, str(UPDATE_HELPER), "recover", "--runtime", str(runtime)],
                        cwd=ROOT,
                        env=base_env,
                        text=True,
                        capture_output=True,
                        timeout=30,
                    )
                    self.assertEqual(recovery.returncode, 0, recovery.stdout + recovery.stderr)
                    self.assertTrue((app / "source").is_symlink())
                    self.assertEqual(os.readlink(app / "source"), "releases/old-release")
                    self.assertEqual(
                        {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected},
                        before_hashes,
                    )
                    recovered_state = {
                        path.name: path.read_text(encoding="utf-8").strip()
                        for path in state_dir.iterdir()
                        if path.is_file()
                    }
                    self.assertEqual(recovered_state, initial_state)
                    recovered = json.loads(journals[0].read_text(encoding="utf-8"))
                    self.assertEqual(recovered["status"], "rolled-back")
                    self.assertEqual(recovered["rollbackErrors"], [])
                    self.assertFalse((app / ".update-transaction.lock").exists())

                    retry = subprocess.run(
                        command,
                        cwd=ROOT,
                        env=base_env,
                        text=True,
                        capture_output=True,
                        timeout=120,
                    )
                    self.assertEqual(retry.returncode, 0, retry.stdout + retry.stderr)
                    self.assertTrue((app / "source" / ".open-nova-runtime-source.json").is_file())
                    self.assertEqual(
                        {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected},
                        before_hashes,
                    )
                    retry_state = {
                        path.name: path.read_text(encoding="utf-8").strip()
                        for path in state_dir.iterdir()
                        if path.is_file()
                    }
                    self.assertEqual(retry_state, initial_state)
                    statuses = sorted(
                        json.loads(path.read_text(encoding="utf-8"))["status"]
                        for path in (app / "update-transactions").glob("*/journal.json")
                    )
                    self.assertEqual(statuses, ["committed", "rolled-back"])
                    self.assertFalse((app / ".update-transaction.lock").exists())
                    continue

                self.assertTrue((app / "source").is_symlink())
                self.assertEqual(os.readlink(app / "source"), "releases/old-release")
                self.assertEqual(
                    {path: hashlib.sha256(path.read_bytes()).hexdigest() for path in protected},
                    before_hashes,
                )
                final_state = {
                    path.name: path.read_text(encoding="utf-8").strip()
                    for path in state_dir.iterdir()
                    if path.is_file()
                }
                self.assertEqual(final_state, initial_state)
                journal = json.loads(journals[0].read_text(encoding="utf-8"))
                self.assertEqual(journal["status"], "rolled-back")
                self.assertEqual(journal["rollbackErrors"], [])
                events = [
                    json.loads(line)["event"]
                    for line in (journals[0].parent / "events.jsonl").read_text(encoding="utf-8").splitlines()
                ]
                self.assertIn(phase, events)
                self.assertFalse((app / ".update-transaction.lock").exists())

    def test_atomic_update_defers_external_rag_skill_registration(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            codex_skills = root / "codex-skills"
            paths = initialize_home(runtime, legacy_diary_root=root / "Diary")
            self._write_prior_runtime_source(runtime)
            write_settings(
                {
                    "externalTools": {
                        "codex": {"skillsRoot": str(codex_skills)},
                        "installerSelectedTools": [{"key": "codex", "name": "Codex", "path": str(root / "codex")}],
                        "installerV2SkillRegistration": {
                            "status": "dashboard-controlled",
                            "supportedNow": True,
                            "selectedTools": [{"key": "codex", "name": "Codex", "path": str(root / "codex")}],
                        },
                    }
                },
                paths,
            )

            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--upgrade",
                    "--source-root",
                    str(ROOT),
                    "--runtime",
                    str(runtime),
                    "--yes",
                    "--enable-rag",
                    "--rag-embedding-mode",
                    "cloud",
                    "--register-rag-skills",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "NOVA_INSTALL_PLATFORM": "Linux",
                    "NOVA_INSTALL_TEST_MODE": "1",
                    "NOVA_LOCATION_FILE": str(root / "location.json"),
                },
                text=True,
                capture_output=True,
                timeout=120,
            )

            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            skill = codex_skills / "open-nova-rag" / "SKILL.md"
            self.assertFalse(skill.exists(), result.stdout + result.stderr)
            saved = (runtime / "config" / "settings.json").read_text(encoding="utf-8")
            self.assertIn('"status": "dashboard-controlled"', saved)
            self.assertNotIn('"status": "installer-applied"', saved)

    def test_installer_copy_uses_semantic_lines_and_display_width_aware_wrapping(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("print_tty_copy()", script)
        self.assertIn("unicodedata.east_asian_width", script)
        self.assertIn('NOVA_INSTALL_COPY_WIDTH="$width"', script)
        self.assertIn('print_tty_copy "$prompt"', script)
        self.assertIn("Continue only if you understand", script)
        self.assertIn("请确认你理解：", script)

    def test_detected_external_tools_use_an_affirmative_selected_marker(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('marker="[✓]"', script)
        self.assertNotIn('marker="[x]"', script)
        self.assertNotIn('marker="✅"', script)

    def test_update_reuses_runtime_state_and_venv_without_deleting_user_data(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('VENV_DIR="${RUNTIME_HOME}/.venv"', script)
        self.assertIn('UPDATE_STAGED_VENV="$(update_transaction_command reserve-artifact', script)
        self.assertIn('--kind venv)', script)
        self.assertNotIn('UPDATE_STAGED_VENV="${candidate_root}/${UPDATE_TRANSACTION_ID}"', script)
        self.assertIn('run_update_candidate_cmd candidate-venv-create', script)
        self.assertIn('run_update_candidate_cmd candidate-pip-upgrade', script)
        self.assertIn('run_update_candidate_cmd candidate-pip-install', script)
        self.assertIn('run-candidate-command', script)
        self.assertIn('record_update_candidate venv "${UPDATE_STAGED_VENV}"', script)
        self.assertNotIn('rm -rf "${VENV_DIR}"', script)
        self.assertNotIn('rm -rf "${RUNTIME_HOME}"', script)
        self.assertNotIn('rm -rf "${RUNTIME_HOME}/data"', script)
        self.assertIn("legacy Python LaunchAgents may receive cache-suppression environment metadata", script)

    def test_install_summary_dashboard_url_uses_runtime_settings_when_available(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("effective_dashboard_url()", script)
        self.assertIn('${RUNTIME_HOME}/config/settings.json', script)
        self.assertIn('dashboard.get("port")', script)
        self.assertIn('dashboard_detail="server enabled at $(effective_dashboard_url)"', script)

    def test_install_summary_llm_and_tools_use_runtime_settings_when_available(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("effective_llm_summary()", script)
        self.assertIn("effective_external_tools_summary()", script)
        self.assertIn('settings.get("llmProvider")', script)
        self.assertIn('external.get("installerSelectedTools")', script)
        self.assertIn('summary_line "${llm_status:-warn}" "LLM generation" "${llm_detail:-unknown}"', script)
        self.assertIn('summary_line ok "external tools" "$(effective_external_tools_summary)"', script)

    def test_install_summary_reads_runtime_settings_during_dry_run_when_available(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertNotIn('if [[ "$DRY_RUN" != "1" && -f "${RUNTIME_HOME}/config/settings.json" ]]; then', script)
        self.assertIn('if [[ -f "${RUNTIME_HOME}/config/settings.json" ]]; then', script)

    def test_installer_runs_post_install_doctor_after_install_actions(self):
        script = INSTALLER.read_text(encoding="utf-8")

        overlay = script.index("apply_installer_settings_overlay")
        post_install = script.index("run_post_install_doctor")
        self.assertGreater(post_install, overlay)
        self.assertIn("Post-install doctor", script)
        self.assertIn("onboarding runtime-status", script)
        self.assertIn("doctor --installer", script)
        self.assertIn("doctor --pipeline", script)
        self.assertIn("doctor --scheduler", script)
        self.assertIn("doctor --rag", script)
        self.assertIn("run_json_cmd", script)
        self.assertIn('run_json_cmd "Runtime status doctor"', script)
        self.assertIn('run_optional_json_cmd "Installer doctor"', script)
        self.assertIn('run_optional_json_cmd "Pipeline doctor"', script)
        self.assertIn('run_optional_json_cmd "Scheduler doctor"', script)
        self.assertIn('INSTALLER_LOG_FILE="${RUNTIME_HOME}/state/logs/installer-v2.log"', script)
        self.assertIn('summary_line ok "installer log"', script)

    def test_full_upgrade_runs_fatal_candidate_doctor_before_verify(self):
        script = INSTALLER.read_text(encoding="utf-8")
        driver = script.split("run_guarded_update_transaction() {", 1)[1].split(
            "print_useful_commands()", 1
        )[0]
        doctor = script.split("run_update_candidate_doctor() {", 1)[1].split(
            "clean_staged_candidate_build_artifacts()", 1
        )[0]

        self.assertLess(driver.index("restore-services"), driver.index("run_update_candidate_doctor"))
        self.assertLess(driver.index("run_update_candidate_doctor"), driver.index("verify --state"))
        self.assertIn("candidate-doctor-started", driver)
        self.assertIn("candidate-doctor-passed", driver)
        self.assertIn('if [[ "$SOURCE_ONLY" != "1" ]]; then', driver)
        self.assertIn('run_json_cmd "Candidate installer doctor"', doctor)
        self.assertIn("doctor --installer", doctor)
        self.assertNotIn("run_optional_json_cmd", doctor)
        self.assertNotIn("onboarding runtime-status", doctor)

    def test_installer_verifies_runtime_dependencies_after_pip_install(self):
        script = INSTALLER.read_text(encoding="utf-8")

        pip_install = script.index('run_cmd "${VENV_PY}" -m pip install "${INSTALL_SPEC}"')
        dependency_gate = script.rindex("run_runtime_dependency_gate")
        cli_shim = script.rindex("create_cli_shim")

        self.assertGreater(dependency_gate, pip_install)
        self.assertLess(dependency_gate, cli_shim)
        self.assertIn("Verifying runtime Dashboard dependency gate", script)
        self.assertIn('("fastapi", "fastapi>=0.110,<1", "Dashboard API")', script)
        self.assertIn('("uvicorn", "uvicorn>=0.29,<1", "Dashboard server")', script)
        self.assertIn('("yaml", "PyYAML>=6,<7", "Dashboard settings YAML")', script)
        self.assertIn('("croniter", "croniter>=2,<7", "Dashboard scheduler")', script)
        self.assertIn('("sentence_transformers", "sentence-transformers>=3,<6", "nova-RAG local embeddings")', script)
        self.assertIn('("torch", "torch>=2,<3", "nova-RAG local embeddings")', script)
        self.assertIn('source_root / "src" / "dashboard" / "app" / "static" / "index.html"', script)
        self.assertIn('importlib.import_module("app.main")', script)
        self.assertIn("Installing missing runtime dependencies detected by dependency gate", script)
        self.assertIn('run_cmd "${VENV_PY}" -m pip install "${missing_packages[@]}"', script)

    def test_enabled_cloud_rag_installs_base_server_dependency_extra(self):
        script = INSTALLER.read_text(encoding="utf-8")
        lifecycle = (ROOT / "src" / "agentic_rag" / "rag_server_lifecycle.py").read_text(encoding="utf-8")
        onboarding = (ROOT / "src" / "data_foundation" / "onboarding_plan.py").read_text(encoding="utf-8")
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        extras = metadata["project"]["optional-dependencies"]
        server_dependencies = [item.lower() for item in extras["rag-server"]]

        for module in ("numpy", "pydantic", "fastapi", "uvicorn"):
            self.assertTrue(
                any(item.startswith(module) for item in server_dependencies),
                (module, server_dependencies),
            )
        self.assertIn('if [[ "$ENABLE_RAG" == "1" ]]; then\n  INSTALL_EXTRAS+=("rag-server")', script)
        self.assertIn('if os.environ.get("NOVA_INSTALL_ENABLE_RAG") == "1":\n    rag_checks = [', script)
        self.assertIn('if os.environ.get("NOVA_INSTALL_RAG_EMBEDDING_MODE") == "local":', script)
        self.assertIn('else "rag-server"', lifecycle)
        self.assertIn("Repair the Open Nova rag-server runtime dependencies", lifecycle)
        self.assertIn('"nova-rag-cloud": "rag-server"', onboarding)

    def test_standalone_wheel_declares_synchronized_runtime_config_module(self):
        metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        self.assertEqual(metadata["tool"]["setuptools"]["py-modules"], ["config"])
        self.assertEqual(metadata["tool"]["setuptools"]["package-dir"]["advanced"], "advanced")
        self.assertIn(".", metadata["tool"]["setuptools"]["packages"]["find"]["where"])
        self.assertIn("advanced*", metadata["tool"]["setuptools"]["packages"]["find"]["include"])
        self.assertEqual((ROOT / "config.py").read_bytes(), (ROOT / "src" / "config.py").read_bytes())

    def test_installer_useful_commands_are_user_facing_cli_commands(self):
        script = INSTALLER.read_text(encoding="utf-8")
        useful = script.split("print_useful_commands() {", 1)[1].split("summary_line()", 1)[0]

        self.assertIn('open-nova onboarding runtime-status --runtime', useful)
        self.assertIn('open-nova doctor --installer --runtime', useful)
        self.assertIn('open-nova doctor --pipeline --runtime', useful)
        self.assertIn('open-nova doctor --scheduler --runtime', useful)
        self.assertIn('open-nova onboarding rollback-plan --runtime', useful)
        self.assertIn('open-nova dashboard restart', useful)
        self.assertNotIn("PYTHONPATH", useful)
        self.assertNotIn('"${VENV_PY}" -m data_foundation.cli', useful)

    def test_non_core_permission_failures_are_warning_only(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("run_optional_cmd", script)
        self.assertIn("run_optional_json_cmd", script)
        self.assertIn("not required for core runtime install", script)
        self.assertIn('run_optional_json_cmd "Scheduler LaunchAgent plist write"', script)
        self.assertIn('run_optional_json_cmd "Scheduler LaunchAgent registration"', script)
        self.assertIn('run_optional_json_cmd "SSE server LaunchAgent service registration"', script)
        self.assertIn("launcher.install_dashboard_launch_agent", script)
        self.assertIn("launcher.install_rag_launch_agent", script)
        self.assertIn("continuing without Desktop shortcut", script)

    def test_installer_creates_product_cli_shim(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("create_cli_shim", script)
        self.assertIn("ensure_cli_on_shell_path", script)
        self.assertIn('CLI_SHIM="${RUNTIME_HOME}/bin/open-nova"', script)
        self.assertIn('USER_CLI_SHIM="${NOVA_INSTALL_USER_CLI_SHIM:-$HOME/.local/bin/open-nova}"', script)
        self.assertIn("# >>> open-nova installer PATH >>>", script)
        self.assertIn("unset WORKSPACE_DIR DIARY_OUTPUT_DIR TMP_WORKSPACE NOVA_DATA_DB_PATH NOVA_DATA_EXPORT_DIR TASK_DB_PATH", script)
        self.assertIn('export PYTHONDONTWRITEBYTECODE="1"', script)
        self.assertIn('local shim_tmp="${CLI_SHIM}.tmp.$$"', script)
        self.assertIn('mv -f "${shim_tmp}" "${CLI_SHIM}"', script)
        self.assertNotIn('export DIARY_OUTPUT_DIR="${DIARY_OUTPUT_DIR}"', script)
        self.assertNotIn('export TMP_WORKSPACE="${RUNTIME_HOME}/state/tmp"', script)
        self.assertNotIn('export NOVA_DATA_DB_PATH="${RUNTIME_HOME}/data/nova_data.sqlite3"', script)
        self.assertNotIn('export NOVA_DATA_EXPORT_DIR="${SNAPSHOTS_OUTPUT_DIR}"', script)
        self.assertIn("export_runtime_environment", script)
        self.assertIn('exec "${VENV_PY}" -m data_foundation.cli "\\$@"', script)
        self.assertIn("ln -sf", script)
        self.assertIn("deploy_runtime_source", script)
        self.assertIn('DEPLOY_SOURCE_ROOT="${RUNTIME_HOME}/app/source"', script)
        self.assertIn('INSTALL_SPEC="${DEPLOY_SOURCE_ROOT}', script)
        self.assertIn(".open-nova-runtime-source.json", script)

    def test_upgrade_recreates_product_cli_shim_after_transaction(self):
        script = INSTALLER.read_text(encoding="utf-8")
        start = script.index(
            'if [[ "$UPGRADE" == "1" ]]; then\n  run_guarded_update_transaction'
        )
        end = script.index('run_cmd mkdir -p "${RUNTIME_HOME}"', start)
        upgrade_flow = script[start:end]

        self.assertLess(upgrade_flow.index("run_guarded_update_transaction"), upgrade_flow.index("create_cli_shim"))
        self.assertLess(upgrade_flow.index("create_cli_shim"), upgrade_flow.index('if [[ "$SOURCE_ONLY" == "1" ]]'))

    def test_runtime_source_copy_excludes_local_state_and_machine_settings(self):
        script = INSTALLER.read_text(encoding="utf-8")

        for name in (
            '".env"',
            '".git"',
            '".playwright-cli"',
            '"__pycache__"',
            '"artifacts"',
            '"cache"',
            '"data"',
            '"location.json"',
            '"logs"',
            '"runtime.json"',
            '"settings.json"',
            '"snapshots"',
            '"state"',
        ):
            with self.subTest(name=name):
                self.assertIn(name, script)
        for suffix in ('".db"', '".log"', '".sqlite"', '".sqlite3"'):
            with self.subTest(suffix=suffix):
                self.assertIn(suffix, script)
        self.assertIn('name.startswith(".env.")', script)

    def test_runtime_source_copy_writes_provenance_manifest(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('"schemaVersion": 2', script)
        self.assertIn("def privacy_safe_source_locator(source_path):", script)
        self.assertIn('"sourceLocator": privacy_safe_source_locator(source)', script)
        self.assertNotIn('"sourceRoot": str(source.resolve())', script)
        self.assertNotIn('"deployedSourceRoot": str(deploy_target.expanduser().absolute())', script)
        self.assertNotIn('"releaseRoot": str(release_target.expanduser().absolute())', script)
        self.assertIn('pwd.getpwuid(os.getuid()).pw_dir', script)
        self.assertIn('"copiedAt": datetime.now().astimezone().isoformat()', script)
        self.assertIn('"pyprojectVersion": None', script)
        self.assertIn('git_value("rev-parse", "HEAD")', script)
        self.assertIn('git_value("rev-parse", "--abbrev-ref", "HEAD")', script)
        self.assertIn('git_optional("config", "--get", "remote.origin.url")', script)
        self.assertIn('git_optional("remote", "get-url", first_remote)', script)
        self.assertIn("def redact_git_remote(value):", script)
        self.assertIn('if parsed.scheme == "file"', script)
        self.assertIn('if not isinstance(remote, str):', script)
        self.assertIn('r"[A-Za-z0-9][A-Za-z0-9._+!-]{0,127}"', script)
        self.assertIn("scp_remote = re.fullmatch", script)
        self.assertIn('"remote": redact_git_remote(remote)', script)
        self.assertIn('git_value("status", "--porcelain")', script)
        self.assertIn('"policy": contract["policy"]', script)
        self.assertIn('"preCommitWriterContract": contract["preCommitWriterContract"]', script)
        self.assertIn('"migrationSetSha256": migration_set_digest.hexdigest()', script)
        self.assertIn('(target / ".open-nova-runtime-source.json").write_text', script)

    def test_runtime_source_deploy_uses_versioned_release_symlink(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('local releases_root="${app_root}/releases"', script)
        self.assertIn('"deploymentMode": "release-symlink"', script)
        self.assertIn('"releaseLocator": {"kind": "runtime-relative"', script)
        self.assertIn("os.symlink(release, link)", script)
        self.assertNotIn("os.replace(tmp, link)", script)
        self.assertNotIn("os.unlink(tmp)", script)
        self.assertIn("the no-clobber pointer was preserved", script)
        self.assertNotIn('rm -rf "${DEPLOY_SOURCE_ROOT}"', script)

    def test_runtime_source_deploy_uses_allowlist_not_full_repo_copy(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("allowed_top_level = {", script)
        for name in ('"src"', '"advanced"', '"install"', '"pyproject.toml"', '"LICENSE"', '"MANIFEST.in"', '"config.py"'):
            with self.subTest(name=name):
                self.assertIn(name, script)
        self.assertIn("for name in sorted(allowed_top_level):", script)
        self.assertNotIn("shutil.copytree(source, target, ignore=ignore, symlinks=True)", script)
        copy_block = script.split("allowed_top_level = {", 1)[1].split("manifest = {", 1)[0]
        self.assertNotIn('"tests"', copy_block)
        self.assertNotIn('"docs"', copy_block)
        self.assertNotIn('"README.md"', copy_block)

    def test_runtime_source_artifacts_are_cleaned_after_doctor(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("cleanup_runtime_source_artifacts()", script)
        self.assertIn('rm -rf "${DEPLOY_SOURCE_ROOT}/build" "${DEPLOY_SOURCE_ROOT}/dist"', script)
        self.assertIn('find -H "${DEPLOY_SOURCE_ROOT}"', script)
        self.assertIn('-name "__pycache__"', script)
        self.assertIn('-name "*.egg-info"', script)
        entry = script.rsplit("run_post_install_doctor", 1)[1]
        self.assertIn("cleanup_runtime_source_artifacts", entry)
        self.assertLess(entry.index("cleanup_runtime_source_artifacts"), entry.index("print_install_summary"))

    def test_runtime_source_artifact_cleanup_follows_only_active_source_symlink(self):
        script = INSTALLER.read_text(encoding="utf-8")
        function_start = script.index("cleanup_runtime_source_artifacts() {")
        function_end = script.index("\n}\n\nrun_runtime_dependency_check()", function_start) + 2
        cleanup_function = script[function_start:function_end]

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app_root = root / "runtime" / "app"
            release = app_root / "releases" / "20260712T000000-test"
            release.mkdir(parents=True)
            source = app_root / "source"
            source.symlink_to(Path("releases") / release.name)

            artifacts = (
                release / "build",
                release / "dist",
                release / "open_nova.egg-info",
                release / "src" / "open_nova.egg-info",
                release / "src" / "package" / "__pycache__",
            )
            for artifact in artifacts:
                artifact.mkdir(parents=True)
                (artifact / "generated.txt").write_text("generated\n", encoding="utf-8")

            ordinary_file = release / "src" / "package" / "module.py"
            ordinary_file.write_text("VALUE = 1\n", encoding="utf-8")
            outside = root / "outside"
            outside_egg_info = outside / "external.egg-info"
            outside_cache = outside / "__pycache__"
            outside_egg_info.mkdir(parents=True)
            outside_cache.mkdir()
            nested_symlink = release / "linked-tree"
            nested_symlink.symlink_to(outside, target_is_directory=True)

            harness = "\n".join(
                (
                    "set -euo pipefail",
                    "progress_start() { :; }",
                    "progress_ok() { :; }",
                    'DRY_RUN=0',
                    'DEPLOY_SOURCE_ROOT="$1"',
                    cleanup_function,
                    "cleanup_runtime_source_artifacts",
                )
            )
            result = subprocess.run(
                ["zsh", "-c", harness, "cleanup-runtime-source-test", str(source)],
                cwd=ROOT,
                text=True,
                capture_output=True,
                check=False,
            )

            self.assertEqual(result.returncode, 0, msg=result.stderr)
            self.assertTrue(source.is_symlink())
            self.assertEqual(os.readlink(source), str(Path("releases") / release.name))
            self.assertTrue(release.is_dir())
            self.assertTrue(ordinary_file.is_file())
            for artifact in artifacts:
                with self.subTest(artifact=artifact):
                    self.assertFalse(artifact.exists())
            self.assertTrue(nested_symlink.is_symlink())
            self.assertTrue(outside_egg_info.is_dir())
            self.assertTrue(outside_cache.is_dir())

    def test_installer_exports_runtime_environment_before_service_registration(self):
        script = INSTALLER.read_text(encoding="utf-8")

        export_call = script.index("export_runtime_environment")
        scheduler = script.index("Registering managed Open Nova scheduler LaunchAgents")
        dashboard = script.index("Installing SSE server LaunchAgent service")
        self.assertLess(export_call, scheduler)
        self.assertLess(export_call, dashboard)
        self.assertIn("unset WORKSPACE_DIR DIARY_OUTPUT_DIR TMP_WORKSPACE NOVA_DATA_DB_PATH NOVA_DATA_EXPORT_DIR TASK_DB_PATH", script)

    def test_installer_has_guarded_upgrade_mode(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("--upgrade", script)
        self.assertIn("--source-only", script)
        self.assertIn("--sync-runtime-source", script)
        self.assertIn("UPGRADE=1", script)
        self.assertIn("--upgrade requires an existing runtime", script)
        self.assertIn("Proceed with upgrade now?", script)
        self.assertIn("preserving runtime settings and secrets", script)
        self.assertIn("Open Nova installer v2 upgrade complete", script)
        self.assertIn('if [[ "$UPGRADE" != "1" || "$LANGUAGE_SET" == "1" ]]; then', script)
        self.assertIn('first_install_or("NOVA_INSTALL_LLM_SET") and enable_llm', script)
        self.assertIn('first_install_or("NOVA_INSTALL_RAG_SET")', script)
        self.assertIn('first_install_or("NOVA_INSTALL_DIARY_OUTPUT_SET")', script)

    def test_guarded_candidate_environment_uses_absolute_env_binary(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertEqual(script.count("/usr/bin/env -i"), 2)
        self.assertEqual(script.count("/usr/bin/env\n      -i"), 2)
        self.assertEqual(
            script.count("/usr/bin/env PYTHONDONTWRITEBYTECODE=1"),
            2,
        )
        self.assertNotIn("-- env -i", script)
        self.assertNotRegex(script, r"(?m)^\s+env -i(?:\s|\\)")

    def test_installer_llm_provider_keeps_provider_and_api_separate(self):
        script = INSTALLER.read_text(encoding="utf-8")
        llm_provider_case = script.split("--llm-provider)", 1)[1].split("--llm-endpoint)", 1)[0]

        self.assertIn('LLM_PROVIDER="$2"', llm_provider_case)
        self.assertIn('LLM_PROVIDER_MODE="preset"', llm_provider_case)
        self.assertNotIn('LLM_API="$2"', llm_provider_case)
        self.assertIn("NOVA_INSTALL_LLM_API", script)

    def test_source_only_dry_run_skips_settings_dependencies_and_services(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--source-only",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Staging source snapshot", output)
        self.assertNotIn("copy source snapshot", output)
        self.assertNotIn(".open-nova-runtime-source.json", output)
        self.assertNotIn("source-only dry-run complete", output)
        self.assertNotIn("-m venv", output)
        self.assertNotIn("-m pip install", output)
        self.assertNotIn("onboarding runtime-apply", output)
        self.assertNotIn("apply runtime bootstrap", output.lower())
        self.assertNotIn("Creating Desktop diary shortcut", output)
        self.assertFalse(runtime.exists())

    def test_installer_persists_distinct_nova_task_feature_flag(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn('"novaTask": os.environ["NOVA_INSTALL_ENABLE_NOVA_TASK"] == "1"', script)
        self.assertIn('"taskAuditSink": os.environ["NOVA_INSTALL_ENABLE_NOVA_TASK"] == "1"', script)

    def test_installer_declares_install_time_language_profile(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("--language LOCALE", script)
        self.assertIn("NOVA_INSTALL_LANGUAGE", script)
        self.assertIn("apply_language_profile", script)
        self.assertIn('"locale": os.environ["NOVA_INSTALL_LANGUAGE"]', script)
        self.assertIn('"languageProfile": os.environ["NOVA_INSTALL_PIPELINE_LANGUAGE_PROFILE"]', script)
        self.assertIn('"englishEnabled": os.environ["NOVA_INSTALL_PIPELINE_ENGLISH_ENABLED"] == "1"', script)
        self.assertIn('"diarySchemaVersion": os.environ["NOVA_INSTALL_PIPELINE_DIARY_SCHEMA_VERSION"]', script)
        self.assertIn('"promptPayloadProfile": os.environ["NOVA_INSTALL_PIPELINE_PROMPT_PAYLOAD_PROFILE"]', script)
        self.assertIn('update.setdefault("rag", {})["languageProfile"] = os.environ["NOVA_INSTALL_RAG_LANGUAGE_PROFILE"]', script)
        self.assertIn('RAG_LOCAL_MODEL="all-MiniLM-L6-v2"', script)
        self.assertIn('--language "${INSTALL_LANGUAGE}"', script)

    def test_dry_run_can_select_english_language_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--language",
                    "en-US",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Install summary", output)
        self.assertIn("Useful commands:", output)
        self.assertNotIn("language: en-US", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_english_rag_uses_english_local_embedding_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--language",
                    "en-US",
                    "--enable-rag",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("nova-RAG: local embeddings; model all-MiniLM-L6-v2; dimension 384", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_english_rag_preserves_explicit_local_embedding_model(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--language",
                    "en-US",
                    "--enable-rag",
                    "--rag-local-model",
                    "BAAI/bge-large-en-v1.5",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("nova-RAG: local embeddings; model BAAI/bge-large-en-v1.5; dimension 1024", output)
        self.assertFalse(runtime.exists())

    def test_installer_rejects_unknown_language_profile(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--language",
                    "fr-FR",
                ],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "NOVA_INSTALL_PLATFORM": "Darwin"},
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, output)
        self.assertIn("--language must be zh-CN or en-US", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_defaults_to_dashboard_scheduler_and_base_dashboard_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Preparing runtime directories", output)
        self.assertIn("Staging source snapshot", output)
        self.assertIn("Creating Python environment", output)
        self.assertIn("Installing runtime dependencies", output)
        self.assertIn("Verifying runtime dependency gate", output)
        self.assertIn("安装摘要", output)
        self.assertIn(f"runtime source {runtime.resolve()}/app/source", output)
        self.assertNotIn("mode: install", output)
        self.assertNotIn("preflight ok:", output)
        self.assertNotIn("copy source snapshot", output)
        self.assertNotIn(".open-nova-runtime-source.json", output)
        self.assertNotIn("-m venv", output)
        self.assertNotIn("-m pip install", output)
        self.assertNotIn("onboarding runtime-apply", output)
        self.assertNotIn("import-check dashboard dependencies", output)
        self.assertIn("doctor --installer", output)
        self.assertIn("doctor --pipeline", output)
        self.assertIn("doctor --scheduler", output)
        self.assertNotIn("--select-active-runtime", output)
        self.assertNotIn("--scheduler-plist-apply", output)
        self.assertNotIn("--scheduler-register-apply", output)
        self.assertNotIn("install_dashboard_launch_agent", output)
        self.assertIn("Creating open-nova CLI shim", output)
        self.assertIn("bin/open-nova", output)
        self.assertNotIn("ln -s", output)
        self.assertNotIn(".zprofile", output)
        self.assertFalse(runtime.exists())

    def test_dashboard_port_auto_falls_back_when_default_port_is_busy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            fake_lsof = bin_dir / "lsof"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_lsof(fake_lsof)
            env = {
                **os.environ,
                "HOME": str(home),
                "PATH": f"{bin_dir}{os.pathsep}{os.environ.get('PATH', '')}",
                "NOVA_INSTALL_LSOF": str(fake_lsof),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-scheduler",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("Dashboard port 3036 is in use; falling back to 8765", output)
        self.assertIn("Dashboard: server enabled at http://127.0.0.1:8765/dashboard", output)
        self.assertNotIn("preflight ok:", output)
        self.assertNotIn("install_dashboard_launch_agent", output)
        self.assertFalse(runtime.exists())

    def test_dashboard_port_auto_can_be_disabled_for_strict_preflight(self):
        script = INSTALLER.read_text(encoding="utf-8")

        self.assertIn("--no-dashboard-port-auto", script)
        self.assertIn("DASHBOARD_PORT_AUTO=0", script)
        self.assertIn("is already in use and --no-dashboard-port-auto is set", script)
        self.assertIn('preflight_check error error "dashboard-port"', script)

    def test_preflight_blocks_missing_python_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(Path(tmp) / "missing-python"),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "NOVA_INSTALL_PLATFORM": "Darwin"},
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, output)
        self.assertIn("preflight error: python-command", output)
        self.assertNotIn("-m venv", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_plans_managed_standalone_python_install_when_default_python_is_too_old(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            low_python = bin_dir / "python3"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_versioned_python(low_python, "3.9.6")
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_INSTALL_MACHINE": "arm64",
                "NOVA_INSTALL_PYTHON_CANDIDATES": str(low_python),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Planning managed Python 3.13.14 install", output)
        self.assertNotIn("cpython-3.13.14%2B20260623-aarch64-apple-darwin-install_only.tar.gz", output)
        self.assertNotIn("verify sha256 804c86c8665b18eb0df5070a79d828229018d145baea38a71a5c74c03f9b11d4", output)
        self.assertNotIn("preflight warn: python-bootstrap", output)
        self.assertNotIn("brew install", output)
        self.assertNotIn("preflight error: python-version", output)
        self.assertFalse(runtime.exists())

    def test_upgrade_dry_run_reports_upgrade_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--upgrade",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "NOVA_INSTALL_PLATFORM": "Darwin"},
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Open Nova installer v2 upgrade dry-run complete", output)
        self.assertNotIn("mode: upgrade", output)
        self.assertNotIn("dry-run only", output)
        self.assertIn("Creating Python environment", output)
        self.assertIn("Creating open-nova CLI shim", output)
        self.assertFalse(runtime.exists())

    def test_upgrade_requires_existing_runtime_for_real_apply(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            fake_python = Path(tmp) / "python3"
            log_path = Path(tmp) / "commands.log"
            home.mkdir()
            self._write_fake_python(fake_python, log_path)
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--upgrade",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                    "--yes",
                ],
                cwd=ROOT,
                env={**os.environ, "HOME": str(home), "NOVA_INSTALL_PLATFORM": "Darwin"},
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, output)
        self.assertIn("--upgrade requires an existing runtime", output)
        self.assertNotIn("-m venv", log)

    def test_fresh_apply_rejects_existing_runtime_before_writes(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            old_release = runtime / "app" / "releases" / "old"
            fake_python = Path(tmp) / "python3"
            log_path = Path(tmp) / "commands.log"
            old_release.mkdir(parents=True)
            (runtime / "app" / "source").symlink_to("releases/old")
            sentinel = old_release / "operator-owned.txt"
            sentinel.write_text("preserve\n", encoding="utf-8")
            installer_log = runtime / "state" / "logs" / "installer-v2.log"
            installer_log.parent.mkdir(parents=True)
            installer_log.write_text("operator-log\n", encoding="utf-8")
            self._write_fake_python(fake_python, log_path)

            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                    "--yes",
                ],
                cwd=ROOT,
                env={
                    **os.environ,
                    "HOME": str(home),
                    "NOVA_INSTALL_PLATFORM": "Darwin",
                },
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8") if log_path.exists() else ""

            output = result.stdout + result.stderr
            self.assertNotEqual(result.returncode, 0, output)
            self.assertIn("existing Open Nova Runtime state requires --upgrade", output)
            self.assertEqual(os.readlink(runtime / "app" / "source"), "releases/old")
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
            self.assertEqual(
                installer_log.read_text(encoding="utf-8"),
                "operator-log\n",
            )
            self.assertNotIn("-m venv", log)

    def test_dry_run_can_disable_desktop_diary_link(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-desktop-diary-link",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("Desktop diary shortcut:", output)
        self.assertNotIn("Desktop diary shortcut skipped", output)
        self.assertNotIn("Open Nova'", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_can_disable_wizard_with_environment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_INSTALL_WIZARD": "false",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("dry-run only", output)
        self.assertIn("Open Nova installer v2 dry-run complete", output)
        self.assertIn("open-nova doctor --installer", output)
        self.assertIn("open-nova doctor --pipeline", output)
        self.assertIn("open-nova doctor --scheduler", output)
        self.assertNotIn("guided setup", output)

    def test_dry_run_summary_only_hides_command_noise(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_INSTALL_WIZARD": "false",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--summary-only",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("安装摘要", output)
        self.assertIn("常用命令", output)
        self.assertIn("Open Nova installer v2 dry-run complete", output)
        self.assertIn("open-nova doctor --pipeline", output)
        self.assertNotIn("+ mkdir", output)
        self.assertNotIn("Installer preflight", output)
        self.assertFalse(runtime.exists())

    def test_summary_only_uses_selected_english_language(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_INSTALL_WIZARD": "false",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--summary-only",
                    "--language",
                    "en-US",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Install summary", output)
        self.assertIn("Useful commands:", output)
        self.assertNotIn("安装摘要", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_can_disable_scheduler_and_dashboard_server_without_disabling_nova_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("SSE server disabled", output)
        self.assertNotIn("Static snapshot pages such as AI Assets", output)
        self.assertNotIn("--scheduler-register-apply", output)
        self.assertNotIn("install_dashboard_launch_agent", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_reports_output_paths_and_non_secret_llm_provider_metadata(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            diary = home / "DiaryOut"
            reports = home / "ReportsOut"
            snapshots = home / "SnapshotsOut"
            archives = home / "ArchivesOut"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--diary-output",
                    str(diary),
                    "--reports-output",
                    str(reports),
                    "--snapshots-output",
                    str(snapshots),
                    "--archives-output",
                    str(archives),
                    "--llm-provider",
                    "openai-compatible",
                    "--llm-endpoint",
                    "https://llm.example.invalid/v1",
                    "--llm-model",
                    "example-model",
                    "--llm-api-key-env",
                    "NOVA_TEST_LLM_KEY",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"diary artifacts: {diary.resolve()}", output)
        self.assertNotIn(f"reports output: {reports.resolve()}", output)
        self.assertNotIn(f"snapshots output: {snapshots.resolve()}", output)
        self.assertNotIn(f"archives/intermediate output: {archives.resolve()}", output)
        self.assertIn("LLM generation: preset/openai-compatible; model example-model; key env NOVA_TEST_LLM_KEY", output)
        self.assertNotIn("api key env:", output)
        self.assertNotIn("no secret values", output)
        self.assertFalse(runtime.exists())
        self.assertFalse(diary.exists())

    def test_installer_rejects_secret_like_llm_api_key_env_without_echoing_value(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            secret_like = "sk-test-value-that-should-not-be-echoed"
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--llm-provider",
                    "openai-compatible",
                    "--llm-endpoint",
                    "https://llm.example.invalid/v1",
                    "--llm-model",
                    "example-model",
                    "--llm-api-key-env",
                    secret_like,
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 2, output)
        self.assertIn("must look like LLM_API_KEY", output)
        self.assertIn("do not paste the API key value", output)
        self.assertNotIn(secret_like, output)
        self.assertFalse(runtime.exists())

    def test_no_dashboard_is_rejected_because_dashboard_is_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--no-dashboard",
                    "--no-scheduler",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr
        self.assertEqual(result.returncode, 2, output)
        self.assertIn("--no-dashboard is no longer supported", output)
        self.assertIn("--no-dashboard-server", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_enable_dev_test_adds_dev_test_extra(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-dev-test",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr
        deployed = runtime.resolve() / "app" / "source"

        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"runtime source {deployed}", output)
        self.assertNotIn("install dependency spec:", output)
        self.assertNotIn("dev-test: enabled", output)
        self.assertNotIn(f"-m pip install {deployed}[dashboard,dev-test]", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_rag_embedding_server_deployment_is_background_and_nonblocking(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-rag",
                    "--deploy-embedding-server",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr
        deployed = runtime.resolve() / "app" / "source"

        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"runtime source {deployed}", output)
        self.assertNotIn("install dependency spec:", output)
        self.assertNotIn(f"-m pip install {deployed}[dashboard,rag-local]", output)
        self.assertIn("direct background start skipped", output)
        self.assertIn("nova-RAG server LaunchAgent service registration", output)
        self.assertNotIn("install_rag_launch_agent", output)
        self.assertNotIn("deploy-embedding-server.sh", output)
        self.assertNotIn("nohup", output)
        self.assertNotIn("embedding-server-deploy.log", output)
        self.assertNotIn(f"-m pip install {deployed}[rag-local]", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_rag_local_defaults_to_embedding_server_deployment(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-rag",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("nova-RAG server LaunchAgent service registration", output)
        self.assertNotIn("install_rag_launch_agent", output)
        self.assertIn("direct background start skipped", output)
        self.assertNotIn("deploy-embedding-server.sh", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_rag_local_embedding_server_deployment_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-rag",
                    "--no-deploy-embedding-server",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("nova-RAG server LaunchAgent service registration", output)
        self.assertNotIn("install_rag_launch_agent", output)
        self.assertNotIn("Queueing background embedding server deployment", output)
        self.assertNotIn("deploy-embedding-server.sh", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_rag_cloud_mode_does_not_queue_local_embedding_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-rag",
                    "--rag-embedding-mode",
                    "cloud",
                    "--rag-cloud-provider",
                    "example-cloud",
                    "--rag-cloud-endpoint",
                    "https://embed.example.invalid/v1",
                    "--rag-cloud-model",
                    "embed-example",
                    "--rag-cloud-dimension",
                    "1024",
                    "--rag-cloud-api-key-env",
                    "NOVA_TEST_EMBED_KEY",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("nova-RAG: cloud embeddings; provider example-cloud", output)
        self.assertNotIn("nova-RAG embedding mode: cloud", output)
        self.assertNotIn("api key env=NOVA_TEST_EMBED_KEY", output)
        self.assertNotIn("Queueing background embedding server deployment", output)
        self.assertNotIn("install_rag_launch_agent", output)
        self.assertNotIn("nohup", output)
        self.assertFalse(runtime.exists())

    def test_dry_run_rag_local_model_can_select_384_dimension(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--dry-run",
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--enable-rag",
                    "--rag-local-model",
                    "intfloat/multilingual-e5-small",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr
        deployed = runtime.resolve() / "app" / "source"

        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"runtime source {deployed}", output)
        self.assertIn("nova-RAG: local embeddings; model intfloat/multilingual-e5-small; dimension 384", output)
        self.assertNotIn("nova-RAG embedding mode: local", output)
        self.assertFalse(runtime.exists())

    def test_installer_rag_cloud_schema_separates_mode_and_provider_id(self):
        content = INSTALLER.read_text(encoding="utf-8")
        self.assertIn('"mode": embedding_mode', content)
        self.assertIn('"provider": embedding_mode', content)
        self.assertIn('"providerId": "local" if embedding_mode == "local" else os.environ["NOVA_INSTALL_RAG_CLOUD_PROVIDER"]', content)
        self.assertIn('os.environ["NOVA_INSTALL_RAG_LOCAL_MODEL"]', content)
        self.assertIn('os.environ["NOVA_INSTALL_RAG_LOCAL_DIMENSION"]', content)

    def test_fake_python_helpers_execute_manifest_validator_without_cwd_symlink(self):
        for helper_name in ("base", "dependency-remediation"):
            with self.subTest(helper=helper_name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                fake_python = root / "bin" / "python3"
                log_path = root / "commands.log"
                fake_python.parent.mkdir()
                if helper_name == "base":
                    self._write_fake_python(fake_python, log_path)
                else:
                    self._write_fake_python_with_dependency_remediation(
                        fake_python,
                        log_path,
                        root / "dependency-installed.marker",
                    )

                release_id = "20260712T160253-75981-7548"
                releases = root / "runtime" / "app" / "releases"
                staging = releases / f".tmp-{release_id}"
                staging.mkdir(parents=True)
                manifest = staging / ".open-nova-runtime-source.json"
                manifest.write_text('{"schemaVersion": 2}\n', encoding="utf-8")
                validator_marker = staging / "validator-ran.txt"
                validator_script = (
                    "import sys\n"
                    "from pathlib import Path\n"
                    "manifest = Path(sys.argv[1])\n"
                    "release_id = sys.argv[2]\n"
                    "assert manifest.is_file()\n"
                    "(manifest.parent / 'validator-ran.txt').write_text(release_id, encoding='utf-8')\n"
                )
                validated = subprocess.run(
                    [str(fake_python), "-", str(manifest), release_id],
                    cwd=root,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    input=validator_script,
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(validated.returncode, 0, validated.stdout + validated.stderr)
                self.assertEqual(validator_marker.read_text(encoding="utf-8"), release_id)
                self.assertFalse(os.path.lexists(root / release_id))

                release_target = releases / release_id
                release_target.mkdir()
                source_pointer = root / "runtime" / "app" / "source"
                promoted = subprocess.run(
                    [str(fake_python), "-", str(release_target), str(source_pointer)],
                    cwd=root,
                    env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1"},
                    input="raise SystemExit('promotion should be simulated')\n",
                    text=True,
                    capture_output=True,
                    check=False,
                )

                self.assertEqual(promoted.returncode, 0, promoted.stdout + promoted.stderr)
                self.assertTrue(source_pointer.is_symlink())
                self.assertEqual(os.readlink(source_pointer), str(release_target))

    def test_fake_python_smoke_executes_real_installer_path_without_real_pip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_python = bin_dir / "python3"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_python(fake_python, log_path)
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")
            runtime_exists = runtime.resolve().exists()

        output = result.stdout + result.stderr
        deployed = runtime.resolve() / "app" / "source"

        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(runtime_exists)
        self.assertIn("-m venv", log)
        self.assertIn("-m pip install", log)
        self.assertIn(f"{deployed}[dashboard]", log)
        self.assertIn("onboarding runtime-apply", log)
        self.assertIn("onboarding runtime-status", log)
        self.assertIn("doctor --installer", log)
        self.assertIn("doctor --pipeline", log)
        self.assertIn("doctor --scheduler", log)
        self.assertNotIn("--scheduler-register-apply", log)
        self.assertNotIn("install_dashboard_launch_agent", log)

    def test_installer_stores_wizard_llm_api_key_via_stdin_without_echoing_secret(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_python = bin_dir / "python3"
            secret_value = "sk-test-value-that-should-not-be-echoed"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_python(fake_python, log_path)
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
                "NOVA_INSTALL_LLM_API_KEY_VALUE": secret_value,
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(fake_python),
                    "--llm-provider",
                    "openai-compatible",
                    "--llm-endpoint",
                    "https://llm.example.invalid/v1",
                    "--llm-model",
                    "example-model",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")
            installer_log = (runtime / "state" / "logs" / "installer-v2.log").read_text(encoding="utf-8")

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Storing LLM API key in secret store", output)
        self.assertIn("model key --value-stdin", log)
        self.assertIn("open-nova model key --value-stdin", installer_log)
        self.assertNotIn(secret_value, output)
        self.assertNotIn(secret_value, log)
        self.assertNotIn(secret_value, installer_log)

    def test_dependency_gate_installs_missing_mapped_packages_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            marker_path = root / "fastapi-installed.marker"
            fake_python = bin_dir / "python3"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_python_with_dependency_remediation(fake_python, log_path, marker_path)
            env = {
                **os.environ,
                "HOME": str(home),
                "NOVA_INSTALL_PLATFORM": "Darwin",
                "NOVA_LOCATION_FILE": str(home / ".config" / "open-nova" / "location.json"),
            }
            result = subprocess.run(
                [
                    "zsh",
                    str(INSTALLER),
                    "--runtime",
                    str(runtime),
                    "--source-root",
                    str(ROOT),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")
            marker_exists = marker_path.exists()

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertNotIn("Installing missing runtime dependencies detected by dependency gate: fastapi>=0.110,<1", output)
        self.assertNotIn("dependency gate ok: fake remediation passed", output)
        self.assertIn("-m pip install fastapi>=0.110,<1", log)
        self.assertTrue(marker_exists)

    def test_bootstrap_dry_run_uses_local_source_root_and_forwards_installer_options(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            runtime = home / ".open-nova"
            home.mkdir()
            env = self._fresh_bootstrap_env(home)
            result = subprocess.run(
                [
                    "zsh",
                    str(BOOTSTRAP),
                    "--dry-run",
                    "--source-root",
                    str(ROOT),
                    "--",
                    "--runtime",
                    str(runtime),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=ROOT,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("Running installer from source root", output)
        self.assertIn("install/install.sh --source-root", output)
        self.assertIn("--no-scheduler --no-dashboard-server", output)
        self.assertNotIn("dry-run only", output)
        self.assertNotIn("-m pip install", output)
        self.assertNotIn("Scheduler registration skipped by --no-scheduler", output)
        self.assertNotIn("SSE server disabled", output)
        self.assertFalse(runtime.exists())

    def test_bootstrap_dry_run_source_url_prints_clone_without_network(self):
        with tempfile.TemporaryDirectory() as tmp:
            home = Path(tmp) / "Home"
            cache = Path(tmp) / "Cache"
            home.mkdir()
            env = self._fresh_bootstrap_env(home)
            result = subprocess.run(
                [
                    "zsh",
                    str(BOOTSTRAP),
                    "--dry-run",
                    "--source-url",
                    "https://example.invalid/open-nova.git",
                    "--ref",
                    IMMUTABLE_TEST_COMMIT,
                    "--cache-root",
                    str(cache),
                    "--",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=Path(tmp),
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("git clone --filter=blob:none --sparse --no-checkout https://example.invalid/open-nova.git", output)
        self.assertIn("sparse-checkout init --no-cone", output)
        self.assertIn("sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src", output)
        self.assertIn("git -C", output)
        self.assertIn(f"checkout --detach {IMMUTABLE_TEST_COMMIT}", output)
        self.assertIn("install/install.sh --source-root", output)
        self.assertFalse(cache.exists())

    def test_bootstrap_fake_git_smoke_acquires_source_and_runs_installer_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            cache = root / "Cache"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_git = bin_dir / "git"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_git(fake_git, log_path)
            env = self._fresh_bootstrap_env(home)
            result = subprocess.run(
                [
                    "zsh",
                    str(BOOTSTRAP),
                    "--source-url",
                    "https://example.invalid/open-nova.git",
                    "--ref",
                    IMMUTABLE_TEST_COMMIT,
                    "--cache-root",
                    str(cache),
                    "--git",
                    str(fake_git),
                    "--",
                    "--dry-run",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("git clone --filter=blob:none --sparse --no-checkout https://example.invalid/open-nova.git", log)
        self.assertIn("sparse-checkout init --no-cone", log)
        self.assertIn("sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src", log)
        self.assertIn("git -C", log)
        self.assertIn(f"checkout --detach {IMMUTABLE_TEST_COMMIT}", log)
        self.assertIn(f"reset --hard {IMMUTABLE_TEST_COMMIT}", log)
        self.assertNotIn("dry-run only", output)

    def test_bootstrap_stdin_style_with_source_url_does_not_depend_on_script_path(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            cache = root / "Cache"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_git = bin_dir / "git"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_git(fake_git, log_path)
            script = BOOTSTRAP.read_text(encoding="utf-8")
            env = self._fresh_bootstrap_env(home)
            env.update(
                {
                    "NOVA_INSTALL_SOURCE_URL": "https://example.invalid/open-nova.git",
                    "NOVA_INSTALL_REF": IMMUTABLE_TEST_COMMIT,
                    "NOVA_INSTALL_CACHE_ROOT": str(cache),
                    "NOVA_INSTALL_GIT": str(fake_git),
                }
            )
            result = subprocess.run(
                [
                    "zsh",
                    "-c",
                    script,
                    "open-nova-bootstrap",
                    "--",
                    "--dry-run",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("git clone --filter=blob:none --sparse --no-checkout https://example.invalid/open-nova.git", log)
        self.assertIn("sparse-checkout init --no-cone", log)
        self.assertIn("sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src", log)
        self.assertIn(f"checkout --detach {IMMUTABLE_TEST_COMMIT}", log)
        self.assertIn(f"reset --hard {IMMUTABLE_TEST_COMMIT}", log)
        self.assertNotIn("dry-run only", output)

    def test_bootstrap_stdin_style_uses_hosted_default_source_url(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            cache = root / "Cache"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_git = bin_dir / "git"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_git(fake_git, log_path)
            script = BOOTSTRAP.read_text(encoding="utf-8")
            env = self._fresh_bootstrap_env(home)
            env.update(
                {
                    "NOVA_INSTALL_CACHE_ROOT": str(cache),
                    "NOVA_INSTALL_GIT": str(fake_git),
                    "NOVA_INSTALL_REF": IMMUTABLE_TEST_COMMIT,
                }
            )
            result = subprocess.run(
                [
                    "zsh",
                    "-c",
                    script,
                    "open-nova-bootstrap",
                    "--",
                    "--dry-run",
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertIn("git clone --filter=blob:none --sparse --no-checkout https://github.com/Neo-Isshin/open-nova.git", log)
        self.assertIn("sparse-checkout init --no-cone", log)
        self.assertIn("sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src", log)
        self.assertIn(f"checkout --detach {IMMUTABLE_TEST_COMMIT}", log)
        self.assertNotIn("dry-run only", output)

    def test_bootstrap_clean_home_fake_git_fake_python_non_dry_smoke(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            cache = root / "Cache"
            runtime = home / ".open-nova"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_git = bin_dir / "git"
            fake_python = bin_dir / "python3"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_git(fake_git, log_path)
            self._write_fake_python(fake_python, log_path)
            env = self._fresh_bootstrap_env(home)
            result = subprocess.run(
                [
                    "zsh",
                    str(BOOTSTRAP),
                    "--source-url",
                    "https://example.invalid/open-nova.git",
                    "--ref",
                    IMMUTABLE_TEST_COMMIT,
                    "--cache-root",
                    str(cache),
                    "--git",
                    str(fake_git),
                    "--",
                    "--runtime",
                    str(runtime),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            log = log_path.read_text(encoding="utf-8")
            runtime_exists = runtime.resolve().exists()
            profile_text = (home / ".zprofile").read_text(encoding="utf-8")

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertTrue(runtime_exists)
        self.assertIn("git clone --filter=blob:none --sparse --no-checkout https://example.invalid/open-nova.git", log)
        self.assertIn("sparse-checkout init --no-cone", log)
        self.assertIn("sparse-checkout set /pyproject.toml /MANIFEST.in /LICENSE /config.py /install /advanced /src", log)
        self.assertIn(f"checkout --detach {IMMUTABLE_TEST_COMMIT}", log)
        self.assertIn(f"reset --hard {IMMUTABLE_TEST_COMMIT}", log)
        self.assertIn("-m venv", log)
        self.assertIn("-m pip install", log)
        self.assertIn("onboarding runtime-apply", log)
        self.assertNotIn("--scheduler-register-apply", log)
        self.assertIn("# >>> open-nova installer PATH >>>", profile_text)
        self.assertIn('export PATH="$HOME/.local/bin:$PATH"', profile_text)

    def test_installer_shell_path_update_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            home = root / "Home"
            runtime = home / ".open-nova"
            cache = root / "Cache"
            bin_dir = root / "bin"
            log_path = root / "commands.log"
            fake_git = bin_dir / "git"
            fake_python = bin_dir / "python3"
            home.mkdir()
            bin_dir.mkdir()
            self._write_fake_git(fake_git, log_path)
            self._write_fake_python(fake_python, log_path)
            env = self._fresh_bootstrap_env(home)
            result = subprocess.run(
                [
                    "zsh",
                    str(BOOTSTRAP),
                    "--source-url",
                    "https://example.invalid/open-nova.git",
                    "--ref",
                    IMMUTABLE_TEST_COMMIT,
                    "--cache-root",
                    str(cache),
                    "--git",
                    str(fake_git),
                    "--",
                    "--runtime",
                    str(runtime),
                    "--python",
                    str(fake_python),
                    "--no-scheduler",
                    "--no-dashboard-server",
                    "--no-shell-path",
                ],
                cwd=root,
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            profile_exists = (home / ".zprofile").exists()

        output = result.stdout + result.stderr

        self.assertEqual(result.returncode, 0, output)
        self.assertFalse(profile_exists)
        self.assertNotIn("Shell PATH update skipped by --no-shell-path", output)


if __name__ == "__main__":
    unittest.main()
