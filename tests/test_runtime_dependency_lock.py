import argparse
import io
import json
import re
import stat
import sys
import tempfile
import tomllib
import unittest
from contextlib import redirect_stderr
from pathlib import Path
from unittest.mock import patch
from urllib.parse import unquote, urlsplit

from install import dependency_contract
from tools.release import generate_runtime_lock as lock_generator


ROOT = Path(__file__).resolve().parents[1]
LOCK_PATH = ROOT / "install" / "runtime-dependencies.lock.json"
PYPROJECT_PATH = ROOT / "pyproject.toml"
SHA256_RE = re.compile(r"[0-9a-f]{64}\Z")


def _canonical_requirement(requirement: str) -> str:
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", requirement.strip())
    if match is None:
        raise AssertionError(f"invalid test requirement: {requirement}")
    name = re.sub(r"[-_.]+", "-", match.group(1)).lower()
    specifier = re.sub(r"\s+", "", match.group(2))
    return name + ",".join(sorted(specifier.split(","))) if specifier else name


def _direct_name(requirement: str) -> str:
    return re.split(r"[<>=!~]", requirement, maxsplit=1)[0]


def _wheel_record(
    name: str,
    version: str = "1.0",
    *,
    seed: str = "a",
    requires_dist: list[str] | None = None,
) -> dict:
    filename = f"{name.replace('-', '_')}-{version}-py3-none-any.whl"
    return {
        "download_info": {
            "url": f"https://files.pythonhosted.org/packages/aa/bb/{filename}",
            "archive_info": {"hashes": {"sha256": seed * 64}},
        },
        "metadata": {
            "name": name,
            "version": version,
            **({"requires_dist": requires_dist} if requires_dist is not None else {}),
        },
    }


def _write_report(path: Path, records: list[dict]) -> None:
    path.write_text(
        json.dumps({"version": "1", "pip_version": "26.1.2", "install": records}),
        encoding="utf-8",
    )


def _write_fixture_pyproject(path: Path, profiles: dict[str, list[str]]) -> None:
    lines = ["[project]", 'name = "fixture"', 'version = "1.0"', "", "[project.optional-dependencies]"]
    for profile, requirements in profiles.items():
        encoded = ", ".join(json.dumps(requirement) for requirement in requirements)
        lines.append(f"{profile} = [{encoded}]")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _generator_argv(
    pyproject: Path,
    profile_reports: list[tuple[str, Path]],
    environments: list[str],
    output: Path,
) -> list[str]:
    argv = ["generate_runtime_lock.py", "--pyproject", str(pyproject)]
    for profile, report in profile_reports:
        argv.extend(["--profile-report", f"{profile}={report}"])
    for environment in environments:
        argv.extend(["--environment", environment])
    argv.extend(["--output", str(output)])
    return argv


class RuntimeDependencyLockTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        raw = LOCK_PATH.read_text(encoding="utf-8")
        cls.raw_lock = raw
        cls.lock = json.loads(raw, object_pairs_hook=lock_generator._unique_object)

    def test_checked_in_lock_is_canonical_deterministic_json(self):
        self.assertEqual(
            self.raw_lock,
            json.dumps(self.lock, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
        self.assertEqual(self.lock["schemaVersion"], 1)
        self.assertEqual(self.lock["product"], "actanara")
        self.assertEqual(
            self.lock["artifactPolicy"],
            {
                "hashAlgorithm": "sha256",
                "hashesRequired": True,
                "sourceBuildsAllowed": False,
                "wheelsOnly": True,
            },
        )
        self.assertEqual(self.lock["resolver"]["name"], "pip")
        self.assertEqual(self.lock["resolver"]["reportSchemaVersion"], "1")
        self.assertRegex(self.lock["resolver"]["version"], r"^[0-9]+(?:\.[0-9]+)+$")

    def test_lock_profiles_exactly_match_pyproject_optional_dependencies(self):
        pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
        project = pyproject["project"]
        declared = project["optional-dependencies"]
        self.assertEqual(project["dependencies"], [])
        self.assertEqual(set(self.lock["profiles"]), set(declared))
        for profile, requirements in declared.items():
            with self.subTest(profile=profile):
                locked = self.lock["profiles"][profile]
                expected = sorted(_canonical_requirement(requirement) for requirement in requirements)
                self.assertEqual(locked["directRequirements"], expected)
                self.assertEqual(locked["packages"], sorted(set(locked["packages"])))
                self.assertTrue({_direct_name(item) for item in expected}.issubset(locked["packages"]))

    def test_every_locked_artifact_has_exact_wheel_url_and_sha256_evidence(self):
        expected_fields = {"name", "version", "filename", "sha256", "url"}
        for environment_id, environment in self.lock["environments"].items():
            packages = environment["packages"]
            names = [record["name"] for record in packages]
            self.assertEqual(names, sorted(set(names)), environment_id)
            for record in packages:
                with self.subTest(environment=environment_id, package=record.get("name")):
                    self.assertEqual(set(record), expected_fields)
                    self.assertRegex(record["name"], r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
                    self.assertTrue(record["version"])
                    self.assertRegex(record["sha256"], SHA256_RE)
                    parsed = urlsplit(record["url"])
                    self.assertEqual(parsed.scheme, "https")
                    self.assertIn(
                        parsed.hostname,
                        {
                            "files.pythonhosted.org",
                            "download.pytorch.org",
                            "download-r2.pytorch.org",
                        },
                    )
                    if parsed.hostname != "files.pythonhosted.org":
                        self.assertEqual(record["name"], "torch")
                        self.assertTrue(parsed.path.startswith("/whl/cpu/"))
                    self.assertFalse(parsed.query)
                    self.assertFalse(parsed.fragment)
                    self.assertEqual(unquote(Path(parsed.path).name), record["filename"])
                    self.assertTrue(record["filename"].endswith(".whl"))
                    self.assertNotIn("/", record["filename"])
                    self.assertNotIn("\\", record["filename"])

    def test_every_environment_profile_is_the_exact_audited_closure(self):
        expected_environments = {
            f"macos-cpython{python.replace('.', '')}-{suffix}"
            for python in ("3.11", "3.12", "3.13", "3.14")
            for suffix in ("arm64", "x86-64")
        }
        expected_environments.update(
            {"linux-cpython313-arm64", "linux-cpython313-x86-64"}
        )
        self.assertEqual(set(self.lock["environments"]), expected_environments)
        for environment_id, environment in self.lock["environments"].items():
            with self.subTest(environment=environment_id):
                python_mm = environment["pythonMajorMinor"]
                self.assertEqual(environment["implementation"], "cpython")
                self.assertIn(environment["architecture"], {"arm64", "x86_64"})
                if environment["platformFamily"] == "macos":
                    self.assertEqual(
                        environment["abi"],
                        f"cpython-{python_mm.replace('.', '')}-darwin",
                    )
                    self.assertRegex(environment["minimumMacOS"], r"^[0-9]+\.[0-9]+$")
                else:
                    self.assertEqual(environment["platformFamily"], "linux")
                    abi_machine = (
                        "aarch64" if environment["architecture"] == "arm64" else "x86_64"
                    )
                    self.assertEqual(
                        environment["abi"],
                        f"cpython-{python_mm.replace('.', '')}-{abi_machine}-linux-gnu",
                    )
                    self.assertIsNone(environment["minimumMacOS"])
                supported = environment["supportedProfiles"]
                closures = environment["profilePackages"]
                self.assertEqual(supported, sorted(set(supported)))
                self.assertEqual(set(closures), set(supported))
                environment_packages = {record["name"] for record in environment["packages"]}
                assigned: set[str] = set()
                for profile in supported:
                    actual = closures[profile]
                    expected = sorted(
                        set(self.lock["profiles"][profile]["packages"]) & environment_packages
                    )
                    self.assertEqual(actual, expected, f"{environment_id}/{profile}")
                    direct = {
                        _direct_name(requirement)
                        for requirement in self.lock["profiles"][profile]["directRequirements"]
                    }
                    self.assertTrue(direct.issubset(actual), f"{environment_id}/{profile}")
                    assigned.update(actual)
                self.assertEqual(assigned, environment_packages)

    def test_audited_linux_environments_include_rag_local(self):
        expected_supported = ["dashboard", "dev-test", "rag-server"]
        excluded = {
            "macos-cpython313-x86-64",
            "macos-cpython314-x86-64",
        }
        for environment_id in sorted(excluded):
            environment = self.lock["environments"][environment_id]
            with self.subTest(environment=environment_id):
                self.assertEqual(environment["supportedProfiles"], expected_supported)
                self.assertNotIn("rag-local", environment["profilePackages"])
        for environment_id, environment in self.lock["environments"].items():
            if environment_id not in excluded:
                self.assertIn("rag-local", environment["supportedProfiles"], environment_id)

    def test_checked_in_lock_selects_linux_x64_and_arm64_by_runtime_probe(self):
        probes = (
            ("x86_64", "cpython-313-x86_64-linux-gnu", "linux-cpython313-x86-64"),
            ("arm64", "cpython-313-aarch64-linux-gnu", "linux-cpython313-arm64"),
        )
        for architecture, abi, expected_environment in probes:
            probe = {
                "implementation": "cpython",
                "pythonMajorMinor": "3.13",
                "abi": abi,
                "platformFamily": "linux",
                "architecture": architecture,
                "macOSVersion": None,
            }
            probe["environmentId"] = dependency_contract._environment_id(probe)
            with self.subTest(architecture=architecture):
                selection = dependency_contract.load_contract_selection(
                    LOCK_PATH,
                    PYPROJECT_PATH,
                    ["dashboard", "rag-local", "rag-server"],
                    environment_probe=probe,
                )
                self.assertEqual(selection.environment_id, expected_environment)
                self.assertEqual(selection.lock_environment["platformFamily"], "linux")
                self.assertIn("rag-local", selection.profiles)

    def test_generator_output_is_byte_deterministic_across_input_order(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyproject = root / "pyproject.toml"
            _write_fixture_pyproject(pyproject, {"alpha": ["Alpha>=1,<2"], "beta": ["Bravo==2"]})
            alpha = root / "alpha.json"
            beta = root / "beta.json"
            arm = root / "arm.json"
            x86 = root / "x86.json"
            alpha_records = [
                _wheel_record("alpha", seed="a", requires_dist=["shared>=1"]),
                _wheel_record("shared", seed="b"),
            ]
            beta_records = [_wheel_record("bravo", "2", seed="c"), _wheel_record("shared", seed="b")]
            environment_records = alpha_records + [beta_records[0]]
            _write_report(alpha, alpha_records)
            _write_report(beta, beta_records)
            _write_report(arm, environment_records)
            _write_report(x86, environment_records)
            environments = [
                f"fixture-arm64|{arm}|3.11|cpython-311-darwin|macos|arm64|14.0|alpha,beta",
                f"fixture-x86-64|{x86}|3.11|cpython-311-darwin|macos|x86_64|14.0|alpha,beta",
            ]
            first = root / "first.json"
            second = root / "second.json"
            with patch.object(
                sys,
                "argv",
                _generator_argv(pyproject, [("alpha", alpha), ("beta", beta)], environments, first),
            ), redirect_stderr(io.StringIO()) as error:
                first_code = lock_generator.main()
            self.assertEqual(first_code, 0, error.getvalue())

            _write_report(alpha, list(reversed(alpha_records)))
            _write_report(beta, list(reversed(beta_records)))
            _write_report(arm, list(reversed(environment_records)))
            _write_report(x86, list(reversed(environment_records)))
            with patch.object(
                sys,
                "argv",
                _generator_argv(
                    pyproject,
                    [("beta", beta), ("alpha", alpha)],
                    list(reversed(environments)),
                    second,
                ),
            ), redirect_stderr(io.StringIO()) as error:
                second_code = lock_generator.main()
            self.assertEqual(second_code, 0, error.getvalue())

            self.assertEqual(first.read_bytes(), second.read_bytes())
            self.assertEqual(stat.S_IMODE(first.stat().st_mode), 0o644)
            generated = json.loads(first.read_text(encoding="utf-8"))
            self.assertEqual(list(generated["profiles"]), ["alpha", "beta"])
            self.assertEqual(
                list(generated["environments"]),
                ["fixture-arm64", "fixture-x86-64"],
            )

    def test_generator_can_preserve_compatible_audited_base_environments(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyproject = root / "pyproject.toml"
            _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
            profile = root / "profile.json"
            mac_report = root / "mac.json"
            linux_report = root / "linux.json"
            output = root / "base.json"
            records = [_wheel_record("alpha")]
            _write_report(profile, records)
            _write_report(mac_report, records)
            _write_report(linux_report, records)
            base_args = argparse.Namespace(
                pyproject=str(pyproject),
                profile_report=[f"core={profile}"],
                environment=[
                    f"fixture-mac|{mac_report}|3.11|cpython-311-darwin|macos|arm64|14.0|core"
                ],
                base_lock=None,
                output=str(output),
            )
            base = lock_generator.build_lock(base_args)
            output.write_text(
                json.dumps(base, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            linux_args = argparse.Namespace(
                pyproject=str(pyproject),
                profile_report=[f"core={profile}"],
                environment=[
                    f"fixture-linux|{linux_report}|3.11|"
                    "cpython-311-x86_64-linux-gnu|linux|x86_64|-|core"
                ],
                base_lock=str(output),
                output=str(output),
            )

            merged = lock_generator.build_lock(linux_args)

        self.assertEqual(
            list(merged["environments"]),
            ["fixture-linux", "fixture-mac"],
        )
        self.assertEqual(merged["environments"]["fixture-mac"], base["environments"]["fixture-mac"])

    def test_generator_rejects_conflicting_base_environment_evidence(self):
        generated = {
            "schemaVersion": 1,
            "product": "actanara",
            "artifactPolicy": {"wheelsOnly": True},
            "resolver": {"name": "pip"},
            "profiles": {"core": {"directRequirements": ["alpha>=1"], "packages": ["alpha"]}},
            "environments": {"fixture": {"architecture": "arm64"}},
        }
        base = {
            **generated,
            "environments": {"fixture": {"architecture": "x86_64"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "base.json"
            path.write_text(json.dumps(base), encoding="utf-8")

            with self.assertRaisesRegex(
                lock_generator.LockGenerationError,
                "conflicting environment evidence",
            ):
                lock_generator.merge_base_lock(generated, path)

    def test_generator_replaces_base_environment_only_with_explicit_authorization(self):
        generated = {
            "schemaVersion": 1,
            "product": "actanara",
            "artifactPolicy": {"wheelsOnly": True},
            "resolver": {"name": "pip"},
            "profiles": {"core": {"directRequirements": ["alpha>=1"], "packages": ["alpha"]}},
            "environments": {"fixture": {"architecture": "arm64"}},
        }
        base = {
            **generated,
            "environments": {"fixture": {"architecture": "x86_64"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "base.json"
            path.write_text(json.dumps(base), encoding="utf-8")

            merged = lock_generator.merge_base_lock(
                generated,
                path,
                replace_environments=frozenset({"fixture"}),
            )

        self.assertEqual(merged["environments"]["fixture"], {"architecture": "arm64"})

    def test_generator_rejects_unknown_replacement_authorization(self):
        generated = {
            "schemaVersion": 1,
            "product": "actanara",
            "artifactPolicy": {"wheelsOnly": True},
            "resolver": {"name": "pip"},
            "profiles": {"core": {"directRequirements": ["alpha>=1"], "packages": ["alpha"]}},
            "environments": {"fixture": {"architecture": "arm64"}},
        }
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / "base.json"
            path.write_text(json.dumps(generated), encoding="utf-8")

            with self.assertRaisesRegex(
                lock_generator.LockGenerationError,
                "was not generated",
            ):
                lock_generator.merge_base_lock(
                    generated,
                    path,
                    replace_environments=frozenset({"typo"}),
                )

    def test_generator_fails_closed_for_missing_or_tampered_report_evidence(self):
        cases = {}
        valid = _wheel_record("alpha")
        missing_hash = json.loads(json.dumps(valid))
        missing_hash["download_info"]["archive_info"]["hashes"] = {}
        cases["missing-sha256"] = [missing_hash]
        wrong_host = json.loads(json.dumps(valid))
        wrong_host["download_info"]["url"] = "https://example.invalid/alpha-1.0-py3-none-any.whl"
        cases["untrusted-url"] = [wrong_host]
        pytorch_non_torch = json.loads(json.dumps(valid))
        pytorch_non_torch["download_info"]["url"] = (
            "https://download-r2.pytorch.org/whl/cpu/alpha-1.0-py3-none-any.whl"
        )
        cases["pytorch-host-non-torch"] = [pytorch_non_torch]
        insecure_url = json.loads(json.dumps(valid))
        insecure_url["download_info"]["url"] = "http://files.pythonhosted.org/alpha-1.0-py3-none-any.whl"
        cases["insecure-url"] = [insecure_url]
        source_archive = json.loads(json.dumps(valid))
        source_archive["download_info"]["url"] = (
            "https://files.pythonhosted.org/packages/aa/bb/alpha-1.0.tar.gz"
        )
        cases["non-wheel"] = [source_archive]

        for label, records in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                pyproject = root / "pyproject.toml"
                _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
                profile = root / "profile.json"
                environment = root / "environment.json"
                output = root / "lock.json"
                _write_report(profile, records)
                _write_report(environment, [_wheel_record("alpha")])
                environment_spec = (
                    f"fixture|{environment}|3.11|cpython-311-darwin|macos|arm64|14.0|core"
                )
                with patch.object(
                    sys,
                    "argv",
                    _generator_argv(pyproject, [("core", profile)], [environment_spec], output),
                ), redirect_stderr(io.StringIO()) as error:
                    code = lock_generator.main()
                self.assertEqual(code, 2)
                self.assertFalse(output.exists())
                self.assertIn("runtime lock generation failed:", error.getvalue())

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyproject = root / "pyproject.toml"
            _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
            missing = root / "missing-report.json"
            output = root / "lock.json"
            with patch.object(
                sys,
                "argv",
                _generator_argv(
                    pyproject,
                    [("core", missing)],
                    [f"fixture|{missing}|3.11|cpython-311-darwin|macos|arm64|14.0|core"],
                    output,
                ),
            ), redirect_stderr(io.StringIO()) as error:
                code = lock_generator.main()
            self.assertEqual(code, 2)
            self.assertFalse(output.exists())
            self.assertIn("runtime lock generation failed:", error.getvalue())

    def test_generator_rejects_inexact_environment_profile_closures(self):
        cases = {
            "missing-direct": [_wheel_record("shared", seed="b")],
            "unassigned-package": [
                _wheel_record("alpha"),
                _wheel_record("shared", seed="b"),
                _wheel_record("unexpected", seed="c"),
            ],
        }
        for label, environment_records in cases.items():
            with self.subTest(case=label), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                pyproject = root / "pyproject.toml"
                _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
                profile = root / "profile.json"
                environment = root / "environment.json"
                output = root / "lock.json"
                _write_report(profile, [_wheel_record("alpha"), _wheel_record("shared", seed="b")])
                _write_report(environment, environment_records)
                args = argparse.Namespace(
                    pyproject=str(pyproject),
                    profile_report=[f"core={profile}"],
                    environment=[
                        f"fixture|{environment}|3.11|cpython-311-darwin|macos|arm64|14.0|core"
                    ],
                    output=str(output),
                )
                with self.assertRaises(lock_generator.LockGenerationError):
                    lock_generator.build_lock(args)

    def test_generator_proves_requires_dist_transitive_closure_and_markers(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyproject = root / "pyproject.toml"
            _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
            profile = root / "profile.json"
            environment = root / "environment.json"
            output = root / "lock.json"
            incomplete = [
                _wheel_record(
                    "alpha",
                    requires_dist=[
                        "beta>=1",
                        "windows-only>=1; sys_platform == 'win32'",
                    ],
                )
            ]
            _write_report(profile, incomplete)
            _write_report(environment, incomplete)
            args = argparse.Namespace(
                pyproject=str(pyproject),
                profile_report=[f"core={profile}"],
                environment=[
                    f"fixture|{environment}|3.11|cpython-311-darwin|macos|arm64|14.0|core"
                ],
                output=str(output),
            )
            with self.assertRaisesRegex(
                lock_generator.LockGenerationError,
                "omits an active Requires-Dist dependency",
            ):
                lock_generator.build_lock(args)

            complete = [
                *incomplete,
                _wheel_record("beta", requires_dist=[]),
            ]
            _write_report(profile, complete)
            _write_report(environment, complete)
            generated = lock_generator.build_lock(args)
            target = generated["environments"]["fixture"]
            self.assertEqual(target["profilePackages"]["core"], ["alpha", "beta"])
            self.assertEqual(
                [package["name"] for package in target["packages"]],
                ["alpha", "beta"],
            )

    def test_generator_models_linux_abi_and_marker_environment_explicitly(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            pyproject = root / "pyproject.toml"
            _write_fixture_pyproject(pyproject, {"core": ["alpha>=1"]})
            profile = root / "profile.json"
            environment = root / "linux.json"
            output = root / "lock.json"
            records = [
                _wheel_record(
                    "alpha",
                    requires_dist=["linux-helper>=1; sys_platform == 'linux'"],
                ),
                _wheel_record("linux-helper", requires_dist=[]),
            ]
            _write_report(profile, records)
            _write_report(environment, records)
            args = argparse.Namespace(
                pyproject=str(pyproject),
                profile_report=[f"core={profile}"],
                environment=[
                    f"linux-cpython313-x86-64|{environment}|3.13|"
                    "cpython-313-x86_64-linux-gnu|linux|x86_64|-|core"
                ],
                output=str(output),
            )

            generated = lock_generator.build_lock(args)

        target = generated["environments"]["linux-cpython313-x86-64"]
        self.assertEqual(target["platformFamily"], "linux")
        self.assertEqual(target["architecture"], "x86_64")
        self.assertEqual(target["abi"], "cpython-313-x86_64-linux-gnu")
        self.assertIsNone(target["minimumMacOS"])
        self.assertEqual(target["profilePackages"]["core"], ["alpha", "linux-helper"])

    def test_generator_rejects_macos_minimum_version_on_linux(self):
        with tempfile.TemporaryDirectory() as temporary:
            report = Path(temporary) / "linux.json"
            _write_report(report, [_wheel_record("alpha")])
            spec = (
                f"linux-cpython313-arm64|{report}|3.13|"
                "cpython-313-aarch64-linux-gnu|linux|arm64|14.0|core"
            )

            with self.assertRaisesRegex(
                lock_generator.LockGenerationError,
                "minimum macOS field must be '-'",
            ):
                lock_generator._parse_environment(spec)

    def test_marker_evaluation_supports_legacy_pip_vendored_packaging(self):
        class LegacyMarker:
            def evaluate(self, environment):
                return environment["sys_platform"] == "darwin"

        class LegacyRequirement:
            marker = LegacyMarker()

            def __str__(self):
                return "legacy-marker-fixture"

        self.assertTrue(
            lock_generator._requirement_applies(
                LegacyRequirement(),
                {"sys_platform": "darwin", "extra": ""},
                set(),
            )
        )

    def test_generator_rejects_marker_variables_absent_from_lock_identity(self):
        for variable in sorted(lock_generator.UNLOCKED_MARKER_VARIABLES):
            with self.subTest(variable=variable), self.assertRaisesRegex(
                lock_generator.LockGenerationError,
                "absent from the lock identity",
            ):
                lock_generator._parse_report_requirement(
                    f"beta>=1; {variable} >= '1'",
                    package="alpha",
                )


if __name__ == "__main__":
    unittest.main()
