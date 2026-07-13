"""Additive period summary snapshots for weekly/monthly report pages."""

from __future__ import annotations

import calendar
import json
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .diary_markdown import DIARY_PERIOD_PAGE_PROJECTION
from .diary_paths import period_report_path
from .llm_json import parse_llm_json_object
from .llm_provider_catalog import DEFAULT_LLM_TIMEOUT_SECONDS
from .llm_transport import send_anthropic_message, send_openai_compatible_message
from .paths import RuntimePaths
from .reports import LEGACY_ASSET_PROJECTION, read_period_projection, write_period_projection
from .settings import ensure_settings, resolve_llm_provider

DIARY_PERIOD_SUMMARY_PROJECTION = "diary-period-summary-v1"
PeriodSummaryResult = dict[str, Any]
PeriodSummaryGenerator = Callable[[dict], str | PeriodSummaryResult | None]

_PERIOD_SUMMARY_LABELS = {
    "zh": {
        "week": "本周",
        "month": "本月",
        "period": "本周期",
        "report_week": "周报",
        "report_month": "月报",
        "summary_suffix": "总结",
        "data_comparison": "数据与环比",
        "workspace_distribution": "项目投入分布",
        "overview": "本周期总览",
        "workload": "工作强度与深夜投入",
        "progress": "主要进展与难题",
        "risks": "风险与下周期建议",
        "care": "关怀与鼓励",
        "sources": "数据来源与缺口",
        "saved_snapshot": "已保存周期聚合快照；点击生成总结可获得更完整的环比叙事。",
        "workspace_hint": "见 dashboard 的 workspace/project 排行。",
        "workload_hint": "需要结合 token 日序列与时段热力图进一步判断；建议在高强度周期后预留恢复时间。",
        "no_topics": "暂无足够主题数据。",
        "no_lessons": "暂未提取到明确风险或复盘提醒。",
        "care_line": "请在保持节奏的同时预留恢复时间；稳定的长期推进比短期透支更重要。",
        "care_quote": "“行远自迩，笃行不怠。”",
        "source_line": "本总结基于 dashboard 聚合快照、项目使用、任务统计、日记主题与 lessons；缺失数据会影响判断精度。",
    },
    "en": {
        "week": "This Week",
        "month": "This Month",
        "period": "This Period",
        "report_week": "Weekly Report",
        "report_month": "Monthly Report",
        "summary_suffix": " Summary",
        "data_comparison": "Data and Comparison",
        "workspace_distribution": "Project Investment",
        "overview": "Period Overview",
        "workload": "Workload and Late-Hour Focus",
        "progress": "Progress and Challenges",
        "risks": "Risks and Next-Period Recommendations",
        "care": "Care and Encouragement",
        "sources": "Sources and Gaps",
        "saved_snapshot": "The period aggregation snapshot has been saved; generating an LLM summary can add a fuller comparative narrative.",
        "workspace_hint": "See the dashboard workspace/project ranking.",
        "workload_hint": "Use token series and time-of-day heatmaps to judge workload; reserve recovery time after high-intensity periods.",
        "no_topics": "Not enough topic data yet.",
        "no_lessons": "No clear risks or retrospective lessons were extracted.",
        "care_line": "Keep a sustainable pace and leave room to recover; steady long-term progress matters more than short bursts of overextension.",
        "care_quote": '"Great things are done by a series of small things brought together."',
        "source_line": "This summary is based on dashboard aggregation snapshots, project usage, task stats, diary topics, and lessons; missing data may reduce precision.",
    },
}


def _language_profile(paths: RuntimePaths | None = None) -> str:
    if paths is None:
        return "zh"
    settings = ensure_settings(paths)
    pipeline = settings.get("pipeline") if isinstance(settings.get("pipeline"), dict) else {}
    value = str(pipeline.get("languageProfile") or "zh").lower()
    return "en" if value.startswith("en") else "zh"


def _labels(language_profile: str) -> dict[str, str]:
    return _PERIOD_SUMMARY_LABELS["en" if str(language_profile or "").lower().startswith("en") else "zh"]


def _clean_text(value: object) -> str:
    return str(value or "").replace("**", "").strip()


def _period_label(start_date: date, end_date: date, *, language_profile: str = "zh") -> str:
    labels = _labels(language_profile)
    days = (end_date - start_date).days + 1
    if days == 7:
        return labels["week"]
    if start_date.day == 1 and end_date.day >= 28:
        return labels["month"]
    return labels["period"]


def _is_full_month(start_date: date, end_date: date) -> bool:
    return start_date.day == 1 and end_date == end_date.replace(day=calendar.monthrange(end_date.year, end_date.month)[1])


def _report_label(start_date: date, end_date: date, *, language_profile: str = "zh") -> str:
    labels = _labels(language_profile)
    return labels["report_month"] if start_date.day == 1 else labels["report_week"]


def _top_topics(summary_topics: list[dict], *, limit: int = 6) -> list[dict]:
    topics = []
    seen = set()
    for topic in summary_topics:
        title = _clean_text(topic.get("title"))
        if not title or title in seen:
            continue
        seen.add(title)
        topics.append(
            {
                "date": topic.get("date"),
                "title": title,
                "items": [_clean_text(item) for item in (topic.get("items") or []) if _clean_text(item)][:3],
                "sourceDocumentKey": topic.get("sourceDocumentKey"),
            }
        )
        if len(topics) >= limit:
            break
    return topics


def _top_lessons(lessons: list[dict], *, limit: int = 4) -> list[dict]:
    result = []
    for lesson in lessons:
        problem = _clean_text(lesson.get("problem"))
        suggestion = _clean_text(lesson.get("suggestion"))
        if not problem and not suggestion:
            continue
        result.append(
            {
                "date": lesson.get("date"),
                "agent": _clean_text(lesson.get("agent")),
                "problem": problem,
                "suggestion": suggestion,
                "sourceDocumentKey": lesson.get("sourceDocumentKey"),
            }
        )
        if len(result) >= limit:
            break
    return result


def _normalize_high_frequency_topics(topics: object, *, limit: int = 8) -> list[dict]:
    if not isinstance(topics, list):
        return []
    result = []
    seen = set()
    for item in topics:
        if not isinstance(item, dict):
            continue
        topic = _clean_text(item.get("topic") or item.get("title") or item.get("name"))
        if not topic or topic in seen:
            continue
        normalized = {"topic": topic}
        count = item.get("count")
        if isinstance(count, (int, float)) and count > 0:
            normalized["count"] = int(count)
        reason = _clean_text(item.get("reason") or item.get("evidence") or item.get("description"))
        if reason:
            normalized["reason"] = reason
        result.append(normalized)
        seen.add(topic)
        if len(result) >= limit:
            break
    return result


def _coerce_period_summary_result(raw: str | PeriodSummaryResult | None) -> PeriodSummaryResult | None:
    if raw is None:
        return None
    if isinstance(raw, str):
        return {"markdown": raw.strip(), "highFrequencyTopics": []} if raw.strip() else None
    if isinstance(raw, dict):
        markdown = str(raw.get("markdown") or "").strip()
        if not markdown:
            return None
        return {
            "markdown": markdown,
            "highFrequencyTopics": _normalize_high_frequency_topics(raw.get("highFrequencyTopics")),
        }
    return None


def _previous_period_range(start_date: date, end_date: date) -> tuple[date, date]:
    if _is_full_month(start_date, end_date):
        previous_end = date.fromordinal(start_date.toordinal() - 1)
        return previous_end.replace(day=1), previous_end
    days = (end_date - start_date).days + 1
    previous_end = date.fromordinal(start_date.toordinal() - 1)
    previous_start = date.fromordinal(previous_end.toordinal() - days + 1)
    return previous_start, previous_end


def _period_projection_context(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    page_projection: dict | None = None,
) -> dict:
    asset_projection = read_period_projection(paths, start_date, end_date, projection_type=LEGACY_ASSET_PROJECTION)
    page_projection = page_projection or read_period_projection(
        paths,
        start_date,
        end_date,
        projection_type=DIARY_PERIOD_PAGE_PROJECTION,
    )
    asset_metrics = (asset_projection or {}).get("metrics") or {}
    page_metrics = (page_projection or {}).get("metrics") or {}
    return {
        "period": {
            "startDate": start_date.isoformat(),
            "endDate": end_date.isoformat(),
            "days": (end_date - start_date).days + 1,
            "label": _period_label(start_date, end_date, language_profile=_language_profile(paths)),
        },
        "available": asset_projection is not None or page_projection is not None,
        "kpi": asset_metrics.get("kpi") or {},
        "dailyTokenSeries": asset_metrics.get("dailyTokenSeries") or [],
        "workspaceUsage": asset_metrics.get("workspaceUsage") or [],
        "models": asset_metrics.get("models") or [],
        "assetHourlyHeatmap": asset_metrics.get("assetHourlyHeatmap") or {},
        "taskStats": asset_metrics.get("taskStats") or {},
        "cronStats": asset_metrics.get("cronStats") or {},
        "topics": (page_metrics.get("summaryTopics") or [])[:20],
        "lessons": (page_metrics.get("lessons") or [])[:20],
    }


def _fallback_summary_markdown(label: str, lead: str, highlights: list[str], lesson_lines: list[str], *, language_profile: str = "zh") -> str:
    labels = _labels(language_profile)
    overview_title = labels["overview"]
    lines = [
        f"## {overview_title}",
        "",
        lead,
        "",
        f"## {labels['data_comparison']}",
        "",
        f"- {labels['saved_snapshot']}",
        "",
        f"## {labels['workspace_distribution']}",
        "",
        f"- {labels['workspace_hint']}",
        "",
        f"## {labels['workload']}",
        "",
        f"- {labels['workload_hint']}",
        "",
        f"## {labels['progress']}",
        "",
    ]
    lines.extend([f"- {item}" for item in highlights] or [f"- {labels['no_topics']}"])
    lines.extend(["", f"## {labels['risks']}", ""])
    lines.extend([f"- {item}" for item in lesson_lines] or [f"- {labels['no_lessons']}"])
    lines.extend(
        [
            "",
            f"## {labels['care']}",
            "",
            f"- {labels['care_line']}",
            f"- {labels['care_quote']}",
            "",
            f"## {labels['sources']}",
            "",
            f"- {labels['source_line']}",
        ]
    )
    return "\n".join(lines)


def _markdown_lead(markdown: str, fallback: str) -> str:
    for line in str(markdown or "").splitlines():
        text = line.strip()
        if not text or text.startswith("#") or text.startswith(">") or text.startswith("```"):
            continue
        return text.lstrip("-*0123456789.、) ").strip() or fallback
    return fallback


def build_period_summary_payload(paths: RuntimePaths, start_date: date, end_date: date) -> dict:
    """Build a deterministic, non-LLM summary from the period page snapshot."""
    page_projection = read_period_projection(
        paths,
        start_date,
        end_date,
        projection_type=DIARY_PERIOD_PAGE_PROJECTION,
    )
    if page_projection is None:
        raise ValueError("diary period page projection is missing")

    page = page_projection.get("metrics") or {}
    summary_topics = page.get("summaryTopics") or []
    lessons = page.get("lessons") or []
    topics = _top_topics(summary_topics)
    lesson_items = _top_lessons(lessons)
    language_profile = _language_profile(paths)
    labels = _labels(language_profile)
    label = _period_label(start_date, end_date, language_profile=language_profile)

    if topics:
        lead = (
            f"{label} captured {len(summary_topics)} tasks and outcomes, with focus around {topics[0]['title']}."
            if language_profile == "en"
            else f"{label}共沉淀 {len(summary_topics)} 条任务与成果，重点集中在{topics[0]['title']}等方向。"
        )
    else:
        lead = (
            f"{label} has not yet produced enough task or outcome data from diary page snapshots."
            if language_profile == "en"
            else f"{label}尚未从日记页面快照中提取到可汇总的任务与成果。"
        )
    if lesson_items:
        lead += (
            f" {len(lessons)} lessons were also recorded for follow-up review."
            if language_profile == "en"
            else f" 同期记录 {len(lessons)} 条教训与经验，可作为后续复盘输入。"
        )

    highlights = []
    for topic in topics:
        detail = "；".join(topic["items"]) if topic["items"] else "见来源日记条目"
        highlights.append(f"{topic['title']}: {detail}" if language_profile == "en" else f"{topic['title']}：{detail}")

    lesson_lines = []
    for lesson in lesson_items:
        if lesson["suggestion"]:
            lesson_lines.append(
                f"{lesson['agent'] or 'unknown'}: {lesson['problem']}; recommendation: {lesson['suggestion']}"
                if language_profile == "en"
                else f"{lesson['agent'] or 'unknown'}：{lesson['problem']}；建议：{lesson['suggestion']}"
            )
        else:
            lesson_lines.append(f"{lesson['agent'] or 'unknown'}: {lesson['problem']}" if language_profile == "en" else f"{lesson['agent'] or 'unknown'}：{lesson['problem']}")

    payload = {
        "projection": DIARY_PERIOD_SUMMARY_PROJECTION,
        "languageProfile": language_profile,
        "period": f"{start_date.isoformat()} ~ {end_date.isoformat()}",
        "sourceProjection": DIARY_PERIOD_PAGE_PROJECTION,
        "sourceGeneratedAt": page_projection.get("generatedAt"),
        "topicCount": len(summary_topics),
        "lessonCount": len(lessons),
        "summary": {
            "title": f"{label}{labels['summary_suffix']}",
            "lead": lead,
            "highlights": highlights,
            "lessons": lesson_lines,
            "markdown": _fallback_summary_markdown(label, lead, highlights, lesson_lines, language_profile=language_profile),
        },
        "sources": {
            "topics": topics,
            "lessons": lesson_items,
        },
    }
    payload["insightContext"] = build_period_insight_context(paths, start_date, end_date, page_projection=page_projection)
    return payload


def build_period_insight_context(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    page_projection: dict | None = None,
) -> dict:
    current = _period_projection_context(paths, start_date, end_date, page_projection=page_projection)
    prior_start, prior_end = _previous_period_range(start_date, end_date)
    previous = _period_projection_context(paths, prior_start, prior_end)
    return {
        "contextDescription": "Personal AI tool usage and work-review data from local dashboard aggregates, project usage, task stats, and diary-derived topics.",
        "period": current["period"],
        "comparisonPeriod": {**previous["period"], "available": previous["available"]},
        "currentPeriod": current,
        "previousPeriod": previous,
        "kpi": current["kpi"],
        "previousKpi": previous["kpi"],
        "dailyTokenSeries": current["dailyTokenSeries"],
        "workspaceUsage": current["workspaceUsage"],
        "models": current["models"],
        "assetHourlyHeatmap": current["assetHourlyHeatmap"],
        "taskStats": current["taskStats"],
        "cronStats": current["cronStats"],
        "topics": current["topics"],
        "lessons": current["lessons"],
    }


def generate_period_summary_markdown(context: dict, paths: RuntimePaths | None = None) -> str | None:
    result = generate_period_summary_result(context, paths)
    return result["markdown"] if result else None


def generate_period_summary_result(context: dict, paths: RuntimePaths | None = None) -> PeriodSummaryResult | None:
    provider = resolve_llm_provider(paths, redact_secrets=False)
    if not provider.get("apiKey") or not provider.get("endpoint") or not provider.get("model"):
        return None
    language_profile = _language_profile(paths)
    labels = _labels(language_profile)
    if language_profile == "en":
        system = "You are a personal AI tool usage and work-review assistant. Output only a valid JSON object with no preface."
        prompt_lines = [
            "Use the personal AI tool usage and work-review data below to generate an English weekly or monthly report and extract high-frequency themes for the current period.",
            "currentPeriod is the current period data; previousPeriod is the previous week or month and should be used for comparison.",
            "Output only a JSON object with this structure:",
            '{"markdown":"## Period Overview\\n...","highFrequencyTopics":[{"topic":"Theme name","count":3,"reason":"Evidence used"}]}',
            "The markdown field must use these level-2 headings in this exact order:",
            f"## {labels['overview']}",
            f"## {labels['data_comparison']}",
            f"## {labels['workspace_distribution']}",
            f"## {labels['workload']}",
            f"## {labels['progress']}",
            f"## {labels['risks']}",
            f"## {labels['care']}",
            f"## {labels['sources']}",
            "Writing requirements:",
            "- Data and comparison: compare current and previous tokens, messages, project investment, model usage, task stats, and scheduled job data; explicitly say data is missing when unavailable.",
            "- Project investment: use only workspaceUsage and topics; do not invent project names.",
            "- Workload and late-hour focus: discuss peak days, consecutive effort, night/early-morning heatmap patterns, and recovery suggestions without moralizing.",
            "- Progress and challenges: summarize real achievements, solved problems, and remaining blockers from topics and lessons.",
            "- Care and encouragement: provide humane pacing advice and one short encouraging quote or maxim; do not fabricate attribution.",
            "- Sources and gaps: explain that the judgment comes from dashboard aggregation snapshots, project usage, task stats, diary topics, and lessons; list missing data that affects confidence.",
            "- highFrequencyTopics: infer from currentPeriod workspaceUsage, topics, lessons, taskStats, and model/token data; do not simply reuse daily summaryTopics counts.",
            "- highFrequencyTopics.count is evidence strength or coverage count, as an integer from 1 to 9; reason should be one sentence.",
            "- Return at most 8 high-frequency topics, ordered by importance in the current period.",
            "- Stay factual; do not invent projects, numbers, models, or tasks that are not in the input.",
            "- markdown may use only level-2 headings, level-3 headings, paragraphs, and lists.",
        ]
    else:
        system = "你是一个个人 AI 工具使用与工作复盘助手。只输出合法 JSON 对象，不要前言。"
        prompt_lines = [
            "请基于下面的个人 AI 工具使用与工作复盘数据生成一份中文周报或月报，并提炼本周期高频主题。",
            "输入中的 currentPeriod 是本周期数据；previousPeriod 是上周或上月数据，用于环比参考。",
            "必须只输出 JSON 对象，结构如下：",
            '{"markdown":"## 本周期总览\\n...","highFrequencyTopics":[{"topic":"主题名","count":3,"reason":"基于哪些数据判断"}]}',
            "markdown 字段必须使用以下二级标题，且顺序固定：",
            "## 本周期总览",
            "## 数据与环比",
            "## 项目投入分布",
            "## 工作强度与深夜投入",
            "## 主要进展与难题",
            "## 风险与下周期建议",
            "## 关怀与鼓励",
            "## 数据来源与缺口",
            "写作要求：",
            "- 数据与环比：比较本周期与上周或上月的 tokens、messages、项目投入、模型使用、任务与定时任务数据；缺失时明确写“数据缺失”。",
            "- 项目投入分布：只基于 workspaceUsage 和 topics，不要编造项目名。",
            "- 工作强度与深夜投入：合并讨论投入峰值日、连续投入、夜间/凌晨热力分布与恢复建议，避免道德化评价。",
            "- 主要进展与难题：基于 topics 和 lessons，总结真实 achievement、攻克的问题和仍未解决的阻塞。",
            "- 关怀与鼓励：基于本周期 achievement 与工作强度，给出人性化关怀、节奏建议，并附一句简短鼓励性名言或格言；不要强行署名，不要编造出处。",
            "- 数据来源与缺口：说明判断来自 dashboard 聚合快照、项目使用、任务统计、日记主题与 lessons；列出影响判断的缺失数据。",
            "- highFrequencyTopics：由你基于 currentPeriod 的 workspaceUsage、topics、lessons、taskStats 和模型/Token 数据综合提炼，不要复用旧的每日 summaryTopics 聚合计数。",
            "- highFrequencyTopics.count 表示证据强度或覆盖次数，使用 1-9 的整数；reason 用一句话说明依据。",
            "- 高频主题最多 8 个，按本周期重要性排序。",
            "- 保持事实优先，不编造输入中没有的项目、数字、模型或任务。",
            "- markdown 只使用二级标题、三级标题、段落和列表。",
        ]
    prompt_lines.extend(["", json.dumps(context, ensure_ascii=False, sort_keys=True)])
    prompt = "\n".join(prompt_lines)
    sender = send_anthropic_message if provider.get("api") == "anthropic-messages" else send_openai_compatible_message
    raw = sender(
        endpoint=provider["endpoint"],
        api_key=provider["apiKey"],
        model=provider["model"],
        system=system,
        prompt=prompt,
        temperature=0.2,
        max_tokens=8192,
        timeout=int(provider.get("timeoutSeconds") or DEFAULT_LLM_TIMEOUT_SECONDS),
    ).strip()
    parsed = parse_llm_json_object(raw).data
    return _coerce_period_summary_result(parsed)


def write_period_summary_markdown(paths: RuntimePaths, start_date: date, end_date: date, markdown: str) -> Path:
    output = period_report_path(paths.diary_dir, start_date, end_date, label=_report_label(start_date, end_date, language_profile="zh"))
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(markdown.rstrip() + "\n", encoding="utf-8")
    return output


def materialize_period_summary_snapshot(
    paths: RuntimePaths,
    start_date: date,
    end_date: date,
    *,
    source_run_id: int | None,
    generator: PeriodSummaryGenerator | None = None,
) -> str:
    payload = build_period_summary_payload(paths, start_date, end_date)
    summary_result = None
    llm_error = None
    try:
        if generator is not None:
            summary_result = _coerce_period_summary_result(generator(payload["insightContext"]))
        else:
            summary_result = generate_period_summary_result(payload["insightContext"], paths)
    except Exception as exc:
        llm_error = str(exc)
    if summary_result:
        markdown = summary_result["markdown"]
        high_frequency_topics = summary_result.get("highFrequencyTopics") or []
        payload["summary"]["markdown"] = markdown
        payload["summary"]["lead"] = _markdown_lead(markdown, payload["summary"]["lead"])
        payload["summary"]["highFrequencyTopics"] = high_frequency_topics
        payload["highFrequencyTopics"] = high_frequency_topics
        payload["generation"] = {"mode": "llm", "llmError": None}
    else:
        markdown = payload["summary"]["markdown"]
        payload["summary"]["highFrequencyTopics"] = []
        payload["highFrequencyTopics"] = []
        payload["generation"] = {"mode": "deterministic", "llmError": llm_error}
    output = write_period_summary_markdown(paths, start_date, end_date, markdown)
    payload["summary"]["markdownPath"] = str(output)
    return write_period_projection(
        paths,
        start_date,
        end_date,
        payload,
        source_run_id=source_run_id,
        projection_type=DIARY_PERIOD_SUMMARY_PROJECTION,
    )
