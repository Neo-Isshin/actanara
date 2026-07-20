"""Normalized host-platform capabilities used by cross-platform runtime code."""

from __future__ import annotations

import platform
from dataclasses import dataclass


@dataclass(frozen=True)
class PlatformCapabilities:
    system: str
    family: str
    architecture: str
    timer_provider: str | None
    user_service_manager: str | None
    supported: bool


def normalize_architecture(machine: str) -> str:
    value = str(machine or "").strip().lower()
    if value in {"x86_64", "amd64"}:
        return "x86_64"
    if value in {"arm64", "aarch64"}:
        return "arm64"
    return value or "unknown"


def platform_capabilities(
    *,
    system: str | None = None,
    machine: str | None = None,
) -> PlatformCapabilities:
    detected_system = str(system if system is not None else platform.system()).strip()
    architecture = normalize_architecture(machine if machine is not None else platform.machine())
    if detected_system == "Darwin":
        return PlatformCapabilities(
            system=detected_system,
            family="macos",
            architecture=architecture,
            timer_provider="launchd",
            user_service_manager="launchd-user",
            supported=True,
        )
    if detected_system == "Linux":
        return PlatformCapabilities(
            system=detected_system,
            family="linux",
            architecture=architecture,
            timer_provider="systemd",
            user_service_manager="systemd-user",
            supported=True,
        )
    return PlatformCapabilities(
        system=detected_system or "Unknown",
        family="unsupported",
        architecture=architecture,
        timer_provider=None,
        user_service_manager=None,
        supported=False,
    )


def default_timer_provider() -> str:
    """Return a safe settings default without changing persisted operator state."""
    return platform_capabilities().timer_provider or "launchd"
