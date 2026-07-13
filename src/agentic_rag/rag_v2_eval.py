"""Read-only RAG search quality evaluation."""

from __future__ import annotations

import json
import math
import time
import urllib.request
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .rag_settings import RagSettings, resolve_rag_settings
from data_foundation.network import host_for_url, require_loopback_host


BenchmarkSearchFn = Callable[[dict[str, Any]], dict[str, Any]]
DEFAULT_BENCHMARK_PATH = Path(__file__).with_name("rag_eval_queries.jsonl")
PROFILE_BENCHMARK_PATHS = {
    "zh": Path(__file__).with_name("rag_eval_queries.zh.jsonl"),
    "en": Path(__file__).with_name("rag_eval_queries.en.jsonl"),
}
VALID_BENCHMARK_PROFILES = {"default", "extended"}
EXTENDED_BENCHMARK_SOURCE_SETS = {"technical-report-task-events"}
MAX_BENCHMARK_SEARCH_BUDGET_SECONDS = 60.0


def run_rag_eval(
    settings: RagSettings | None = None,
    *,
    benchmark_path: Path | None = None,
    search_fn: BenchmarkSearchFn | None = None,
    profile: str = "default",
) -> dict[str, Any]:
    resolved = settings or resolve_rag_settings()
    selected_profile = str(profile or "default").strip().lower()
    if selected_profile not in VALID_BENCHMARK_PROFILES:
        raise ValueError(f"unsupported RAG benchmark profile: {profile}")
    configured_source_sets = {str(item) for item in resolved.indexing_source_sets}
    extended_missing = sorted(EXTENDED_BENCHMARK_SOURCE_SETS.difference(configured_source_sets))
    profile_disposition = (
        "configured-out"
        if selected_profile == "extended" and extended_missing
        else "configured"
    )
    benchmark_paths = [benchmark_path] if benchmark_path else eval_benchmark_paths(resolved.language_profile)
    cases = read_eval_benchmarks(benchmark_paths)
    search = search_fn or _server_search_fn(resolved)
    results = []
    for case in cases:
        required_source_sets = _case_required_source_sets(case)
        missing_source_sets = sorted(required_source_sets.difference(configured_source_sets))
        if missing_source_sets:
            results.append(
                _configured_out_case(
                    case,
                    required_source_sets=required_source_sets,
                    missing_source_sets=missing_source_sets,
                    expected_skip=selected_profile == "default",
                )
            )
            continue
        payload = _payload_for_case(case)
        started = time.monotonic()
        try:
            response = search(payload)
            result = _evaluate_case(case, response)
        except Exception as exc:  # pragma: no cover - defensive external call boundary
            result = {
                "id": case.get("id"),
                "status": "error",
                "passed": False,
                "error": str(exc),
                "query": case.get("query"),
            }
        result["latencyMs"] = round((time.monotonic() - started) * 1000, 3)
        failure_text = " ".join(
            str(result.get(key) or "") for key in ("error", "responseReason")
        ).lower()
        result["timedOut"] = "timeout" in failure_text or "timed out" in failure_text
        result["requiredSourceSets"] = sorted(required_source_sets)
        results.append(result)
    evaluated = [item for item in results if item.get("status") != "skipped"]
    skipped = [item for item in results if item.get("status") == "skipped"]
    passed = sum(1 for item in evaluated if item.get("passed"))
    failed = len(evaluated) - passed
    unexpected_skips = sum(1 for item in skipped if not item.get("expectedSkip"))
    metric_summary = _metric_summary(evaluated)
    source_coverage = _source_coverage_summary(evaluated)
    if profile_disposition == "configured-out":
        status = "blocked"
    elif failed or unexpected_skips:
        status = "failed"
    else:
        status = "passed"
    return {
        "schemaVersion": 2,
        "status": status,
        "readOnly": True,
        "checkedAt": datetime.now().astimezone().isoformat(),
        "benchmarkPath": str(benchmark_path or DEFAULT_BENCHMARK_PATH),
        "benchmarkPaths": [str(path) for path in benchmark_paths],
        "caseCount": len(results),
        "evaluatedCount": len(evaluated),
        "skippedCount": len(skipped),
        "unexpectedSkipCount": unexpected_skips,
        "passedCount": passed,
        "failedCount": failed,
        "passRate": round(passed / max(len(evaluated), 1), 4),
        "profile": selected_profile,
        "profileDisposition": profile_disposition,
        "configuredSourceSets": sorted(configured_source_sets),
        "extendedRequiredSourceSets": sorted(EXTENDED_BENCHMARK_SOURCE_SETS),
        "extendedMissingSourceSets": extended_missing if selected_profile == "extended" else [],
        "metrics": metric_summary,
        "sourceCoverage": source_coverage,
        "variant": _variant_payload(resolved),
        "cases": results,
        "mutationPolicy": {
            "readOnly": True,
            "legacyMutated": False,
            "v2StoreMutated": False,
            "settingsMutated": False,
        },
    }


def _case_required_source_sets(case: dict[str, Any]) -> set[str]:
    explicit = _string_set(case.get("requiredSourceSets"))
    if explicit:
        return explicit
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    return _string_set(expect.get("sourceSets"))


def _configured_out_case(
    case: dict[str, Any],
    *,
    required_source_sets: set[str],
    missing_source_sets: list[str],
    expected_skip: bool,
) -> dict[str, Any]:
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    return {
        "id": case.get("id"),
        "query": case.get("query"),
        "status": "skipped",
        "passed": None,
        "expectedSkip": expected_skip,
        "skipReason": "source-set-not-configured",
        "requiredSourceSets": sorted(required_source_sets),
        "missingSourceSets": missing_source_sets,
        "expected": expect,
        "observed": {"sourceSets": [], "lifecycles": [], "workTypes": [], "projects": []},
        "latencyMs": 0.0,
        "timedOut": False,
    }


def eval_benchmark_paths(language_profile: str | None = None) -> list[Path]:
    profile = str(language_profile or "zh").strip().lower()
    paths = [DEFAULT_BENCHMARK_PATH]
    profile_path = PROFILE_BENCHMARK_PATHS.get(profile)
    if profile_path:
        paths.append(profile_path)
    return paths


def read_eval_benchmarks(paths: list[Path | None] | tuple[Path | None, ...]) -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    for path in paths:
        if path is None:
            continue
        cases.extend(read_eval_benchmark(path))
    return cases


def read_eval_benchmark(path: Path = DEFAULT_BENCHMARK_PATH) -> list[dict[str, Any]]:
    cases = []
    with Path(path).open("r", encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            payload = json.loads(line)
            if isinstance(payload, dict):
                cases.append(payload)
    return cases


def _payload_for_case(case: dict[str, Any]) -> dict[str, Any]:
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    payload = {
        "query": str(case.get("query") or ""),
        "topK": int(case.get("topK") or 8),
        "includeFullText": False,
        "includeGovernance": True,
    }
    for case_key, payload_key in (
        ("project", "project"),
        ("sourceSets", "sourceSets"),
        ("lifecycles", "lifecycle"),
        ("workTypes", "workType"),
        ("dateRange", "dateRange"),
    ):
        value = case.get(case_key) if case_key in case else expect.get(case_key)
        if value:
            payload[payload_key] = value
    return payload


def _evaluate_case(case: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    expect = case.get("expect") if isinstance(case.get("expect"), dict) else {}
    results = list(response.get("results") or [])
    source_sets = {str(item.get("sourceSet") or "") for item in results}
    lifecycles = {str((item.get("governance") or {}).get("lifecycle") or "") for item in results}
    work_types = {str(item.get("workType") or "") for item in results}
    projects = {str(item.get("project") or "") for item in results if item.get("project")}
    quality = _quality_metrics(expect, response, results)
    checks = {
        "minResults": len(results) >= int(expect.get("minResults") or 1),
        "sourceSets": _matches_any(source_sets, expect.get("sourceSets")),
        "lifecycles": _matches_any(lifecycles, expect.get("lifecycles")),
        "workTypes": _matches_any(work_types, expect.get("workTypes")),
        "projects": _matches_any(projects, expect.get("projects")),
        "recallAtK": _threshold_pass(quality.get("recallAtK"), expect.get("minRecallAtK")),
        "citationHit": _threshold_pass(quality.get("citationHitRate"), expect.get("minCitationHitRate")),
        "evidenceCoverage": _threshold_pass(quality.get("evidenceCoverage"), expect.get("minEvidenceCoverage")),
        "duplicateRate": _max_threshold_pass(quality.get("duplicateRate"), expect.get("maxDuplicateRate")),
        "aggregationCorrectness": _threshold_pass(
            quality.get("aggregationCorrectness"),
            expect.get("minAggregationCorrectness"),
        ),
    }
    active_checks = {key: value for key, value in checks.items() if _expect_active(key, expect)}
    passed = all(active_checks.values()) if active_checks else checks["minResults"]
    top = results[0] if results else {}
    return {
        "id": case.get("id"),
        "query": case.get("query"),
        "status": "passed" if passed else "failed",
        "passed": passed,
        "checks": checks,
        "quality": quality,
        "expected": expect,
        "observed": {
            "sourceSets": sorted(item for item in source_sets if item),
            "lifecycles": sorted(item for item in lifecycles if item),
            "workTypes": sorted(item for item in work_types if item),
            "projects": sorted(item for item in projects if item),
        },
        "resultCount": len(results),
        "responseReason": response.get("reason"),
        "top": {
            "id": top.get("id"),
            "sourceSet": top.get("sourceSet"),
            "lifecycle": (top.get("governance") or {}).get("lifecycle"),
            "workType": top.get("workType"),
            "project": top.get("project"),
            "score": top.get("score"),
            "authorityRank": ((top.get("governance") or {}).get("authorityRank")),
            "provenanceScore": ((top.get("governance") or {}).get("provenanceScore")),
        },
    }


def _matches_any(actual: set[str], expected: Any) -> bool:
    if not expected:
        return True
    expected_set = {str(item) for item in (expected if isinstance(expected, list) else [expected])}
    return bool(actual.intersection(expected_set))


def _expect_active(key: str, expect: dict[str, Any]) -> bool:
    if key == "minResults":
        return True
    mapping = {
        "sourceSets": "sourceSets",
        "lifecycles": "lifecycles",
        "workTypes": "workTypes",
        "projects": "projects",
        "recallAtK": "expectedResultIds",
        "citationHit": "expectedCitationResultIds",
        "evidenceCoverage": "evidenceTerms",
        "duplicateRate": "maxDuplicateRate",
        "aggregationCorrectness": "aggregation",
    }
    if key in {"recallAtK", "citationHit"} and expect.get("relevantEvidence"):
        return True
    return bool(expect.get(mapping[key]))


def _quality_metrics(expect: dict[str, Any], response: dict[str, Any], results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_ids = _string_set(expect.get("expectedResultIds"))
    citation_expected_ids = _string_set(expect.get("expectedCitationResultIds")) or expected_ids
    result_ids = [str(item.get("id") or "") for item in results if item.get("id")]
    result_id_set = set(result_ids)
    citation_pack = list(response.get("citationPack") or [])
    citation_result_ids = {
        str(item.get("resultId") or "")
        for item in citation_pack
        if isinstance(item, dict) and item.get("resultId")
    }
    evidence_terms = [str(item).lower() for item in _as_list(expect.get("evidenceTerms")) if str(item).strip()]
    evidence_text = _response_evidence_text(response, results)
    duplicate_rate = _duplicate_rate(results)
    aggregation = _aggregation_correctness(expect.get("aggregation"), response, results, citation_result_ids)
    relevant_evidence = _stable_evidence_descriptors(expect)
    relevance = _stable_relevance(results, relevant_evidence)
    stable_recall_5 = _stable_recall_at_k(results, relevant_evidence, 5)
    stable_recall_10 = _stable_recall_at_k(results, relevant_evidence, 10)
    metrics = {
        "recallAtK": stable_recall_10 if relevant_evidence else _ratio(len(expected_ids.intersection(result_id_set)), len(expected_ids)),
        "recallAt5": stable_recall_5 if relevant_evidence else _id_recall_at_k(result_ids, expected_ids, 5),
        "recallAt10": stable_recall_10 if relevant_evidence else _id_recall_at_k(result_ids, expected_ids, 10),
        "mrr": _reciprocal_rank(relevance) if relevant_evidence else _id_reciprocal_rank(result_ids, expected_ids),
        "ndcgAt10": _ndcg_at_k(
            relevance,
            10,
            total_relevant=len(relevant_evidence) if relevant_evidence else len(expected_ids),
        ),
        "expectedHits": sorted(expected_ids.intersection(result_id_set)),
        "expectedMisses": sorted(expected_ids.difference(result_id_set)),
        "citationHitRate": _stable_recall_at_k(citation_pack, relevant_evidence, len(citation_pack)) if expect.get("relevantEvidence") else _ratio(
            len(citation_expected_ids.intersection(citation_result_ids)), len(citation_expected_ids)
        ),
        "citationHits": sorted(citation_expected_ids.intersection(citation_result_ids)),
        "citationMisses": sorted(citation_expected_ids.difference(citation_result_ids)),
        "evidenceCoverage": _ratio(
            sum(1 for term in evidence_terms if term in evidence_text),
            len(evidence_terms),
        ),
        "evidenceTermsHit": [term for term in evidence_terms if term in evidence_text],
        "evidenceTermsMissing": [term for term in evidence_terms if term not in evidence_text],
        "duplicateRate": duplicate_rate,
        "aggregationCorrectness": aggregation["score"],
        "aggregation": aggregation,
    }
    return metrics


def _result_matches_evidence(item: dict[str, Any], descriptor: dict[str, Any]) -> bool:
    governance = item.get("governance") if isinstance(item.get("governance"), dict) else {}
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    fields = {
        "sourceSet": item.get("sourceSet") or provenance.get("sourceSet"),
        "lifecycle": governance.get("lifecycle"),
        "workType": item.get("workType"),
        "project": item.get("project"),
    }
    for key, actual in fields.items():
        expected = _string_set(descriptor.get(key) or descriptor.get(f"{key}s"))
        if expected and str(actual or "") not in expected:
            return False
    source_text = "\n".join(str(value or "") for value in (
        item.get("text"), item.get("textPreview"), item.get("excerpt"), item.get("source"), item.get("sourceId"),
        provenance.get("source"), provenance.get("sourcePath"), provenance.get("sourceId"),
    )).lower()
    terms = [str(term).lower() for term in _as_list(descriptor.get("terms")) if str(term).strip()]
    return all(term in source_text for term in terms)


def _stable_evidence_descriptors(expect: dict[str, Any]) -> list[dict[str, Any]]:
    explicit = [item for item in _as_list(expect.get("relevantEvidence")) if isinstance(item, dict)]
    if explicit:
        return explicit
    source_sets = [str(item) for item in _as_list(expect.get("sourceSets")) if str(item).strip()]
    if source_sets:
        return [{"sourceSet": source_set} for source_set in source_sets]
    terms = [str(item) for item in _as_list(expect.get("evidenceTerms")) if str(item).strip()]
    return [{"terms": terms}] if terms else []


def _result_matches_any_evidence(item: dict[str, Any], descriptors: list[dict[str, Any]]) -> bool:
    return any(_result_matches_evidence(item, descriptor) for descriptor in descriptors)


def _stable_relevance(results: list[dict[str, Any]], descriptors: list[dict[str, Any]]) -> list[bool]:
    """Return binary relevance without counting one gold descriptor more than once."""
    matched: set[int] = set()
    relevance: list[bool] = []
    for item in results:
        descriptor_index = next(
            (
                index
                for index, descriptor in enumerate(descriptors)
                if index not in matched and _result_matches_evidence(item, descriptor)
            ),
            None,
        )
        relevance.append(descriptor_index is not None)
        if descriptor_index is not None:
            matched.add(descriptor_index)
    return relevance


def _stable_recall_at_k(results: list[dict[str, Any]], descriptors: list[dict[str, Any]], k: int) -> float:
    if not descriptors:
        return 1.0
    hits = sum(1 for descriptor in descriptors if any(_result_matches_evidence(item, descriptor) for item in results[:k]))
    return _ratio(hits, len(descriptors))


def _id_recall_at_k(result_ids: list[str], expected_ids: set[str], k: int) -> float:
    return _ratio(len(expected_ids.intersection(result_ids[:k])), len(expected_ids))


def _reciprocal_rank(relevance: list[bool]) -> float:
    return round(next((1.0 / rank for rank, hit in enumerate(relevance, 1) if hit), 0.0), 4)


def _id_reciprocal_rank(result_ids: list[str], expected_ids: set[str]) -> float:
    return _reciprocal_rank([item in expected_ids for item in result_ids]) if expected_ids else 1.0


def _ndcg_at_k(relevance: list[bool], k: int, *, total_relevant: int | None = None) -> float:
    values = [1.0 if hit else 0.0 for hit in relevance[:k]]
    dcg = sum(value / math.log2(rank + 1) for rank, value in enumerate(values, 1))
    ideal_hits = min(k, max(int(total_relevant if total_relevant is not None else sum(values)), 0))
    ideal = sum(1.0 / math.log2(rank + 1) for rank in range(1, ideal_hits + 1))
    return round(min(dcg / ideal, 1.0), 4) if ideal else 0.0


def _response_evidence_text(response: dict[str, Any], results: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for item in results:
        parts.extend(str(item.get(key) or "") for key in ("text", "textPreview", "source", "sourceSet", "date"))
    for citation in response.get("citationPack") or []:
        if isinstance(citation, dict):
            parts.extend(str(citation.get(key) or "") for key in ("excerpt", "source", "date", "resultId"))
    synthesis = response.get("answerSynthesis") if isinstance(response.get("answerSynthesis"), dict) else {}
    parts.append(str(synthesis.get("summary") or ""))
    for bullet in synthesis.get("bullets") or []:
        if isinstance(bullet, dict):
            parts.append(str(bullet.get("text") or ""))
        else:
            parts.append(str(bullet))
    aggregation = response.get("eventAggregation") if isinstance(response.get("eventAggregation"), dict) else {}
    agentic = response.get("agentic") if isinstance(response.get("agentic"), dict) else {}
    if not aggregation and isinstance(agentic.get("eventAggregation"), dict):
        aggregation = agentic["eventAggregation"]
    parts.append(json.dumps(aggregation, ensure_ascii=False, sort_keys=True))
    return "\n".join(parts).lower()


def _duplicate_rate(results: list[dict[str, Any]]) -> float:
    keys = [_dedupe_key(item) for item in results]
    keys = [key for key in keys if key]
    if not keys:
        return 0.0
    duplicate_count = len(keys) - len(set(keys))
    return round(duplicate_count / max(len(keys), 1), 4)


def _dedupe_key(item: dict[str, Any]) -> str:
    governance = item.get("governance") if isinstance(item.get("governance"), dict) else {}
    provenance = item.get("provenance") if isinstance(item.get("provenance"), dict) else {}
    for value in (
        governance.get("duplicateGroupKey"),
        provenance.get("dedupeKey"),
        provenance.get("sourceId"),
        item.get("sourceId"),
        item.get("id"),
    ):
        if value:
            return str(value)
    text = str(item.get("textPreview") or item.get("text") or "").strip().lower()
    return " ".join(text.split())[:160]


def _aggregation_correctness(
    raw_expectation: Any,
    response: dict[str, Any],
    results: list[dict[str, Any]],
    citation_result_ids: set[str],
) -> dict[str, Any]:
    if not isinstance(raw_expectation, dict) or not raw_expectation:
        return {"score": 1.0, "active": False, "checks": {}}
    checks: dict[str, bool] = {}
    text = _response_evidence_text(response, results)
    expected_event_count = raw_expectation.get("expectedEventCount")
    if expected_event_count is not None:
        event_count_text = str(expected_event_count).lower()
        event_count = _event_aggregation_payload(response).get("eventCount")
        checks["eventCount"] = event_count == expected_event_count or event_count_text in text
    count_terms = [str(item).lower() for item in _as_list(raw_expectation.get("countTerms")) if str(item).strip()]
    if count_terms:
        checks["countTerms"] = any(term in text for term in count_terms)
    required_ids = _string_set(raw_expectation.get("requiredEventIds"))
    if required_ids:
        result_ids = {str(item.get("id") or "") for item in results if item.get("id")}
        checks["requiredEventIds"] = bool(required_ids.intersection(result_ids.union(citation_result_ids)))
    required_terms = [str(item).lower() for item in _as_list(raw_expectation.get("requiredTerms")) if str(item).strip()]
    if required_terms:
        checks["requiredTerms"] = all(term in text for term in required_terms)
    score = _ratio(sum(1 for value in checks.values() if value), len(checks))
    return {"score": score, "active": True, "checks": checks}


def _event_aggregation_payload(response: dict[str, Any]) -> dict[str, Any]:
    if isinstance(response.get("eventAggregation"), dict):
        return response["eventAggregation"]
    agentic = response.get("agentic") if isinstance(response.get("agentic"), dict) else {}
    if isinstance(agentic.get("eventAggregation"), dict):
        return agentic["eventAggregation"]
    return {}


def _metric_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    quality_items = [item.get("quality") for item in results if isinstance(item.get("quality"), dict)]
    metric_names = (
        "recallAtK",
        "recallAt5",
        "recallAt10",
        "mrr",
        "ndcgAt10",
        "citationHitRate",
        "evidenceCoverage",
        "duplicateRate",
        "aggregationCorrectness",
    )
    summary = {
        name: _average([float(item[name]) for item in quality_items if item.get(name) is not None])
        for name in metric_names
    }
    latencies = sorted(float(item.get("latencyMs") or 0.0) for item in results)
    summary.update({
        "latencyP50Ms": _percentile(latencies, 0.50),
        "latencyP95Ms": _percentile(latencies, 0.95),
        "timeoutRate": _ratio(sum(1 for item in results if item.get("timedOut")), len(results)),
    })
    return summary


def _percentile(values: list[float], percentile: float) -> float:
    if not values:
        return 0.0
    index = max(0, min(len(values) - 1, math.ceil(percentile * len(values)) - 1))
    return round(values[index], 3)


def _source_coverage_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    expected_source_sets: set[str] = set()
    observed_source_sets: set[str] = set()
    expected_lifecycles: set[str] = set()
    observed_lifecycles: set[str] = set()
    expected_work_types: set[str] = set()
    observed_work_types: set[str] = set()
    missing_cases: list[dict[str, Any]] = []
    for item in results:
        expected = item.get("expected") if isinstance(item.get("expected"), dict) else {}
        observed = item.get("observed") if isinstance(item.get("observed"), dict) else {}
        case_expected_sources = _string_set(expected.get("sourceSets"))
        case_observed_sources = _string_set(observed.get("sourceSets"))
        expected_source_sets.update(case_expected_sources)
        observed_source_sets.update(case_observed_sources)
        expected_lifecycles.update(_string_set(expected.get("lifecycles")))
        observed_lifecycles.update(_string_set(observed.get("lifecycles")))
        expected_work_types.update(_string_set(expected.get("workTypes")))
        observed_work_types.update(_string_set(observed.get("workTypes")))
        missing = sorted(case_expected_sources.difference(case_observed_sources))
        if missing:
            missing_cases.append({"id": item.get("id"), "missingSourceSets": missing})
    missing_source_sets = sorted(expected_source_sets.difference(observed_source_sets))
    return {
        "schemaVersion": 1,
        "method": "search-result-observed",
        "status": "passed" if not missing_source_sets and not missing_cases else "failed",
        "diagnostic": (
            "missingSourceSets means benchmark search returned no result from an expected sourceSet; "
            "it does not by itself prove the active index is missing that source. "
            "Use read_v2_coverage() for index source discovery/indexed-chunk coverage."
        ),
        "expectedSourceSets": sorted(expected_source_sets),
        "observedSourceSets": sorted(observed_source_sets),
        "missingSourceSets": missing_source_sets,
        "expectedLifecycles": sorted(expected_lifecycles),
        "observedLifecycles": sorted(observed_lifecycles),
        "expectedWorkTypes": sorted(expected_work_types),
        "observedWorkTypes": sorted(observed_work_types),
        "missingCases": missing_cases,
    }


def _variant_payload(settings: RagSettings) -> dict[str, Any]:
    return {
        "embeddingProvider": settings.embedding_provider,
        "embeddingProviderId": settings.embedding_provider_id,
        "embeddingModel": settings.embedding_model,
        "embeddingDimension": settings.embedding_dimension,
        "rerankerEnabled": settings.reranker_enabled,
        "rerankerProvider": settings.reranker_provider,
        "retrievalTopK": settings.retrieval_top_k,
        "recencyHalfLifeDays": settings.recency_half_life_days,
        "languageProfile": settings.language_profile,
        "mode": settings.mode,
    }


def _threshold_pass(value: Any, threshold: Any) -> bool:
    if threshold is None:
        return True
    return float(value or 0.0) >= float(threshold)


def _max_threshold_pass(value: Any, threshold: Any) -> bool:
    if threshold is None:
        return True
    return float(value or 0.0) <= float(threshold)


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 4)


def _average(values: list[float]) -> float:
    if not values:
        return 0.0
    return round(sum(values) / len(values), 4)


def _string_set(value: Any) -> set[str]:
    return {str(item) for item in _as_list(value) if str(item).strip()}


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _server_search_fn(settings: RagSettings) -> BenchmarkSearchFn:
    server_host = require_loopback_host(settings.server_host)
    search_url = f"http://{host_for_url(server_host)}:{settings.server_port}/search"
    search_budget_seconds = min(
        max(float(settings.retrieval_latency_budget_seconds), 0.1),
        MAX_BENCHMARK_SEARCH_BUDGET_SECONDS,
    )

    def search(payload: dict[str, Any]) -> dict[str, Any]:
        server_payload = _server_payload(payload)
        server_payload["latency_budget_ms"] = int(round(search_budget_seconds * 1000))
        body = json.dumps(server_payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(
            search_url,
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=search_budget_seconds + 5.0) as response:
            result = json.loads(response.read().decode("utf-8"))
        return result if isinstance(result, dict) else {"results": result}

    return search


def _server_payload(payload: dict[str, Any]) -> dict[str, Any]:
    result = {
        "query": payload["query"],
        "top_k": int(payload.get("topK") or payload.get("top_k") or 8),
        "include_full_text": bool(payload.get("includeFullText", False)),
        "include_governance": bool(payload.get("includeGovernance", True)),
    }
    for payload_key, server_key in (
        ("project", "project"),
        ("sourceSets", "source_sets"),
        ("lifecycle", "lifecycle"),
        ("workType", "work_type"),
    ):
        value = payload.get(payload_key)
        if isinstance(value, list):
            result[server_key] = [str(item) for item in value if str(item).strip()]
        elif isinstance(value, str) and value.strip():
            result[server_key] = [value.strip()] if server_key in {"source_sets", "lifecycle", "work_type"} else value.strip()
    date_range = payload.get("dateRange") if isinstance(payload.get("dateRange"), dict) else {}
    if date_range.get("from"):
        result["date_from"] = str(date_range["from"])
    if date_range.get("to"):
        result["date_to"] = str(date_range["to"])
    return result
