"""Disabled-by-default RAG reranker policy.

Reranking remains an explicit policy layer. The default provider is
``none``, which records metadata and leaves base retrieval order unchanged. The
``local-score`` provider is deterministic and uses only existing score
components; external model or LLM rerankers are not enabled by this policy.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


SUPPORTED_RERANKER_PROVIDERS = {"none", "local-score"}


@dataclass(frozen=True)
class RerankerPolicy:
    enabled: bool = False
    provider: str = "none"
    model: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "provider": self.provider,
            "model": self.model,
        }


def build_reranker_policy(
    settings: Any | None = None,
    *,
    enabled: bool | None = None,
    provider: str | None = None,
) -> RerankerPolicy:
    if settings is not None:
        setting_enabled = bool(getattr(settings, "reranker_enabled", False))
        setting_provider = str(getattr(settings, "reranker_provider", "none") or "none").strip()
        setting_model = getattr(settings, "reranker_model", None)
    else:
        setting_enabled = False
        setting_provider = "none"
        setting_model = None
    return RerankerPolicy(
        enabled=setting_enabled if enabled is None else bool(enabled),
        provider=(setting_provider if provider is None else str(provider or "none")).strip() or "none",
        model=str(setting_model).strip() if setting_model else None,
    )


def apply_reranker(
    *,
    query: str,
    ranked: dict[str, Any],
    policy: RerankerPolicy | dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected = _coerce_policy(policy)
    results = list(ranked.get("results") or [])
    metadata: dict[str, Any] = {
        "enabled": selected.enabled,
        "provider": selected.provider,
        "applied": False,
        "status": "disabled",
        "reason": "reranker-disabled",
        "inputCount": len(results),
        "outputCount": len(results),
    }
    if selected.model:
        metadata["model"] = selected.model

    if not selected.enabled:
        return {**ranked, "reranker": metadata}

    if selected.provider not in SUPPORTED_RERANKER_PROVIDERS:
        raise ValueError(
            f"Unsupported RAG reranker provider {selected.provider!r}; "
            f"supported providers: {sorted(SUPPORTED_RERANKER_PROVIDERS)}"
        )

    if selected.provider == "none":
        metadata.update(
            {
                "status": "deferred",
                "reason": "provider-none-noop",
                "queryLength": len(str(query)),
            }
        )
        return {**ranked, "reranker": metadata}

    if selected.provider == "local-score":
        reranked = [_with_local_rerank_score(query, item) for item in results]
        reranked.sort(key=lambda item: item["rerankScore"], reverse=True)
        output = [_strip_internal_rerank(item) for item in reranked]
        metadata.update(
            {
                "applied": True,
                "status": "applied",
                "reason": "local-score-evidence-authority",
                "outputCount": len(output),
            }
        )
        return {**ranked, "results": output, "reranker": metadata}

    return {**ranked, "reranker": metadata}


def _coerce_policy(policy: RerankerPolicy | dict[str, Any] | None) -> RerankerPolicy:
    if isinstance(policy, RerankerPolicy):
        return policy
    if isinstance(policy, dict):
        return RerankerPolicy(
            enabled=bool(policy.get("enabled", False)),
            provider=str(policy.get("provider") or "none").strip() or "none",
            model=str(policy.get("model")).strip() if policy.get("model") else None,
        )
    return RerankerPolicy()


def _with_local_rerank_score(query: str, result: dict[str, Any]) -> dict[str, Any]:
    components = result.get("scoreComponents") or {}
    governance = result.get("governance") if isinstance(result.get("governance"), dict) else {}
    base = _float(result.get("score"), 0.0)
    keyword = _float(components.get("keyword"), 0.0)
    recency = _float(components.get("recency"), 0.0)
    intent = _float(components.get("intentBoost"), 1.0)
    authority = max(min(_float(governance.get("authorityRank"), _float(components.get("authorityRank"), 0.0)), 100.0), 0.0) / 100.0
    provenance = max(min(_float(governance.get("provenanceScore"), _float(components.get("provenanceScore"), 0.0)), 1.0), 0.0)
    lifecycle = _lifecycle_score(str(governance.get("lifecycle") or ""))
    term_overlap = _query_term_overlap(query, result)
    exact_phrase = 1.0 if _normalized_query(query) and _normalized_query(query) in _result_text(result) else 0.0
    local_score = (
        base
        + (keyword * 0.10)
        + (term_overlap * 0.16)
        + (exact_phrase * 0.08)
        + (recency * 0.03)
        + max(intent - 1.0, 0.0) * 0.14
        + (authority * 0.06)
        + (provenance * 0.05)
        + (lifecycle * 0.06)
    )
    enriched = dict(result)
    enriched["rerankScore"] = round(local_score, 6)
    enriched["scoreComponents"] = {
        **components,
        "rerankerLocalScore": round(local_score, 6),
        "rerankerTermOverlap": round(term_overlap, 6),
        "rerankerExactPhrase": round(exact_phrase, 6),
        "rerankerAuthority": round(authority, 6),
        "rerankerProvenance": round(provenance, 6),
        "rerankerLifecycle": round(lifecycle, 6),
    }
    return enriched


def _strip_internal_rerank(result: dict[str, Any]) -> dict[str, Any]:
    cleaned = dict(result)
    cleaned.pop("rerankScore", None)
    return cleaned


def _float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _query_term_overlap(query: str, result: dict[str, Any]) -> float:
    terms = _terms(query)
    if not terms:
        return 0.0
    text = _result_text(result)
    hits = sum(1 for term in terms if term in text)
    return hits / max(len(terms), 1)


def _terms(text: str) -> list[str]:
    terms = re.findall(r"[\u4e00-\u9fff]{2,8}|[a-zA-Z0-9_-]{3,30}", str(text).lower())
    seen: set[str] = set()
    deduped: list[str] = []
    for term in terms:
        if term in seen:
            continue
        seen.add(term)
        deduped.append(term)
    return deduped


def _result_text(result: dict[str, Any]) -> str:
    parts = [
        str(result.get("text") or ""),
        str(result.get("textPreview") or ""),
        " ".join(str(tag) for tag in result.get("tags") or []),
        str(result.get("sourceSet") or ""),
        str(result.get("workType") or ""),
    ]
    return " ".join(parts).lower()


def _normalized_query(query: str) -> str:
    return " ".join(str(query or "").lower().split())


def _lifecycle_score(lifecycle: str) -> float:
    return {
        "current-state": 1.0,
        "canonical": 0.95,
        "task-history": 0.82,
        "period-summary": 0.78,
        "structured-report": 0.74,
        "narrative": 0.7,
        "metric": 0.68,
        "snapshot": 0.65,
        "episodic": 0.58,
    }.get(str(lifecycle or ""), 0.45)
