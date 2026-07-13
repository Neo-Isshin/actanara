"""Shared RAG retrieval scoring helpers.

The retriever is intentionally model-agnostic: callers provide the query
embedding and already-loaded chunks. This keeps legacy JSONL reads and v2
storage decisions outside the scoring core.
"""

from __future__ import annotations

import math
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

try:
    from .rag_agentic import attach_agentic_context
    from .rag_memory_governance import governance_for_chunk
    from .rag_reranker import apply_reranker
except ImportError:  # pragma: no cover - direct script fallback
    from rag_agentic import attach_agentic_context  # type: ignore
    from rag_memory_governance import governance_for_chunk  # type: ignore
    from rag_reranker import apply_reranker  # type: ignore


DEFAULT_MIN_SIMILARITY = None
TECHNICAL_LAYER_BOOST = 1.2
INTENT_TAG_BOOST = 1.08
INTENT_WORK_TYPE_BOOST = 1.05
SUBQUERY_MATCH_BOOST = 0.12
EXACT_COVERAGE_WEIGHT = 0.42
NORMAL_RECENCY_TIE_BREAKER_WEIGHT = 0.06
STRONG_RECENCY_MIN_FACTOR = 0.35
META_DISCUSSION_SCORE_PENALTY = 0.55
AUTHORITATIVE_SOURCE_PASS_WEIGHT = 1.08
EXACT_RECALL_PASS_WEIGHT = 1.05
REWRITE_PASS_WEIGHT = 1.02

META_DISCUSSION_MARKERS = (
    "召回质量",
    "召回失败",
    "弱召回",
    "检索质量",
    "质量硬化",
    "质量问题",
    "召回率",
    "真实索引",
    "真实查询",
    "真正证据",
    "真正包含",
    "内置 eval",
    "passrate",
    "benchmark",
    "top-1000",
    "top-20",
    "topk",
    "默认 top-5",
    "q0-q2",
    "exact/entity coverage",
    "quality gate",
    "quality hardening",
    "retrieval quality",
    "needsmoreevidence",
    "测试结果",
)
AUTHORITATIVE_FACT_SOURCE_SETS = {
    "lessons",
    "task-board-snapshot",
    "foundation-period-projections",
    "technical-report-task-events",
    "diary-markdown-sections",
    "diary-markdown-embedded-json",
}
AUTHORITATIVE_LIFECYCLES = {
    "canonical",
    "current-state",
    "period-summary",
    "task-history",
    "structured-report",
    "narrative",
}

KEY_TERM_MARKERS = (
    "问题",
    "端口",
    "不可用",
    "回环地址",
    "回环",
    "网络",
    "故障",
    "解决",
    "修复",
    "迁移",
    "配置",
    "精品",
    "旧",
    "看板",
    "任务",
    "决策",
    "复盘",
    "运维",
    "可恢复性",
    "publicbaseurl",
    "allowedorigins",
    "tailscale",
    "dashboard",
    "localhost",
    "host",
    "batch",
    "vps",
    "rag",
    "bug",
    "ip",
)
STOP_KEY_TERMS = {
    "什么",
    "多少",
    "几次",
    "为什么",
    "怎么",
    "是否",
    "之前",
    "当前",
    "最近",
    "最新",
    "today",
    "recent",
    "latest",
    "is",
    "are",
    "do",
    "does",
    "did",
    "can",
    "could",
    "should",
    "would",
    "please",
    "what",
    "why",
    "how",
    "was",
    "were",
    "the",
}


def tokenize(text: str) -> list[str]:
    return re.findall(r"[\u4e00-\u9fff]{2,8}|[a-zA-Z0-9_-]{3,30}", str(text).lower())


def cosine_similarity(a: Iterable[float], b: Iterable[float]) -> float:
    left = [float(x) for x in a]
    right = [float(y) for y in b]
    dot = sum(x * y for x, y in zip(left, right))
    norm_left = math.sqrt(sum(x * x for x in left))
    norm_right = math.sqrt(sum(y * y for y in right))
    return float(dot / (norm_left * norm_right + 1e-9))


def keyword_score(query_words: list[str], text: str) -> float:
    if not query_words:
        return 0.0
    text_lower = str(text).lower()
    hits = sum(1 for word in query_words if word in text_lower)
    return hits / max(len(query_words), 1)


def recency_score(date_value: Any, *, half_life_days: int, now: datetime | None = None) -> float:
    date_text = str(date_value or "")[:10]
    try:
        dt = datetime.strptime(date_text, "%Y-%m-%d")
    except ValueError:
        return 0.5
    reference = now or datetime.now()
    age_days = max((reference.replace(tzinfo=None) - dt).total_seconds() / 86400, 0.0)
    return math.exp(-0.693 * age_days / max(half_life_days, 1))


def infer_tags(chunk: dict[str, Any]) -> list[str]:
    tags = {str(tag).strip() for tag in chunk.get("tags", []) if str(tag).strip()}
    layer = str(chunk.get("layer") or "").lower()
    text = str(chunk.get("text") or "")
    text_lower = text.lower()

    if chunk.get("date"):
        tags.add("daily")
    if layer == "lesson":
        tags.update({"lesson", "decision"})
    if layer == "technical":
        tags.add("coding")
    if any(marker in text_lower for marker in ("traceback", "exception", "error:", "failed:", "incident")):
        tags.update({"incident", "coding"})
    if any(marker in text_lower for marker in ("故障", "报错", "失败", "不可用", "问题", "宕机", "恢复")):
        tags.add("incident")
    if any(marker in text_lower for marker in ("port", "端口", "config", "配置", "dns", "ip", "环境变量", "不可用")):
        tags.add("config")
    if any(marker in text_lower for marker in ("迁移", "migration", "migrate", "旧 vps", "精品 vps")):
        tags.add("migration")
    if any(marker in text_lower for marker in ("task", "todo", "done", "checkbox", "任务")):
        tags.add("task")
    if any(marker in text_lower for marker in ("def ", "class ", "pytest", "unittest", "src/", "git ", "代码", "测试")):
        tags.add("coding")
    if not tags:
        tags.add("general")
    return sorted(tags)


def infer_work_type(chunk: dict[str, Any], tags: list[str] | None = None) -> str:
    tag_set = set(tags if tags is not None else infer_tags(chunk))
    for candidate in ("incident", "config", "migration", "lesson", "task", "coding", "daily"):
        if candidate in tag_set:
            return candidate
    return "general"


def build_query_plan(
    query: str,
    *,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    role_filter: str | None = None,
    tag_filter: Iterable[str] | None = None,
    project_filter: str | None = None,
    source_set_filter: Iterable[str] | None = None,
    lifecycle_filter: Iterable[str] | None = None,
    work_type_filter: Iterable[str] | None = None,
) -> dict[str, Any]:
    query_lower = str(query or "").lower()
    explicit_tags = sorted({str(tag).strip() for tag in (tag_filter or []) if str(tag).strip()})
    explicit_source_sets = sorted({str(item).strip() for item in (source_set_filter or []) if str(item).strip()})
    explicit_lifecycles = sorted({str(item).strip() for item in (lifecycle_filter or []) if str(item).strip()})
    explicit_work_types = sorted({str(item).strip() for item in (work_type_filter or []) if str(item).strip()})
    preferred_tags: set[str] = set(explicit_tags)
    intents: set[str] = set()

    if any(marker in query_lower for marker in ("traceback", "exception", "error", "failed", "incident", "失败", "报错", "故障")):
        intents.add("incident")
        preferred_tags.update({"incident", "coding"})
    if any(marker in query_lower for marker in ("问题", "几次", "多少次", "最严重", "怎么解决", "解决", "恢复")):
        intents.add("incident")
        intents.add("aggregation")
        preferred_tags.add("incident")
    if any(
        marker in query_lower
        for marker in (
            "port",
            "端口",
            "config",
            "配置",
            "dns",
            "ip",
            "不可用",
            "网络",
            "回环地址",
            "回环",
            "tailscale",
            "publicbaseurl",
            "allowedorigins",
            "localhost",
        )
    ):
        intents.add("config")
        preferred_tags.add("config")
    if any(marker in query_lower for marker in ("迁移", "migration", "migrate", "旧 vps", "精品 vps")):
        intents.add("migration")
        preferred_tags.add("migration")
    if any(marker in query_lower for marker in ("code", "coding", "pytest", "unittest", "src/", "bug", "代码", "测试", "实现")):
        intents.add("coding")
        preferred_tags.add("coding")
    if any(marker in query_lower for marker in ("task", "todo", "任务", "看板", "进度")):
        intents.add("task")
        preferred_tags.add("task")
    if any(marker in query_lower for marker in ("lesson", "decision", "经验", "教训", "决策", "复盘")):
        intents.add("lesson")
        preferred_tags.update({"lesson", "decision"})
    if any(marker in query_lower for marker in ("diary", "daily", "today", "yesterday", "今天", "昨天", "日记", "日常")):
        intents.add("daily")
        preferred_tags.add("daily")

    recency_bias = "strong" if any(
        marker in query_lower for marker in ("latest", "recent", "today", "yesterday", "最近", "最新", "今天", "昨天")
    ) else "normal"
    if not intents:
        intents.add("general")
        preferred_tags.add("general")
    sub_queries = _decompose_query_terms(query, intents=sorted(intents), preferred_tags=sorted(preferred_tags))

    return {
        "schemaVersion": 2,
        "version": 2,
        "strategy": "bounded-multi-pass-dense-keyword-exact-intent-reranker-agentic",
        "intents": sorted(intents),
        "preferredTags": sorted(preferred_tags),
        "subQueries": sub_queries,
        "explicitFilters": {
            "date": date_filter,
            "dateFrom": date_from,
            "dateTo": date_to,
            "role": role_filter,
            "project": project_filter,
            "tags": explicit_tags,
            "sourceSets": explicit_source_sets,
            "lifecycles": explicit_lifecycles,
            "workTypes": explicit_work_types,
        },
        "recencyBias": recency_bias,
        "stages": [
            "query-decomposition",
            "dense-vector-search",
            "exact-entity-recall",
            "server-side-requery",
            "authoritative-source-pass",
            "meta-discussion-suppression",
            "multi-hop-lexical-expansion",
            "subquery-match-boost",
            "keyword-overlap",
            "intent-aware-recency",
            "intent-tag-boost",
            "quality-gate",
            "multi-pass-fusion",
            "reranker-policy",
            "citation-pack",
            "extractive-answer-synthesis",
        ],
        "filterPolicy": "only explicit filters are hard filters; inferred intent and recency are soft boosts",
    }


def build_search_result(
    *,
    chunk: dict[str, Any],
    dense_score: float,
    keyword: float,
    recency: float,
    intent_boost: float,
    subquery_match: float,
    subquery_boost: float,
    exact_coverage: float,
    citable_exact_coverage: float = 0.0,
    recency_factor: float,
    lexical: float,
    meta_discussion: float,
    evidence_authority: float,
    governance: dict[str, Any],
    final_score: float,
) -> dict[str, Any]:
    tags = infer_tags(chunk)
    source = chunk.get("source") or chunk.get("agent") or chunk.get("layer") or ""
    timestamp = chunk.get("timestamp") or chunk.get("date") or ""
    text = str(chunk.get("text") or "")
    source_set = chunk.get("sourceSet")
    source_type = chunk.get("sourceType")
    source_id = chunk.get("sourceId")
    source_path = chunk.get("sourcePath")
    return {
        "id": chunk.get("id"),
        "score": round(final_score, 6),
        "scoreComponents": {
            "dense": round(dense_score, 6),
            "keyword": round(keyword, 6),
            "recency": round(recency, 6),
            "recencyFactor": round(recency_factor, 6),
            "layerBoost": _layer_boost(chunk),
            "intentBoost": round(intent_boost, 6),
            "subQueryMatch": round(subquery_match, 6),
            "subQueryBoost": round(subquery_boost, 6),
            "lexical": round(lexical, 6),
            "exactCoverage": round(exact_coverage, 6),
            "retrievalExactCoverage": round(exact_coverage, 6),
            "citableExactCoverage": round(citable_exact_coverage, 6),
            "metaDiscussion": round(meta_discussion, 6),
            "evidenceAuthority": round(evidence_authority, 6),
            "governanceWeight": round(float(governance.get("retrievalWeight") or 1.0), 6),
            "provenanceScore": round(float(governance.get("provenanceScore") or 0.0), 6),
            "authorityRank": int(governance.get("authorityRank") or 0),
        },
        "text": text,
        "textPreview": text[:500],
        "role": chunk.get("role") or chunk.get("agent") or "",
        "timestamp": str(timestamp)[:16].replace("T", " "),
        "date": chunk.get("date", ""),
        "source": Path(str(source)).name if source else "",
        "sourceSet": source_set,
        "sourceType": source_type,
        "sourceId": source_id,
        "agent": chunk.get("agent"),
        "project": chunk.get("project"),
        "tags": tags,
        "workType": infer_work_type(chunk, tags),
        "governance": governance,
        "provenance": {
            "indexId": chunk.get("id"),
            "layer": chunk.get("layer"),
            "source": str(source) if source else None,
            "sourceSet": source_set,
            "sourceType": source_type,
            "sourceId": source_id,
            "sourcePath": source_path,
            "dedupeKey": chunk.get("dedupeKey"),
        },
    }


def rank_chunks(
    *,
    query: str,
    query_embedding: list[float],
    chunks: list[dict[str, Any]],
    top_k: int,
    similarity_weight: float,
    keyword_weight: float,
    recency_half_life_days: int,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    role_filter: str | None = None,
    tag_filter: Iterable[str] | None = None,
    project_filter: str | None = None,
    source_set_filter: Iterable[str] | None = None,
    lifecycle_filter: Iterable[str] | None = None,
    work_type_filter: Iterable[str] | None = None,
    min_similarity: float | None = DEFAULT_MIN_SIMILARITY,
    now: datetime | None = None,
    reranker_policy: dict[str, Any] | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    query_dim = len(query_embedding)
    query_plan = build_query_plan(
        query,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        role_filter=role_filter,
        tag_filter=tag_filter,
        project_filter=project_filter,
        source_set_filter=source_set_filter,
        lifecycle_filter=lifecycle_filter,
        work_type_filter=work_type_filter,
    )
    query_words = _expanded_query_words(query_plan)
    key_terms = _quality_key_terms(query_plan)
    required_tags = {str(tag).strip() for tag in (tag_filter or []) if str(tag).strip()}
    required_source_sets = {str(item).strip() for item in (source_set_filter or []) if str(item).strip()}
    required_lifecycles = {str(item).strip() for item in (lifecycle_filter or []) if str(item).strip()}
    required_work_types = {str(item).strip() for item in (work_type_filter or []) if str(item).strip()}
    results: list[dict[str, Any]] = []
    skipped_dimension = 0
    filtered = 0

    for chunk in chunks:
        if date_filter and chunk.get("date") != date_filter:
            filtered += 1
            continue
        if not _date_in_range(chunk.get("date"), date_from=date_from, date_to=date_to):
            filtered += 1
            continue
        if role_filter and (chunk.get("role") or chunk.get("agent")) != role_filter:
            filtered += 1
            continue
        if project_filter and chunk.get("project") != project_filter:
            filtered += 1
            continue
        if required_source_sets and str(chunk.get("sourceSet") or "") not in required_source_sets:
            filtered += 1
            continue

        tags = infer_tags(chunk)
        if required_tags and required_tags.isdisjoint(tags):
            filtered += 1
            continue
        governance = _chunk_governance(chunk)
        if required_lifecycles and str(governance.get("lifecycle") or "") not in required_lifecycles:
            filtered += 1
            continue
        if required_work_types and infer_work_type(chunk, tags) not in required_work_types:
            filtered += 1
            continue

        embedding = chunk.get("embedding") or []
        if len(embedding) != query_dim:
            skipped_dimension += 1
            continue

        dense = _finite_score(cosine_similarity(query_embedding, embedding))
        scored = score_chunk(
            query_words=query_words,
            chunk=chunk,
            dense_score=dense,
            similarity_weight=similarity_weight,
            keyword_weight=keyword_weight,
            recency_half_life_days=recency_half_life_days,
            min_similarity=min_similarity,
            now=now,
            query_plan=query_plan,
            key_terms=key_terms,
        )
        if scored is None:
            filtered += 1
            continue
        results.append(scored)

    return _finalize_ranked_response(
        query=query,
        total_indexed=len(chunks),
        top_k=top_k,
        results=results,
        query_plan=query_plan,
        skipped_dimension=skipped_dimension,
        filtered=filtered,
        explicit_filter_count=sum(
            1
            for value in (
                date_filter,
                date_from,
                date_to,
                role_filter,
                project_filter,
                list(tag_filter or []),
                list(source_set_filter or []),
                list(lifecycle_filter or []),
                list(work_type_filter or []),
            )
            if value
        ),
        reranker_policy=reranker_policy,
        language_profile=language_profile,
    )


def rank_scored_chunks(
    *,
    query: str,
    chunks: list[dict[str, Any]],
    dense_scores: Iterable[float],
    top_k: int,
    similarity_weight: float,
    keyword_weight: float,
    recency_half_life_days: int,
    date_filter: str | None = None,
    date_from: str | None = None,
    date_to: str | None = None,
    role_filter: str | None = None,
    tag_filter: Iterable[str] | None = None,
    project_filter: str | None = None,
    source_set_filter: Iterable[str] | None = None,
    lifecycle_filter: Iterable[str] | None = None,
    work_type_filter: Iterable[str] | None = None,
    min_similarity: float | None = DEFAULT_MIN_SIMILARITY,
    now: datetime | None = None,
    reranker_policy: dict[str, Any] | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    query_plan = build_query_plan(
        query,
        date_filter=date_filter,
        date_from=date_from,
        date_to=date_to,
        role_filter=role_filter,
        tag_filter=tag_filter,
        project_filter=project_filter,
        source_set_filter=source_set_filter,
        lifecycle_filter=lifecycle_filter,
        work_type_filter=work_type_filter,
    )
    query_words = _expanded_query_words(query_plan)
    key_terms = _quality_key_terms(query_plan)
    required_tags = {str(tag).strip() for tag in (tag_filter or []) if str(tag).strip()}
    required_source_sets = {str(item).strip() for item in (source_set_filter or []) if str(item).strip()}
    required_lifecycles = {str(item).strip() for item in (lifecycle_filter or []) if str(item).strip()}
    required_work_types = {str(item).strip() for item in (work_type_filter or []) if str(item).strip()}
    results: list[dict[str, Any]] = []
    filtered = 0

    for chunk, dense in zip(chunks, dense_scores):
        if date_filter and chunk.get("date") != date_filter:
            filtered += 1
            continue
        if not _date_in_range(chunk.get("date"), date_from=date_from, date_to=date_to):
            filtered += 1
            continue
        if role_filter and (chunk.get("role") or chunk.get("agent")) != role_filter:
            filtered += 1
            continue
        if project_filter and chunk.get("project") != project_filter:
            filtered += 1
            continue
        if required_source_sets and str(chunk.get("sourceSet") or "") not in required_source_sets:
            filtered += 1
            continue
        tags = infer_tags(chunk)
        if required_tags and required_tags.isdisjoint(tags):
            filtered += 1
            continue
        governance = _chunk_governance(chunk)
        if required_lifecycles and str(governance.get("lifecycle") or "") not in required_lifecycles:
            filtered += 1
            continue
        if required_work_types and infer_work_type(chunk, tags) not in required_work_types:
            filtered += 1
            continue
        scored = score_chunk(
            query_words=query_words,
            chunk=chunk,
            dense_score=_finite_score(dense),
            similarity_weight=similarity_weight,
            keyword_weight=keyword_weight,
            recency_half_life_days=recency_half_life_days,
            min_similarity=min_similarity,
            now=now,
            query_plan=query_plan,
            key_terms=key_terms,
        )
        if scored is None:
            filtered += 1
            continue
        results.append(scored)

    return _finalize_ranked_response(
        query=query,
        total_indexed=len(chunks),
        top_k=top_k,
        results=results,
        query_plan=query_plan,
        filtered=filtered,
        explicit_filter_count=sum(
            1
            for value in (
                date_filter,
                date_from,
                date_to,
                role_filter,
                project_filter,
                list(tag_filter or []),
                list(source_set_filter or []),
                list(lifecycle_filter or []),
                list(work_type_filter or []),
            )
            if value
        ),
        reranker_policy=reranker_policy,
        language_profile=language_profile,
    )


def score_chunk(
    *,
    query_words: list[str],
    chunk: dict[str, Any],
    dense_score: float,
    similarity_weight: float,
    keyword_weight: float,
    recency_half_life_days: int,
    min_similarity: float | None = DEFAULT_MIN_SIMILARITY,
    now: datetime | None = None,
    query_plan: dict[str, Any] | None = None,
    key_terms: Iterable[str] | None = None,
) -> dict[str, Any] | None:
    dense_score = _finite_score(dense_score)
    if min_similarity is not None and dense_score < min_similarity:
        return None
    keyword = keyword_score(query_words, str(chunk.get("text") or ""))
    subquery_match = _subquery_match_score(query_plan, str(chunk.get("text") or ""))
    subquery_boost = 1.0 + (subquery_match * SUBQUERY_MATCH_BOOST)
    recency = recency_score(chunk.get("date"), half_life_days=recency_half_life_days, now=now)
    lexical = max(keyword, subquery_match)
    exact_coverage = _key_term_coverage(key_terms or [], _searchable_chunk_text(chunk))
    citable_exact_coverage = _key_term_coverage(key_terms or [], _citable_chunk_text(chunk))
    fused = dense_score * similarity_weight + lexical * keyword_weight + exact_coverage * EXACT_COVERAGE_WEIGHT
    recency_factor = _recency_factor(query_plan, recency)
    intent_boost = _intent_boost(chunk, query_plan)
    governance = _chunk_governance(chunk)
    meta_discussion = _meta_discussion_score(_searchable_chunk_text(chunk), query_plan)
    evidence_authority = _evidence_authority_score(chunk, governance)
    meta_factor = 1.0 - (meta_discussion * (1.0 - META_DISCUSSION_SCORE_PENALTY))
    final_score = (
        fused
        * recency_factor
        * _layer_boost(chunk)
        * intent_boost
        * subquery_boost
        * meta_factor
        * (1.0 + (evidence_authority * 0.04))
        * float(governance.get("retrievalWeight") or 1.0)
    )
    return build_search_result(
        chunk=chunk,
        dense_score=dense_score,
        keyword=keyword,
        recency=recency,
        intent_boost=intent_boost,
        subquery_match=subquery_match,
        subquery_boost=subquery_boost,
        exact_coverage=exact_coverage,
        citable_exact_coverage=citable_exact_coverage,
        recency_factor=recency_factor,
        lexical=lexical,
        meta_discussion=meta_discussion,
        evidence_authority=evidence_authority,
        governance=governance,
        final_score=final_score,
    )


def _finalize_ranked_response(
    *,
    query: str,
    total_indexed: int,
    top_k: int,
    results: list[dict[str, Any]],
    query_plan: dict[str, Any],
    skipped_dimension: int | None = None,
    filtered: int = 0,
    explicit_filter_count: int = 0,
    reranker_policy: dict[str, Any] | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    results.sort(key=lambda item: item["score"], reverse=True)
    deduped_results, dedupe_meta = dedupe_ranked_results(results)
    ranked = {
        "query": query,
        "totalIndexed": total_indexed,
        "returned": min(len(deduped_results), top_k),
        "filtered": filtered,
        "queryPlan": query_plan,
        "dedupe": dedupe_meta,
        "results": deduped_results[:top_k],
    }
    if skipped_dimension is not None:
        ranked["skippedDimension"] = skipped_dimension
    reranked = apply_reranker(query=query, ranked=ranked, policy=reranker_policy)
    final_results = list(reranked.get("results") or [])
    quality = evaluate_retrieval_quality(query_plan=query_plan, results=final_results)
    reranked["quality"] = quality
    reranked["retrievalController"] = _retrieval_controller_payload(
        query_plan=query_plan,
        scored_results=results,
        deduped_results=deduped_results,
        final_results=final_results,
        quality=quality,
        explicit_filter_count=explicit_filter_count,
    )
    return attach_agentic_context(query=query, ranked=reranked, language_profile=language_profile)


def build_retrieval_passes(query: str, query_plan: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Build bounded server-side recall passes for the embedding server."""
    plan = query_plan or build_query_plan(query)
    key_terms = _quality_key_terms(plan)
    passes: list[dict[str, Any]] = [
        {
            "id": "baseline-hybrid",
            "query": query,
            "weight": 1.0,
            "purpose": "primary dense/lexical recall",
        }
    ]
    exact_query = " ".join(key_terms[:8]).strip()
    if exact_query and exact_query.lower() != str(query or "").lower().strip():
        passes.append(
            {
                "id": "exact-entity-recall",
                "query": exact_query,
                "weight": EXACT_RECALL_PASS_WEIGHT,
                "purpose": "rare entity, number and config term recall",
            }
        )
    rewrite_query = _best_rewrite_query(plan)
    if rewrite_query and rewrite_query.lower() not in {str(query or "").lower().strip(), exact_query.lower()}:
        passes.append(
            {
                "id": "subquery-rewrite",
                "query": rewrite_query,
                "weight": REWRITE_PASS_WEIGHT,
                "purpose": "intent rewrite recall",
            }
        )
    authoritative_sets = _authoritative_source_sets_for_query(plan)
    if authoritative_sets and not _has_explicit_source_filter(plan):
        passes.append(
            {
                "id": "authoritative-source-pass",
                "query": exact_query or rewrite_query or query,
                "weight": AUTHORITATIVE_SOURCE_PASS_WEIGHT,
                "sourceSets": authoritative_sets,
                "purpose": "prefer durable facts and current-state sources",
            }
        )
    return passes[:4]


def fuse_ranked_passes(
    *,
    query: str,
    query_plan: dict[str, Any],
    ranked_passes: list[dict[str, Any]],
    total_indexed: int,
    top_k: int,
    reranker_policy: dict[str, Any] | None = None,
    language_profile: str | None = None,
) -> dict[str, Any]:
    """Fuse already-ranked pass results into one final response envelope."""
    fused_results: list[dict[str, Any]] = []
    pass_summaries: list[dict[str, Any]] = []
    for item in ranked_passes:
        pass_id = str(item.get("id") or "unknown-pass")
        ranked = item.get("ranked") if isinstance(item.get("ranked"), dict) else {}
        weight = _finite_score(item.get("weight")) or 1.0
        results = list(ranked.get("results") or [])
        pass_summaries.append(
            {
                "id": pass_id,
                "query": item.get("query"),
                "candidateCount": len(results),
                "returned": len(results),
                "sourceSets": list(item.get("sourceSets") or []),
                "purpose": item.get("purpose"),
            }
        )
        for result in results:
            merged = dict(result)
            components = dict(merged.get("scoreComponents") or {})
            components["passWeight"] = round(weight, 6)
            components["passId"] = pass_id
            merged["scoreComponents"] = components
            merged["retrievalPasses"] = sorted(set(list(merged.get("retrievalPasses") or []) + [pass_id]))
            merged["score"] = round(_finite_score(merged.get("score")) * weight, 6)
            fused_results.append(merged)

    fused_results.sort(key=lambda result: _finite_score(result.get("score")), reverse=True)
    fused_results = _merge_duplicate_pass_hits(fused_results)
    deduped_results, dedupe_meta = dedupe_ranked_results(fused_results)
    ranked = {
        "query": query,
        "totalIndexed": total_indexed,
        "returned": min(len(deduped_results), top_k),
        "filtered": sum(int((item.get("ranked") or {}).get("filtered") or 0) for item in ranked_passes),
        "queryPlan": query_plan,
        "dedupe": dedupe_meta,
        "results": deduped_results[:top_k],
    }
    reranked = apply_reranker(query=query, ranked=ranked, policy=reranker_policy)
    final_results = list(reranked.get("results") or [])
    quality = evaluate_retrieval_quality(query_plan=query_plan, results=final_results)
    reranked["quality"] = quality
    reranked["retrievalController"] = _retrieval_controller_payload(
        query_plan=query_plan,
        scored_results=fused_results,
        deduped_results=deduped_results,
        final_results=final_results,
        quality=quality,
        explicit_filter_count=0,
        controller_passes=pass_summaries,
    )
    return attach_agentic_context(query=query, ranked=reranked, language_profile=language_profile)


def evaluate_retrieval_quality(*, query_plan: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    """Return a deterministic quality gate for external agents and Dashboard."""
    key_terms = _quality_key_terms(query_plan)
    anchor_terms = _quality_anchor_terms(_quality_primary_query(query_plan))
    citable_evidence_text = "\n".join(_result_citable_text(item) for item in results).lower()
    retrieval_evidence_text = "\n".join(_result_retrieval_text(item) for item in results).lower()
    covered_terms = [term for term in key_terms if term in citable_evidence_text]
    missing_terms = [term for term in key_terms if term not in citable_evidence_text]
    missing_anchor_terms = [term for term in anchor_terms if term not in citable_evidence_text]
    coverage = round(len(covered_terms) / max(len(key_terms), 1), 4) if key_terms else 1.0
    retrieval_covered_terms = [term for term in key_terms if term in retrieval_evidence_text]
    retrieval_missing_terms = [term for term in key_terms if term not in retrieval_evidence_text]
    retrieval_coverage = (
        round(len(retrieval_covered_terms) / max(len(key_terms), 1), 4) if key_terms else 1.0
    )
    top_components = (results[0].get("scoreComponents") or {}) if results else {}
    top_citable_exact_coverage = (
        _key_term_coverage(key_terms, _result_citable_text(results[0])) if results else 0.0
    )
    meta_discussion_top = bool(
        results and _meta_discussion_score(_result_citable_text(results[0]), query_plan) >= 0.5
    )
    non_meta_exact_count = sum(
        1
        for item in results
        if _key_term_coverage(key_terms, _result_citable_text(item)) >= 0.5
        and _meta_discussion_score(_result_citable_text(item), query_plan) < 0.5
    )
    authoritative_count = sum(
        1
        for item in results
        if _finite_score((item.get("scoreComponents") or {}).get("evidenceAuthority")) >= 0.65
        and _finite_score((item.get("scoreComponents") or {}).get("metaDiscussion")) < 0.5
    )
    dense_only_top = bool(
        results
        and _finite_score(top_components.get("dense")) > 0
        and _finite_score(top_components.get("keyword")) <= 0
        and _finite_score(top_components.get("subQueryMatch")) <= 0
        and top_citable_exact_coverage <= 0
    )
    lifecycle_values = [
        str((item.get("governance") or {}).get("lifecycle") or "")
        for item in results
        if isinstance(item.get("governance"), dict)
    ]
    episodic_only_final_state = bool(
        results
        and lifecycle_values
        and all(value == "episodic" for value in lifecycle_values)
        and _final_state_query(query_plan)
    )
    if not results:
        status = "insufficient"
    elif dense_only_top or episodic_only_final_state:
        status = "weak"
    elif meta_discussion_top and not _meta_discussion_query(query_plan):
        status = "weak"
    elif _final_state_query(query_plan) and key_terms and not non_meta_exact_count:
        status = "weak"
    elif missing_anchor_terms:
        status = "weak"
    elif key_terms and coverage < 0.5:
        status = "weak"
    else:
        status = "strong"
    return {
        "schemaVersion": 1,
        "status": status,
        "needsMoreEvidence": status != "strong",
        "resultCount": len(results),
        "keyTerms": key_terms,
        "coveredTerms": covered_terms,
        "missingTerms": missing_terms,
        "coverage": coverage,
        "coverageBasis": "citable-text-only",
        "retrievalCoveredTerms": retrieval_covered_terms,
        "retrievalMissingTerms": retrieval_missing_terms,
        "retrievalCoverage": retrieval_coverage,
        "flags": {
            "denseOnlyTop": dense_only_top,
            "episodicOnlyFinalState": episodic_only_final_state,
            "metaDiscussionTop": meta_discussion_top,
            "hasNonMetaExactEvidence": non_meta_exact_count > 0,
            "hasAuthoritativeEvidence": authoritative_count > 0,
            "metadataOnlyTermCoverage": retrieval_coverage > coverage,
        },
        "recommendations": _quality_recommendations(
            status=status,
            key_terms=key_terms,
            missing_terms=missing_terms,
            dense_only_top=dense_only_top,
            episodic_only_final_state=episodic_only_final_state,
            meta_discussion_top=meta_discussion_top,
            non_meta_exact_count=non_meta_exact_count,
        ),
    }


def _retrieval_controller_payload(
    *,
    query_plan: dict[str, Any],
    scored_results: list[dict[str, Any]],
    deduped_results: list[dict[str, Any]],
    final_results: list[dict[str, Any]],
    quality: dict[str, Any],
    explicit_filter_count: int,
    controller_passes: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    exact_count = sum(
        1
        for item in scored_results
        if _finite_score((item.get("scoreComponents") or {}).get("exactCoverage")) > 0
    )
    subquery_count = sum(
        1
        for item in scored_results
        if _finite_score((item.get("scoreComponents") or {}).get("subQueryMatch")) > 0
    )
    passes = list(controller_passes or [])
    if not passes:
        passes = [
            {"id": "baseline-hybrid", "candidateCount": len(scored_results)},
            {"id": "exact-entity-recall", "candidateCount": exact_count},
        ]
        if len(query_plan.get("subQueries") or []) > 1:
            passes.append({"id": "subquery-rewrite", "candidateCount": subquery_count})
    if explicit_filter_count:
        passes.append(
            {
                "id": "filtered-source-aware",
                "candidateCount": len(scored_results),
                "filterCount": explicit_filter_count,
            }
        )
    passes.extend(
        [
            {
                "id": "fusion-rerank-dedupe",
                "candidateCount": len(deduped_results),
                "returned": len(final_results),
            },
            {
                "id": "quality-gate",
                "status": quality.get("status"),
                "needsMoreEvidence": bool(quality.get("needsMoreEvidence")),
            },
        ]
    )
    return {
        "schemaVersion": 1,
        "mode": "bounded-deterministic-multi-pass",
        "serverSide": True,
        "executionPolicy": "single request; bounded internal dense, lexical, exact-entity, filter-aware fusion and quality gate",
        "passesRun": [item["id"] for item in passes],
        "passes": passes,
        "qualityStatus": quality.get("status"),
        "needsMoreEvidence": bool(quality.get("needsMoreEvidence")),
    }


def _quality_recommendations(
    *,
    status: str,
    key_terms: list[str],
    missing_terms: list[str],
    dense_only_top: bool,
    episodic_only_final_state: bool,
    meta_discussion_top: bool,
    non_meta_exact_count: int,
) -> list[str]:
    if status == "strong":
        return []
    recommendations = []
    if missing_terms:
        recommendations.append("retry-exact-missing-terms:" + ",".join(missing_terms[:6]))
    elif key_terms:
        recommendations.append("inspect-lower-ranked-exact-entity-evidence")
    if dense_only_top:
        recommendations.append("retry-with-rare-entities-or-quoted-phrase")
    if episodic_only_final_state:
        recommendations.append("retry-with-canonical-current-state-or-summary-filters")
    if meta_discussion_top:
        recommendations.append("retry-with-meta-discussion-suppressed")
    if key_terms and non_meta_exact_count <= 0:
        recommendations.append("retry-with-authoritative-source-pass")
    if not recommendations:
        recommendations.append("expand-query-or-increase-top-k")
    return recommendations


def _quality_key_terms(query_plan: dict[str, Any] | None) -> list[str]:
    if not query_plan:
        return []
    primary_query = _quality_primary_query(query_plan)
    query_lower = primary_query.lower()
    terms: list[str] = []

    def add_term(value: str) -> None:
        normalized = " ".join(str(value or "").lower().split()).strip(" .,;:!?()[]{}'\"")
        if not normalized or normalized in STOP_KEY_TERMS or normalized in terms:
            return
        if len(normalized) == 1 and not re.search(r"\bbatch\s+" + re.escape(normalized) + r"\b", query_lower):
            return
        terms.append(normalized)

    for anchor in _quality_anchor_terms(primary_query):
        add_term(anchor)
    for pattern in (
        r"\bbatch\s+[a-z0-9]\b",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b\d{2,}\b",
        r"\b[0-9a-f]{7,40}\b",
        r"\b[a-zA-Z][a-zA-Z0-9_-]{1,40}\b",
    ):
        for match in re.findall(pattern, primary_query, flags=re.IGNORECASE):
            add_term(match)
    for marker in KEY_TERM_MARKERS:
        if marker in query_lower:
            add_term(marker)
    for token in tokenize(primary_query):
        if re.search(r"[\u4e00-\u9fff]", token):
            if any(stop in token for stop in STOP_KEY_TERMS):
                continue
            add_term(token)
    return terms[:12]


def _quality_primary_query(query_plan: dict[str, Any] | None) -> str:
    if not query_plan:
        return ""
    for item in query_plan.get("subQueries") or []:
        if isinstance(item, dict) and str(item.get("id") or "") == "Q0":
            return str(item.get("query") or "")
    first_subquery = (query_plan.get("subQueries") or [""])[0]
    if isinstance(first_subquery, dict):
        return str(first_subquery.get("query") or "")
    return str(first_subquery or "")


def _quality_anchor_terms(primary_query: str) -> list[str]:
    """Return query entities whose absence makes otherwise broad recall unsafe."""
    anchors: list[str] = []

    def add_anchor(value: str) -> None:
        normalized = str(value or "").lower().strip(" ,;!?()[]{}'\"`")
        if not normalized or normalized in STOP_KEY_TERMS or normalized in anchors:
            return
        anchors.append(normalized)

    for pattern in (
        r"(?<![\w.-])(?:(?:~|\.\.?|[a-zA-Z0-9_.-]+)?/)(?:[a-zA-Z0-9_.-]+/)*[a-zA-Z0-9_.-]+",
        r"(?<![\w.-])(?:\.[a-zA-Z0-9_-]+|[a-zA-Z0-9_-][a-zA-Z0-9_.-]*\.[a-zA-Z][a-zA-Z0-9]{0,7})(?![\w.-])",
        r"(?<![a-zA-Z0-9_.:-])(?=[a-zA-Z0-9_.:-]{3,41}(?![a-zA-Z0-9_.:-]))(?=[a-zA-Z0-9_.:-]*[a-zA-Z])(?=[a-zA-Z0-9_.:-]*\d)[a-zA-Z][a-zA-Z0-9]*(?:[-_.:][a-zA-Z0-9]+)+(?![a-zA-Z0-9_.:-])",
        r"(?<![a-zA-Z0-9_.:-])v\d+(?:\.\d+)*(?![a-zA-Z0-9_.:-])",
        r"\b\d{1,3}(?:\.\d{1,3}){3}\b",
        r"\b\d{2,}\b",
        r"\b[0-9a-f]{7,40}\b",
    ):
        for match in re.findall(pattern, primary_query, flags=re.IGNORECASE):
            add_anchor(match)
    for token in re.findall(r"\b[a-zA-Z][a-zA-Z0-9]{2,40}\b", primary_query):
        if any(character.islower() for character in token) and any(character.isupper() for character in token[1:]):
            add_anchor(token)
    for match in re.findall(
        r"(?<![a-zA-Z0-9_.:-])[A-Z][A-Z0-9]{1,7}(?![a-zA-Z0-9_.:-])",
        primary_query,
    ):
        add_anchor(match)
    return anchors


def _key_term_coverage(terms: Iterable[str], text: str) -> float:
    unique_terms = []
    for term in terms:
        normalized = str(term or "").lower().strip()
        if normalized and normalized not in unique_terms:
            unique_terms.append(normalized)
    if not unique_terms:
        return 0.0
    text_lower = str(text or "").lower()
    hits = sum(1 for term in unique_terms if term in text_lower)
    return hits / max(len(unique_terms), 1)


def _searchable_chunk_text(chunk: dict[str, Any]) -> str:
    """Return body plus metadata used only for recall scoring and ranking."""
    parts: list[str] = []
    for key in (
        "id",
        "text",
        "textPreview",
        "source",
        "sourceSet",
        "sourceType",
        "sourceId",
        "sourcePath",
        "agent",
        "project",
        "layer",
        "date",
        "dedupeKey",
    ):
        parts.append(str(chunk.get(key) or ""))
    parts.extend(str(tag) for tag in chunk.get("tags") or [])
    governance = chunk.get("governance") if isinstance(chunk.get("governance"), dict) else {}
    parts.extend(
        str(governance.get(key) or "")
        for key in ("lifecycle", "authorityRank", "duplicateGroupKey", "provenanceScore")
    )
    return "\n".join(parts).lower()


def _citable_chunk_text(chunk: dict[str, Any]) -> str:
    """Return text that can be exposed as a citation excerpt."""
    return str(chunk.get("text") or "").lower()


def _result_retrieval_text(result: dict[str, Any]) -> str:
    """Return result body plus metadata for retrieval-coverage telemetry."""
    parts: list[str] = []
    for key in (
        "id",
        "text",
        "textPreview",
        "source",
        "sourceSet",
        "sourceType",
        "sourceId",
        "date",
        "workType",
        "project",
    ):
        parts.append(str(result.get(key) or ""))
    parts.extend(str(tag) for tag in result.get("tags") or [])
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
    parts.extend(str(provenance.get(key) or "") for key in ("sourceId", "dedupeKey", "sourcePath"))
    parts.extend(str(governance.get(key) or "") for key in ("lifecycle", "duplicateGroupKey"))
    return "\n".join(parts)


def _result_citable_text(result: dict[str, Any]) -> str:
    """Mirror the citation-pack body fallback without opaque identifiers."""
    return str(result.get("text") or result.get("textPreview") or "")


def _recency_factor(query_plan: dict[str, Any] | None, recency: float) -> float:
    recency = max(0.0, min(_finite_score(recency), 1.0))
    if query_plan and query_plan.get("recencyBias") == "strong":
        return STRONG_RECENCY_MIN_FACTOR + ((1.0 - STRONG_RECENCY_MIN_FACTOR) * recency)
    return 1.0 + (recency * NORMAL_RECENCY_TIE_BREAKER_WEIGHT)


def _final_state_query(query_plan: dict[str, Any]) -> bool:
    intents = set(str(item) for item in query_plan.get("intents") or [])
    preferred = set(str(item) for item in query_plan.get("preferredTags") or [])
    return bool((intents | preferred).intersection({"config", "migration", "task", "lesson", "decision"}))


def _meta_discussion_score(text: str, query_plan: dict[str, Any] | None) -> float:
    if _meta_discussion_query(query_plan):
        return 0.0
    lower = str(text or "").lower()
    hits = sum(1 for marker in META_DISCUSSION_MARKERS if marker in lower)
    if not hits:
        return 0.0
    return min(1.0, hits / 3)


def _meta_discussion_query(query_plan: dict[str, Any] | None) -> bool:
    if not query_plan:
        return False
    query = " ".join(str(item.get("query") or "") for item in query_plan.get("subQueries") or [] if isinstance(item, dict)).lower()
    return any(marker in query for marker in ("召回质量", "rag quality", "retrieval quality", "eval", "benchmark", "q0", "q1", "q2"))


def _evidence_authority_score(chunk: dict[str, Any], governance: dict[str, Any]) -> float:
    source_set = str(chunk.get("sourceSet") or "")
    lifecycle = str(governance.get("lifecycle") or "")
    score = 0.0
    if source_set in AUTHORITATIVE_FACT_SOURCE_SETS:
        score += 0.45
    if lifecycle in AUTHORITATIVE_LIFECYCLES:
        score += 0.35
    score += min(max(_finite_score(governance.get("authorityRank")) / 100, 0.0), 1.0) * 0.2
    return min(score, 1.0)


def _best_rewrite_query(query_plan: dict[str, Any]) -> str:
    for item in query_plan.get("subQueries") or []:
        if not isinstance(item, dict) or item.get("id") == "Q0":
            continue
        query = str(item.get("query") or "").strip()
        if query:
            return query
    return ""


def _authoritative_source_sets_for_query(query_plan: dict[str, Any]) -> list[str]:
    intents = set(str(item) for item in query_plan.get("intents") or [])
    preferred = set(str(item) for item in query_plan.get("preferredTags") or [])
    values = intents | preferred
    if "task" in values:
        return ["task-board-snapshot", "technical-report-task-events", "diary-markdown-sections"]
    if values.intersection({"config", "migration", "lesson", "decision"}):
        return ["lessons", "foundation-period-projections", "technical-report-task-events", "diary-markdown-sections"]
    return []


def _has_explicit_source_filter(query_plan: dict[str, Any]) -> bool:
    filters = query_plan.get("explicitFilters") if isinstance(query_plan.get("explicitFilters"), dict) else {}
    return bool(filters.get("sourceSets"))


def _merge_duplicate_pass_hits(results: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: list[dict[str, Any]] = []
    by_key: dict[str, dict[str, Any]] = {}
    for index, result in enumerate(results):
        key = _result_dedupe_key(result) or f"unique:{index}:{result.get('id') or ''}"
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = result
            merged.append(result)
            continue
        pass_ids = sorted(set(list(existing.get("retrievalPasses") or []) + list(result.get("retrievalPasses") or [])))
        existing["retrievalPasses"] = pass_ids
        components = dict(existing.get("scoreComponents") or {})
        component_pass_ids = sorted(
            set(
                list(components.get("passIds") or [])
                + [str(components.get("passId") or "")]
                + list((result.get("scoreComponents") or {}).get("passIds") or [])
                + [str((result.get("scoreComponents") or {}).get("passId") or "")]
            )
            - {""}
        )
        components["passIds"] = component_pass_ids
        existing["scoreComponents"] = components
        if _finite_score(result.get("score")) > _finite_score(existing.get("score")):
            existing["score"] = result.get("score")
    return merged


def dedupe_ranked_results(results: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Remove duplicate evidence groups after scoring and before topK truncation."""
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    duplicates: list[dict[str, Any]] = []
    for result in results:
        key = _result_dedupe_key(result)
        if key and key in seen:
            duplicates.append(
                {
                    "id": result.get("id"),
                    "dedupeKey": key,
                    "sourceSet": result.get("sourceSet"),
                    "score": result.get("score"),
                }
            )
            continue
        if key:
            seen.add(key)
        deduped.append(result)
    return deduped, {
        "applied": True,
        "strategy": "governance-provenance-text",
        "inputCount": len(results),
        "outputCount": len(deduped),
        "duplicatesRemoved": len(duplicates),
        "duplicateRate": round(len(duplicates) / max(len(results), 1), 4),
        "removed": duplicates[:20],
    }


def _result_dedupe_key(result: dict[str, Any]) -> str | None:
    governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
    provenance = result.get("provenance") if isinstance(result.get("provenance"), dict) else {}
    for prefix, value in (
        ("governance", governance.get("duplicateGroupKey")),
        ("provenance-dedupe", provenance.get("dedupeKey")),
        ("provenance-source-id", provenance.get("sourceId")),
        ("source-id", result.get("sourceId")),
    ):
        normalized = str(value or "").strip()
        if normalized and not normalized.endswith(":"):
            return f"{prefix}:{normalized}"
    text = " ".join(str(result.get("textPreview") or result.get("text") or "").lower().split())
    if len(text) >= 48:
        source_set = str(result.get("sourceSet") or "unknown")
        date = str(result.get("date") or "")
        return f"text:{source_set}:{date}:{text[:180]}"
    return None


def _layer_boost(chunk: dict[str, Any]) -> float:
    return TECHNICAL_LAYER_BOOST if chunk.get("layer") in {"technical", "lesson"} else 1.0


def _intent_boost(chunk: dict[str, Any], query_plan: dict[str, Any] | None) -> float:
    if not query_plan:
        return 1.0
    preferred = set(query_plan.get("preferredTags") or [])
    if not preferred or preferred == {"general"}:
        return 1.0
    tags = set(infer_tags(chunk))
    boost = 1.0
    if tags.intersection(preferred):
        boost *= INTENT_TAG_BOOST
    work_type = infer_work_type(chunk, sorted(tags))
    if work_type in preferred:
        boost *= INTENT_WORK_TYPE_BOOST
    return boost


def _subquery_match_score(query_plan: dict[str, Any] | None, text: str) -> float:
    if not query_plan:
        return 0.0
    best = 0.0
    for item in query_plan.get("subQueries") or []:
        if not isinstance(item, dict):
            continue
        words = tokenize(str(item.get("query") or ""))
        if not words:
            continue
        best = max(best, keyword_score(words, text))
    return best


def _chunk_governance(chunk: dict[str, Any]) -> dict[str, Any]:
    governance = chunk.get("governance")
    return governance if isinstance(governance, dict) else governance_for_chunk(chunk)


def _date_in_range(value: Any, *, date_from: str | None, date_to: str | None) -> bool:
    date_text = str(value or "")[:10]
    if not date_from and not date_to:
        return True
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", date_text):
        return False
    if date_from and date_text < str(date_from)[:10]:
        return False
    if date_to and date_text > str(date_to)[:10]:
        return False
    return True


def _decompose_query_terms(query: str, *, intents: Iterable[str], preferred_tags: Iterable[str]) -> list[dict[str, Any]]:
    normalized = " ".join(str(query or "").split())
    if not normalized:
        return []
    items: list[dict[str, Any]] = [{"id": "Q0", "query": normalized, "purpose": "primary recall"}]
    intent_set = set(intents)
    tag_set = set(preferred_tags)
    if "incident" in intent_set:
        items.append({"id": "Q1", "query": f"{normalized} error failed traceback 修复 故障 问题 恢复 解决", "purpose": "incident evidence"})
    if "aggregation" in intent_set:
        items.append({"id": "Q6", "query": f"{normalized} 几次 多少次 次数 最严重 时间线", "purpose": "event aggregation evidence"})
    if "config" in intent_set:
        items.append({"id": "Q7", "query": f"{normalized} port 端口 config 配置 dns ip 不可用 原因", "purpose": "configuration evidence"})
    if "migration" in intent_set:
        items.append({"id": "Q8", "query": f"{normalized} 迁移 migration migrate 旧 新 变更 切换", "purpose": "migration evidence"})
    if "coding" in intent_set:
        items.append({"id": "Q2", "query": f"{normalized} code src pytest unittest implementation 代码 测试", "purpose": "coding evidence"})
    if "task" in intent_set:
        items.append({"id": "Q3", "query": f"{normalized} task todo progress TASK_BOARD 任务 进度", "purpose": "task-state evidence"})
    if "lesson" in intent_set or "decision" in tag_set:
        items.append({"id": "Q4", "query": f"{normalized} lesson decision 经验 教训 决策", "purpose": "lesson evidence"})
    if "daily" in intent_set:
        items.append({"id": "Q5", "query": f"{normalized} diary daily today yesterday 日记 日常", "purpose": "daily-memory evidence"})
    seen: set[str] = set()
    deduped: list[dict[str, Any]] = []
    for item in items:
        key = str(item["query"]).lower()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _finite_score(value: Any) -> float:
    try:
        score = float(value)
    except (TypeError, ValueError):
        return 0.0
    if not math.isfinite(score):
        return 0.0
    return score


def _expanded_query_words(query_plan: dict[str, Any]) -> list[str]:
    words: list[str] = []
    for item in query_plan.get("subQueries") or []:
        if isinstance(item, dict):
            words.extend(tokenize(str(item.get("query") or "")))
    words.extend(str(tag).lower() for tag in query_plan.get("preferredTags") or [])
    seen: set[str] = set()
    deduped: list[str] = []
    for word in words:
        if word in seen:
            continue
        seen.add(word)
        deduped.append(word)
    return deduped
