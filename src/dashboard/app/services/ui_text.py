"""Profile-aware Dashboard display text helpers."""

from __future__ import annotations

from data_foundation.pipeline_language import resolve_pipeline_language_profile
from data_foundation.settings import resolve_pipeline_settings


def dashboard_language_profile() -> str:
    try:
        pipeline = resolve_pipeline_settings()
        return resolve_pipeline_language_profile(str(pipeline.get("languageProfile") or "zh")).profile_id
    except Exception:
        return "zh"


def is_english_profile(profile: str | None = None) -> bool:
    return resolve_pipeline_language_profile(profile or dashboard_language_profile()).profile_id == "en"
