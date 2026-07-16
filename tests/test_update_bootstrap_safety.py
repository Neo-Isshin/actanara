import json
import os
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BOOTSTRAP = ROOT / "install" / "bootstrap.sh"
DEFAULT_SOURCE_URL = "https://github.com/Neo-Isshin/open-nova.git"
DEFAULT_LATEST_RELEASE_API = "https://api.github.com/repos/Neo-Isshin/open-nova/releases/latest"
COMMIT = "b" * 40
OTHER_COMMIT = "c" * 40
TAG_OBJECT = "a" * 40


class UpdateBootstrapSafetyTests(unittest.TestCase):
    def setUp(self) -> None:
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.home = self.root / "Home"
        self.bin_dir = self.root / "bin"
        self.cache = self.root / "Cache"
        self.git_log = self.root / "git.log"
        self.install_log = self.root / "install.log"
        self.location = self.root / "location.json"
        self.fake_git = self.bin_dir / "git"
        self.fake_curl = self.bin_dir / "curl"
        self.fake_installer = self.root / "install.sh"
        self.home.mkdir()
        self.bin_dir.mkdir()
        self._write_fakes()

    def _write_fakes(self) -> None:
        self.fake_installer.write_text(
            """#!/usr/bin/env zsh
set -eu
print -r -- "$*" >> "$NOVA_TEST_INSTALL_LOG"
""",
            encoding="utf-8",
        )
        self.fake_installer.chmod(0o755)
        self.fake_curl.write_text(
            """#!/usr/bin/env zsh
set -eu
if [[ "${NOVA_TEST_CURL_FAIL:-0}" == "1" ]]; then
  exit 22
fi
print -r -- "${NOVA_TEST_RELEASE_JSON:-}"
""",
            encoding="utf-8",
        )
        self.fake_curl.chmod(0o755)
        self.fake_git.write_text(
            """#!/usr/bin/env zsh
set -eu
print -r -- "$*" >> "$NOVA_TEST_GIT_LOG"
if [[ "${1:-}" == "ls-remote" ]]; then
  if [[ "${NOVA_TEST_LS_REMOTE_FAIL:-0}" == "1" ]]; then
    exit 2
  fi
  tag="${NOVA_TEST_RELEASE_TAG:-v1.2.3}"
  print -r -- "${NOVA_TEST_TAG_OBJECT}\trefs/tags/${tag}"
  if [[ -n "${NOVA_TEST_PEELED_COMMIT:-}" ]]; then
    print -r -- "${NOVA_TEST_PEELED_COMMIT}\trefs/tags/${tag}^{}"
  fi
  exit 0
fi
if [[ "${1:-}" == "clone" ]]; then
  target="${@: -1}"
  mkdir -p "$target/.git" "$target/install"
  cp "$NOVA_TEST_INSTALLER" "$target/install/install.sh"
  chmod +x "$target/install/install.sh"
  exit 0
fi
if [[ "${1:-}" == "-C" && "${3:-}" == "remote" && "${4:-}" == "get-url" ]]; then
  print -r -- "${NOVA_TEST_SOURCE_URL}"
  exit 0
fi
if [[ "${1:-}" == "-C" && "${3:-}" == "rev-parse" ]]; then
  if [[ "${NOVA_TEST_REV_PARSE_FAIL:-0}" == "1" ]]; then
    exit 1
  fi
  print -r -- "${NOVA_TEST_REV_PARSE_COMMIT:-${NOVA_TEST_COMMIT}}"
  exit 0
fi
exit 0
""",
            encoding="utf-8",
        )
        self.fake_git.chmod(0o755)

    def _environment(self, **overrides: str) -> dict[str, str]:
        env = os.environ.copy()
        for name in (
            "NOVA_HOME",
            "NOVA_INSTALL_RUNTIME",
            "NOVA_INSTALL_SOURCE_ROOT",
            "NOVA_INSTALL_SOURCE_URL",
            "NOVA_INSTALL_REF",
            "NOVA_INSTALL_CACHE_ROOT",
            "NOVA_INSTALL_GIT",
            "NOVA_INSTALL_CURL",
            "NOVA_INSTALL_PLUTIL",
        ):
            env.pop(name, None)
        env.update(
            {
                "HOME": str(self.home),
                "NOVA_LOCATION_FILE": str(self.location),
                "NOVA_INSTALL_CURL": str(self.fake_curl),
                "NOVA_TEST_GIT_LOG": str(self.git_log),
                "NOVA_TEST_INSTALL_LOG": str(self.install_log),
                "NOVA_TEST_INSTALLER": str(self.fake_installer),
                "NOVA_TEST_SOURCE_URL": DEFAULT_SOURCE_URL,
                "NOVA_INSTALL_VERBOSE": "1",
                "NOVA_TEST_RELEASE_JSON": json.dumps(
                    {
                        "name": "Open Nova v1.2.3",
                        "tag_name": "v1.2.3",
                        "draft": False,
                        "prerelease": False,
                    }
                ),
                "NOVA_TEST_RELEASE_TAG": "v1.2.3",
                "NOVA_TEST_TAG_OBJECT": TAG_OBJECT,
                "NOVA_TEST_PEELED_COMMIT": COMMIT,
                "NOVA_TEST_COMMIT": COMMIT,
            }
        )
        env.update(overrides)
        return env

    def _run(self, *arguments: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["zsh", str(BOOTSTRAP), *arguments],
            cwd=self.root,
            env=env or self._environment(),
            text=True,
            capture_output=True,
            check=False,
        )

    def _remote_arguments(self, *, source_url: str = DEFAULT_SOURCE_URL, ref: str | None = None) -> list[str]:
        arguments = [
            "--source-url",
            source_url,
            "--cache-root",
            str(self.cache),
            "--git",
            str(self.fake_git),
        ]
        if ref is not None:
            arguments.extend(["--ref", ref])
        arguments.extend(["--", "--runtime", str(self.root / "runtime"), "--no-scheduler", "--no-dashboard-server"])
        return arguments

    def _output(self, result: subprocess.CompletedProcess[str]) -> str:
        return result.stdout + result.stderr

    def _write_marker(self, runtime: Path, relative: str = "config/settings.json") -> None:
        marker = runtime / relative
        marker.parent.mkdir(parents=True, exist_ok=True)
        marker.write_text("marker\n", encoding="utf-8")

    def _write_updateable_runtime(self, runtime: Path) -> None:
        config = runtime / "config"
        release = runtime / "app" / "releases" / "installed"
        config.mkdir(parents=True, exist_ok=True)
        release.mkdir(parents=True, exist_ok=True)
        (config / "settings.json").write_text(
            '{"features":{"rag":false},"rag":{"enabled":false}}\n',
            encoding="utf-8",
        )
        (config / "runtime.json").write_text(
            '{"runtime":"open-nova"}\n',
            encoding="utf-8",
        )
        (release / ".open-nova-runtime-source.json").write_text(
            json.dumps(
                {
                    "schemaVersion": 2,
                    "product": "open-nova",
                    "deploymentMode": "release-symlink",
                },
                sort_keys=True,
            )
            + "\n",
            encoding="utf-8",
        )
        (runtime / "app" / "source").symlink_to("releases/installed")

    def test_github_defaults_are_canonical_and_do_not_reference_legacy_host(self) -> None:
        script = BOOTSTRAP.read_text(encoding="utf-8")

        self.assertIn(f'DEFAULT_SOURCE_URL="{DEFAULT_SOURCE_URL}"', script)
        self.assertIn(f'DEFAULT_LATEST_RELEASE_API="{DEFAULT_LATEST_RELEASE_API}"', script)
        self.assertNotIn("git" + "ea", script.lower())

    def test_hosted_stream_is_one_compound_command_and_truncation_executes_nothing(self) -> None:
        script = BOOTSTRAP.read_text(encoding="utf-8")
        self.assertTrue(script.startswith("#!/usr/bin/env zsh\n"))
        self.assertIn("if true; then\nset -euo pipefail", script)
        self.assertTrue(script.endswith("\nfi\n"))

        truncated = script[:-3]
        result = subprocess.run(
            ["zsh", "-c", truncated, "open-nova-truncated-bootstrap"],
            cwd=self.root,
            env=self._environment(),
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0, self._output(result))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.git_log.exists())
        self.assertFalse(self.install_log.exists())

    def test_default_remote_resolves_annotated_stable_tag_to_peeled_commit(self) -> None:
        result = self._run(*self._remote_arguments())
        output = self._output(result)
        git_log = self.git_log.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, output)
        self.assertIn(f"refs/tags/v1.2.3^{{}}", git_log)
        self.assertIn("clone --filter=blob:none --sparse --no-checkout", git_log)
        self.assertIn(f"checkout --detach {COMMIT}", git_log)
        self.assertIn(f"reset --hard {COMMIT}", git_log)
        self.assertNotIn("origin/HEAD", git_log)
        self.assertTrue(self.install_log.is_file())
        installer_args = self.install_log.read_text(encoding="utf-8").split()
        self.assertNotIn("--upgrade", installer_args)
        self.assertNotIn("--yes", installer_args)

    def test_default_remote_resolves_lightweight_stable_tag_to_direct_commit(self) -> None:
        result = self._run(
            *self._remote_arguments(),
            env=self._environment(
                NOVA_TEST_TAG_OBJECT=COMMIT,
                NOVA_TEST_PEELED_COMMIT="",
                NOVA_TEST_REV_PARSE_COMMIT=COMMIT,
            ),
        )
        git_log = self.git_log.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, self._output(result))
        self.assertIn(f"checkout --detach {COMMIT}", git_log)
        self.assertIn(f"reset --hard {COMMIT}", git_log)
        self.assertNotIn("origin/HEAD", git_log)

    def test_hosted_stdin_bootstrap_never_adopts_the_current_checkout(self) -> None:
        script = BOOTSTRAP.read_text(encoding="utf-8")
        env = self._environment(
            NOVA_INSTALL_REF=COMMIT,
            NOVA_INSTALL_CACHE_ROOT=str(self.cache),
            NOVA_INSTALL_GIT=str(self.fake_git),
        )

        result = subprocess.run(
            [
                "zsh",
                "-c",
                script,
                "open-nova-hosted-bootstrap",
                "--",
                "--runtime",
                str(self.root / "runtime"),
                "--no-scheduler",
                "--no-dashboard-server",
            ],
            cwd=ROOT,
            env=env,
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, self._output(result))
        git_log = self.git_log.read_text(encoding="utf-8")
        self.assertIn("clone --filter=blob:none --sparse --no-checkout", git_log)
        self.assertIn(DEFAULT_SOURCE_URL, git_log)
        self.assertNotIn(str(ROOT / "install" / "install.sh"), self.install_log.read_text(encoding="utf-8"))

    def test_no_release_rate_limit_nonstable_malformed_or_wrong_tag_fail_before_cache_write(self) -> None:
        cases = (
            ({"NOVA_TEST_CURL_FAIL": "1"}, "latest stable Open Nova release could not be read"),
            (
                {
                    "NOVA_TEST_RELEASE_JSON": json.dumps(
                        {"message": "API rate limit exceeded", "documentation_url": "https://docs.github.com/"}
                    )
                },
                "latest stable Open Nova release response is invalid",
            ),
            (
                {
                    "NOVA_TEST_RELEASE_JSON": json.dumps(
                        {"tag_name": "v1.2.3", "draft": True, "prerelease": False}
                    )
                },
                "latest Open Nova release is not stable",
            ),
            (
                {
                    "NOVA_TEST_RELEASE_JSON": json.dumps(
                        {"tag_name": "v1.2.3", "draft": False, "prerelease": True}
                    )
                },
                "latest Open Nova release is not stable",
            ),
            (
                {
                    "NOVA_TEST_RELEASE_JSON": json.dumps(
                        {
                            "name": "Open Nova v1.2.3 — WITHDRAWN",
                            "tag_name": "v1.2.3",
                            "draft": False,
                            "prerelease": False,
                        }
                    )
                },
                "latest Open Nova release was withdrawn",
            ),
            (
                {
                    "NOVA_TEST_RELEASE_JSON": json.dumps(
                        {"tag_name": "../main", "draft": False, "prerelease": False}
                    )
                },
                "invalid version tag",
            ),
            (
                {"NOVA_TEST_RELEASE_TAG": "v9.9.9"},
                "did not resolve to an exact version",
            ),
        )
        for index, (overrides, expected) in enumerate(cases):
            with self.subTest(index=index):
                cache = self.root / f"Cache-{index}"
                arguments = self._remote_arguments()
                arguments[arguments.index(str(self.cache))] = str(cache)
                result = self._run(*arguments, env=self._environment(**overrides))
                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn(expected, self._output(result))
                self.assertFalse(cache.exists())

    def test_withdrawn_release_name_fails_without_plutil_for_escaped_and_lowercase_titles(self) -> None:
        payloads = (
            (
                '{"name":"Open Nova v1.2.3 \\u2014 '
                '\\u0057\\u0049\\u0054\\u0048\\u0044\\u0052\\u0041\\u0057\\u004e",'
                '"tag_name":"v1.2.3","draft":false,"prerelease":false}'
            ),
            json.dumps(
                {
                    "name": "open nova v1.2.3 — withdrawn",
                    "tag_name": "v1.2.3",
                    "draft": False,
                    "prerelease": False,
                }
            ),
        )
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                cache = self.root / f"Withdrawn-Cache-{index}"
                arguments = self._remote_arguments()
                arguments[arguments.index(str(self.cache))] = str(cache)
                result = self._run(
                    *arguments,
                    env=self._environment(
                        NOVA_INSTALL_PLUTIL="/no/such/plutil",
                        NOVA_TEST_RELEASE_JSON=payload,
                    ),
                )
                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn("latest Open Nova release was withdrawn", self._output(result))
                self.assertFalse(cache.exists())

    def test_release_name_missing_or_null_is_accepted_for_compatibility_without_plutil(self) -> None:
        payloads = (
            {"tag_name": "v1.2.3", "draft": False, "prerelease": False},
            {"name": None, "tag_name": "v1.2.3", "draft": False, "prerelease": False},
        )
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                cache = self.root / f"Compatible-Cache-{index}"
                arguments = self._remote_arguments()
                arguments[arguments.index(str(self.cache))] = str(cache)
                result = self._run(
                    *arguments,
                    env=self._environment(
                        NOVA_INSTALL_PLUTIL="/no/such/plutil",
                        NOVA_TEST_RELEASE_JSON=json.dumps(payload),
                    ),
                )
                self.assertEqual(result.returncode, 0, self._output(result))

    def test_duplicate_or_malformed_release_name_fails_closed_without_plutil(self) -> None:
        payloads = (
            '{"name":"safe","name":"WITHDRAWN","tag_name":"v1.2.3",'
            '"draft":false,"prerelease":false}',
            '{"name":"safe","\\u006eame":"WITHDRAWN","tag_name":"v1.2.3",'
            '"draft":false,"prerelease":false}',
            '{"name":"bad\\qescape","tag_name":"v1.2.3",'
            '"draft":false,"prerelease":false}',
            '{"name":{"title":"WITHDRAWN"},"tag_name":"v1.2.3",'
            '"draft":false,"prerelease":false}',
            '{"name":"safe","tag_name":"v1.2.3",'
            '"draft":false,"prerelease":false} trailing',
        )
        for index, payload in enumerate(payloads):
            with self.subTest(index=index):
                cache = self.root / f"Invalid-Name-Cache-{index}"
                arguments = self._remote_arguments()
                arguments[arguments.index(str(self.cache))] = str(cache)
                result = self._run(
                    *arguments,
                    env=self._environment(
                        NOVA_INSTALL_PLUTIL="/no/such/plutil",
                        NOVA_TEST_RELEASE_JSON=payload,
                    ),
                )
                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn("latest stable Open Nova release response is invalid", self._output(result))
                self.assertFalse(cache.exists())

    def test_custom_remote_without_commit_and_symbolic_remote_ref_fail_closed(self) -> None:
        cases = (
            ("https://example.invalid/open-nova.git", None, "custom source URL"),
            (DEFAULT_SOURCE_URL, "main", "full 40- or 64-character commit ID"),
            (DEFAULT_SOURCE_URL, "v1.2.3", "full 40- or 64-character commit ID"),
        )
        for index, (source_url, ref, expected) in enumerate(cases):
            with self.subTest(index=index):
                cache = self.root / f"Cache-{index}"
                arguments = self._remote_arguments(source_url=source_url, ref=ref)
                arguments[arguments.index(str(self.cache))] = str(cache)
                result = self._run(*arguments)
                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn(expected, self._output(result))
                self.assertFalse(cache.exists())

    def test_custom_remote_with_full_commit_is_detached_and_never_uses_head(self) -> None:
        source_url = "https://example.invalid/open-nova.git"
        env = self._environment(NOVA_TEST_SOURCE_URL=source_url)
        result = self._run(*self._remote_arguments(source_url=source_url, ref=COMMIT), env=env)
        git_log = self.git_log.read_text(encoding="utf-8")

        self.assertEqual(result.returncode, 0, self._output(result))
        self.assertIn(f"checkout --detach {COMMIT}", git_log)
        self.assertNotIn("origin/HEAD", git_log)

    def test_official_https_cache_urls_with_or_without_dot_git_are_equivalent(self) -> None:
        source = self.cache / "source"
        (source / ".git").mkdir(parents=True)
        (source / "install").mkdir()
        shutil_installer = source / "install" / "install.sh"
        shutil_installer.write_bytes(self.fake_installer.read_bytes())
        shutil_installer.chmod(0o755)

        result = self._run(
            *self._remote_arguments(ref=COMMIT),
            env=self._environment(
                NOVA_TEST_SOURCE_URL="https://github.com/Neo-Isshin/open-nova"
            ),
        )

        self.assertEqual(result.returncode, 0, self._output(result))
        self.assertTrue(self.install_log.is_file())

    def test_truly_different_cache_source_still_fails_without_installer_writes(self) -> None:
        source = self.cache / "source"
        (source / ".git").mkdir(parents=True)
        sentinel = source / "operator-owned.txt"
        sentinel.write_text("preserve\n", encoding="utf-8")

        result = self._run(
            *self._remote_arguments(ref=COMMIT),
            env=self._environment(
                NOVA_TEST_SOURCE_URL="https://github.com/other/open-nova.git",
                NOVA_INSTALL_VERBOSE="1",
            ),
        )

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertIn("download cache source does not match", self._output(result))
        self.assertEqual(sentinel.read_text(encoding="utf-8"), "preserve\n")
        self.assertFalse(self.install_log.exists())

    def test_full_sha256_commit_is_accepted(self) -> None:
        commit = "d" * 64
        source_url = "https://example.invalid/open-nova.git"
        result = self._run(
            *self._remote_arguments(source_url=source_url, ref=commit),
            env=self._environment(
                NOVA_TEST_SOURCE_URL=source_url,
                NOVA_TEST_COMMIT=commit,
                NOVA_TEST_REV_PARSE_COMMIT=commit,
            ),
        )

        self.assertEqual(result.returncode, 0, self._output(result))
        self.assertIn(f"checkout --detach {commit}", self.git_log.read_text(encoding="utf-8"))

    def test_source_root_and_ref_are_rejected_without_touching_user_checkout(self) -> None:
        source = self.root / "user-source"
        installer = source / "install" / "install.sh"
        installer.parent.mkdir(parents=True)
        installer.write_text("user-owned\n", encoding="utf-8")
        before = installer.read_bytes()

        result = self._run(
            "--source-root",
            str(source),
            "--ref",
            COMMIT,
            "--git",
            str(self.fake_git),
            "--",
            "--runtime",
            str(self.root / "runtime"),
        )

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertIn("cannot be combined", self._output(result))
        self.assertEqual(installer.read_bytes(), before)
        self.assertFalse(self.git_log.exists())
        self.assertFalse(self.cache.exists())

    def test_oneliner_auto_updates_target_nova_home_default_and_pointer_runtimes(self) -> None:
        for index, name in enumerate(("target", "nova-home", "default", "pointer")):
            with self.subTest(name=name):
                case_home = self.root / f"Home-{index}"
                case_home.mkdir()
                case_location = self.root / f"location-{index}.json"
                case_runtime = self.root / f"existing-{name}-{index}"
                env_values = {
                    "HOME": str(case_home),
                    "NOVA_LOCATION_FILE": str(case_location),
                    "NOVA_INSTALL_PLUTIL": "/no/such/plutil",
                }
                if name == "nova-home":
                    env_values["NOVA_HOME"] = str(case_runtime)
                elif name == "default":
                    case_runtime = case_home / ".open-nova"
                elif name == "pointer":
                    case_location.write_text(
                        json.dumps({"novaHome": str(case_runtime)}),
                        encoding="utf-8",
                    )
                self._write_updateable_runtime(case_runtime)
                cache = self.root / f"runtime-guard-cache-{index}"
                arguments = self._remote_arguments(ref=COMMIT)
                arguments[arguments.index(str(self.cache))] = str(cache)
                runtime_index = arguments.index(str(self.root / "runtime"))
                if name == "target":
                    arguments[runtime_index] = str(case_runtime)
                else:
                    del arguments[runtime_index - 1 : runtime_index + 1]
                self.install_log.unlink(missing_ok=True)
                result = self._run(*arguments, env=self._environment(**env_values))
                self.assertEqual(result.returncode, 0, self._output(result))
                installer_args = self.install_log.read_text(encoding="utf-8").split()
                self.assertEqual(installer_args.count("--upgrade"), 1)
                self.assertEqual(installer_args.count("--yes"), 1)
                self.assertNotIn("--force-rebuild", installer_args)
                runtime_arg = installer_args.index("--runtime")
                self.assertEqual(Path(installer_args[runtime_arg + 1]), case_runtime)
                self.assertTrue(cache.exists())

    def test_partial_runtime_marker_still_fails_before_clone(self) -> None:
        runtime = self.root / "runtime"
        self._write_marker(runtime)
        result = self._run(
            *self._remote_arguments(ref=COMMIT),
            env=self._environment(NOVA_INSTALL_VERBOSE="1"),
        )

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertIn("existing Open Nova state is incomplete", self._output(result))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.install_log.exists())

    def test_foreign_runtime_manifest_still_fails_before_clone(self) -> None:
        runtime = self.root / "runtime"
        self._write_updateable_runtime(runtime)
        manifest = runtime / "app" / "source" / ".open-nova-runtime-source.json"
        manifest.write_text(
            '{"product":"other","deploymentMode":"release-symlink"}\n',
            encoding="utf-8",
        )

        result = self._run(*self._remote_arguments(ref=COMMIT))

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.install_log.exists())

    def test_symlinked_location_pointer_fails_before_clone(self) -> None:
        actual = self.root / "actual-location.json"
        actual.write_text(
            json.dumps({"novaHome": str(self.root / "runtime")}),
            encoding="utf-8",
        )
        location = self.root / "linked-location.json"
        location.symlink_to(actual)
        arguments = self._remote_arguments(ref=COMMIT)
        runtime_index = arguments.index(str(self.root / "runtime"))
        del arguments[runtime_index - 1 : runtime_index + 1]

        result = self._run(
            *arguments,
            env=self._environment(NOVA_LOCATION_FILE=str(location)),
        )

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.install_log.exists())

    def test_malformed_or_unsafe_location_pointer_fails_before_clone(self) -> None:
        cases = ("not-json", json.dumps({"novaHome": "relative/runtime"}))
        for index, payload in enumerate(cases):
            with self.subTest(index=index):
                location = self.root / f"unsafe-location-{index}.json"
                location.write_text(payload, encoding="utf-8")
                cache = self.root / f"unsafe-pointer-cache-{index}"
                arguments = self._remote_arguments(ref=COMMIT)
                arguments[arguments.index(str(self.cache))] = str(cache)
                runtime_index = arguments.index(str(self.root / "runtime"))
                del arguments[runtime_index - 1 : runtime_index + 1]
                result = self._run(
                    *arguments,
                    env=self._environment(
                        NOVA_LOCATION_FILE=str(location),
                        NOVA_INSTALL_PLUTIL="/no/such/plutil",
                    ),
                )
                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn("Open Nova", self._output(result))
                self.assertFalse(cache.exists())

    def test_existing_open_nova_launch_agent_fails_before_clone(self) -> None:
        launch_agent = self.home / "Library" / "LaunchAgents" / "com.open-nova.dashboard.plist"
        launch_agent.parent.mkdir(parents=True)
        launch_agent.write_text("plist\n", encoding="utf-8")

        result = self._run(*self._remote_arguments(ref=COMMIT))

        self.assertEqual(result.returncode, 2, self._output(result))
        self.assertIn("Open Nova", self._output(result))
        self.assertFalse(self.cache.exists())
        self.assertFalse(self.git_log.exists())

    def test_upgrade_bypasses_fresh_install_collision_guard(self) -> None:
        runtime = self.root / "runtime"
        self._write_marker(runtime)
        launch_agent = self.home / "Library" / "LaunchAgents" / "com.open-nova.dashboard.plist"
        launch_agent.parent.mkdir(parents=True)
        launch_agent.write_text("plist\n", encoding="utf-8")
        arguments = self._remote_arguments(ref=COMMIT)
        arguments.append("--upgrade")

        result = self._run(*arguments)

        self.assertEqual(result.returncode, 0, self._output(result))
        self.assertTrue(self.install_log.is_file())
        self.assertEqual(
            self.install_log.read_text(encoding="utf-8").split().count("--upgrade"),
            1,
        )

    def test_apply_rejects_non_commit_or_nonmatching_object_without_head_fallback(self) -> None:
        cases = (
            {"NOVA_TEST_REV_PARSE_FAIL": "1"},
            {"NOVA_TEST_REV_PARSE_COMMIT": OTHER_COMMIT},
        )
        for index, overrides in enumerate(cases):
            with self.subTest(index=index):
                self.git_log.unlink(missing_ok=True)
                self.install_log.unlink(missing_ok=True)
                result = self._run(
                    *self._remote_arguments(ref=COMMIT),
                    env=self._environment(**overrides),
                )
                git_log = self.git_log.read_text(encoding="utf-8")

                self.assertEqual(result.returncode, 2, self._output(result))
                self.assertIn("does not match required commit", self._output(result))
                self.assertNotIn("origin/HEAD", git_log)
                self.assertNotIn("checkout --detach", git_log)
                self.assertNotIn("reset --hard", git_log)
                self.assertFalse(self.install_log.exists())


if __name__ == "__main__":
    unittest.main()
