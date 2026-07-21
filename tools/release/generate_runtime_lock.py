#!/usr/bin/env python3
"""Build the checked-in runtime dependency lock from audited pip reports.

The generator never resolves dependencies.  Every input report must already be
the result of a wheel-only, target-specific ``pip install --dry-run --report``
command.  This script only validates and normalizes that evidence.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import tempfile
import tomllib
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

try:
    from packaging.markers import default_environment
    from packaging.requirements import InvalidRequirement, Requirement
except ModuleNotFoundError:  # The test/runtime interpreter still has pip's vendored copy.
    from pip._vendor.packaging.markers import default_environment
    from pip._vendor.packaging.requirements import InvalidRequirement, Requirement


NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
ENVIRONMENT_FIELDS = 8
PYPI_ARTIFACT_HOST = "files.pythonhosted.org"
PYTORCH_CPU_ARTIFACT_HOSTS = frozenset(
    {"download.pytorch.org", "download-r2.pytorch.org"}
)
UNLOCKED_MARKER_VARIABLES = frozenset(
    {
        "implementation_version",
        "platform_release",
        "platform_version",
        "python_full_version",
    }
)


class LockGenerationError(RuntimeError):
    pass


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise LockGenerationError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _load_json(path: Path) -> dict[str, Any]:
    try:
        payload = json.loads(
            path.read_text(encoding="utf-8"), object_pairs_hook=_unique_object
        )
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LockGenerationError(f"pip report is unreadable: {path}") from exc
    if not isinstance(payload, dict):
        raise LockGenerationError(f"pip report must be a JSON object: {path}")
    return payload


def _canonical_name(value: str) -> str:
    if not NAME_RE.fullmatch(value):
        raise LockGenerationError(f"invalid distribution name: {value!r}")
    return re.sub(r"[-_.]+", "-", value).lower()


def _canonical_requirement(value: str) -> str:
    text = str(value).strip()
    if any(token in text for token in (";", "@", "[", "]")):
        raise LockGenerationError(
            f"runtime direct requirements must be simple name/specifier contracts: {text}"
        )
    match = re.fullmatch(r"([A-Za-z0-9][A-Za-z0-9._-]*)(.*)", text)
    if match is None:
        raise LockGenerationError(f"invalid runtime direct requirement: {text}")
    name = _canonical_name(match.group(1))
    specifier = re.sub(r"\s+", "", match.group(2))
    if not specifier:
        return name
    parts = specifier.split(",")
    if any(not part or not re.fullmatch(r"(?:===|==|!=|~=|<=|>=|<|>)[^,]+", part) for part in parts):
        raise LockGenerationError(f"unsupported runtime requirement specifier: {text}")
    return name + ",".join(sorted(parts))


def _report_packages(path: Path) -> tuple[str, dict[str, dict[str, Any]]]:
    report = _load_json(path)
    if report.get("version") != "1" or not isinstance(report.get("pip_version"), str):
        raise LockGenerationError(f"unsupported pip report schema: {path}")
    install = report.get("install")
    if not isinstance(install, list) or not install:
        raise LockGenerationError(f"pip report has no install closure: {path}")
    packages: dict[str, dict[str, Any]] = {}
    for raw in install:
        if not isinstance(raw, dict):
            raise LockGenerationError(f"pip report install record is invalid: {path}")
        metadata = raw.get("metadata")
        download = raw.get("download_info")
        if not isinstance(metadata, dict) or not isinstance(download, dict):
            raise LockGenerationError(f"pip report record lacks metadata/download evidence: {path}")
        name = _canonical_name(str(metadata.get("name") or ""))
        version = str(metadata.get("version") or "")
        if not version or any(character in version for character in "\0\r\n"):
            raise LockGenerationError(f"invalid locked version for {name}: {version!r}")
        url = str(download.get("url") or "")
        try:
            parsed = urlsplit(url)
            port = parsed.port
        except ValueError as exc:
            raise LockGenerationError(f"locked artifact URL is invalid: {name}") from exc
        trusted_pytorch_cpu = (
            name == "torch"
            and parsed.hostname in PYTORCH_CPU_ARTIFACT_HOSTS
            and parsed.path.startswith("/whl/cpu/")
        )
        if (
            parsed.scheme != "https"
            or not (
                parsed.hostname == PYPI_ARTIFACT_HOST or trusted_pytorch_cpu
            )
            or parsed.username is not None
            or parsed.password is not None
            or port is not None
            or parsed.query
            or parsed.fragment
        ):
            raise LockGenerationError(
                f"locked artifact must use an approved exact HTTPS wheel source: {name}"
            )
        filename = unquote(Path(parsed.path).name)
        if not filename.endswith(".whl") or "/" in filename or "\\" in filename:
            raise LockGenerationError(f"locked artifact is not a safe wheel filename: {filename!r}")
        archive = download.get("archive_info")
        hashes = archive.get("hashes") if isinstance(archive, dict) else None
        sha256 = hashes.get("sha256") if isinstance(hashes, dict) else None
        if not isinstance(sha256, str) or not SHA256_RE.fullmatch(sha256):
            raise LockGenerationError(f"locked wheel has no SHA-256: {name}")
        raw_requires = metadata.get("requires_dist", [])
        if raw_requires is None:
            raw_requires = []
        if not isinstance(raw_requires, list) or any(
            not isinstance(requirement, str) or not requirement.strip()
            for requirement in raw_requires
        ):
            raise LockGenerationError(f"locked wheel has invalid Requires-Dist metadata: {name}")
        record = {
            "name": name,
            "version": version,
            "filename": filename,
            "sha256": sha256,
            "url": url,
            "requiresDist": list(raw_requires),
        }
        if name in packages and packages[name] != record:
            raise LockGenerationError(f"duplicate conflicting distribution in report: {name}")
        packages[name] = record
    return str(report["pip_version"]), packages


def _locked_artifact_record(package: dict[str, Any]) -> dict[str, str]:
    return {
        key: str(package[key])
        for key in ("name", "version", "filename", "sha256", "url")
    }


def _marker_environment(
    python_mm: str,
    architecture: str,
    platform_family: str,
) -> dict[str, str]:
    environment = default_environment()
    if platform_family == "macos":
        marker_machine = architecture
        marker_system = "Darwin"
        marker_sys_platform = "darwin"
    elif platform_family == "linux":
        marker_machine = "aarch64" if architecture == "arm64" else architecture
        marker_system = "Linux"
        marker_sys_platform = "linux"
    else:  # The environment parser rejects this before marker evaluation.
        raise LockGenerationError(f"unsupported platform family: {platform_family}")
    environment.update(
        {
            "implementation_name": "cpython",
            "implementation_version": f"{python_mm}.0",
            "os_name": "posix",
            "platform_machine": marker_machine,
            "platform_python_implementation": "CPython",
            "platform_release": "",
            "platform_system": marker_system,
            "platform_version": "",
            "python_full_version": f"{python_mm}.0",
            "python_version": python_mm,
            "sys_platform": marker_sys_platform,
            "extra": "",
        }
    )
    return environment


def _parse_report_requirement(raw: str, *, package: str) -> Requirement:
    try:
        requirement = Requirement(raw)
    except InvalidRequirement as exc:
        raise LockGenerationError(
            f"invalid Requires-Dist metadata for {package}: {raw!r}"
        ) from exc
    if requirement.url is not None:
        raise LockGenerationError(
            f"Requires-Dist must not use a direct URL ({package} -> {requirement.name})"
        )
    marker_text = str(requirement.marker or "")
    unlocked_variables = sorted(
        variable
        for variable in UNLOCKED_MARKER_VARIABLES
        if re.search(rf"\b{re.escape(variable)}\b", marker_text)
    )
    if unlocked_variables:
        raise LockGenerationError(
            "Requires-Dist marker uses environment values absent from the lock identity "
            f"({package} -> {requirement.name}): {', '.join(unlocked_variables)}"
        )
    return requirement


def _requirement_applies(
    requirement: Requirement,
    environment: dict[str, str],
    active_extras: set[str],
) -> bool:
    if requirement.marker is None:
        return True
    try:
        return any(
            _evaluate_metadata_marker(requirement.marker, {**environment, "extra": extra})
            for extra in ("", *sorted(active_extras))
        )
    except Exception as exc:
        raise LockGenerationError(
            f"Requires-Dist marker could not be evaluated: {requirement}"
        ) from exc


def _evaluate_metadata_marker(marker: Any, environment: dict[str, str]) -> bool:
    """Evaluate with modern packaging semantics and support pip's older vendor copy."""
    try:
        return bool(marker.evaluate(environment, context="metadata"))
    except TypeError as exc:
        if "unexpected keyword argument 'context'" not in str(exc):
            raise
        return bool(marker.evaluate(environment))


def _profile_dependency_closure(
    packages: dict[str, dict[str, Any]],
    direct_requirements: list[str],
    *,
    python_mm: str,
    architecture: str,
    platform_family: str,
    environment_id: str,
    profile: str,
) -> list[str]:
    marker_environment = _marker_environment(python_mm, architecture, platform_family)
    selected_extras: dict[str, set[str]] = {}
    processed_extras: dict[str, frozenset[str]] = {}
    pending: list[str] = []

    def add(requirement: Requirement, *, parent: str) -> None:
        name = _canonical_name(requirement.name)
        package = packages.get(name)
        if package is None:
            raise LockGenerationError(
                "environment report omits an active Requires-Dist dependency "
                f"({environment_id}/{profile}: {parent} -> {name})"
            )
        if requirement.specifier and not requirement.specifier.contains(
            str(package["version"])
        ):
            raise LockGenerationError(
                "environment report resolved a version outside Requires-Dist "
                f"({environment_id}/{profile}: {requirement}, got {package['version']})"
            )
        extras = selected_extras.setdefault(name, set())
        new_extras = set(requirement.extras) - extras
        if name not in processed_extras or new_extras:
            pending.append(name)
        extras.update(requirement.extras)

    for raw_requirement in direct_requirements:
        add(_parse_report_requirement(raw_requirement, package=f"profile:{profile}"), parent="direct")

    while pending:
        name = pending.pop(0)
        active_extras = set(selected_extras[name])
        frozen_extras = frozenset(active_extras)
        if processed_extras.get(name) == frozen_extras:
            continue
        processed_extras[name] = frozen_extras
        for raw_requirement in packages[name]["requiresDist"]:
            requirement = _parse_report_requirement(raw_requirement, package=name)
            if _requirement_applies(requirement, marker_environment, active_extras):
                add(requirement, parent=name)
    return sorted(selected_extras)


def _parse_assignment(value: str, *, label: str) -> tuple[str, Path]:
    name, separator, raw_path = value.partition("=")
    if not separator or not NAME_RE.fullmatch(name) or not raw_path:
        raise LockGenerationError(f"{label} must use NAME=PATH: {value!r}")
    return name, Path(raw_path).expanduser().resolve(strict=True)


def _parse_environment(
    value: str,
) -> tuple[str, Path, str, str, str, str, str | None, list[str]]:
    fields = value.split("|")
    if len(fields) != ENVIRONMENT_FIELDS:
        raise LockGenerationError(
            "--environment must use "
            "ID|REPORT|PYTHON_MAJOR_MINOR|ABI|PLATFORM_FAMILY|ARCH|MIN_MACOS_OR_DASH|PROFILES"
        )
    (
        environment_id,
        raw_report,
        python_mm,
        abi,
        platform_family,
        architecture,
        minimum_macos,
        raw_profiles,
    ) = fields
    if not NAME_RE.fullmatch(environment_id):
        raise LockGenerationError(f"invalid environment id: {environment_id!r}")
    if not re.fullmatch(r"3\.(?:11|12|13|14)", python_mm):
        raise LockGenerationError(f"unsupported Python lock version: {python_mm!r}")
    python_tag = python_mm.replace(".", "")
    if platform_family == "macos":
        expected_abi = f"cpython-{python_tag}-darwin"
    elif platform_family == "linux":
        abi_machine = "aarch64" if architecture == "arm64" else architecture
        expected_abi = f"cpython-{python_tag}-{abi_machine}-linux-gnu"
    else:
        raise LockGenerationError(f"unsupported platform family: {platform_family}")
    if abi != expected_abi:
        raise LockGenerationError(f"environment ABI must be {expected_abi}: {environment_id}")
    if architecture not in {"arm64", "x86_64"}:
        raise LockGenerationError(f"unsupported lock architecture: {architecture}")
    if platform_family == "macos":
        if not re.fullmatch(r"[0-9]+\.[0-9]+", minimum_macos):
            raise LockGenerationError(f"invalid minimum macOS version: {minimum_macos}")
        parsed_minimum_macos: str | None = minimum_macos
    else:
        if minimum_macos != "-":
            raise LockGenerationError(
                f"Linux environment minimum macOS field must be '-': {environment_id}"
            )
        parsed_minimum_macos = None
    profiles = sorted(set(filter(None, raw_profiles.split(","))))
    if not profiles:
        raise LockGenerationError(f"environment has no supported profiles: {environment_id}")
    return (
        environment_id,
        Path(raw_report).expanduser().resolve(strict=True),
        python_mm,
        abi,
        platform_family,
        architecture,
        parsed_minimum_macos,
        profiles,
    )


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, raw_tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    tmp = Path(raw_tmp)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o644)
        os.replace(tmp, path)
        directory_fd = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        try:
            tmp.unlink()
        except FileNotFoundError:
            pass


def build_lock(args: argparse.Namespace) -> dict[str, Any]:
    pyproject_path = Path(args.pyproject).expanduser().resolve(strict=True)
    try:
        pyproject = tomllib.loads(pyproject_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise LockGenerationError("pyproject.toml is unreadable") from exc
    optional = ((pyproject.get("project") or {}).get("optional-dependencies") or {})
    if not isinstance(optional, dict):
        raise LockGenerationError("pyproject optional dependencies are invalid")

    profile_reports: dict[str, Path] = {}
    for assignment in args.profile_report:
        profile, report_path = _parse_assignment(assignment, label="--profile-report")
        if profile in profile_reports:
            raise LockGenerationError(f"duplicate profile report: {profile}")
        profile_reports[profile] = report_path
    if set(profile_reports) != set(optional):
        raise LockGenerationError(
            "profile report set must exactly match pyproject optional dependency profiles"
        )

    profiles: dict[str, Any] = {}
    resolver_versions: set[str] = set()
    for profile in sorted(profile_reports):
        declared = optional.get(profile)
        if not isinstance(declared, list) or not all(isinstance(item, str) for item in declared):
            raise LockGenerationError(f"pyproject profile is invalid: {profile}")
        pip_version, packages = _report_packages(profile_reports[profile])
        resolver_versions.add(pip_version)
        profiles[profile] = {
            "directRequirements": sorted(_canonical_requirement(item) for item in declared),
            "packages": sorted(packages),
        }

    environments: dict[str, Any] = {}
    for raw_environment in args.environment:
        (
            environment_id,
            report_path,
            python_mm,
            abi,
            platform_family,
            architecture,
            minimum_macos,
            supported_profiles,
        ) = _parse_environment(raw_environment)
        if environment_id in environments:
            raise LockGenerationError(f"duplicate environment: {environment_id}")
        if not set(supported_profiles).issubset(profiles):
            raise LockGenerationError(f"environment references an unknown profile: {environment_id}")
        pip_version, packages = _report_packages(report_path)
        resolver_versions.add(pip_version)
        profile_packages = {
            profile: _profile_dependency_closure(
                packages,
                profiles[profile]["directRequirements"],
                python_mm=python_mm,
                architecture=architecture,
                platform_family=platform_family,
                environment_id=environment_id,
                profile=profile,
            )
            for profile in supported_profiles
        }
        unassigned = sorted(
            set(packages)
            - {
                name
                for profile in supported_profiles
                for name in profile_packages[profile]
            }
        )
        if unassigned:
            raise LockGenerationError(
                "environment report contains packages absent from every audited "
                f"profile ({environment_id}): {', '.join(unassigned)}"
            )
        for profile in supported_profiles:
            direct_names = {
                requirement.split(",", 1)[0].split("<", 1)[0].split(">", 1)[0]
                .split("=", 1)[0]
                .split("!", 1)[0]
                .split("~", 1)[0]
                for requirement in profiles[profile]["directRequirements"]
            }
            missing_direct = sorted(direct_names - set(profile_packages[profile]))
            if missing_direct:
                raise LockGenerationError(
                    "environment profile is missing a direct requirement "
                    f"({environment_id}/{profile}): {', '.join(missing_direct)}"
                )
        environments[environment_id] = {
            "implementation": "cpython",
            "pythonMajorMinor": python_mm,
            "abi": abi,
            "platformFamily": platform_family,
            "architecture": architecture,
            "minimumMacOS": minimum_macos,
            "supportedProfiles": supported_profiles,
            "profilePackages": profile_packages,
            "packages": [_locked_artifact_record(packages[name]) for name in sorted(packages)],
        }

    if len(resolver_versions) != 1:
        raise LockGenerationError("all audited pip reports must use the same resolver version")
    generated = {
        "schemaVersion": 1,
        "product": "actanara",
        "artifactPolicy": {
            "hashAlgorithm": "sha256",
            "hashesRequired": True,
            "sourceBuildsAllowed": False,
            "wheelsOnly": True,
        },
        "resolver": {
            "name": "pip",
            "reportSchemaVersion": "1",
            "version": resolver_versions.pop(),
        },
        "profiles": profiles,
        "environments": environments,
    }
    base_lock = getattr(args, "base_lock", None)
    if base_lock:
        generated = merge_base_lock(
            generated,
            Path(base_lock).expanduser().resolve(strict=True),
            replace_environments=frozenset(
                getattr(args, "replace_environment", ()) or ()
            ),
        )
    return generated


def merge_base_lock(
    generated: dict[str, Any],
    base_path: Path,
    *,
    replace_environments: frozenset[str] = frozenset(),
) -> dict[str, Any]:
    """Merge audited targets, replacing base evidence only when explicitly authorized."""
    base = _load_json(base_path)
    expected_root_fields = {
        "schemaVersion",
        "product",
        "artifactPolicy",
        "resolver",
        "profiles",
        "environments",
    }
    if set(base) != expected_root_fields:
        raise LockGenerationError("base lock has an unsupported root schema")
    for field in ("schemaVersion", "product", "artifactPolicy", "resolver"):
        if base.get(field) != generated.get(field):
            raise LockGenerationError(f"base lock {field} does not match generated evidence")
    base_profiles = base.get("profiles")
    base_environments = base.get("environments")
    if not isinstance(base_profiles, dict) or not isinstance(base_environments, dict):
        raise LockGenerationError("base lock profile or environment catalog is invalid")
    if set(base_profiles) != set(generated["profiles"]):
        raise LockGenerationError("base lock profiles do not match generated evidence")
    unknown_generated = sorted(replace_environments - set(generated["environments"]))
    if unknown_generated:
        raise LockGenerationError(
            "replacement environment was not generated in this invocation: "
            + ", ".join(unknown_generated)
        )
    unknown_base = sorted(replace_environments - set(base_environments))
    if unknown_base:
        raise LockGenerationError(
            "replacement environment is absent from the base lock: "
            + ", ".join(unknown_base)
        )

    merged_profiles: dict[str, Any] = {}
    for profile in sorted(generated["profiles"]):
        existing = base_profiles.get(profile)
        candidate = generated["profiles"][profile]
        if not isinstance(existing, dict) or set(existing) != {"directRequirements", "packages"}:
            raise LockGenerationError(f"base lock profile is invalid: {profile}")
        if existing.get("directRequirements") != candidate.get("directRequirements"):
            raise LockGenerationError(
                f"base lock direct requirements changed for profile: {profile}"
            )
        existing_packages = existing.get("packages")
        candidate_packages = candidate.get("packages")
        if (
            not isinstance(existing_packages, list)
            or existing_packages != sorted(set(existing_packages))
            or not all(isinstance(item, str) for item in existing_packages)
        ):
            raise LockGenerationError(f"base lock profile package audit is invalid: {profile}")
        merged_profiles[profile] = {
            "directRequirements": list(candidate["directRequirements"]),
            "packages": sorted(set(existing_packages) | set(candidate_packages)),
        }

    merged_environments = dict(base_environments)
    for environment_id, candidate in generated["environments"].items():
        existing = merged_environments.get(environment_id)
        if (
            existing is not None
            and existing != candidate
            and environment_id not in replace_environments
        ):
            raise LockGenerationError(
                f"base lock contains conflicting environment evidence: {environment_id}"
            )
        merged_environments[environment_id] = candidate
    return {
        **generated,
        "profiles": merged_profiles,
        "environments": dict(sorted(merged_environments.items())),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pyproject", required=True)
    parser.add_argument("--profile-report", action="append", default=[], required=True)
    parser.add_argument("--environment", action="append", default=[], required=True)
    parser.add_argument(
        "--base-lock",
        help="Existing compatible lock whose audited environments should be preserved.",
    )
    parser.add_argument(
        "--replace-environment",
        action="append",
        default=[],
        help=(
            "Environment ID whose base evidence may be replaced by evidence generated "
            "in this invocation. Repeat for each intentional replacement."
        ),
    )
    parser.add_argument("--output", required=True)
    return parser


def main() -> int:
    try:
        args = build_parser().parse_args()
        payload = build_lock(args)
        _atomic_json(Path(args.output).expanduser().absolute(), payload)
    except (LockGenerationError, OSError) as exc:
        print(f"runtime lock generation failed: {exc}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
