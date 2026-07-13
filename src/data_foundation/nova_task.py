"""Authoritative Nova-Task v2 SQLite graph helpers.

These operations are shared by the Dashboard, reconciliation tools, and the
production pipeline.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

from .db import connect, migrate
from .nova_task_layers import (
    CANDIDATE_STATUS_CONFIRMED,
    CANDIDATE_STATUS_DEFERRED,
    CANDIDATE_STATUS_MERGED,
    CANDIDATE_STATUS_PENDING_REVIEW,
    CANDIDATE_STATUS_REJECTED,
    CANDIDATE_STATUS_SUPERSEDED,
    LAYER_PROJECT_GRAPH,
    NODE_CREATED_BY_AGENT,
    NODE_CREATED_BY_HUMAN,
    NODE_MANAGED_BY_AGENT,
    NODE_MANAGED_BY_HUMAN,
    ORIGIN_OBSERVED,
    ORIGIN_PLANNED,
    STATE_AUTHORITY_OBSERVED_SIGNAL,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
    TASK_NODE_STATUS_ACTIVE,
    TASK_NODE_STATUS_ARCHIVED,
    TASK_NODE_STATUS_AUTOMATIC,
    TASK_NODE_STATUS_BLOCKED,
    TASK_NODE_STATUS_COMPLETED,
    TASK_NODE_STATUS_DONE,
    TASK_NODE_STATUS_PAUSED,
    TASK_NODE_STATUS_PLANNED,
    TASK_NODE_STATUS_SETTLED,
    TASK_NODE_STATUS_STALE,
    allows_planned_state_machine,
    project_graph_metadata,
)
from .paths import RuntimePaths
from .workspace_attribution import build_workspace_attribution_catalog, canonical_workspace_name


def _now() -> str:
    return datetime.now().astimezone().isoformat()


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:12]}"


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _load_json(raw: str | None, fallback: Any) -> Any:
    try:
        return json.loads(raw or "")
    except Exception:
        return fallback


def _candidate_fingerprint(*, source_event_id: str | None, evidence: list[str] | None, reason: str) -> str:
    payload = {"sourceEventId": source_event_id or "", "evidence": evidence or [], "reason": reason}
    return hashlib.sha256(_json(payload).encode("utf-8")).hexdigest()


_STATUS_SIGNAL_MAP = {
    "pending": TASK_NODE_STATUS_PLANNED,
    "planned": TASK_NODE_STATUS_PLANNED,
    "ongoing": TASK_NODE_STATUS_ACTIVE,
    "active": TASK_NODE_STATUS_ACTIVE,
    "blocked": TASK_NODE_STATUS_BLOCKED,
    "paused": TASK_NODE_STATUS_PAUSED,
    "completed": TASK_NODE_STATUS_DONE,
    "complete": TASK_NODE_STATUS_DONE,
    "done": TASK_NODE_STATUS_DONE,
    "automatic": TASK_NODE_STATUS_AUTOMATIC,
    "observed": TASK_NODE_STATUS_AUTOMATIC,
    "settled": TASK_NODE_STATUS_SETTLED,
    "stale": TASK_NODE_STATUS_STALE,
    "aborted": TASK_NODE_STATUS_ARCHIVED,
    "archived": TASK_NODE_STATUS_ARCHIVED,
}

_STATUS_TAGS = {"delayed", "paused", "waiting_external", "needs_review", "stale", "low_confidence"}
_TASK_NODE_STATUSES = {
    TASK_NODE_STATUS_ACTIVE,
    TASK_NODE_STATUS_PLANNED,
    TASK_NODE_STATUS_BLOCKED,
    TASK_NODE_STATUS_PAUSED,
    TASK_NODE_STATUS_COMPLETED,
    TASK_NODE_STATUS_DONE,
    TASK_NODE_STATUS_AUTOMATIC,
    TASK_NODE_STATUS_SETTLED,
    TASK_NODE_STATUS_STALE,
    TASK_NODE_STATUS_ARCHIVED,
}
_PENDING_CANDIDATE_STATUSES = {CANDIDATE_STATUS_PENDING_REVIEW, "pending"}
_HUMAN_MANUAL_ACTORS = {
    "dashboard",
    "operator",
    "user",
    "human",
    "nova-task-planning-import",
}


def _managed_by(metadata: dict[str, Any] | None) -> str:
    if not isinstance(metadata, dict):
        return NODE_MANAGED_BY_HUMAN
    value = str(metadata.get("managedBy") or "").strip()
    if value in {NODE_MANAGED_BY_AGENT, NODE_MANAGED_BY_HUMAN}:
        return value
    if metadata.get("origin") == ORIGIN_OBSERVED or metadata.get("stateAuthority") == STATE_AUTHORITY_OBSERVED_SIGNAL:
        return NODE_MANAGED_BY_AGENT
    return NODE_MANAGED_BY_HUMAN


def _created_by(metadata: dict[str, Any] | None, *, actor: str) -> str:
    if isinstance(metadata, dict):
        value = str(metadata.get("createdBy") or "").strip()
        if value in {NODE_CREATED_BY_AGENT, NODE_CREATED_BY_HUMAN}:
            return value
        if metadata.get("origin") == ORIGIN_OBSERVED or metadata.get("stateAuthority") == STATE_AUTHORITY_OBSERVED_SIGNAL:
            return NODE_CREATED_BY_AGENT
    return NODE_CREATED_BY_HUMAN if actor in _HUMAN_MANUAL_ACTORS else NODE_CREATED_BY_AGENT


def _normalize_node_metadata(metadata: dict[str, Any] | None, *, actor: str) -> dict[str, Any]:
    resolved = dict(metadata or {})
    created_by = _created_by(resolved, actor=actor)
    managed_by = _managed_by(resolved)
    if "managedBy" not in resolved:
        managed_by = NODE_MANAGED_BY_HUMAN if created_by == NODE_CREATED_BY_HUMAN else NODE_MANAGED_BY_AGENT
    resolved["createdBy"] = created_by
    resolved["managedBy"] = managed_by
    return resolved


def _is_human_manual_actor(actor: str) -> bool:
    return str(actor or "") in _HUMAN_MANUAL_ACTORS


def _normalize_requested_status_for_manager(status: str, *, managed_by: str) -> str:
    if managed_by == NODE_MANAGED_BY_AGENT:
        if status in {TASK_NODE_STATUS_SETTLED, TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE}:
            return TASK_NODE_STATUS_SETTLED
        if status == TASK_NODE_STATUS_ARCHIVED:
            return TASK_NODE_STATUS_ARCHIVED
        if status == TASK_NODE_STATUS_STALE:
            return TASK_NODE_STATUS_STALE
        return TASK_NODE_STATUS_AUTOMATIC
    if status == TASK_NODE_STATUS_COMPLETED:
        return TASK_NODE_STATUS_DONE
    if status == TASK_NODE_STATUS_SETTLED:
        return TASK_NODE_STATUS_DONE
    if status in {TASK_NODE_STATUS_AUTOMATIC, TASK_NODE_STATUS_STALE}:
        return TASK_NODE_STATUS_ACTIVE
    return status


def normalize_candidate_status_filter(status: str | None) -> str:
    value = str(status or CANDIDATE_STATUS_PENDING_REVIEW).strip()
    return CANDIDATE_STATUS_PENDING_REVIEW if value in {"", "pending"} else value


def _normalize_status_signal(value: Any) -> str | None:
    return _STATUS_SIGNAL_MAP.get(str(value or "").strip().lower())


def _status_tags(value: Any) -> list[str]:
    return [item for item in _string_list(value) if item in _STATUS_TAGS]


@dataclass(frozen=True)
class NovaTaskNode:
    node_id: str
    title: str
    node_type: str
    status: str
    parent_node_id: str | None
    progress: int


@dataclass(frozen=True)
class NovaTaskCandidate:
    candidate_id: str
    candidate_type: str
    proposed_title: str
    status: str
    proposed_parent_node_id: str | None
    source_fingerprint: str


@dataclass(frozen=True)
class NovaTaskCandidateDecision:
    candidate_id: str
    decision_id: str
    status: str


@dataclass(frozen=True)
class NovaTaskBoardExport:
    export_id: str
    target_path: str
    content_sha256: str
    node_count: int


@dataclass(frozen=True)
class NovaTaskEvidenceIngest:
    event_count: int
    candidate_count: int
    pending_candidate_count: int
    malformed: bool = False


@dataclass(frozen=True)
class NovaTaskAnchorReconciliation:
    project_count: int
    candidate_count: int
    skipped_count: int
    pending_candidate_count: int


def create_task_node(
    paths: RuntimePaths,
    *,
    title: str,
    node_type: str = "task",
    parent_node_id: str | None = None,
    status: str = "active",
    progress: int = 0,
    scope: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
    actor: str = "system",
    node_id: str | None = None,
) -> NovaTaskNode:
    """Create an authoritative task graph node and audit the write."""
    migrate(paths)
    created_at = _now()
    task_id = node_id or _new_id("NT")
    resolved_metadata = _normalize_node_metadata(metadata, actor=actor)
    resolved_status = _normalize_requested_status_for_manager(str(status or "active"), managed_by=_managed_by(resolved_metadata))
    resolved_progress = int(progress)
    if resolved_status in {TASK_NODE_STATUS_DONE, TASK_NODE_STATUS_SETTLED, TASK_NODE_STATUS_COMPLETED}:
        resolved_progress = 100
    with connect(paths) as connection:
        connection.execute(
            """
            INSERT INTO nova_task_nodes(
                node_id, parent_node_id, node_type, title, status, progress,
                scope_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                task_id,
                parent_node_id,
                node_type,
                title,
                resolved_status,
                resolved_progress,
                _json(scope),
                _json(resolved_metadata),
                created_at,
                created_at,
            ),
        )
        if _managed_by(resolved_metadata) == NODE_MANAGED_BY_HUMAN:
            _claim_human_management_path(
                connection,
                node_id=task_id,
                actor=actor,
                now=created_at,
                reason="human_node_created_or_attached",
                include_self=False,
            )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, after_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("NTA"),
                created_at,
                actor,
                "create_node",
                task_id,
                _json(
                    {
                        "nodeId": task_id,
                        "title": title,
                        "nodeType": node_type,
                        "status": resolved_status,
                        "createdBy": resolved_metadata.get("createdBy"),
                        "managedBy": resolved_metadata.get("managedBy"),
                    }
                ),
            ),
        )
    return NovaTaskNode(task_id, title, node_type, resolved_status, parent_node_id, resolved_progress)


def update_task_node(
    paths: RuntimePaths,
    *,
    node_id: str,
    actor: str,
    title: str | None = None,
    status: str | None = None,
    parent_node_id: Any = ...,
    progress: int | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskNode:
    """Update an authoritative task graph node and audit the write."""
    migrate(paths)
    allowed_status = _TASK_NODE_STATUSES
    now = _now()
    with connect(paths) as connection:
        row = connection.execute("SELECT * FROM nova_task_nodes WHERE node_id = ?", (node_id,)).fetchone()
        if row is None:
            raise ValueError(f"Nova-Task v2 node not found: {node_id}")
        before = dict(row)
        resolved_title = str(title).strip() if title is not None else row["title"]
        if not resolved_title:
            raise ValueError("title cannot be empty")
        resolved_metadata = _load_json(row["metadata_json"], {})
        if not isinstance(resolved_metadata, dict):
            resolved_metadata = {}
        if metadata:
            resolved_metadata.update(metadata)
        manual_takeover = _is_human_manual_actor(actor) and (
            title is not None or status is not None or parent_node_id is not ... or progress is not None or bool(metadata)
        )
        if manual_takeover:
            resolved_metadata["managedBy"] = NODE_MANAGED_BY_HUMAN
            resolved_metadata.setdefault("humanManagedAt", now)
            resolved_metadata["humanManagedBy"] = actor
        resolved_status = str(status or row["status"])
        if resolved_status not in allowed_status:
            raise ValueError(f"unsupported Nova-Task status: {resolved_status}")
        resolved_status = _normalize_requested_status_for_manager(
            resolved_status,
            managed_by=_managed_by(resolved_metadata),
        )
        resolved_parent = row["parent_node_id"] if parent_node_id is ... else parent_node_id
        if resolved_parent == "":
            resolved_parent = None
        if resolved_parent == node_id:
            raise ValueError("node cannot be parented to itself")
        if resolved_parent is not None:
            parent = connection.execute("SELECT 1 FROM nova_task_nodes WHERE node_id = ?", (resolved_parent,)).fetchone()
            if parent is None:
                raise ValueError(f"parent node not found: {resolved_parent}")
            cursor = resolved_parent
            seen = {node_id}
            while cursor:
                if cursor in seen:
                    raise ValueError("parent update would create a cycle")
                seen.add(cursor)
                parent_row = connection.execute(
                    "SELECT parent_node_id FROM nova_task_nodes WHERE node_id = ?",
                    (cursor,),
                ).fetchone()
                cursor = parent_row["parent_node_id"] if parent_row is not None else None
        resolved_progress = int(progress if progress is not None else row["progress"])
        resolved_progress = max(0, min(100, resolved_progress))
        completed_at = row["completed_at"]
        if resolved_status in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE, TASK_NODE_STATUS_SETTLED} and completed_at is None:
            completed_at = now
            resolved_progress = 100
        if resolved_status not in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE, TASK_NODE_STATUS_SETTLED}:
            completed_at = None
        connection.execute(
            """
            UPDATE nova_task_nodes
            SET title = ?, status = ?, parent_node_id = ?, progress = ?,
                completed_at = ?, updated_at = ?, metadata_json = ?
            WHERE node_id = ?
            """,
            (
                resolved_title,
                resolved_status,
                resolved_parent,
                resolved_progress,
                completed_at,
                now,
                _json(resolved_metadata),
                node_id,
            ),
        )
        after = {
            "nodeId": node_id,
            "title": resolved_title,
            "status": resolved_status,
            "parentNodeId": resolved_parent,
            "progress": resolved_progress,
            "completedAt": completed_at,
        }
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, before_json, after_json
            ) VALUES (?, ?, ?, 'update_node', ?, ?, ?)
            """,
            (_new_id("NTA"), now, actor, node_id, _json(before), _json(after)),
        )
        if manual_takeover:
            _claim_human_management_path(
                connection,
                node_id=node_id,
                actor=actor,
                now=now,
                reason="human_manual_edit",
                include_self=False,
            )
    return NovaTaskNode(node_id, resolved_title, row["node_type"], resolved_status, resolved_parent, resolved_progress)


def _claim_human_management_path(
    connection: Any,
    *,
    node_id: str,
    actor: str,
    now: str,
    reason: str,
    include_self: bool = True,
) -> int:
    current = str(node_id or "")
    changed = 0
    seen: set[str] = set()
    first = True
    while current:
        if current in seen:
            return changed
        seen.add(current)
        row = connection.execute("SELECT * FROM nova_task_nodes WHERE node_id = ?", (current,)).fetchone()
        if row is None:
            return changed
        if include_self or not first:
            metadata = _load_json(row["metadata_json"], {})
            if not isinstance(metadata, dict):
                metadata = {}
            before_managed_by = _managed_by(metadata)
            if before_managed_by != NODE_MANAGED_BY_HUMAN:
                before = dict(row)
                metadata["createdBy"] = _created_by(metadata, actor=actor)
                metadata["managedBy"] = NODE_MANAGED_BY_HUMAN
                metadata["humanManagedAt"] = now
                metadata["humanManagedBy"] = actor
                metadata["humanManagementReason"] = reason
                status = str(row["status"] or "")
                if status in {TASK_NODE_STATUS_AUTOMATIC, TASK_NODE_STATUS_SETTLED, TASK_NODE_STATUS_STALE}:
                    status = TASK_NODE_STATUS_DONE if status == TASK_NODE_STATUS_SETTLED else TASK_NODE_STATUS_ACTIVE
                completed_at = row["completed_at"]
                if status == TASK_NODE_STATUS_DONE and completed_at is None:
                    completed_at = now
                if status != TASK_NODE_STATUS_DONE:
                    completed_at = None
                progress = 100 if status == TASK_NODE_STATUS_DONE else int(row["progress"] or 0)
                connection.execute(
                    """
                    UPDATE nova_task_nodes
                    SET status = ?, progress = ?, completed_at = ?, metadata_json = ?, updated_at = ?
                    WHERE node_id = ?
                    """,
                    (status, progress, completed_at, _json(metadata), now, current),
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_audit_log(
                        audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
                    ) VALUES (?, ?, ?, 'claim_node_management_human', ?, ?, ?, ?)
                    """,
                    (
                        _new_id("NTA"),
                        now,
                        actor,
                        current,
                        _json(before),
                        _json(
                            {
                                "nodeId": current,
                                "status": status,
                                "managedBy": NODE_MANAGED_BY_HUMAN,
                                "createdBy": metadata.get("createdBy"),
                            }
                        ),
                        _json({"reason": reason, "sourceNodeId": node_id}),
                    ),
                )
                changed += 1
        first = False
        current = row["parent_node_id"]
    return changed


def create_task_candidate(
    paths: RuntimePaths,
    *,
    candidate_type: str,
    proposed_title: str,
    reason: str,
    proposed_parent_node_id: str | None = None,
    matched_node_id: str | None = None,
    confidence: str = "unknown",
    evidence: list[str] | None = None,
    source_event_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskCandidate:
    """Create or return an idempotent candidate awaiting user review."""
    migrate(paths)
    now = _now()
    fingerprint = _candidate_fingerprint(source_event_id=source_event_id, evidence=evidence, reason=reason)
    with connect(paths) as connection:
        existing = connection.execute(
            """
            SELECT candidate_id, candidate_type, proposed_title, status,
                   proposed_parent_node_id, source_fingerprint
            FROM nova_task_candidates
            WHERE candidate_type = ?
              AND proposed_title = ?
              AND COALESCE(proposed_parent_node_id, '') = COALESCE(?, '')
              AND source_fingerprint = ?
            """,
            (candidate_type, proposed_title, proposed_parent_node_id, fingerprint),
        ).fetchone()
        if existing:
            return NovaTaskCandidate(
                existing["candidate_id"],
                existing["candidate_type"],
                existing["proposed_title"],
                existing["status"],
                existing["proposed_parent_node_id"],
                existing["source_fingerprint"],
            )
        candidate_id = _new_id("NTC")
        connection.execute(
            """
            INSERT INTO nova_task_candidates(
                candidate_id, candidate_type, proposed_title, proposed_parent_node_id,
                matched_node_id, status, confidence, reason, evidence_json,
                source_event_id, source_fingerprint, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                candidate_id,
                candidate_type,
                proposed_title,
                proposed_parent_node_id,
                matched_node_id,
                CANDIDATE_STATUS_PENDING_REVIEW,
                confidence,
                reason,
                _json(evidence or []),
                source_event_id,
                fingerprint,
                _json(metadata),
                now,
                now,
            ),
        )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, candidate_id, after_json
            ) VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("NTA"),
                now,
                "pipeline",
                "create_candidate",
                candidate_id,
                _json({"candidateId": candidate_id, "candidateType": candidate_type, "title": proposed_title}),
            ),
        )
    return NovaTaskCandidate(
        candidate_id,
        candidate_type,
        proposed_title,
        CANDIDATE_STATUS_PENDING_REVIEW,
        proposed_parent_node_id,
        fingerprint,
    )


def _node_level(node_type: str) -> int:
    return {
        "track": 1,
        "workstream": 2,
        "task": 3,
        "subtask": 4,
        "step": 5,
    }.get(str(node_type or ""), 3)


def _suggested_node_type_from_item(item: dict[str, Any], *, fallback: str) -> str:
    raw_type = str(item.get("suggested_node_type") or item.get("node_type") or "").strip()
    aliases = {
        "deliverable": "task",
        "action": "step",
        "check": "step",
    }
    raw_type = aliases.get(raw_type, raw_type)
    if raw_type in {"track", "workstream", "task", "subtask", "step"}:
        return raw_type
    try:
        level = int(item.get("proposed_level") or item.get("level") or 0)
    except (TypeError, ValueError):
        level = 0
    return {
        1: "track",
        2: "workstream",
        3: "task",
        4: "subtask",
        5: "step",
    }.get(level, fallback)


def normalized_l1_anchor_title(value: str | None) -> str:
    canonical = canonical_workspace_name(value).lower()
    canonical = canonical.replace("_", "-")
    canonical = re.sub(r"\s+", "-", canonical)
    for suffix in ("-system", "-project", "-project-development", "-app", "系统", "项目开发"):
        if canonical.endswith(suffix):
            canonical = canonical[: -len(suffix)].strip("- ")
    return "".join(ch for ch in canonical if ch.isalnum())


def _root_workspace_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    workspace = metadata.get("workspace") if isinstance(metadata, dict) else {}
    return workspace if isinstance(workspace, dict) else {}


def _workspace_payload_from_anchor_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    workspace = _root_workspace_metadata(metadata)
    root_path = str(workspace.get("rootPath") or "")
    if not root_path:
        return {}
    return {
        "displayName": str(workspace.get("displayName") or metadata.get("proposedTitle") or Path(root_path).name),
        "rootPath": root_path,
        "confidence": workspace.get("confidence"),
        "evidence": workspace.get("evidence"),
        "sources": workspace.get("sources") if isinstance(workspace.get("sources"), list) else [],
        "observationCount": int(workspace.get("observationCount") or 0),
    }


def _root_matches_project(row: Any, *, title: str, root_path: str) -> bool:
    metadata = _load_json(row["metadata_json"], {})
    workspace = _root_workspace_metadata(metadata if isinstance(metadata, dict) else {})
    if root_path and str(workspace.get("rootPath") or "") == root_path:
        return True
    normalized = normalized_l1_anchor_title(title)
    title_candidates = [
        str(row["title"] or ""),
        str(workspace.get("displayName") or ""),
        str(workspace.get("name") or ""),
    ]
    if root_path:
        title_candidates.append(Path(root_path).name)
    return bool(normalized and any(normalized_l1_anchor_title(candidate) == normalized for candidate in title_candidates))


def _existing_root_node_for_project(connection: Any, *, title: str, root_path: str) -> str | None:
    row = _existing_root_row_for_project(connection, title=title, root_path=root_path)
    return str(row["node_id"]) if row is not None else None


def _existing_root_row_for_project(connection: Any, *, title: str, root_path: str) -> Any | None:
    rows = connection.execute(
        """
        SELECT *
        FROM nova_task_nodes
        WHERE parent_node_id IS NULL
          AND status IN ('active', 'planned', 'blocked')
        """
    ).fetchall()
    for row in rows:
        if _root_matches_project(row, title=title, root_path=root_path):
            return row
    return None


def _bind_workspace_metadata_to_l1(
    paths: RuntimePaths,
    *,
    node_id: str,
    project: dict[str, Any],
    display_name: str,
    root_path: str,
) -> bool:
    now = _now()
    with connect(paths) as connection:
        row = connection.execute(
            """
            SELECT *
            FROM nova_task_nodes
            WHERE node_id = ?
              AND parent_node_id IS NULL
              AND status IN ('active', 'planned', 'blocked')
            """,
            (node_id,),
        ).fetchone()
        if row is None:
            return False
        return _bind_workspace_metadata_to_l1_row(
            connection,
            row=row,
            workspace={
                "displayName": display_name,
                "rootPath": root_path,
                "confidence": project.get("confidence"),
                "evidence": project.get("evidence"),
                "sources": project.get("sources") if isinstance(project.get("sources"), list) else [],
                "observationCount": int(project.get("observation_count") or 0),
            },
            actor="pipeline",
            now=now,
        )


def _bind_workspace_metadata_to_l1_row(
    connection: Any,
    *,
    row: Any,
    workspace: dict[str, Any],
    actor: str,
    now: str,
) -> bool:
    root_path = str(workspace.get("rootPath") or "")
    if not root_path:
        return False
    metadata = _load_json(row["metadata_json"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    existing_workspace = _root_workspace_metadata(metadata)
    existing_root = str(existing_workspace.get("rootPath") or "")
    if existing_root:
        return False
    before = dict(row)
    metadata["workspace"] = {
        **existing_workspace,
        **workspace,
        "boundFrom": "workspace-attribution",
        "boundAt": now,
    }
    connection.execute(
        """
        UPDATE nova_task_nodes
        SET metadata_json = ?, updated_at = ?
        WHERE node_id = ?
        """,
        (_json(metadata), now, row["node_id"]),
    )
    connection.execute(
        """
        INSERT INTO nova_task_audit_log(
            audit_id, occurred_at, actor, action, node_id, before_json, after_json
        ) VALUES (?, ?, ?, 'bind_l1_workspace_anchor', ?, ?, ?)
        """,
        (
            _new_id("NTA"),
            now,
            actor,
            row["node_id"],
            _json(before),
            _json({"nodeId": row["node_id"], "workspace": metadata["workspace"]}),
        ),
    )
    return True


def _parser_observed_project(project: dict[str, Any]) -> bool:
    sources = project.get("sources") if isinstance(project.get("sources"), list) else []
    return any(str(source) not in {"runtime", "settings"} for source in sources)


def _covered_by_existing_root_anchor(connection: Any, title: str) -> bool:
    proposed = canonical_workspace_name(title).lower()
    rows = connection.execute(
        """
        SELECT title
        FROM nova_task_nodes
        WHERE parent_node_id IS NULL
          AND status IN ('active', 'planned', 'blocked')
        """
    ).fetchall()
    for row in rows:
        root_title = canonical_workspace_name(row["title"]).lower()
        if root_title and (proposed == root_title or proposed.startswith(root_title)):
            return True
        anchors = re.findall(r"[a-z0-9][a-z0-9._-]{2,}", root_title)
        if any(anchor in proposed for anchor in anchors):
            return True
    return False


def reconcile_workspace_project_anchors(
    paths: RuntimePaths,
    *,
    observed_paths: list[str | Path] | tuple[str | Path, ...] = (),
) -> NovaTaskAnchorReconciliation:
    """Create review candidates for high-confidence workspace project anchors.

    Level 1 project anchors are protected: deterministic parser evidence may
    create a candidate, but never writes a root graph node without operator
    confirmation.
    """
    migrate(paths)
    catalog = build_workspace_attribution_catalog(paths, observed_paths=observed_paths)
    projects = [item for item in catalog.get("projects") or [] if isinstance(item, dict)]
    skipped = 0
    with connect(paths, read_only=True) as connection:
        before_candidates = int(connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0])
    for project in projects:
        display_name = canonical_workspace_name(project.get("display_name"))
        root_path = str(project.get("root_path") or "")
        if not display_name or not root_path:
            skipped += 1
            continue
        if project.get("confidence") != "high" or project.get("evidence") != "project-marker":
            skipped += 1
            continue
        if not _parser_observed_project(project):
            skipped += 1
            continue
        with connect(paths, read_only=True) as connection:
            existing_node_id = _existing_root_node_for_project(connection, title=display_name, root_path=root_path)
        if existing_node_id:
            _bind_workspace_metadata_to_l1(
                paths,
                node_id=existing_node_id,
                project=project,
                display_name=display_name,
                root_path=root_path,
            )
            skipped += 1
            continue
        metadata = {
            "novaTaskLayer": "planning_overlay",
            "source": "workspace-attribution",
            "candidateKind": "project_anchor",
            "suggestedNodeType": "track",
            "level": 1,
            "reviewPolicy": "manual_required_for_level_1",
            "workspace": {
                "displayName": display_name,
                "rootPath": root_path,
                "confidence": project.get("confidence"),
                "evidence": project.get("evidence"),
                "sources": project.get("sources") if isinstance(project.get("sources"), list) else [],
                "observationCount": int(project.get("observation_count") or 0),
            },
        }
        candidate = create_task_candidate(
            paths,
            candidate_type="parent_task",
            proposed_title=display_name,
            reason="High-confidence parser workspace project anchor requires operator approval before Level 1 graph write.",
            evidence=[f"workspace:{root_path}", f"name:{display_name}"],
            confidence="high",
            metadata=metadata,
        )
        del candidate
    with connect(paths, read_only=True) as connection:
        after_candidates = int(connection.execute("SELECT COUNT(*) FROM nova_task_candidates").fetchone()[0])
    created = max(0, after_candidates - before_candidates)
    return NovaTaskAnchorReconciliation(len(projects), created, skipped, pending_candidate_count(paths))


def confirm_candidate_as_task(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    title: str | None = None,
    parent_node_id: str | None = None,
    node_type: str | None = None,
    reason: str | None = None,
) -> NovaTaskNode:
    """Confirm a pending-review candidate into the authoritative active task graph."""
    migrate(paths)
    now = _now()
    with connect(paths) as connection:
        candidate = connection.execute(
            "SELECT * FROM nova_task_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"Nova-Task v2 candidate not found: {candidate_id}")
        if candidate["status"] not in _PENDING_CANDIDATE_STATUSES:
            raise ValueError(f"Nova-Task v2 candidate is not pending review: {candidate_id}")
        if candidate["candidate_type"] == "status_update":
            matched_node_id = candidate["matched_node_id"]
            if not matched_node_id:
                raise ValueError(f"Nova-Task v2 status update candidate has no matched node: {candidate_id}")
            node = connection.execute("SELECT * FROM nova_task_nodes WHERE node_id = ?", (matched_node_id,)).fetchone()
            if node is None:
                raise ValueError(f"Nova-Task v2 matched node not found: {matched_node_id}")
            decision_id = _new_id("NTD")
            before = dict(node)
            metadata = _load_json(node["metadata_json"], {})
            candidate_metadata = _load_json(candidate["metadata_json"], {})
            raw = candidate_metadata.get("raw") if isinstance(candidate_metadata, dict) else {}
            if not isinstance(metadata, dict):
                metadata = {}
            target_status = _normalize_status_signal(
                candidate_metadata.get("target_status") if isinstance(candidate_metadata, dict) else None
            )
            if target_status is None and isinstance(raw, dict):
                target_status = _normalize_status_signal(raw.get("target_status") or raw.get("suggested_status"))
            target_status = target_status or "completed"
            status_reason = str(
                (candidate_metadata.get("status_reason") if isinstance(candidate_metadata, dict) else None)
                or (raw.get("status_reason") if isinstance(raw, dict) else None)
                or reason
                or candidate["reason"]
                or ""
            )
            status_tags = _status_tags(
                (candidate_metadata.get("status_tags") if isinstance(candidate_metadata, dict) else None)
                or (raw.get("status_tags") if isinstance(raw, dict) else None)
            )
            metadata.update(
                {
                    "statusReason": status_reason,
                    "statusTags": status_tags,
                    "statusSourceEventId": candidate["source_event_id"],
                    "statusEvidence": _load_json(candidate["evidence_json"], []),
                    "statusSignal": raw if isinstance(raw, dict) else {},
                    "statusUpdatedBy": actor,
                }
            )
            completed_at = now if target_status in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE} else None
            progress = 100 if target_status in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE} else int(node["progress"] or 0)
            connection.execute(
                """
                UPDATE nova_task_nodes
                SET status = ?, progress = ?, completed_at = ?, updated_at = ?, metadata_json = ?
                WHERE node_id = ?
                """,
                (target_status, progress, completed_at, now, _json(metadata), matched_node_id),
            )
            connection.execute(
                """
                UPDATE nova_task_candidates
                SET status = 'confirmed', updated_at = ?, decided_at = ?
                WHERE candidate_id = ?
                """,
                (now, now, candidate_id),
            )
            after = {
                "nodeId": matched_node_id,
                "status": target_status,
                "progress": progress,
                "completedAt": completed_at,
                "statusReason": metadata.get("statusReason"),
                "statusTags": metadata.get("statusTags"),
            }
            connection.execute(
                """
                INSERT INTO nova_task_reconciliation_decisions(
                    decision_id, candidate_id, decision_type, actor, reason,
                    before_json, after_json, created_at
                ) VALUES (?, ?, 'status_update', ?, ?, ?, ?, ?)
                """,
                (decision_id, candidate_id, actor, reason, _json(before), _json(after), now),
            )
            connection.execute(
                """
                INSERT INTO nova_task_audit_log(
                    audit_id, occurred_at, actor, action, node_id, candidate_id,
                    decision_id, before_json, after_json
                ) VALUES (?, ?, ?, 'confirm_status_update_candidate', ?, ?, ?, ?, ?)
                """,
                (_new_id("NTA"), now, actor, matched_node_id, candidate_id, decision_id, _json(before), _json(after)),
            )
            return NovaTaskNode(
                matched_node_id,
                node["title"],
                node["node_type"],
                target_status,
                node["parent_node_id"],
                progress,
            )
        resolved_title = title or candidate["proposed_title"]
        resolved_parent = parent_node_id if parent_node_id is not None else candidate["proposed_parent_node_id"]
        candidate_metadata = _load_json(candidate["metadata_json"], {})
        suggested_type = candidate_metadata.get("suggestedNodeType") if isinstance(candidate_metadata, dict) else None
        resolved_type = node_type or suggested_type or ("subtask" if candidate["candidate_type"] == "subtask" else "task")
        is_workspace_project_anchor = (
            isinstance(candidate_metadata, dict)
            and candidate_metadata.get("candidateKind") == "project_anchor"
            and candidate_metadata.get("source") == "workspace-attribution"
        )
        workspace_payload = _workspace_payload_from_anchor_metadata(candidate_metadata) if isinstance(candidate_metadata, dict) else {}
        if is_workspace_project_anchor:
            existing = _existing_root_row_for_project(
                connection,
                title=resolved_title,
                root_path=str(workspace_payload.get("rootPath") or ""),
            )
            if existing is not None:
                decision_id = _new_id("NTD")
                before = dict(candidate)
                _bind_workspace_metadata_to_l1_row(
                    connection,
                    row=existing,
                    workspace=workspace_payload,
                    actor=actor,
                    now=now,
                )
                after = {
                    "nodeId": existing["node_id"],
                    "title": existing["title"],
                    "parentNodeId": existing["parent_node_id"],
                    "nodeType": existing["node_type"],
                    "decisionType": "attach_existing_l1",
                }
                connection.execute(
                    """
                    UPDATE nova_task_candidates
                    SET status = 'confirmed', matched_node_id = ?, updated_at = ?, decided_at = ?
                    WHERE candidate_id = ?
                    """,
                    (existing["node_id"], now, now, candidate_id),
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_reconciliation_decisions(
                        decision_id, candidate_id, decision_type, actor, reason,
                        before_json, after_json, created_at
                    ) VALUES (?, ?, 'attached', ?, ?, ?, ?, ?)
                    """,
                    (decision_id, candidate_id, actor, reason, _json(before), _json(after), now),
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_audit_log(
                        audit_id, occurred_at, actor, action, node_id, candidate_id,
                        decision_id, before_json, after_json
                    ) VALUES (?, ?, ?, 'attach_l1_candidate_to_existing_node', ?, ?, ?, ?, ?)
                    """,
                    (_new_id("NTA"), now, actor, existing["node_id"], candidate_id, decision_id, _json(before), _json(after)),
                )
                return NovaTaskNode(
                    existing["node_id"],
                    existing["title"],
                    existing["node_type"],
                    existing["status"],
                    existing["parent_node_id"],
                    int(existing["progress"] or 0),
                )
        node_id = _new_id("NT")
        decision_id = _new_id("NTD")
        before = dict(candidate)
        after = {"nodeId": node_id, "title": resolved_title, "parentNodeId": resolved_parent, "nodeType": resolved_type}
        node_metadata = {"confirmedFromCandidate": candidate_id}
        node_status = "active"
        if isinstance(candidate_metadata, dict):
            node_metadata["candidateMetadata"] = candidate_metadata
            if is_workspace_project_anchor:
                node_metadata.update(
                    project_graph_metadata(
                        origin=ORIGIN_OBSERVED,
                        state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                        createdFrom="workspace-attribution",
                        workspace=workspace_payload,
                    )
                )
                node_status = "active"
            elif candidate_metadata.get("novaTaskLayer") == "planning_overlay":
                node_metadata.update(
                    project_graph_metadata(
                        origin=ORIGIN_PLANNED,
                        state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                    )
                )
                node_status = "planned"
        connection.execute(
            """
            INSERT INTO nova_task_nodes(
                node_id, parent_node_id, node_type, title, status, progress,
                scope_json, metadata_json, created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, 0, '{}', ?, ?, ?)
            """,
            (node_id, resolved_parent, resolved_type, resolved_title, node_status, _json(node_metadata), now, now),
        )
        connection.execute(
            """
            UPDATE nova_task_candidates
            SET status = 'confirmed', updated_at = ?, decided_at = ?
            WHERE candidate_id = ?
            """,
            (now, now, candidate_id),
        )
        connection.execute(
            """
            INSERT INTO nova_task_reconciliation_decisions(
                decision_id, candidate_id, decision_type, actor, reason,
                before_json, after_json, created_at
            ) VALUES (?, ?, 'confirm', ?, ?, ?, ?, ?)
            """,
            (decision_id, candidate_id, actor, reason, _json(before), _json(after), now),
        )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, candidate_id,
                decision_id, before_json, after_json
            ) VALUES (?, ?, ?, 'confirm_candidate_as_task', ?, ?, ?, ?, ?)
            """,
            (_new_id("NTA"), now, actor, node_id, candidate_id, decision_id, _json(before), _json(after)),
        )
    return NovaTaskNode(node_id, resolved_title, resolved_type, node_status, resolved_parent, 0)


def list_task_candidates(paths: RuntimePaths, *, status: str = CANDIDATE_STATUS_PENDING_REVIEW, limit: int = 50) -> list[dict[str, Any]]:
    """List review candidates with parsed evidence for Dashboard review."""
    migrate(paths)
    bounded_limit = max(1, min(int(limit), 200))
    normalized_status = normalize_candidate_status_filter(status)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT candidate_id, candidate_type, proposed_title, proposed_parent_node_id,
                   matched_node_id, status, confidence, reason, evidence_json,
                   source_event_id, metadata_json, created_at, updated_at, decided_at
            FROM nova_task_candidates
            WHERE status = ?
            ORDER BY updated_at DESC, created_at DESC, candidate_id
            LIMIT ?
            """,
            (normalized_status, bounded_limit),
        ).fetchall()
    candidates: list[dict[str, Any]] = []
    for row in rows:
        evidence = json.loads(row["evidence_json"] or "[]")
        metadata = json.loads(row["metadata_json"] or "{}")
        candidates.append(
            {
                "candidateId": row["candidate_id"],
                "candidateType": row["candidate_type"],
                "proposedTitle": row["proposed_title"],
                "proposedParentNodeId": row["proposed_parent_node_id"],
                "matchedNodeId": row["matched_node_id"],
                "status": row["status"],
                "confidence": row["confidence"],
                "reason": row["reason"],
                "evidence": evidence if isinstance(evidence, list) else [],
                "sourceEventId": row["source_event_id"],
                "metadata": metadata if isinstance(metadata, dict) else {},
                "createdAt": row["created_at"],
                "updatedAt": row["updated_at"],
                "decidedAt": row["decided_at"],
            }
        )
    return candidates


def _decide_candidate(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    decision_type: str,
    status: str,
    reason: str | None = None,
) -> NovaTaskCandidateDecision:
    migrate(paths)
    now = _now()
    with connect(paths) as connection:
        candidate = connection.execute(
            "SELECT * FROM nova_task_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"Nova-Task v2 candidate not found: {candidate_id}")
        if candidate["status"] not in _PENDING_CANDIDATE_STATUSES:
            raise ValueError(f"Nova-Task v2 candidate is not pending review: {candidate_id}")
        decision_id = _new_id("NTD")
        before = dict(candidate)
        after = {"candidateId": candidate_id, "status": status}
        connection.execute(
            """
            UPDATE nova_task_candidates
            SET status = ?, updated_at = ?, decided_at = ?
            WHERE candidate_id = ?
            """,
            (status, now, now, candidate_id),
        )
        connection.execute(
            """
            INSERT INTO nova_task_reconciliation_decisions(
                decision_id, candidate_id, decision_type, actor, reason,
                before_json, after_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (decision_id, candidate_id, decision_type, actor, reason, _json(before), _json(after), now),
        )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, candidate_id,
                decision_id, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_new_id("NTA"), now, actor, f"{decision_type}_candidate", candidate_id, decision_id, _json(before), _json(after)),
        )
    return NovaTaskCandidateDecision(candidate_id, decision_id, status)


def reject_task_candidate(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    reason: str | None = None,
) -> NovaTaskCandidateDecision:
    return _decide_candidate(
        paths,
        candidate_id=candidate_id,
        actor=actor,
        decision_type="reject",
        status="rejected",
        reason=reason,
    )


def defer_task_candidate(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    reason: str | None = None,
) -> NovaTaskCandidateDecision:
    return _decide_candidate(
        paths,
        candidate_id=candidate_id,
        actor=actor,
        decision_type="defer",
        status="deferred",
        reason=reason,
    )


def _close_candidate_with_reference(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    decision_type: str,
    status: str,
    reason: str | None = None,
    target_candidate_id: str | None = None,
    target_node_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskCandidateDecision:
    migrate(paths)
    if not target_candidate_id and not target_node_id:
        raise ValueError(f"Nova-Task v2 {decision_type} requires a target candidate or node")
    if target_candidate_id and target_candidate_id == candidate_id:
        raise ValueError("candidate cannot reference itself")
    now = _now()
    with connect(paths) as connection:
        candidate = connection.execute(
            "SELECT * FROM nova_task_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"Nova-Task v2 candidate not found: {candidate_id}")
        if candidate["status"] not in _PENDING_CANDIDATE_STATUSES:
            raise ValueError(f"Nova-Task v2 candidate is not pending review: {candidate_id}")
        target_candidate = None
        target_node = None
        if target_candidate_id:
            target_candidate = connection.execute(
                "SELECT candidate_id, proposed_title, status FROM nova_task_candidates WHERE candidate_id = ?",
                (target_candidate_id,),
            ).fetchone()
            if target_candidate is None:
                raise ValueError(f"Nova-Task v2 target candidate not found: {target_candidate_id}")
        if target_node_id:
            target_node = connection.execute(
                "SELECT node_id, title, node_type FROM nova_task_nodes WHERE node_id = ?",
                (target_node_id,),
            ).fetchone()
            if target_node is None:
                raise ValueError(f"Nova-Task v2 target node not found: {target_node_id}")
        decision_id = _new_id("NTD")
        before = dict(candidate)
        existing_metadata = _load_json(candidate["metadata_json"], {})
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        reference_metadata = {
            "decisionType": decision_type,
            "decidedBy": actor,
            "decisionReason": reason,
            "candidateActionMetadata": metadata or {},
        }
        if target_candidate is not None:
            reference_metadata.update(
                {
                    "targetCandidateId": target_candidate["candidate_id"],
                    "targetCandidateTitle": target_candidate["proposed_title"],
                    "targetCandidateStatus": target_candidate["status"],
                }
            )
        if target_node is not None:
            reference_metadata.update(
                {
                    "targetNodeId": target_node["node_id"],
                    "targetNodeTitle": target_node["title"],
                    "targetNodeType": target_node["node_type"],
                }
            )
        existing_metadata.update(reference_metadata)
        after = {"candidateId": candidate_id, "status": status, **reference_metadata}
        connection.execute(
            """
            UPDATE nova_task_candidates
            SET status = ?, matched_node_id = COALESCE(?, matched_node_id), metadata_json = ?,
                updated_at = ?, decided_at = ?
            WHERE candidate_id = ?
            """,
            (status, target_node_id, _json(existing_metadata), now, now, candidate_id),
        )
        connection.execute(
            """
            INSERT INTO nova_task_reconciliation_decisions(
                decision_id, candidate_id, decision_type, actor, reason,
                before_json, after_json, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (decision_id, candidate_id, decision_type, actor, reason, _json(before), _json(after), now),
        )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, candidate_id,
                decision_id, before_json, after_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                _new_id("NTA"),
                now,
                actor,
                f"{decision_type}_candidate",
                target_node_id,
                candidate_id,
                decision_id,
                _json(before),
                _json(after),
            ),
        )
    return NovaTaskCandidateDecision(candidate_id, decision_id, status)


def merge_task_candidate(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    reason: str | None = None,
    target_candidate_id: str | None = None,
    target_node_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskCandidateDecision:
    return _close_candidate_with_reference(
        paths,
        candidate_id=candidate_id,
        actor=actor,
        decision_type="merge",
        status=CANDIDATE_STATUS_MERGED,
        reason=reason,
        target_candidate_id=target_candidate_id,
        target_node_id=target_node_id,
        metadata=metadata,
    )


def supersede_task_candidate(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    actor: str,
    reason: str | None = None,
    target_candidate_id: str | None = None,
    target_node_id: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskCandidateDecision:
    return _close_candidate_with_reference(
        paths,
        candidate_id=candidate_id,
        actor=actor,
        decision_type="supersede",
        status=CANDIDATE_STATUS_SUPERSEDED,
        reason=reason,
        target_candidate_id=target_candidate_id,
        target_node_id=target_node_id,
        metadata=metadata,
    )


def attach_task_candidate_to_node(
    paths: RuntimePaths,
    *,
    candidate_id: str,
    target_node_id: str,
    actor: str,
    reason: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> NovaTaskCandidateDecision:
    """Close a pending-review candidate as represented by an existing graph node."""
    migrate(paths)
    now = _now()
    with connect(paths) as connection:
        candidate = connection.execute(
            "SELECT * FROM nova_task_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
        if candidate is None:
            raise ValueError(f"Nova-Task v2 candidate not found: {candidate_id}")
        if candidate["status"] not in _PENDING_CANDIDATE_STATUSES:
            raise ValueError(f"Nova-Task v2 candidate is not pending review: {candidate_id}")
        node = connection.execute(
            "SELECT node_id, title, node_type FROM nova_task_nodes WHERE node_id = ?",
            (target_node_id,),
        ).fetchone()
        if node is None:
            raise ValueError(f"Nova-Task v2 target node not found: {target_node_id}")
        decision_id = _new_id("NTD")
        before = dict(candidate)
        existing_metadata = _load_json(candidate["metadata_json"], {})
        if not isinstance(existing_metadata, dict):
            existing_metadata = {}
        existing_metadata.update(
            {
                "attachedToNodeId": target_node_id,
                "attachedToNodeTitle": node["title"],
                "attachedBy": actor,
                "attachReason": reason,
                "candidateActionMetadata": metadata or {},
            }
        )
        after = {
            "candidateId": candidate_id,
            "status": CANDIDATE_STATUS_CONFIRMED,
            "matchedNodeId": target_node_id,
            "decisionType": "attached",
        }
        connection.execute(
            """
            UPDATE nova_task_candidates
            SET status = 'confirmed', matched_node_id = ?, metadata_json = ?,
                updated_at = ?, decided_at = ?
            WHERE candidate_id = ?
            """,
            (target_node_id, _json(existing_metadata), now, now, candidate_id),
        )
        connection.execute(
            """
            INSERT INTO nova_task_reconciliation_decisions(
                decision_id, candidate_id, decision_type, actor, reason,
                before_json, after_json, created_at
            ) VALUES (?, ?, 'attached', ?, ?, ?, ?, ?)
            """,
            (decision_id, candidate_id, actor, reason, _json(before), _json(after), now),
        )
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, candidate_id,
                decision_id, before_json, after_json
            ) VALUES (?, ?, ?, 'attach_candidate_to_node', ?, ?, ?, ?, ?)
            """,
            (_new_id("NTA"), now, actor, target_node_id, candidate_id, decision_id, _json(before), _json(after)),
        )
    return NovaTaskCandidateDecision(candidate_id, decision_id, CANDIDATE_STATUS_CONFIRMED)


def pending_candidate_count(paths: RuntimePaths) -> int:
    """Return pending-review Level-1 candidate count, including legacy pending rows."""
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM nova_task_candidates
                WHERE status IN ('pending_review', 'pending')
                  AND candidate_type = 'parent_task'
                """
            ).fetchone()[0]
        )


def pending_review_candidate_count(paths: RuntimePaths) -> int:
    """Semantic alias for pending_candidate_count."""
    return pending_candidate_count(paths)


def diary_tasks_snapshot(paths: RuntimePaths) -> dict[str, int]:
    """Return diary embedded-JSON task counts from Nova-Task SQLite authority."""
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            SELECT status, COUNT(*) AS count
            FROM nova_task_nodes
            WHERE status IN ('active', 'planned', 'blocked', 'paused', 'completed', 'done', 'automatic', 'settled', 'stale')
            GROUP BY status
            """
        ).fetchall()
    counts = {row["status"]: int(row["count"] or 0) for row in rows}
    return {
        "InProgress": (
            counts.get("active", 0)
            + counts.get("planned", 0)
            + counts.get("blocked", 0)
            + counts.get("paused", 0)
            + counts.get("automatic", 0)
            + counts.get("stale", 0)
        ),
        "Completed": counts.get("completed", 0) + counts.get("done", 0) + counts.get("settled", 0),
    }


def render_task_graph_context(paths: RuntimePaths, *, max_nodes: int = 40) -> str:
    """Render compact active graph context for technical-pass task matching."""
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT node_id, parent_node_id, node_type, title, status, progress, sort_order, metadata_json
                FROM nova_task_nodes
                WHERE status IN ('active', 'planned', 'blocked')
                ORDER BY COALESCE(parent_node_id, ''), sort_order, title, node_id
                LIMIT ?
                """,
                (max_nodes,),
            )
        ]
    if not rows:
        return "Nova-Task v2 active graph is empty."
    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    by_id = {str(row["node_id"]): row for row in rows}
    for row in rows:
        parent = row["parent_node_id"]
        if parent is not None and parent not in by_id:
            parent = None
        by_parent.setdefault(parent, []).append(row)
    for siblings in by_parent.values():
        siblings.sort(key=lambda item: (int(item["sort_order"] or 0), str(item["title"]), str(item["node_id"])))

    lines = [
        "Nova-Task v2 compact active graph:",
        "- Treat these IDs/titles/aliases as matching context only.",
        "- New parent tasks must be emitted as pending-review candidates, not authoritative task state.",
    ]

    def append_tree(node: dict[str, Any], depth: int) -> None:
        indent = "  " * depth
        progress = int(node["progress"] or 0)
        metadata = _load_json(node.get("metadata_json"), {}) if isinstance(node, dict) else {}
        if not isinstance(metadata, dict):
            metadata = {}
        layer = str(metadata.get("novaTaskLayer") or LAYER_PROJECT_GRAPH)
        origin = str(metadata.get("origin") or "unknown")
        state_authority = str(metadata.get("stateAuthority") or "manual_or_unknown")
        lines.append(
            f"{indent}- {node['node_id']} | {node['node_type']} | {node['status']} | {progress}% | "
            f"{node['title']} | layer={layer} | origin={origin} | stateAuthority={state_authority}"
        )
        for child in by_parent.get(node["node_id"], []):
            append_tree(child, depth + 1)

    for root in by_parent.get(None, []):
        append_tree(root, 0)
    if len(rows) >= max_nodes:
        lines.append(f"- Context truncated at {max_nodes} nodes.")
    return "\n".join(lines)


def _extract_nova_task_payload(markdown: str) -> dict[str, Any] | None:
    blocks = re.findall(r"```(?:yaml|yml)?\s*\n([\s\S]*?)```", markdown, flags=re.IGNORECASE)
    candidates = [block for block in blocks if re.search(r"(?m)^\s*nova_task\s*:", block)]
    if not candidates and re.search(r"(?m)^\s*nova_task\s*:", markdown):
        candidates = [markdown]
    if not candidates:
        return None
    try:
        import yaml  # type: ignore

        loaded = yaml.safe_load(candidates[-1])
    except Exception:
        return None
    if not isinstance(loaded, dict):
        return None
    payload = loaded.get("nova_task")
    return payload if isinstance(payload, dict) else None


def _list_payload(payload: dict[str, Any], key: str) -> list[dict[str, Any]]:
    value = payload.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def _string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


def _existing_node_ids(connection: Any, values: Any) -> list[str]:
    ids = []
    for value in _string_list(values):
        existing = _existing_node_id(connection, value)
        if existing:
            ids.append(existing)
    return ids


def _event_id(
    *,
    business_date: date,
    source_type: str,
    event_type: str,
    summary: str,
    evidence: list[str],
    node_id: str | None,
) -> str:
    digest = hashlib.sha256(
        _json(
            {
                "businessDate": business_date.isoformat(),
                "sourceType": source_type,
                "eventType": event_type,
                "summary": summary,
                "evidence": evidence,
                "nodeId": node_id or "",
            }
        ).encode("utf-8")
    ).hexdigest()
    return f"NTEV-{digest[:16]}"


def _insert_event(
    connection: Any,
    *,
    business_date: date,
    source_path: Path | None,
    source_sha256: str,
    source_type: str,
    event_type: str,
    summary: str,
    evidence: list[str],
    confidence: str,
    matched_node_id: str | None,
    metadata: dict[str, Any] | None = None,
) -> tuple[str, bool]:
    now = _now()
    allowed_event_types = {
        "progress",
        "completion_signal",
        "remaining_work",
        "candidate_parent",
        "candidate_subtask",
        "unresolved",
    }
    resolved_event_type = event_type if event_type in allowed_event_types else "progress"
    resolved_node_id = matched_node_id
    if resolved_node_id is not None:
        exists = connection.execute("SELECT 1 FROM nova_task_nodes WHERE node_id = ?", (resolved_node_id,)).fetchone()
        if exists is None:
            resolved_node_id = None
    event_id = _event_id(
        business_date=business_date,
        source_type=source_type,
        event_type=resolved_event_type,
        summary=summary,
        evidence=evidence,
        node_id=resolved_node_id,
    )
    cursor = connection.execute(
        """
        INSERT OR IGNORE INTO nova_task_events(
            event_id, business_date, source_type, source_path, source_sha256,
            source_locator, matched_node_id, event_type, confidence, summary,
            evidence_json, metadata_json, created_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            event_id,
            business_date.isoformat(),
            source_type,
            str(source_path) if source_path else None,
            source_sha256,
            "nova_task",
            resolved_node_id,
            resolved_event_type,
            confidence if confidence in {"high", "medium", "low", "unknown"} else "unknown",
            summary,
            _json(evidence),
            _json(metadata),
            now,
        ),
    )
    return event_id, cursor.rowcount > 0


def _existing_node_id(connection: Any, node_id: str | None) -> str | None:
    value = str(node_id or "")
    if not value:
        return None
    exists = connection.execute("SELECT 1 FROM nova_task_nodes WHERE node_id = ?", (value,)).fetchone()
    return value if exists is not None else None


def _node_depth(connection: Any, node_id: str | None) -> int | None:
    value = str(node_id or "")
    if not value:
        return None
    depth = 0
    seen: set[str] = set()
    current = value
    while current:
        if current in seen:
            return None
        seen.add(current)
        row = connection.execute("SELECT parent_node_id FROM nova_task_nodes WHERE node_id = ?", (current,)).fetchone()
        if row is None:
            return None
        depth += 1
        current = row["parent_node_id"]
    return depth


def _apply_node_status_from_signal(
    connection: Any,
    *,
    node_id: str,
    event_id: str,
    item: dict[str, Any],
    evidence: list[str],
    business_date: date,
    actor: str = "pipeline",
) -> bool:
    node = connection.execute("SELECT * FROM nova_task_nodes WHERE node_id = ?", (node_id,)).fetchone()
    target_status = _normalize_status_signal(item.get("target_status") or item.get("suggested_status"))
    if node is None or target_status is None or node["status"] == target_status:
        return False
    now = _now()
    before = dict(node)
    metadata = _load_json(node["metadata_json"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    status_reason = str(
        item.get("status_reason")
        or item.get("completion_method")
        or item.get("summary")
        or "LLM status signal"
    )
    status_tags = _status_tags(item.get("status_tags"))
    metadata.update(
        {
            "statusReason": status_reason,
            "statusTags": status_tags,
            "statusSourceEventId": event_id,
            "statusEvidence": evidence,
            "statusSignal": item,
            "statusBusinessDate": business_date.isoformat(),
            "statusUpdatedBy": actor,
        }
    )
    completed_at = now if target_status in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE} else None
    progress = 100 if target_status in {TASK_NODE_STATUS_COMPLETED, TASK_NODE_STATUS_DONE} else int(node["progress"] or 0)
    after = {
        "nodeId": node_id,
        "status": target_status,
        "progress": progress,
        "completedAt": completed_at,
        "statusReason": status_reason,
        "statusTags": status_tags,
    }
    connection.execute(
        """
        UPDATE nova_task_nodes
        SET status = ?, progress = ?, completed_at = ?, updated_at = ?, metadata_json = ?
        WHERE node_id = ?
        """,
        (target_status, progress, completed_at, now, _json(metadata), node_id),
    )
    connection.execute(
        """
        INSERT INTO nova_task_audit_log(
            audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
        ) VALUES (?, ?, ?, 'auto_update_low_level_task_status', ?, ?, ?, ?)
        """,
        (
            _new_id("NTA"),
            now,
            actor,
            node_id,
            _json(before),
            _json(after),
            _json({"sourceEventId": event_id, "businessDate": business_date.isoformat()}),
        ),
    )
    return True


def ingest_nova_task_evidence(
    paths: RuntimePaths,
    *,
    markdown: str,
    business_date: date,
    source_path: Path | None = None,
    source_type: str = "technical_report",
) -> NovaTaskEvidenceIngest:
    """Ingest Nova-Task v2 evidence and write non-Level-1 graph changes directly.

    Level-1 parent tasks remain the only review-candidate path. Lower-level
    nodes and status signals are materialized directly when they can be
    validated against an existing graph parent/node.
    """
    migrate(paths)
    payload = _extract_nova_task_payload(markdown)
    if payload is None:
        return NovaTaskEvidenceIngest(0, 0, pending_candidate_count(paths), malformed="nova_task:" in markdown)

    source_sha256 = hashlib.sha256(markdown.encode("utf-8")).hexdigest()
    inserted_events = 0
    candidate_count = 0
    candidate_specs: list[dict[str, Any]] = []
    node_specs: list[dict[str, Any]] = []
    with connect(paths) as connection:
        def existing_equivalent_child(proposed_title: str, parent_node_id: str | None) -> str | None:
            rows = connection.execute(
                """
                SELECT node_id, title
                FROM nova_task_nodes
                WHERE COALESCE(parent_node_id, '') = COALESCE(?, '')
                  AND status != 'archived'
                """,
                (parent_node_id,),
            ).fetchall()
            proposed = str(proposed_title or "").strip().casefold()
            for row in rows:
                if str(row["title"] or "").strip().casefold() == proposed:
                    return str(row["node_id"])
            return None

        def handle_reconciliation_hint(kind: str, item: dict[str, Any]) -> None:
            nonlocal inserted_events
            evidence = _string_list(item.get("evidence"))
            summary = str(item.get("reason") or f"LLM proposed {kind} hierarchy reconciliation")
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type="progress",
                summary=summary,
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=None,
                metadata={
                    "raw": item,
                    "hint_type": kind,
                    "directGraphPolicy": "direct_when_deterministic_no_candidate",
                },
            )
            inserted_events += int(inserted)
            if kind == "reparent":
                child_id = _existing_node_id(connection, str(item.get("child_task_id") or "") or None)
                parent_id = _existing_node_id(connection, str(item.get("proposed_parent_task_id") or "") or None)
                if not child_id or not parent_id:
                    return
                if _node_depth(connection, child_id) == 1:
                    return
                before = connection.execute("SELECT * FROM nova_task_nodes WHERE node_id = ?", (child_id,)).fetchone()
                connection.execute(
                    "UPDATE nova_task_nodes SET parent_node_id = ?, updated_at = ? WHERE node_id = ?",
                    (parent_id, _now(), child_id),
                )
                connection.execute(
                    """
                    INSERT INTO nova_task_audit_log(
                        audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
                    ) VALUES (?, ?, ?, 'direct_reparent_node', ?, ?, ?, ?)
                    """,
                    (
                        _new_id("NTA"),
                        _now(),
                        "pipeline",
                        child_id,
                        _json(dict(before) if before is not None else {}),
                        _json({"nodeId": child_id, "parentNodeId": parent_id}),
                        _json({"sourceEventId": event_id, "raw": item}),
                    ),
                )

        def handle_status_signal(item: dict[str, Any], *, default_status: str | None = None) -> None:
            nonlocal inserted_events
            evidence = _string_list(item.get("evidence"))
            matched_node_id = _existing_node_id(connection, str(item.get("task_id") or "") or None)
            target_status = _normalize_status_signal(item.get("target_status") or item.get("suggested_status") or default_status)
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type="completion_signal",
                summary=str(target_status or item.get("suggested_status") or "status signal"),
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=matched_node_id,
                metadata={"raw": item, "target_status": target_status},
            )
            inserted_events += int(inserted)
            if target_status is None or matched_node_id is None:
                return
            depth = _node_depth(connection, matched_node_id)
            if "needs_review" in set(_status_tags(item.get("status_tags"))):
                return
            if depth is not None and depth > 1:
                _apply_node_status_from_signal(
                    connection,
                    node_id=matched_node_id,
                    event_id=event_id,
                    item={**item, "target_status": target_status},
                    evidence=evidence,
                    business_date=business_date,
                    actor="pipeline",
                )

        for item in _list_payload(payload, "matched_tasks"):
            evidence = _string_list(item.get("evidence"))
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type=str(item.get("event_type") or "progress"),
                summary=str(item.get("summary") or ""),
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=str(item.get("task_id") or "") or None,
                metadata={"raw": item},
            )
            inserted_events += int(inserted)
            del event_id
        for item in _list_payload(payload, "completion_signals"):
            handle_status_signal(item, default_status="completed")
        for item in _list_payload(payload, "status_signals"):
            handle_status_signal(item)
        for item in _list_payload(payload, "unresolved"):
            evidence = _string_list(item.get("evidence"))
            _, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type="unresolved",
                summary=str(item.get("summary") or ""),
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=None,
                metadata={"raw": item, "reason": item.get("reason")},
            )
            inserted_events += int(inserted)
        for item in _list_payload(payload, "candidate_parent_tasks"):
            evidence = _string_list(item.get("evidence"))
            proposed_title = str(item.get("proposed_title") or "")
            if _covered_by_existing_root_anchor(connection, proposed_title):
                continue
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type="candidate_parent",
                summary=proposed_title,
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=None,
                metadata={"raw": item},
            )
            inserted_events += int(inserted)
            candidate_specs.append(
                {
                    "candidate_type": "parent_task",
                    "proposed_title": proposed_title,
                    "reason": str(item.get("reason") or "LLM proposed parent task"),
                    "evidence": evidence,
                    "source_event_id": event_id,
                    "confidence": str(item.get("confidence") or "unknown"),
                    "metadata": {
                        "novaTaskLayer": "planning_overlay",
                        "raw": item,
                        "suggestedNodeType": _suggested_node_type_from_item(item, fallback="track"),
                        "reviewPolicy": "manual_required_for_level_1",
                    },
                }
            )
        for item in _list_payload(payload, "candidate_subtasks"):
            evidence = _string_list(item.get("evidence"))
            title = str(item.get("proposed_title") or "").strip()
            parent_id = str(item.get("proposed_parent_task_id") or "") or None
            parent_node_id = _existing_node_id(connection, parent_id)
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_sha256,
                source_type=source_type,
                event_type="candidate_subtask",
                summary=title,
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=parent_node_id,
                metadata={"raw": item, "proposed_parent_ref": parent_id},
            )
            inserted_events += int(inserted)
            if not title or not parent_node_id:
                continue
            suggested_type = _suggested_node_type_from_item(item, fallback="subtask")
            if suggested_type == "track" or existing_equivalent_child(title, parent_node_id):
                continue
            node_specs.append(
                {
                    "title": title,
                    "node_type": suggested_type,
                    "parent_node_id": parent_node_id,
                    "metadata": {
                        **project_graph_metadata(
                            origin=ORIGIN_OBSERVED,
                            state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                        ),
                        "createdFrom": source_type,
                        "sourceEventId": event_id,
                        "rawCandidateSubtask": item,
                        "proposed_parent_ref": parent_id,
                        "evidence": evidence,
                    },
                }
            )
        for item in _list_payload(payload, "reparent_hints"):
            handle_reconciliation_hint("reparent", item)
        for item in _list_payload(payload, "group_hints"):
            handle_reconciliation_hint("group", item)
        for item in _list_payload(payload, "merge_hints"):
            handle_reconciliation_hint("merge", item)
        for item in _list_payload(payload, "demote_hints"):
            handle_reconciliation_hint("demote", item)

    for spec in candidate_specs:
        if not spec["proposed_title"]:
            continue
        create_task_candidate(paths, **spec)
        candidate_count += 1
    for spec in node_specs:
        create_task_node(paths, actor="pipeline", status="active", **spec)

    return NovaTaskEvidenceIngest(inserted_events, candidate_count, pending_candidate_count(paths))


def _checkbox(status: str) -> str:
    return "x" if status in {"completed", "done", "settled"} else " "


def _status_label(status: str) -> str:
    return {
        "active": "Active",
        "planned": "Planned",
        "blocked": "Blocked",
        "paused": "Paused",
        "completed": "Completed",
        "done": "Done",
        "automatic": "Automatic",
        "settled": "Settled",
        "stale": "Stale",
        "archived": "Archived",
    }.get(status, status)


def _node_line(node: dict[str, Any], depth: int) -> str:
    indent = "  " * depth
    progress = int(node["progress"] or 0)
    suffix_parts = [str(node["node_type"]), _status_label(str(node["status"]))]
    if progress:
        suffix_parts.append(f"{progress}%")
    suffix = " - ".join(suffix_parts)
    return f"{indent}- [{_checkbox(str(node['status']))}] **[{node['node_id']}]** {node['title']} ({suffix})"


def render_task_board_markdown(paths: RuntimePaths) -> str:
    """Render deterministic TASK_BOARD.md content from authoritative SQLite state."""
    migrate(paths)
    with connect(paths, read_only=True) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT node_id, parent_node_id, node_type, title, status, progress,
                       sort_order, created_at
                FROM nova_task_nodes
                ORDER BY COALESCE(parent_node_id, ''), sort_order, title, node_id
                """
            )
        ]

    by_parent: dict[str | None, list[dict[str, Any]]] = {}
    by_id = {str(row["node_id"]): row for row in rows}
    for row in rows:
        parent = row["parent_node_id"]
        if parent is not None and parent not in by_id:
            parent = None
        by_parent.setdefault(parent, []).append(row)
    for siblings in by_parent.values():
        siblings.sort(key=lambda item: (int(item["sort_order"] or 0), str(item["title"]), str(item["node_id"])))

    def append_tree(lines: list[str], node: dict[str, Any], depth: int) -> None:
        lines.append(_node_line(node, depth))
        for child in by_parent.get(node["node_id"], []):
            if child["status"] == "archived" and node["status"] != "archived":
                continue
            append_tree(lines, child, depth + 1)

    roots = by_parent.get(None, [])
    sections = [
        ("active", "Active"),
        ("planned", "Planned"),
        ("blocked", "Blocked"),
        ("paused", "Paused"),
        ("done", "Done"),
        ("completed", "Completed"),
        ("automatic", "Automatic"),
        ("settled", "Settled"),
        ("stale", "Stale"),
        ("archived", "Archived"),
    ]
    lines = [
        "# TASK_BOARD.md",
        "",
        "> Generated from Nova-Task v2 SQLite authority.",
        "> This file is a reading projection; do not edit it as task authority.",
        "",
    ]
    for status, heading in sections:
        section_roots = [node for node in roots if node["status"] == status]
        if not section_roots:
            continue
        lines.extend([f"## {heading}", ""])
        for root in section_roots:
            append_tree(lines, root, 0)
        lines.append("")
    if not roots:
        lines.extend(["## Active", "", "_No confirmed Nova-Task v2 nodes._", ""])
    return "\n".join(lines).rstrip() + "\n"


def export_task_board_markdown(paths: RuntimePaths, target_path: str | None = None) -> NovaTaskBoardExport:
    """Write TASK_BOARD.md from SQLite and record the projection export."""
    migrate(paths)
    content = render_task_board_markdown(paths)
    output = paths.task_board_path if target_path is None else paths.home.joinpath(target_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(content, encoding="utf-8")
    now = _now()
    digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
    export_id = _new_id("NTE")
    with connect(paths) as connection:
        node_count = int(connection.execute("SELECT COUNT(*) FROM nova_task_nodes").fetchone()[0])
        node_ids = [
            row["node_id"]
            for row in connection.execute(
                "SELECT node_id FROM nova_task_nodes ORDER BY COALESCE(parent_node_id, ''), sort_order, title, node_id"
            )
        ]
        connection.execute(
            """
            INSERT INTO nova_task_exports(
                export_id, export_type, target_path, content_sha256,
                generated_at, source_snapshot_json, metadata_json
            ) VALUES (?, 'task_board_markdown', ?, ?, ?, ?, ?)
            """,
            (
                export_id,
                str(output),
                digest,
                now,
                _json({"nodeCount": node_count, "nodeIds": node_ids}),
                _json({"authority": "Nova-Task v2 SQLite", "projection": "TASK_BOARD.md"}),
            ),
        )
    return NovaTaskBoardExport(export_id, str(output), digest, node_count)
