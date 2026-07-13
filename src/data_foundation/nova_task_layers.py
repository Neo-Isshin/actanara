"""Nova-Task three-layer domain contract.

Nova-Task is not a generic todo app. It maintains:

1. Project Graph: durable project/subsystem/deliverable/action structure.
2. Evidence Ledger: observed daily engineering progress, blockers, fixes, and
   validation evidence.
3. Planning Overlay: user-approved RFC/PRD/roadmap intent and Level-1 proposals.

The current SQLite tables predate this vocabulary, so these constants are used
as compatibility metadata until a future storage migration can make the layers
first-class columns.
"""

from __future__ import annotations

from typing import Any

LAYER_PROJECT_GRAPH = "project_graph"
LAYER_EVIDENCE_LEDGER = "evidence_ledger"
LAYER_PLANNING_OVERLAY = "planning_overlay"

ORIGIN_OBSERVED = "observed"
ORIGIN_PLANNED = "planned"

STATE_AUTHORITY_OBSERVED_SIGNAL = "observed_signal"
STATE_AUTHORITY_PLANNED_STATE_MACHINE = "planned_state_machine"

TASK_NODE_STATUS_PLANNED = "planned"
TASK_NODE_STATUS_ACTIVE = "active"
TASK_NODE_STATUS_BLOCKED = "blocked"
TASK_NODE_STATUS_PAUSED = "paused"
TASK_NODE_STATUS_COMPLETED = "completed"
TASK_NODE_STATUS_DONE = "done"
TASK_NODE_STATUS_AUTOMATIC = "automatic"
TASK_NODE_STATUS_SETTLED = "settled"
TASK_NODE_STATUS_STALE = "stale"
TASK_NODE_STATUS_ARCHIVED = "archived"

TASK_NODE_STATUS_DESCRIPTIONS = {
    TASK_NODE_STATUS_PLANNED: "Confirmed future intent with no required observed development evidence yet.",
    TASK_NODE_STATUS_ACTIVE: "Real development or maintenance activity is underway or observed for this node.",
    TASK_NODE_STATUS_BLOCKED: "The node remains valid but has an explicit blocker.",
    TASK_NODE_STATUS_PAUSED: "The node remains valid but has been intentionally put aside without an explicit blocker.",
    TASK_NODE_STATUS_COMPLETED: "The node is finished with confirmable outcome or evidence.",
    TASK_NODE_STATUS_DONE: "Human-managed planned work is finished.",
    TASK_NODE_STATUS_AUTOMATIC: "Agent-managed observed work is maintained automatically from evidence.",
    TASK_NODE_STATUS_SETTLED: "Agent-managed observed work has closed-loop evidence.",
    TASK_NODE_STATUS_STALE: "Agent-managed observed work has not received fresh evidence recently.",
    TASK_NODE_STATUS_ARCHIVED: "The node is no longer managed as active work; it may be cancelled, superseded, merged, or retained as history.",
}

NODE_CREATED_BY_AGENT = "agent"
NODE_CREATED_BY_HUMAN = "human"
NODE_MANAGED_BY_AGENT = "agent"
NODE_MANAGED_BY_HUMAN = "human"

CANDIDATE_STATUS_PENDING_REVIEW = "pending_review"
CANDIDATE_STATUS_CONFIRMED = "confirmed"
CANDIDATE_STATUS_MERGED = "merged"
CANDIDATE_STATUS_SUPERSEDED = "superseded"
CANDIDATE_STATUS_REJECTED = "rejected"
CANDIDATE_STATUS_DEFERRED = "deferred"

CANDIDATE_STATUS_DESCRIPTIONS = {
    CANDIDATE_STATUS_PENDING_REVIEW: "System proposal awaiting operator review.",
    CANDIDATE_STATUS_CONFIRMED: "Operator accepted the proposal into, or attached it to, the Project Graph.",
    CANDIDATE_STATUS_MERGED: "Proposal was folded into another candidate or graph node.",
    CANDIDATE_STATUS_SUPERSEDED: "Proposal was replaced by a newer proposal or deterministic graph binding.",
    CANDIDATE_STATUS_REJECTED: "Operator rejected the proposal.",
    CANDIDATE_STATUS_DEFERRED: "Operator postponed the proposal for later review.",
}

LEVEL_NODE_TYPES = {
    1: "track",
    2: "workstream",
    3: "task",
    4: "subtask",
    5: "step",
}

NODE_TYPE_LEVELS = {node_type: level for level, node_type in LEVEL_NODE_TYPES.items()}


def node_type_for_level(level: int, *, fallback: str = "task") -> str:
    return LEVEL_NODE_TYPES.get(int(level or 0), fallback)


def level_for_node_type(node_type: str | None, *, fallback: int = 3) -> int:
    return NODE_TYPE_LEVELS.get(str(node_type or ""), fallback)


def layer_metadata(layer: str, **extra: Any) -> dict[str, Any]:
    metadata = {"novaTaskLayer": layer}
    metadata.update(extra)
    return metadata


def project_graph_metadata(
    *,
    origin: str,
    state_authority: str,
    created_by: str | None = None,
    managed_by: str | None = None,
    **extra: Any,
) -> dict[str, Any]:
    if created_by is None:
        created_by = NODE_CREATED_BY_HUMAN if origin == ORIGIN_PLANNED else NODE_CREATED_BY_AGENT
    if managed_by is None:
        managed_by = NODE_MANAGED_BY_HUMAN if origin == ORIGIN_PLANNED else NODE_MANAGED_BY_AGENT
    return layer_metadata(
        LAYER_PROJECT_GRAPH,
        origin=origin,
        stateAuthority=state_authority,
        createdBy=created_by,
        managedBy=managed_by,
        **extra,
    )


def allows_planned_state_machine(metadata: dict[str, Any] | None) -> bool:
    if not isinstance(metadata, dict):
        return False
    return (
        metadata.get("novaTaskLayer") == LAYER_PROJECT_GRAPH
        and metadata.get("origin") == ORIGIN_PLANNED
        and metadata.get("stateAuthority") == STATE_AUTHORITY_PLANNED_STATE_MACHINE
        and metadata.get("managedBy", NODE_MANAGED_BY_HUMAN) == NODE_MANAGED_BY_HUMAN
    )
