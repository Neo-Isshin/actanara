"""Stable network-boundary predicates shared by Settings and services."""

from __future__ import annotations

import ipaddress


RAG_SERVER_NON_LOOPBACK_ISSUE_CODE = "rag-server-non-loopback"
RAG_INTERNAL_AUTHORIZATION_ISSUE_CODE = "rag-internal-authorization-unavailable"


def is_loopback_host(value: object) -> bool:
    host = str(value or "").strip().lower()
    if host == "localhost":
        return True
    if host.startswith("[") and host.endswith("]"):
        host = host[1:-1]
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def require_loopback_host(value: object, *, field: str = "rag.server.host") -> str:
    host = str(value or "").strip()
    if not is_loopback_host(host):
        raise ValueError(
            f"{field} must be localhost or a numeric loopback address; "
            "non-loopback nova-RAG serving is blocked in macOS v1"
        )
    return host


def host_for_url(value: object) -> str:
    """Format a validated host for URL authority use, including IPv6 brackets."""
    host = str(value or "").strip()
    bare = host[1:-1] if host.startswith("[") and host.endswith("]") else host
    return f"[{bare}]" if ":" in bare else bare
