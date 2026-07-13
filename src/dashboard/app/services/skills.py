#!/usr/bin/env python3
"""Skills 清单服务 — 复用 skills_inventory.py 的扫描逻辑"""
import json
import os
import re
from pathlib import Path
from datetime import datetime
from typing import Optional, Dict, List, Any
from data_foundation.settings import default_external_tool_path, external_tool_path
from data_foundation.time import resolve_timezone

CUSTOM_SKILLS_DIR = default_external_tool_path("openclaw", "skillsRoot")
SYSTEM_SKILLS_DIR = default_external_tool_path("openclaw", "systemSkillsRoot")
_DEFAULT_CUSTOM_SKILLS_DIR = CUSTOM_SKILLS_DIR
_DEFAULT_SYSTEM_SKILLS_DIR = SYSTEM_SKILLS_DIR


def extract_yaml_description(skill_dir: Path) -> Optional[str]:
    """从 SKILL.md 的 YAML frontmatter 中提取 description 字段"""
    skill_md = skill_dir / "SKILL.md"
    if not skill_md.exists():
        return None
    try:
        content = skill_md.read_text(encoding="utf-8")
        match = re.match(r"^---\n(.*?)\n---", content, re.DOTALL)
        if not match:
            return None
        yaml_content = match.group(1)
        for line in yaml_content.split("\n"):
            if line.startswith("description:"):
                return line.split(":", 1)[1].strip().strip("\"'")
        return None
    except Exception:
        return None


def scan_skills(skills_dir: Path) -> list:
    results = []
    if not skills_dir.is_dir():
        return results
    for item in os.listdir(skills_dir):
        skill_path = skills_dir / item
        if not skill_path.is_dir():
            continue
        desc = extract_yaml_description(skill_path)
        mtime = skill_path.stat().st_mtime
        results.append({
            "name": item.replace("-", " ").replace("_", " "),
            "id": item,
            "description": desc or "",
            "path": str(skill_path),
            "lastModified": datetime.fromtimestamp(mtime, tz=resolve_timezone()).strftime("%Y-%m-%d"),
        })
    return results


def _openclaw_path(key: str, fallback_dir: Path) -> Path:
    try:
        return external_tool_path("openclaw", key)
    except Exception:
        return fallback_dir


def _custom_skills_dir() -> Path:
    if CUSTOM_SKILLS_DIR != _DEFAULT_CUSTOM_SKILLS_DIR:
        return CUSTOM_SKILLS_DIR
    return _openclaw_path("skillsRoot", _DEFAULT_CUSTOM_SKILLS_DIR)


def _system_skills_dir() -> Path:
    if SYSTEM_SKILLS_DIR != _DEFAULT_SYSTEM_SKILLS_DIR:
        return SYSTEM_SKILLS_DIR
    return _openclaw_path("systemSkillsRoot", _DEFAULT_SYSTEM_SKILLS_DIR)


def get_all_skills() -> dict:
    custom = scan_skills(_custom_skills_dir())
    system = scan_skills(_system_skills_dir())
    return {
        "custom": custom,
        "system": system,
        "totalCustom": len(custom),
        "totalSystem": len(system),
        "updatedAt": datetime.now(resolve_timezone()).strftime("%Y-%m-%d %H:%M:%S"),
    }
