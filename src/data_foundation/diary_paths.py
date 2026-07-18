"""Diary Markdown storage layout helpers."""

from __future__ import annotations

import shutil
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path

DIARY_REPORT_PREFIXES = {
    "zh": {
        "narrative": "日记",
        "technical": "技术进展",
        "learning": "智慧沉淀",
    },
    "en": {
        "narrative": "diary",
        "technical": "technical",
        "learning": "learning",
    },
}


def normalize_diary_language_profile(language_profile: str = "zh") -> str:
    value = str(language_profile or "").lower()
    if value.startswith("en"):
        return "en"
    if value == "mixed":
        return "mixed"
    return "zh"


def diary_day_dir(root: Path, business_date: date | str) -> Path:
    target = date.fromisoformat(business_date) if isinstance(business_date, str) else business_date
    return root / f"diary-{target.year:04d}" / f"diary-{target.year:04d}-{target.month:02d}" / f"{target.month:02d}-{target.day:02d}"


def compact_diary_day_dir(root: Path, business_date: date | str) -> Path:
    target = date.fromisoformat(business_date) if isinstance(business_date, str) else business_date
    return root / f"{target.year:04d}" / f"{target.month:02d}" / f"{target.day:02d}"


def legacy_diary_day_dir(root: Path, business_date: date | str) -> Path:
    target = date.fromisoformat(business_date) if isinstance(business_date, str) else business_date
    return root / f"diary-{target.isoformat()}"


def preferred_diary_day_dir(root: Path, business_date: date | str) -> Path:
    return diary_day_dir(root, business_date)


def existing_diary_day_dirs(root: Path, business_date: date | str) -> list[Path]:
    candidates = [diary_day_dir(root, business_date), compact_diary_day_dir(root, business_date), legacy_diary_day_dir(root, business_date)]
    return [path for path in candidates if path.exists()]


def diary_markdown_paths(root: Path, business_date: date | str, pattern: str = "*.md") -> list[Path]:
    paths: list[Path] = []
    seen: set[Path] = set()
    for day_dir in existing_diary_day_dirs(root, business_date):
        for markdown_path in sorted(day_dir.glob(pattern)):
            if markdown_path.is_file() and markdown_path not in seen:
                paths.append(markdown_path)
                seen.add(markdown_path)
    return _prefer_no_activity_narrative(paths)


def _prefer_no_activity_narrative(paths: list[Path]) -> list[Path]:
    narrative_prefixes = tuple(prefix for profile in DIARY_REPORT_PREFIXES.values() for prefix in (profile["narrative"],))
    no_activity_stamps = {
        _narrative_stamp(path.name, no_activity=True)
        for path in paths
        if _narrative_stamp(path.name, no_activity=True) is not None
    }
    if not no_activity_stamps:
        return paths
    filtered = []
    for path in paths:
        if path.name.endswith(".md") and not path.name.endswith("-no-activity.md") and path.name.startswith(narrative_prefixes):
            stamp = _narrative_stamp(path.name, no_activity=False)
            if stamp in no_activity_stamps:
                continue
        filtered.append(path)
    return filtered


def _narrative_stamp(name: str, *, no_activity: bool) -> str | None:
    suffix = "-no-activity.md" if no_activity else ".md"
    if not name.endswith(suffix):
        return None
    for profile in DIARY_REPORT_PREFIXES.values():
        prefix = f"{profile['narrative']}-"
        if name.startswith(prefix):
            stamp = name.removeprefix(prefix).removesuffix(suffix)
            return stamp if stamp.isdigit() else None
    return None


def diary_report_path(root: Path, business_date: date | str, prefix: str) -> Path:
    target = date.fromisoformat(business_date) if isinstance(business_date, str) else business_date
    stamp = target.strftime("%y%m%d")
    return preferred_diary_day_dir(root, target) / f"{prefix}-{stamp}.md"


def diary_report_prefix(report_type: str, language_profile: str = "zh") -> str:
    profile = normalize_diary_language_profile(language_profile)
    if profile == "mixed":
        profile = "zh"
    try:
        return DIARY_REPORT_PREFIXES[profile][report_type]
    except KeyError as exc:
        raise ValueError(f"unsupported diary report type: {report_type}") from exc


def diary_report_type_for_filename(filename: str, *, language_profile: str = "mixed") -> str:
    profile = normalize_diary_language_profile(language_profile)
    normalized = filename.lower()
    profiles = ("zh", "en") if profile == "mixed" else (profile,)
    for candidate in profiles:
        prefixes = DIARY_REPORT_PREFIXES[candidate]
        if candidate == "zh":
            if _matches_report_filename(filename, prefixes["narrative"], allow_no_activity=True):
                return "narrative"
            if _matches_report_filename(filename, prefixes["technical"]):
                return "technical"
            if _matches_report_filename(filename, prefixes["learning"]):
                return "learning"
            continue
        if _matches_report_filename(normalized, prefixes["narrative"], allow_no_activity=True):
            return "narrative"
        if _matches_report_filename(normalized, prefixes["technical"]):
            return "technical"
        if _matches_report_filename(normalized, prefixes["learning"]):
            return "learning"
    return "unknown"


def _matches_report_filename(filename: str, prefix: str, *, allow_no_activity: bool = False) -> bool:
    suffix = r"(?:-no-activity)?\.md" if allow_no_activity else r"\.md"
    return bool(re.match(rf"^{re.escape(prefix)}-\d{{6}}{suffix}$", filename))


def diary_profile_report_path(
    root: Path,
    business_date: date | str,
    report_type: str,
    *,
    language_profile: str = "zh",
) -> Path:
    return diary_report_path(root, business_date, diary_report_prefix(report_type, language_profile))


def diary_report_paths(
    root: Path,
    business_date: date | str,
    report_type: str,
    *,
    language_profile: str = "zh",
) -> list[Path]:
    prefix = diary_report_prefix(report_type, language_profile)
    return diary_markdown_paths(root, business_date, f"{prefix}-*.md")


def diary_learning_report_path(root: Path, business_date: date | str, *, language_profile: str = "zh") -> Path:
    return diary_profile_report_path(root, business_date, "learning", language_profile=language_profile)


def diary_narrative_report_path(root: Path, business_date: date | str, *, language_profile: str = "zh") -> Path:
    return diary_profile_report_path(root, business_date, "narrative", language_profile=language_profile)


def diary_no_activity_report_path(root: Path, business_date: date | str, *, language_profile: str = "zh") -> Path:
    target = date.fromisoformat(business_date) if isinstance(business_date, str) else business_date
    stamp = target.strftime("%y%m%d")
    prefix = diary_report_prefix("narrative", language_profile)
    return preferred_diary_day_dir(root, target) / f"{prefix}-{stamp}-no-activity.md"


def diary_technical_report_path(root: Path, business_date: date | str, *, language_profile: str = "zh") -> Path:
    return diary_profile_report_path(root, business_date, "technical", language_profile=language_profile)


def period_report_path(root: Path, start_date: date, end_date: date, *, label: str) -> Path:
    owner = start_date if label == "周报" else end_date
    month_dir = root / f"diary-{owner.year:04d}" / f"diary-{owner.year:04d}-{owner.month:02d}"
    if label == "周报":
        iso = start_date.isocalendar()
        name = f"summary-{iso.year}-W{iso.week:02d}-周报.md"
    elif label == "月报":
        name = f"summary-{owner.year:04d}-{owner.month:02d}-月报.md"
    else:
        stamp = f"{start_date.strftime('%Y%m%d')}-{end_date.strftime('%Y%m%d')}"
        name = f"summary-{stamp}-{label}.md"
    return month_dir / name


def iter_diary_markdown_files(root: Path) -> list[Path]:
    files = (
        list(root.glob("diary-????/diary-????-??/??-??/*.md"))
        + list(root.glob("????/??/??/*.md"))
        + list(root.glob("diary-????-??-??/*.md"))
    )
    return sorted(path for path in files if path.is_file())


@dataclass(frozen=True)
class DiaryLayoutMove:
    source: str
    destination: str
    status: str


def plan_diary_layout_migration(root: Path) -> dict:
    """Return old-layout files that would move to diary-YYYY/diary-YYYY-MM/MM-DD without writing."""
    moves: list[DiaryLayoutMove] = []
    seen_sources: set[Path] = set()
    for old_dir in sorted(root.glob("diary-????-??-??")):
        if not old_dir.is_dir():
            continue
        try:
            target_date = date.fromisoformat(old_dir.name.replace("diary-", ""))
        except ValueError:
            continue
        _append_day_dir_moves(moves, seen_sources, old_dir, diary_day_dir(root, target_date))
    for compact_day in sorted(root.glob("????/??/??")):
        if not compact_day.is_dir():
            continue
        try:
            target_date = date(int(compact_day.parts[-3]), int(compact_day.parts[-2]), int(compact_day.parts[-1]))
        except (ValueError, IndexError):
            continue
        _append_day_dir_moves(moves, seen_sources, compact_day, diary_day_dir(root, target_date))
    return {
        "dryRun": True,
        "diaryRoot": str(root),
        "moves": [move.__dict__ for move in moves],
        "wouldMove": sum(1 for move in moves if move.status == "would-move"),
        "alreadyPresent": sum(1 for move in moves if move.status == "already-present"),
        "conflicts": sum(1 for move in moves if move.status == "conflict"),
    }


def apply_diary_layout_migration(root: Path, *, confirmation_text: str) -> dict:
    required = "MIGRATE ACTANARA DIARY LAYOUT"
    if confirmation_text != required:
        raise ValueError(f"confirmationText must be exactly: {required}")
    plan = plan_diary_layout_migration(root)
    if plan["conflicts"]:
        raise ValueError("diary layout migration has conflicts; resolve them before applying")
    moved = []
    for item in plan["moves"]:
        if item["status"] != "would-move":
            continue
        source = Path(item["source"])
        destination = Path(item["destination"])
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(source), str(destination))
        moved.append(item)
    for old_dir in sorted(root.glob("diary-????-??-??")):
        try:
            old_dir.rmdir()
        except OSError:
            pass
    for compact_month in sorted(root.glob("????/??/??")):
        try:
            compact_month.rmdir()
        except OSError:
            pass
    for compact_month in sorted(root.glob("????/??")):
        try:
            compact_month.rmdir()
        except OSError:
            pass
    for compact_year in sorted(root.glob("????")):
        try:
            compact_year.rmdir()
        except OSError:
            pass
    return {**plan, "dryRun": False, "moved": moved, "confirmationTextRequired": required}


def _append_day_dir_moves(moves: list[DiaryLayoutMove], seen_sources: set[Path], source_dir: Path, destination_dir: Path) -> None:
    for source in sorted(source_dir.iterdir()):
        if not source.is_file() or source in seen_sources:
            continue
        seen_sources.add(source)
        destination = destination_dir / source.name
        if destination.exists() and destination.read_bytes() != source.read_bytes():
            status = "conflict"
        elif destination.exists():
            status = "already-present"
        else:
            status = "would-move"
        moves.append(DiaryLayoutMove(str(source), str(destination), status))
