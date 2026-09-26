"""Read-only runtime coverage and source artifacts; never register or run agents."""

from datetime import datetime, timezone
import hashlib
import json
import threading
import time

from data_foundation.external_tool_definitions import TOOL_CATALOG
from data_foundation.runtime_sources.registry import LOCAL_RUNTIME_IDS, build_runtime
from data_foundation.settings import resolve_external_tool_paths

_cache = {}
_lock = threading.RLock()
_TTL = 30


def _load(tool_id, paths=None):
    if tool_id not in LOCAL_RUNTIME_IDS:
        raise ValueError("Unsupported runtime source")
    fields = resolve_external_tool_paths(paths)[tool_id]
    signature = hashlib.sha256(json.dumps(fields, default=str, sort_keys=True).encode()).hexdigest()
    key = (tool_id, signature)
    with _lock:
        previous = _cache.get(key)
        if previous and time.monotonic() - previous[0] < _TTL:
            return previous[1]
        runtime = build_runtime(tool_id, fields)
        artifacts = tuple(runtime.artifacts())
        sessions = tuple(runtime.sessions())
        messages = tuple(runtime.dialogue())
        usage = tuple(runtime.usage())
        documents = tuple(getattr(runtime, "documents", lambda: ())())
        # Only bounded cache lifetime. No snapshots of private content are written.
        value = (runtime, artifacts, sessions, messages, usage, documents)
        for old_key, (created, _) in list(_cache.items()):
            if time.monotonic() - created >= _TTL:
                _cache.pop(old_key, None)
        _cache[key] = (time.monotonic(), value)
        return value


def inventory(paths=None, *, force=False):
    if force:
        with _lock:
            _cache.clear()
    items = []
    for tool_id in LOCAL_RUNTIME_IDS:
        definition = TOOL_CATALOG[tool_id]
        item = {"id": tool_id, "name": definition["name"], "emoji": definition["emoji"],
            "experimental": bool(definition.get("experimental")), "validation": definition.get("validation", "existing-adapter"),
            "coverage": definition.get("coverage", "local-partial"),
            "sessionUnit": "history-files" if tool_id == "aider" else "sessions",
            "skillRegistration": bool((definition.get("globalSkillRegistration") or {}).get("targets")),
            "sessionCount": None, "messageCount": None, "documentCount": None, "usageEventCount": None}
        try:
            runtime, artifacts, sessions, messages, usage, documents = _load(tool_id, paths)
            diagnostics = getattr(runtime, "diagnostics", [])
            if len(artifacts) >= 5000:
                diagnostics = [*diagnostics, {"code": "discovery-limit"}]
            readable = bool(sessions or messages or usage or documents)
            item.update(status="partial" if readable and (diagnostics or item["experimental"]) else "ready" if readable else "unrecognized" if artifacts and diagnostics else "empty" if artifacts else "not-found",
                sessionCount=len(sessions), messageCount=len(messages), documentCount=len(documents),
                usageEventCount=len(usage), usageStatus=runtime.usage_status if usage or runtime.usage_status == "available" else "unavailable",
                undatedMessages=sum(r.occurred_at is None for r in messages),
                diagnostics=list(dict.fromkeys(d["code"] for d in diagnostics)))
        except Exception:
            item.update(status="unavailable", diagnostics=["source-read-failed"])
        items.append(item)
    return {"schemaVersion": 1, "readOnly": True, "generatedAt": datetime.now(timezone.utc).isoformat(), "items": items}


def details(tool_id, paths=None):
    runtime, _, sessions, messages, _, documents = _load(tool_id, paths)
    def stamp(value):
        return value.isoformat() if value else None
    ordered = sorted(messages, key=lambda m: stamp(m.occurred_at) or "", reverse=True)
    return {"id": tool_id, "name": TOOL_CATALOG[tool_id]["name"], "coverage": TOOL_CATALOG[tool_id].get("coverage", "local-partial"),
        "readOnly": True, "totalMessages": len(messages), "truncated": len(messages) > 100,
        "messages": [{"role": m.role, "content": m.content[:12000], "truncated": len(m.content) > 12000,
                      "occurredAt": stamp(m.occurred_at), "sessionId": m.external_session_key} for m in ordered[:100]],
        "documents": [{"id": d.external_document_key, "title": d.title, "kind": d.kind,
                       "occurredAt": stamp(d.occurred_at), "sourceVariant": d.source_variant} for d in documents]}


def document(tool_id, document_id, paths=None):
    _, _, _, _, _, documents = _load(tool_id, paths)
    doc = next((d for d in documents if d.external_document_key == document_id), None)
    if doc is None:
        raise LookupError("Source document not found")
    return {"id": doc.external_document_key, "title": doc.title, "kind": doc.kind,
        "content": doc.content[:200000], "truncated": len(doc.content) > 200000,
        "source": TOOL_CATALOG[tool_id]["name"], "readOnly": True, "verification": "source-authored",
        "occurredAt": doc.occurred_at.isoformat() if doc.occurred_at else None}
