"""Dashboard facade for Nova-Task v2 candidate review."""

from __future__ import annotations

import os
import sys
import json

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "../../../")))

from data_foundation.nova_task import (
    confirm_candidate_as_task,
    create_task_node,
    defer_task_candidate,
    export_task_board_markdown,
    list_task_candidates,
    merge_task_candidate,
    reject_task_candidate,
    supersede_task_candidate,
    update_task_node,
)
from data_foundation.nova_task_layers import (
    LAYER_PROJECT_GRAPH,
    NODE_MANAGED_BY_AGENT,
    NODE_MANAGED_BY_HUMAN,
    ORIGIN_PLANNED,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
    project_graph_metadata,
)
from data_foundation.nova_task_planning_import import apply_planning_import_artifact, import_planning_document
from data_foundation.db import connect
from data_foundation.paths import load_paths
from .dashboard_state import attach_dashboard_state


def _dashboard_paths():
    return load_paths()


def disabled_board_payload(**extra) -> dict:
    return {
        "enabled": False,
        "reason": "Nova-Task subsystem is disabled by settings.",
        **extra,
    }


def task_board_payload(*, enabled: bool) -> dict:
    if not enabled:
        return attach_dashboard_state(
            disabled_board_payload(
                novaTaskEnabled=False,
                tasks=[],
                grouped={},
                tree=[],
                nodes=[],
                lastModified=None,
            ),
            status="unavailable",
        )
    nova_tree = tree()
    roots = nova_tree.get("roots", [])
    nodes = nova_tree.get("nodes", [])
    return attach_dashboard_state(
        {
            "novaTaskEnabled": True,
            "authority": "nova-task-v2-sqlite",
            "tasks": [],
            "grouped": {},
            "tree": roots,
            "nodes": nodes,
            "lastModified": None,
        },
        empty=not roots and not nodes,
    )


def candidate_status() -> dict:
    return l1_review_status()


def l1_review_status() -> dict:
    count = _pending_l1_candidate_count(_dashboard_paths())
    return {
        "l1ReviewCount": count,
        "hasL1Review": count > 0,
        "pendingReviewCount": count,
        "hasPendingReview": count > 0,
        # Compatibility fields for existing Dashboard/API callers.
        "pendingCount": count,
        "hasPending": count > 0,
    }


def _export_boards(paths) -> None:
    export_task_board_markdown(paths)


def candidates(*, status: str = "pending_review", limit: int = 50) -> dict:
    return l1_review_items(status=status, limit=limit)


def l1_review_items(*, status: str = "pending_review", limit: int = 50) -> dict:
    items = [
        item
        for item in list_task_candidates(_dashboard_paths(), status=status, limit=limit)
        if item.get("candidateType") == "parent_task"
    ]
    items = [_candidate_display_item(item) for item in items]
    return {"items": items, "candidates": items, "count": len(items), **l1_review_status()}


def _pending_l1_candidate_count(paths) -> int:
    with connect(paths, read_only=True) as connection:
        return int(
            connection.execute(
                """
                SELECT COUNT(*)
                FROM nova_task_l1_review_items
                WHERE status IN ('pending_review', 'pending')
                """
            ).fetchone()[0]
        )


def planning_import(*, title: str, content: str, apply: bool = True) -> dict:
    result = import_planning_document(
        _dashboard_paths(),
        document_title=title,
        document_text=content,
        apply=apply,
    )
    return {
        "status": "ok",
        "applied": result.applied,
        "artifactPath": result.artifact_path,
        "rootNodeId": result.root_node_id,
        "rootCreated": result.root_created,
        "nodeCreatedCount": result.node_created_count,
        "nodeReusedCount": result.node_reused_count,
        "skippedCount": result.skipped_count,
        "responsePreview": result.response_preview,
        "previewTree": result.preview_tree,
        "validationReport": result.validation_report,
    }


def apply_planning_import(*, artifact_path: str) -> dict:
    result = apply_planning_import_artifact(_dashboard_paths(), artifact_path=artifact_path)
    return {
        "status": "ok",
        "applied": result.applied,
        "artifactPath": result.artifact_path,
        "rootNodeId": result.root_node_id,
        "rootCreated": result.root_created,
        "nodeCreatedCount": result.node_created_count,
        "nodeReusedCount": result.node_reused_count,
        "skippedCount": result.skipped_count,
        "responsePreview": result.response_preview,
        "previewTree": result.preview_tree,
        "validationReport": result.validation_report,
    }


def _metadata(raw: str | None) -> dict:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _workspace(metadata: dict) -> dict:
    workspace = metadata.get("workspace") if isinstance(metadata, dict) else {}
    return workspace if isinstance(workspace, dict) else {}


def _anchor_profile(metadata: dict) -> str:
    workspace = _workspace(metadata)
    origin = str(metadata.get("origin") or "")
    if origin == "planned" and not workspace.get("rootPath"):
        return "planned_pathless_l1"
    if origin == "planned" and workspace.get("rootPath"):
        return "planned_path_backed_l1"
    if origin == "observed" and workspace.get("rootPath"):
        return "observed_path_backed_l1"
    return ""


def _candidate_display_item(item: dict) -> dict:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    workspace = _workspace(metadata)
    return {
        **item,
        "reviewStatus": "pending_review" if item.get("status") in {"pending", "pending_review"} else item.get("status"),
        "source": metadata.get("source") or metadata.get("createdFrom") or metadata.get("created_from"),
        "origin": metadata.get("origin"),
        "stateAuthority": metadata.get("stateAuthority"),
        "candidateKind": metadata.get("candidateKind"),
        "workspace": workspace,
        "workspaceRootPath": workspace.get("rootPath"),
        "workspaceDisplayName": workspace.get("displayName"),
    }


def nodes() -> dict:
    paths = _dashboard_paths()
    with connect(paths, read_only=True) as connection:
        items = []
        for row in connection.execute(
                """
                SELECT node_id, parent_node_id, node_type, title, status, progress, metadata_json
                FROM nova_task_nodes
                WHERE status IN ('active', 'planned', 'blocked')
                ORDER BY COALESCE(parent_node_id, ''), sort_order, title, node_id
                """
        ):
            metadata = _metadata(row["metadata_json"])
            workspace = _workspace(metadata)
            items.append(
                {
                    "nodeId": row["node_id"],
                    "parentNodeId": row["parent_node_id"],
                    "nodeType": row["node_type"],
                    "title": row["title"],
                    "status": row["status"],
                    "progress": int(row["progress"] or 0),
                    "layer": metadata.get("novaTaskLayer") or LAYER_PROJECT_GRAPH,
                    "origin": metadata.get("origin"),
                    "stateAuthority": metadata.get("stateAuthority"),
                    "createdBy": metadata.get("createdBy"),
                    "managedBy": metadata.get("managedBy"),
                    "workspace": workspace,
                    "workspaceRootPath": workspace.get("rootPath"),
                    "workspaceDisplayName": workspace.get("displayName"),
                    "anchorProfile": _anchor_profile(metadata),
                    "statusReason": metadata.get("statusReason"),
                    "statusTags": metadata.get("statusTags") if isinstance(metadata.get("statusTags"), list) else [],
                }
            )
    return {"nodes": items, "count": len(items)}


def tree() -> dict:
    paths = _dashboard_paths()
    with connect(paths, read_only=True) as connection:
        rows = []
        for row in connection.execute(
                """
                SELECT node_id, parent_node_id, node_type, title, status, progress,
                       completed_at, sort_order, created_at, metadata_json
                FROM nova_task_nodes
                WHERE status != 'archived'
                ORDER BY COALESCE(parent_node_id, ''), sort_order, title, node_id
                """
        ):
            metadata = _metadata(row["metadata_json"])
            workspace = _workspace(metadata)
            rows.append(
                {
                    "nodeId": row["node_id"],
                    "parentNodeId": row["parent_node_id"],
                    "nodeType": row["node_type"],
                    "title": row["title"],
                    "status": row["status"],
                    "progress": int(row["progress"] or 0),
                    "completedAt": row["completed_at"],
                    "layer": metadata.get("novaTaskLayer") or LAYER_PROJECT_GRAPH,
                    "origin": metadata.get("origin"),
                    "stateAuthority": metadata.get("stateAuthority"),
                    "createdBy": metadata.get("createdBy"),
                    "managedBy": metadata.get("managedBy"),
                    "workspace": workspace,
                    "workspaceRootPath": workspace.get("rootPath"),
                    "workspaceDisplayName": workspace.get("displayName"),
                    "anchorProfile": _anchor_profile(metadata),
                    "statusReason": metadata.get("statusReason"),
                    "statusTags": metadata.get("statusTags") if isinstance(metadata.get("statusTags"), list) else [],
                    "children": [],
                }
            )
    by_id = {item["nodeId"]: item for item in rows}
    roots: list[dict] = []
    for item in rows:
        parent_id = item["parentNodeId"]
        if parent_id and parent_id in by_id:
            by_id[parent_id]["children"].append(item)
        else:
            item["parentNodeId"] = None
            roots.append(item)
    return {"nodes": rows, "roots": roots, "count": len(rows)}


def recent_direct_writes(*, limit: int = 20) -> dict:
    paths = _dashboard_paths()
    bounded_limit = max(1, min(int(limit or 20), 100))
    with connect(paths, read_only=True) as connection:
        audit_rows = connection.execute(
            """
            SELECT audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
            FROM nova_task_audit_log
            WHERE action IN (
                'create_node',
                'auto_update_low_level_task_status',
                'direct_reparent_node',
                'auto_promote_planned_ancestor_to_active',
                'auto_promote_planned_l1_to_active'
            )
              AND actor IN ('pipeline', 'nova-task-work-graph')
            ORDER BY occurred_at DESC, audit_id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        event_rows = connection.execute(
            """
            SELECT event_id, business_date, source_type, matched_node_id, event_type, confidence, summary, created_at
            FROM nova_task_events
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        guard_rows = connection.execute(
            """
            SELECT event_id, business_date, source_type, matched_node_id, event_type,
                   confidence, summary, metadata_json, created_at
            FROM nova_task_events
            WHERE json_extract(metadata_json, '$.levelValidation') = 'rejected'
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
        routing_hint_rows = connection.execute(
            """
            SELECT event_id, business_date, source_type, matched_node_id, event_type,
                   confidence, summary, metadata_json, created_at
            FROM nova_task_events
            WHERE json_extract(metadata_json, '$.hintEventType') = 'routing_hint'
            ORDER BY created_at DESC, event_id DESC
            LIMIT ?
            """,
            (bounded_limit,),
        ).fetchall()
    audits = []
    for row in audit_rows:
        audits.append(
            {
                "auditId": row["audit_id"],
                "occurredAt": row["occurred_at"],
                "actor": row["actor"],
                "action": row["action"],
                "nodeId": row["node_id"],
                "before": _metadata(row["before_json"]),
                "after": _metadata(row["after_json"]),
                "metadata": _metadata(row["metadata_json"]),
            }
        )
    events = [
        {
            "eventId": row["event_id"],
            "businessDate": row["business_date"],
            "sourceType": row["source_type"],
            "matchedNodeId": row["matched_node_id"],
            "eventType": row["event_type"],
            "confidence": row["confidence"],
            "summary": row["summary"],
            "createdAt": row["created_at"],
        }
        for row in event_rows
    ]
    deferred_by_guard = []
    for row in guard_rows:
        metadata = _metadata(row["metadata_json"])
        deferred_by_guard.append(
            {
                "eventId": row["event_id"],
                "businessDate": row["business_date"],
                "sourceType": row["source_type"],
                "matchedNodeId": row["matched_node_id"],
                "eventType": row["event_type"],
                "confidence": row["confidence"],
                "summary": row["summary"],
                "createdAt": row["created_at"],
                "reason": metadata.get("levelValidationReason"),
                "resolvedParentNodeId": metadata.get("resolvedParentNodeId"),
                "proposedParentRef": metadata.get("proposed_parent_ref"),
            }
        )
    routing_hints = []
    for row in routing_hint_rows:
        metadata = _metadata(row["metadata_json"])
        raw = metadata.get("raw") if isinstance(metadata.get("raw"), dict) else {}
        aliases = raw.get("aliases") if isinstance(raw.get("aliases"), list) else []
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        negative_rules = raw.get("negative_rules") if isinstance(raw.get("negative_rules"), list) else []
        routing_hints.append(
            {
                "eventId": row["event_id"],
                "businessDate": row["business_date"],
                "sourceType": row["source_type"],
                "matchedNodeId": row["matched_node_id"],
                "eventType": row["event_type"],
                "confidence": row["confidence"],
                "summary": row["summary"],
                "createdAt": row["created_at"],
                "hintId": raw.get("hint_id"),
                "boundaryType": raw.get("boundary_type"),
                "targetNodeId": raw.get("target_node_id"),
                "targetLevel": raw.get("target_level"),
                "aliases": aliases,
                "evidence": evidence,
                "negativeRules": negative_rules,
                "nonAuthority": bool(metadata.get("nonAuthority")),
                "scope": metadata.get("scope"),
            }
        )
    return {
        "audits": audits,
        "events": events,
        "deferredByGuard": deferred_by_guard,
        "routingHints": routing_hints,
        "auditCount": len(audits),
        "eventCount": len(events),
        "deferredByGuardCount": len(deferred_by_guard),
        "routingHintCount": len(routing_hints),
    }


def update_node(
    node_id: str,
    *,
    title: str | None = None,
    status: str | None = None,
    parent_node_id=...,
    progress: int | None = None,
    completion_method: str | None = None,
    managed_by: str | None = None,
) -> dict:
    paths = _dashboard_paths()
    metadata = {}
    if completion_method:
        metadata["completionMethod"] = completion_method
    if managed_by in {NODE_MANAGED_BY_AGENT, NODE_MANAGED_BY_HUMAN}:
        metadata["managedBy"] = managed_by
    if (status is not None or progress is not None) and managed_by != NODE_MANAGED_BY_AGENT:
        metadata.update(
            project_graph_metadata(
                origin=ORIGIN_PLANNED,
                state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                updatedFrom="dashboard",
                managed_by=metadata.get("managedBy") or NODE_MANAGED_BY_HUMAN,
            )
        )
    actor = (
        "dashboard-manage-toggle"
        if managed_by == NODE_MANAGED_BY_AGENT and title is None and parent_node_id is ... and progress is None and not completion_method
        else "dashboard"
    )
    node = update_task_node(
        paths,
        node_id=node_id,
        actor=actor,
        title=title,
        status=status,
        parent_node_id=parent_node_id,
        progress=progress,
        metadata=metadata or None,
    )
    _export_boards(paths)
    return {
        "status": "ok",
        "node": {
            "nodeId": node.node_id,
            "title": node.title,
            "nodeType": node.node_type,
            "parentNodeId": node.parent_node_id,
            "taskStatus": node.status,
            "progress": node.progress,
        },
    }


def create_node(
    *,
    title: str,
    status: str = "planned",
    parent_node_id: str | None = None,
    node_type: str | None = None,
) -> dict:
    paths = _dashboard_paths()
    resolved_type = node_type or ("subtask" if parent_node_id else "task")
    node = create_task_node(
        paths,
        title=title,
        node_type=resolved_type,
        parent_node_id=parent_node_id,
        status=status,
        actor="dashboard",
        metadata=project_graph_metadata(
            origin=ORIGIN_PLANNED,
            state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
            createdFrom="dashboard",
        ),
    )
    _export_boards(paths)
    return {
        "status": "ok",
        "node": {
            "nodeId": node.node_id,
            "title": node.title,
            "nodeType": node.node_type,
            "parentNodeId": node.parent_node_id,
            "taskStatus": node.status,
            "progress": node.progress,
        },
    }


def confirm_candidate(
    candidate_id: str,
    *,
    title: str | None = None,
    reason: str | None = None,
    parent_node_id: str | None = None,
    node_type: str | None = None,
) -> dict:
    paths = _dashboard_paths()
    _require_l1_review_candidate(paths, candidate_id)
    node = confirm_candidate_as_task(
        paths,
        candidate_id=candidate_id,
        actor="dashboard",
        title=title,
        parent_node_id=parent_node_id,
        node_type=node_type,
        reason=reason,
    )
    _export_boards(paths)
    return {
        "status": "ok",
        "node": {
            "nodeId": node.node_id,
            "title": node.title,
            "nodeType": node.node_type,
            "parentNodeId": node.parent_node_id,
            "taskStatus": node.status,
            "progress": node.progress,
        },
        **candidate_status(),
    }


def reject_candidate(candidate_id: str, *, reason: str | None = None) -> dict:
    paths = _dashboard_paths()
    _require_l1_review_candidate(paths, candidate_id)
    decision = reject_task_candidate(
        paths,
        candidate_id=candidate_id,
        actor="dashboard",
        reason=reason,
    )
    _export_boards(paths)
    return {"status": "ok", "candidateId": decision.candidate_id, "decisionId": decision.decision_id, **candidate_status()}


def delete_candidate(candidate_id: str, *, reason: str | None = None) -> dict:
    return reject_candidate(candidate_id, reason=reason or "Deleted from candidate whitelist")


def defer_candidate(candidate_id: str, *, reason: str | None = None) -> dict:
    paths = _dashboard_paths()
    _require_l1_review_candidate(paths, candidate_id)
    decision = defer_task_candidate(
        paths,
        candidate_id=candidate_id,
        actor="dashboard",
        reason=reason,
    )
    _export_boards(paths)
    return {"status": "ok", "candidateId": decision.candidate_id, "decisionId": decision.decision_id, **candidate_status()}


def merge_candidate(
    candidate_id: str,
    *,
    reason: str | None = None,
    target_candidate_id: str | None = None,
    target_node_id: str | None = None,
) -> dict:
    paths = _dashboard_paths()
    _require_l1_review_candidate(paths, candidate_id)
    decision = merge_task_candidate(
        paths,
        candidate_id=candidate_id,
        actor="dashboard",
        reason=reason,
        target_candidate_id=target_candidate_id,
        target_node_id=target_node_id,
    )
    _export_boards(paths)
    return {"status": "ok", "candidateId": decision.candidate_id, "decisionId": decision.decision_id, **candidate_status()}


def supersede_candidate(
    candidate_id: str,
    *,
    reason: str | None = None,
    target_candidate_id: str | None = None,
    target_node_id: str | None = None,
) -> dict:
    paths = _dashboard_paths()
    _require_l1_review_candidate(paths, candidate_id)
    decision = supersede_task_candidate(
        paths,
        candidate_id=candidate_id,
        actor="dashboard",
        reason=reason,
        target_candidate_id=target_candidate_id,
        target_node_id=target_node_id,
    )
    _export_boards(paths)
    return {"status": "ok", "candidateId": decision.candidate_id, "decisionId": decision.decision_id, **candidate_status()}


def _require_l1_review_candidate(paths, candidate_id: str) -> None:
    with connect(paths, read_only=True) as connection:
        row = connection.execute(
            "SELECT candidate_type, status FROM nova_task_candidates WHERE candidate_id = ?",
            (candidate_id,),
        ).fetchone()
    if row is None:
        raise ValueError(f"Nova-Task L1 review item not found: {candidate_id}")
    if row["candidate_type"] != "parent_task":
        raise ValueError(f"Nova-Task review item is not Level 1: {candidate_id}")
    if row["status"] not in {"pending_review", "pending"}:
        raise ValueError(f"Nova-Task L1 review item is not pending review: {candidate_id}")
