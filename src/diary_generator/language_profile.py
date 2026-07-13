"""Language profile helpers for diary generator passes."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class DiaryGeneratorLanguageProfile:
    pipeline_language_profile: str
    diary_schema_version: str
    prompt_payload_profile: str
    display_locale: str
    rag_language_profile: str

    @property
    def is_english(self) -> bool:
        return self.pipeline_language_profile == "en"


def current_language_profile(environ: dict[str, str] | None = None) -> DiaryGeneratorLanguageProfile:
    env = environ if environ is not None else os.environ
    profile = str(env.get("NOVA_PIPELINE_LANGUAGE_PROFILE") or "zh").strip()
    if profile in {"zh-CN", "zh_CN"}:
        profile = "zh"
    elif profile in {"en-US", "en_US"}:
        profile = "en"
    if profile not in {"zh", "en"}:
        profile = "zh"
    return DiaryGeneratorLanguageProfile(
        pipeline_language_profile=profile,
        diary_schema_version=str(
            env.get("NOVA_DIARY_SCHEMA_VERSION") or ("diary-v1-en" if profile == "en" else "diary-v1-zh")
        ),
        prompt_payload_profile=str(
            env.get("NOVA_PROMPT_PAYLOAD_PROFILE") or ("en-US" if profile == "en" else "zh-CN")
        ),
        display_locale=str(env.get("NOVA_DISPLAY_LOCALE") or ("en-US" if profile == "en" else "zh-CN")),
        rag_language_profile=str(env.get("NOVA_RAG_LANGUAGE_PROFILE") or ("en" if profile == "en" else "zh")),
    )
