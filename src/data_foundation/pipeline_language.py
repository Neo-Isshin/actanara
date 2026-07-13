"""Diary pipeline language profile contracts."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class PipelineLanguageProfile:
    profile_id: str
    locale: str
    diary_schema_version: str
    prompt_payload_profile: str
    rag_language_profile: str
    status: str


PIPELINE_LANGUAGE_PROFILES: dict[str, PipelineLanguageProfile] = {
    "zh": PipelineLanguageProfile(
        profile_id="zh",
        locale="zh-CN",
        diary_schema_version="diary-v1-zh",
        prompt_payload_profile="zh-CN",
        rag_language_profile="zh",
        status="production",
    ),
    "en": PipelineLanguageProfile(
        profile_id="en",
        locale="en-US",
        diary_schema_version="diary-v1-en",
        prompt_payload_profile="en-US",
        rag_language_profile="en",
        status="gated",
    ),
}
DEFAULT_PIPELINE_LANGUAGE_PROFILE = "zh"


def valid_pipeline_language_profiles() -> set[str]:
    return set(PIPELINE_LANGUAGE_PROFILES)


def resolve_pipeline_language_profile(value: str | None = None) -> PipelineLanguageProfile:
    normalized = str(value or DEFAULT_PIPELINE_LANGUAGE_PROFILE).strip()
    if normalized in {"zh-CN", "zh_CN"}:
        normalized = "zh"
    elif normalized in {"en-US", "en_US"}:
        normalized = "en"
    return PIPELINE_LANGUAGE_PROFILES.get(normalized, PIPELINE_LANGUAGE_PROFILES[DEFAULT_PIPELINE_LANGUAGE_PROFILE])
