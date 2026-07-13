"""Deterministic Agentic RAG response helpers.

This layer is intentionally extractive and read-only. It turns ranked retrieval
results into a stable evidence contract for Dashboard, CLI and external agents
without calling an LLM or mutating indexes.
"""

from __future__ import annotations

import re
from typing import Any


MAX_EXCERPT_CHARS = 280


def attach_agentic_context(
    *,
    query: str,
    ranked: dict[str, Any],
    max_citations: int = 5,
    language_profile: str | None = None,
) -> dict[str, Any]:
    results = list(ranked.get("results") or [])
    query_plan = dict(ranked.get("queryPlan") or {})
    citation_pack = build_citation_pack(query=query, results=results, max_citations=max_citations)
    decomposition = build_query_decomposition(query=query, query_plan=query_plan, citation_pack=citation_pack)
    event_aggregation = build_event_aggregation(
        query=query,
        query_plan=query_plan,
        citation_pack=citation_pack,
    )
    synthesis = build_answer_synthesis(
        query=query,
        citation_pack=citation_pack,
        decomposition=decomposition,
        event_aggregation=event_aggregation,
        language_profile=language_profile,
    )
    quality = ranked.get("quality") if isinstance(ranked.get("quality"), dict) else {}
    retrieval_controller = (
        ranked.get("retrievalController") if isinstance(ranked.get("retrievalController"), dict) else {}
    )
    agentic = {
        "schemaVersion": 2,
        "version": 2,
        "mode": "deterministic-extractive-bounded-multi-pass",
        "implementedCapabilities": [
            "intent-planning",
            "hybrid-retrieval",
            "exact-entity-recall",
            "recency-aware-ranking",
            "metadata-aware-ranking",
            "bounded-multi-pass-retrieval",
            "quality-gate",
            "optional-reranker-policy",
            "citation-pack",
            "query-decomposition",
            "multi-hop-evidence-linking",
            "event-aggregation",
            "extractive-answer-synthesis",
        ],
        "limits": [
            "no-llm-query-rewrite",
            "no-llm-answer-generation",
            "no-writeback-memory",
        ],
        "decomposition": decomposition,
        "eventAggregation": event_aggregation,
        "citationPack": citation_pack,
        "answerSynthesis": synthesis,
        "quality": quality,
        "retrievalController": retrieval_controller,
    }
    return {
        **ranked,
        "schemaVersion": 2,
        "available": True,
        "citationPack": citation_pack,
        "eventAggregation": event_aggregation,
        "answerSynthesis": synthesis,
        "agentic": agentic,
    }


def build_citation_pack(*, query: str, results: list[dict[str, Any]], max_citations: int = 5) -> list[dict[str, Any]]:
    query_terms = _terms(query)
    citations: list[dict[str, Any]] = []
    for rank, result in enumerate(results[: max(max_citations, 0)], start=1):
        text = str(result.get("text") or result.get("textPreview") or "")
        matched_terms = sorted(term for term in query_terms if term in text.lower())
        score = _float(result.get("score"), 0.0)
        confidence = "high" if score >= 0.75 else "medium" if score >= 0.35 else "low"
        citations.append(
            {
                "citationId": f"C{rank}",
                "rank": rank,
                "resultId": result.get("id"),
                "confidence": confidence,
                "score": score,
                "source": result.get("source") or "",
                "date": result.get("date") or "",
                "timestamp": result.get("timestamp") or "",
                "agent": result.get("agent") or result.get("role") or "",
                "project": result.get("project"),
                "tags": list(result.get("tags") or []),
                "workType": result.get("workType") or "general",
                "excerpt": _excerpt(text),
                "whySelected": _why_selected(result, matched_terms),
                "provenance": result.get("provenance") or {},
                "scoreComponents": result.get("scoreComponents") or {},
            }
        )
    return citations


def build_query_decomposition(
    *,
    query: str,
    query_plan: dict[str, Any],
    citation_pack: list[dict[str, Any]],
) -> dict[str, Any]:
    intents = list(query_plan.get("intents") or ["general"])
    preferred_tags = list(query_plan.get("preferredTags") or [])
    recency_bias = str(query_plan.get("recencyBias") or "normal")
    subqueries = [{"id": "Q0", "query": query, "purpose": "primary recall"}]
    if "incident" in intents:
        subqueries.append({"id": "Q1", "query": f"{query} error failure rollback fix", "purpose": "incident evidence"})
    if "coding" in intents:
        subqueries.append({"id": "Q2", "query": f"{query} code test implementation", "purpose": "coding evidence"})
    if "task" in intents:
        subqueries.append({"id": "Q3", "query": f"{query} task progress decision", "purpose": "task-state evidence"})
    if "lesson" in intents:
        subqueries.append({"id": "Q4", "query": f"{query} lesson decision outcome", "purpose": "lesson evidence"})
    if recency_bias == "strong":
        subqueries.append({"id": "Q5", "query": f"{query} latest recent", "purpose": "recent-memory evidence"})

    seen = set()
    deduped = []
    for item in subqueries:
        key = str(item["query"]).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)

    links = build_evidence_links(citation_pack)
    return {
        "strategy": "deterministic-intent-decomposition",
        "intents": intents,
        "preferredTags": preferred_tags,
        "recencyBias": recency_bias,
        "subqueries": deduped,
        "multiHopLinks": links,
        "executionPolicy": "bounded deterministic retrieval with exposed subqueries, exact-entity fusion, evidence linking and quality gate",
    }


def build_evidence_links(citation_pack: list[dict[str, Any]]) -> list[dict[str, Any]]:
    links: list[dict[str, Any]] = []
    for left_index, left in enumerate(citation_pack):
        for right in citation_pack[left_index + 1 :]:
            reasons = []
            if left.get("date") and left.get("date") == right.get("date"):
                reasons.append("same-date")
            shared_tags = sorted(set(left.get("tags") or []).intersection(right.get("tags") or []))
            if shared_tags:
                reasons.append("shared-tags:" + ",".join(shared_tags[:3]))
            if left.get("source") and left.get("source") == right.get("source"):
                reasons.append("same-source")
            if not reasons:
                continue
            links.append(
                {
                    "from": left.get("citationId"),
                    "to": right.get("citationId"),
                    "reasons": reasons,
                }
            )
            if len(links) >= 8:
                return links
    return links


def build_answer_synthesis(
    *,
    query: str,
    citation_pack: list[dict[str, Any]],
    decomposition: dict[str, Any],
    event_aggregation: dict[str, Any] | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    answer_type = _answer_type(query=query, decomposition=decomposition)
    if not citation_pack:
        return {
            "status": "no-evidence",
            "answerType": answer_type,
            "summary": _no_evidence_summary(language_profile),
            "bullets": [],
            "citations": [],
            "citationIds": [],
            "method": "extractive",
        }

    bullets = []
    for citation in citation_pack[:3]:
        bullets.append(
            {
                "text": citation["excerpt"],
                "citationId": citation["citationId"],
                "confidence": citation["confidence"],
            }
        )
    top = citation_pack[0]
    summary = _synthesis_summary(
        answer_type=answer_type,
        top=top,
        citation_pack=citation_pack,
        event_aggregation=event_aggregation or {},
        language_profile=language_profile,
    )
    citation_ids = [item["citationId"] for item in citation_pack[:3]]
    return {
        "status": "ready",
        "answerType": answer_type,
        "summary": summary,
        "bullets": bullets,
        "citations": citation_ids,
        "citationIds": citation_ids,
        "method": "extractive",
        "queryCoverage": {
            "subqueryCount": len(decomposition.get("subqueries") or []),
            "evidenceCount": len(citation_pack),
            "multiHopLinkCount": len(decomposition.get("multiHopLinks") or []),
            "eventCount": (event_aggregation or {}).get("eventCount"),
        },
    }


def _answer_type(*, query: str, decomposition: dict[str, Any]) -> str:
    intents = set(decomposition.get("intents") or [])
    preferred = set(decomposition.get("preferredTags") or [])
    query_lower = str(query or "").lower()
    if "aggregation" in intents or any(marker in query_lower for marker in ("几次", "多少次", "最严重")):
        return "event-aggregation"
    if "migration" in intents or "migration" in preferred or any(marker in query_lower for marker in ("迁移", "migration", "migrate")):
        return "migration"
    if "config" in intents or "config" in preferred or any(marker in query_lower for marker in ("端口", "配置", "config", "port", "不可用")):
        return "configuration"
    if "incident" in intents or any(marker in query_lower for marker in ("故障", "问题", "bug", "修复", "incident", "error")):
        return "incident"
    if "task" in intents:
        return "task-state"
    return "evidence-summary"


def _synthesis_summary(
    *,
    answer_type: str,
    top: dict[str, Any],
    citation_pack: list[dict[str, Any]],
    event_aggregation: dict[str, Any],
    language_profile: str | None = None,
) -> str:
    if _normalized_language_profile(language_profile) == "zh":
        return _synthesis_summary_zh(
            answer_type=answer_type,
            top=top,
            citation_pack=citation_pack,
            event_aggregation=event_aggregation,
        )
    event_count = int(event_aggregation.get("eventCount") or 0)
    if answer_type == "event-aggregation" and event_count:
        severe = event_aggregation.get("mostSevereEvent") if isinstance(event_aggregation.get("mostSevereEvent"), dict) else None
        if severe:
            return (
                f"Found {event_count} distinct event(s). Most severe evidence is "
                f"{','.join(severe.get('citationIds') or []) or top['citationId']}."
            )
        return f"Found {event_count} distinct event(s) across {len(citation_pack)} citation(s)."
    if answer_type == "incident" and event_count:
        resolution = event_aggregation.get("resolutionCitations") or []
        if resolution:
            return f"Incident evidence is linked to resolution citation(s): {', '.join(resolution[:3])}."
        return f"Incident evidence is supported by {event_count} distinct event(s)."
    if answer_type == "configuration":
        return f"Configuration evidence is {top['citationId']} from {top.get('date') or 'unknown date'}."
    if answer_type == "migration":
        return f"Migration evidence is {top['citationId']} from {top.get('date') or 'unknown date'}."
    if answer_type == "task-state":
        return f"Task-state evidence is {top['citationId']} from {top.get('date') or 'unknown date'}."
    return f"Top evidence is {top['citationId']} from {top.get('date') or 'unknown date'} with {top['confidence']} confidence."


def _synthesis_summary_zh(
    *,
    answer_type: str,
    top: dict[str, Any],
    citation_pack: list[dict[str, Any]],
    event_aggregation: dict[str, Any],
) -> str:
    event_count = int(event_aggregation.get("eventCount") or 0)
    top_date = top.get("date") or "未知日期"
    if answer_type == "event-aggregation" and event_count:
        severe = event_aggregation.get("mostSevereEvent") if isinstance(event_aggregation.get("mostSevereEvent"), dict) else None
        if severe:
            citation = ",".join(severe.get("citationIds") or []) or top["citationId"]
            return f"找到 {event_count} 个独立事件，最严重证据为 {citation}。"
        return f"找到 {event_count} 个独立事件，覆盖 {len(citation_pack)} 条引用。"
    if answer_type == "incident" and event_count:
        resolution = event_aggregation.get("resolutionCitations") or []
        if resolution:
            return f"故障证据已关联解决引用：{', '.join(resolution[:3])}。"
        return f"故障证据由 {event_count} 个独立事件支持。"
    if answer_type == "configuration":
        return f"配置证据为 {top['citationId']}，日期 {top_date}。"
    if answer_type == "migration":
        return f"迁移证据为 {top['citationId']}，日期 {top_date}。"
    if answer_type == "task-state":
        return f"任务状态证据为 {top['citationId']}，日期 {top_date}。"
    return f"首要证据为 {top['citationId']}，日期 {top_date}，置信度 {top['confidence']}。"


def _no_evidence_summary(language_profile: str | None) -> str:
    if _normalized_language_profile(language_profile) == "zh":
        return "没有匹配该查询的已索引证据。"
    return "No indexed evidence matched the query."


def _normalized_language_profile(language_profile: str | None) -> str:
    return "zh" if str(language_profile or "").strip().lower().startswith("zh") else "en"


def build_event_aggregation(
    *,
    query: str,
    query_plan: dict[str, Any],
    citation_pack: list[dict[str, Any]],
) -> dict[str, Any]:
    intents = set(query_plan.get("intents") or [])
    query_lower = str(query or "").lower()
    active = bool(
        intents.intersection({"incident", "aggregation", "config", "migration"})
        or any(marker in query_lower for marker in ("几次", "多少次", "最严重", "故障", "不可用", "迁移", "修复", "bug", "incident", "error"))
    )
    if not active:
        return {
            "schemaVersion": 1,
            "status": "not-applicable",
            "eventCount": 0,
            "events": [],
            "timeline": [],
            "mostSevereEvent": None,
            "resolutionCitations": [],
        }
    events = _dedupe_events(citation_pack)
    timeline = sorted(
        [
            {
                "eventId": event["eventId"],
                "date": event.get("date") or "",
                "citationIds": event["citationIds"],
                "summary": event["summary"],
            }
            for event in events
        ],
        key=lambda item: (item.get("date") or "", item.get("eventId") or ""),
    )
    most_severe = max(events, key=_event_severity_score, default=None)
    resolution_citations = [
        citation.get("citationId")
        for citation in citation_pack
        if _looks_like_resolution(str(citation.get("excerpt") or ""))
    ]
    return {
        "schemaVersion": 1,
        "status": "ready" if events else "no-events",
        "strategy": "citation-dedupe-timeline",
        "eventCount": len(events),
        "events": events,
        "timeline": timeline,
        "mostSevereEvent": most_severe,
        "resolutionCitations": [item for item in resolution_citations if item],
    }


def _dedupe_events(citation_pack: list[dict[str, Any]]) -> list[dict[str, Any]]:
    events: dict[str, dict[str, Any]] = {}
    for citation in citation_pack:
        key = _event_key(citation)
        current = events.setdefault(
            key,
            {
                "eventId": key,
                "date": citation.get("date") or "",
                "resultIds": [],
                "citationIds": [],
                "summary": citation.get("excerpt") or "",
                "maxScore": 0.0,
                "sources": [],
            },
        )
        if citation.get("resultId"):
            current["resultIds"].append(citation.get("resultId"))
        if citation.get("citationId"):
            current["citationIds"].append(citation.get("citationId"))
        if citation.get("source"):
            current["sources"].append(citation.get("source"))
        current["maxScore"] = max(_float(citation.get("score"), 0.0), _float(current.get("maxScore"), 0.0))
        if len(str(citation.get("excerpt") or "")) > len(str(current.get("summary") or "")):
            current["summary"] = citation.get("excerpt") or ""
    for event in events.values():
        event["resultIds"] = _unique_strings(event.get("resultIds") or [])
        event["citationIds"] = _unique_strings(event.get("citationIds") or [])
        event["sources"] = _unique_strings(event.get("sources") or [])
    return list(events.values())


def _event_key(citation: dict[str, Any]) -> str:
    provenance = citation.get("provenance") if isinstance(citation.get("provenance"), dict) else {}
    for value in (
        provenance.get("dedupeKey"),
        provenance.get("sourceId"),
        citation.get("resultId"),
    ):
        normalized = str(value or "").strip()
        if normalized:
            return normalized
    return f"{citation.get('date') or 'unknown'}:{str(citation.get('excerpt') or '')[:80]}"


def _event_severity_score(event: dict[str, Any]) -> float:
    text = str(event.get("summary") or "").lower()
    severity = _float(event.get("maxScore"), 0.0)
    if any(marker in text for marker in ("最严重", "严重", "critical", "major", "outage", "宕机")):
        severity += 0.4
    if any(marker in text for marker in ("故障", "失败", "不可用", "incident", "error")):
        severity += 0.2
    return severity


def _looks_like_resolution(text: str) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in ("解决", "恢复", "修复", "resolved", "fix", "fixed", "migration", "迁移"))


def _unique_strings(values: list[Any]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        text = str(value or "").strip()
        if not text or text in seen:
            continue
        seen.add(text)
        result.append(text)
    return result


def _why_selected(result: dict[str, Any], matched_terms: list[str]) -> list[str]:
    reasons = []
    components = result.get("scoreComponents") or {}
    if _float(components.get("dense"), 0.0) > 0:
        reasons.append("dense-similarity")
    if _float(components.get("exactCoverage"), 0.0) > 0:
        reasons.append("exact-entity-coverage")
    if _float(components.get("keyword"), 0.0) > 0 or matched_terms:
        reasons.append("keyword-overlap")
    if _float(components.get("recency"), 0.0) >= 0.5:
        reasons.append("recent-memory")
    if _float(components.get("intentBoost"), 1.0) > 1.0:
        reasons.append("intent-match")
    if matched_terms:
        reasons.append("matched:" + ",".join(matched_terms[:5]))
    return reasons or ["ranked-evidence"]


def _excerpt(text: str) -> str:
    clean = re.sub(r"\s+", " ", str(text)).strip()
    if len(clean) <= MAX_EXCERPT_CHARS:
        return clean
    return clean[: MAX_EXCERPT_CHARS - 3].rstrip() + "..."


def _terms(text: str) -> set[str]:
    return set(re.findall(r"[\u4e00-\u9fff]{2,8}|[a-zA-Z0-9_-]{3,30}", str(text).lower()))


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default
