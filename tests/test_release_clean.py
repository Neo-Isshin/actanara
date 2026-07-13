import json
import sys
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "src"))

from data_foundation.release_clean import repository_clean_deployment_check
from data_foundation.business_day_audit import business_day_hardcode_inventory


def _pre_scan_runtime_source_manifest() -> dict[str, object]:
    digest = "0" * 64
    return {
        "schemaVersion": 2,
        "product": "open-nova",
        "sourceLocator": {
            "kind": "login-home-relative",
            "pathComponents": ["Desktop", "DEV", "open-nova"],
        },
        "deployedSourceLocator": {
            "kind": "runtime-relative",
            "pathComponents": ["app", "source"],
        },
        "releaseLocator": {
            "kind": "runtime-relative",
            "pathComponents": ["app", "releases", "candidate"],
        },
        "deploymentMode": "release-symlink",
        "copiedAt": "2026-07-11T00:00:00-07:00",
        "pyprojectVersion": "1.0.1",
        "git": {
            "available": False,
            "commit": None,
            "branch": None,
            "remote": None,
            "dirty": None,
        },
        "databaseCompatibility": {
            "schemaVersion": 1,
            "policy": "rollback-compatible-additive-only",
            "preCommitWriterContract": "prior-reader-compatible-v1",
            "minimumReadableSchema": "unversioned",
            "maximumReadableSchema": "0001_base",
            "migrationSetSha256": digest,
            "migrations": [
                {
                    "version": "0001_base",
                    "sha256": digest,
                    "rollbackClass": "rollback-compatible-additive",
                }
            ],
        },
    }


class ReleaseCleanTests(unittest.TestCase):
    def test_clean_deployment_check_passes_secret_refs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "settings.py").write_text(
                'config = {"apiKeyEnv": "NOVA_RAG_CLOUD_API_KEY", "secretRef": "llm-provider-api-key"}\n',
                encoding="utf-8",
            )

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["findings"], [])
        self.assertIn("businessDayHardcodes", payload)

    def test_clean_deployment_check_does_not_join_masked_secret_branch_to_next_line(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src").mkdir()
            (root / "src" / "provider.py").write_text(
                'if candidate == MASKED_SECRET:\n    normalized_candidate.pop("apiKey", None)\n',
                encoding="utf-8",
            )

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["findings"], [])

    def test_clean_deployment_secret_assignment_true_positive_matrix(self):
        synthetic_value = "synthetic-" + ("a" * 24)
        fixtures = {
            "same-line.py": f'API_KEY = "{synthetic_value}"\n',
            "generator-lookalike.py": "api_key = secrets.token_urlsafe_attacker_value_123456\n",
            "ordinary.json": json.dumps({"token": synthetic_value}) + "\n",
            "suffix-keys.json": json.dumps(
                {"llmApiKey": synthetic_value, "gatewayToken": synthetic_value, "dbPassword": synthetic_value}
            )
            + "\n",
            "quoted-pretty.json": '{\n  "apiKey"\n  :\n  "' + synthetic_value + '"\n}\n',
            "bom-pretty.json": '\ufeff\n{\n  "accessToken": "' + synthetic_value + '"\n}\n',
            "quoted-json.json": json.dumps(json.dumps({"clientSecret": synthetic_value})) + "\n",
            "config.yaml": "password:\n  " + synthetic_value + "\n",
            "block.yaml": "privateKey: |-\n  " + synthetic_value + "\n",
            "block-comment.yaml": "- dbPassword: |- # synthetic fixture\n    " + synthetic_value + "!@#\n",
            ".env.release": "API_KEY=\\\n  " + synthetic_value + "\n",
            "multiple.py": 'token = process.environment_reference\npassword = "' + synthetic_value + '"\n',
            "redacted-substring.json": '{"apiKey": "live-redacted-but-still-secret-1234567890"}\n',
            "none-prefix.json": '{"password": "none-but-this-is-a-real-value-1234567890"}\n',
            "masked-substring.json": '{"API_KEY": "live-masked-but-still-secret-1234567890"}\n',
            "plus-secret.json": json.dumps(
                {"password": "correct horse " + "+" + " battery staple 123456789"}
            )
            + "\n",
            "document-prefix.json": json.dumps(
                {"password": "document." + "real-password-material-123456789"}
            )
            + "\n",
            "process-prefix.json": json.dumps(
                {"password": "process." + "real-password-material-123456789"}
            )
            + "\n",
        }
        for name, content in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / name).write_text(content, encoding="utf-8")

                payload = repository_clean_deployment_check(root)

                self.assertEqual(payload["status"], "blocked")
                findings = [item for item in payload["findings"] if item["kind"] == "possible-raw-secret"]
                self.assertEqual(len(findings), 1)
                self.assertNotIn(synthetic_value, repr(findings))

    def test_clean_deployment_secret_assignment_false_positive_matrix(self):
        fixtures = {
            "masked.py": 'if candidate == MASKED_SECRET:\n    normalized_candidate.pop("apiKey", None)\n',
            "code-ref.py": "token = process.environment_reference\n",
            "generated-secret.py": "api_key = secrets.token_urlsafe(48)\n",
            "adjacent.py": 'status = "secret:"\nidentifier = "abcdefghijklmnopqrstuvwx"\n',
            "refs.json": '{"secretRef": "llm-provider-api-key", "apiKeyEnv": "NOVA_RAG_CLOUD_API_KEY"}\n',
            "placeholder.yaml": "secretRef:\n  open-nova-keychain-reference\n",
            "encoded-placeholder.json": '{"endpoint": "https://example.invalid/admin?token=%5Bredacted%5D&mode=ops"}\n',
            "comment.yaml": "# rotate secret:\n  abcdefghijklmnopqrstuvwx\n",
        }
        for name, content in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / name).write_text(content, encoding="utf-8")

                payload = repository_clean_deployment_check(root)

                self.assertEqual(payload["status"], "passed")
                self.assertEqual(payload["findings"], [])

    def test_structured_json_duplicate_key_reports_the_actual_raw_value_line(self):
        synthetic_value = "synthetic-" + ("b" * 24)
        secret_key = "to" + "ken"
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "duplicate.json").write_text(
                f'{{\n  "{secret_key}": "process.environment_reference",\n'
                f'  "{secret_key}": "{synthetic_value}"\n}}\n',
                encoding="utf-8",
            )

            payload = repository_clean_deployment_check(root)

        finding = next(item for item in payload["findings"] if item["kind"] == "possible-raw-secret")
        self.assertEqual(finding["line"], 3)

    def test_clean_deployment_fails_closed_for_oversize_unscanned_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "oversize.js").write_text("x" * ((2 * 1024 * 1024) + 1), encoding="utf-8")

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "blocked")
        finding = payload["findings"][0]
        self.assertEqual(finding["kind"], "unscanned-oversize")
        self.assertNotIn("content", finding)

    def test_clean_deployment_check_blocks_runtime_artifacts_and_raw_secrets(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "state").mkdir()
            (root / "state" / "job.log").write_text("runtime log", encoding="utf-8")
            (root / "config").mkdir()
            (root / "config" / "settings.json").write_text("{}", encoding="utf-8")
            (root / "src").mkdir()
            secret_assignment = "API_" + "KEY = " + '"sk-test-abcdefghijklmnopqrstuvwxyz"\n'
            (root / "src" / "bad.py").write_text(
                secret_assignment,
                encoding="utf-8",
            )

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "blocked")
        kinds = {item["kind"] for item in payload["findings"]}
        self.assertIn("runtime-artifact", kinds)
        self.assertIn("possible-raw-secret", kinds)

    def test_runtime_source_manifest_privacy_schema_is_fail_closed(self):
        private_marker = "/Users/private-operator/Desktop/open-nova"
        fixtures = {
            "legacy-absolute": {
                "schemaVersion": 1,
                "sourceRoot": private_marker,
            },
            "v2-traversal": {
                **_pre_scan_runtime_source_manifest(),
                "sourceLocator": {
                    "kind": "login-home-relative",
                    "pathComponents": ["..", "private"],
                },
            },
            "v2-private-extra": {
                **_pre_scan_runtime_source_manifest(),
                "debugPath": private_marker,
            },
            "v2-private-nested-extra": {
                **_pre_scan_runtime_source_manifest(),
                "sourceLocator": {
                    "kind": "unavailable",
                    "issue": "outside-login-home",
                    "raw": private_marker,
                },
            },
            "v2-wrong-product": {
                **_pre_scan_runtime_source_manifest(),
                "product": "different-product",
            },
            "v2-source-old": {
                **_pre_scan_runtime_source_manifest(),
                "deployedSourceLocator": {
                    "kind": "runtime-relative",
                    "pathComponents": ["app", "source-old"],
                },
            },
            "v2-file-remote": {
                **_pre_scan_runtime_source_manifest(),
                "git": {
                    "available": True,
                    "commit": "0" * 40,
                    "branch": "main",
                    "remote": "file:///Users/private-operator/open-nova",
                    "dirty": False,
                },
            },
            "v2-private-version": {
                **_pre_scan_runtime_source_manifest(),
                "pyprojectVersion": private_marker,
            },
            "v2-private-branch": {
                **_pre_scan_runtime_source_manifest(),
                "git": {
                    "available": True,
                    "commit": "0" * 40,
                    "branch": private_marker,
                    "remote": None,
                    "dirty": False,
                },
            },
            "v2-non-string-remote": {
                **_pre_scan_runtime_source_manifest(),
                "git": {
                    "available": True,
                    "commit": "0" * 40,
                    "branch": "main",
                    "remote": 123,
                    "dirty": False,
                },
            },
        }
        for name, manifest in fixtures.items():
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                (root / ".open-nova-runtime-source.json").write_text(
                    json.dumps(manifest) + "\n",
                    encoding="utf-8",
                )

                payload = repository_clean_deployment_check(root)

                self.assertEqual(payload["status"], "blocked")
                self.assertNotIn(private_marker, repr(payload["findings"]))

        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / ".open-nova-runtime-source.json").write_text(
                json.dumps(_pre_scan_runtime_source_manifest())
                + "\n",
                encoding="utf-8",
            )

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "passed")
        self.assertEqual(payload["findings"], [])

    def test_clean_deployment_check_includes_untracked_git_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            subprocess.run(["git", "init"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root / "src").mkdir()
            (root / "src" / "tracked.py").write_text("OK = True\n", encoding="utf-8")
            subprocess.run(["git", "add", "src/tracked.py"], cwd=root, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            (root / "state").mkdir()
            (root / "state" / "job.log").write_text("runtime log\n", encoding="utf-8")

            payload = repository_clean_deployment_check(root)

        self.assertEqual(payload["status"], "blocked")
        self.assertTrue(any(item["path"] == "state/job.log" for item in payload["findings"]))

    def test_business_day_hardcode_inventory_classifies_authority_and_legacy(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "src" / "data_foundation").mkdir(parents=True)
            (root / "src" / "data_foundation" / "time.py").write_text(
                'DEFAULT_TIMEZONE = "Asia/Hong_Kong"\nDEFAULT_BUSINESS_DAY_START_HOUR = 4\n',
                encoding="utf-8",
            )
            (root / "src" / "dashboard" / "app" / "services").mkdir(parents=True)
            (root / "src" / "dashboard" / "app" / "services" / "diary.py").write_text(
                "hkt = timezone(timedelta(hours=8))\n",
                encoding="utf-8",
            )
            (root / "src" / "new_module.py").write_text(
                'window = "HKT 04:00-03:59"\n',
                encoding="utf-8",
            )

            payload = business_day_hardcode_inventory(root)

        self.assertEqual(payload["authority"], "data_foundation.time")
        self.assertEqual(payload["businessDayStartHour"], 4)
        categories = {item["path"]: item["category"] for item in payload["findings"]}
        self.assertEqual(categories["src/data_foundation/time.py"], "authority")
        self.assertEqual(categories["src/dashboard/app/services/diary.py"], "legacy-hardcode")
        self.assertEqual(categories["src/new_module.py"], "needs-review")


if __name__ == "__main__":
    unittest.main()
