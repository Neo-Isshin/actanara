"""Adapter contracts and registration support."""

from .base import Cursor, NormalizedEvent, SourceArtifact, ToolAdapter
from .registry import RegisteredTool, ToolRegistry
from .usage import (
    ClaudeCodeAdapter,
    CodexAdapter,
    CronAdapter,
    GeminiCliAdapter,
    HermesAdapter,
    OpenClawAdapter,
    default_usage_adapters,
)

__all__ = [
    "ClaudeCodeAdapter",
    "CodexAdapter",
    "CronAdapter",
    "Cursor",
    "GeminiCliAdapter",
    "HermesAdapter",
    "NormalizedEvent",
    "OpenClawAdapter",
    "RegisteredTool",
    "SourceArtifact",
    "ToolAdapter",
    "ToolRegistry",
    "default_usage_adapters",
]
