"""LLM-assisted reconciliation for the Nova-Task work graph.

This job is intentionally separate from the daily diary pipeline. It can ask an
LLM to classify project graph, evidence ledger, and planning overlay signals,
but authority writes still flow through Nova-Task deterministic validation.
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Callable

from .llm_transport import send_anthropic_message, send_openai_compatible_message
from .nova_task_layers import (
    LAYER_EVIDENCE_LEDGER,
    LAYER_PLANNING_OVERLAY,
    LAYER_PROJECT_GRAPH,
    NODE_CREATED_BY_AGENT,
    NODE_MANAGED_BY_AGENT,
    NODE_MANAGED_BY_HUMAN,
    ORIGIN_OBSERVED,
    STATE_AUTHORITY_OBSERVED_SIGNAL,
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
    layer_metadata,
    level_for_node_type,
    project_graph_metadata,
)
from .nova_task import (
    NovaTaskEvidenceIngest,
    _apply_node_status_from_signal,
    _extract_nova_task_payload,
    _insert_event,
    _list_payload,
    _managed_by,
    _new_id,
    _node_depth,
    _string_list,
    _suggested_node_type_from_item,
    attach_task_candidate_to_node,
    confirm_candidate_as_task,
    create_task_candidate,
    create_task_node,
    defer_task_candidate,
    export_task_board_markdown,
    ingest_nova_task_evidence,
    list_task_candidates,
    merge_task_candidate,
    pending_candidate_count,
    reject_task_candidate,
    render_task_graph_context,
    supersede_task_candidate,
)
from .db import connect
from .paths import RuntimePaths, load_paths
from .settings import resolve_llm_provider
from .time import business_now, business_today
from .workspace_attribution import build_workspace_attribution_catalog
from .workspace_attribution import canonical_workspace_name

LlmSender = Callable[..., str]

SOURCE_TYPE_WORK_GRAPH = "nova_task_work_graph"
ACTOR_WORK_GRAPH = "nova-task-work-graph"
LEGACY_SOURCE_TYPE_RECONCILIATION = "nova_task_reconciliation"
LEGACY_ACTOR_RECONCILIATION = "nova-task-reconciliation"


SYSTEM_PROMPT = """You are a Nova-Task work-graph reconciliation analyst.
Nova-Task is an AI-native engineering memory system with three layers:

1. Project Graph: durable L1-L5 structure for projects, subsystems, deliverables, implementation tasks, and actions.
2. Evidence Ledger: observed daily engineering progress, blockers, fixes, validations, risks, and lessons. Evidence can attach to graph nodes without creating new nodes.
3. Planning Overlay: RFC/PRD/roadmap/user-approved intent. True task state machines are meaningful only for planned work or user-approved Level-1 roots.

You may classify evidence and propose graph changes, but you never write authority directly. Nova-Task performs deterministic validation, dedupe, numbering, audit, and graph writes.

Layer and level rules:
1. First classify each meaningful item into one of: project_graph, evidence_ledger, planning_overlay.
2. L1 Project/Product Root: real long-lived project or product boundary, usually workspace/project anchor. Examples: actanara, TokenClock, actanara. L1 always requires user approval and must not be created as candidate_subtasks.
3. L2 Subsystem/Major Workstream: durable subsystem or operational stream inside an L1. Examples: RAG subsystem, dashboard-webui, database/foundation, nova-task subsystem, release rollout, infrastructure operations. L2 parent must be an existing L1 anchor.
4. L3 Deliverable/Feature Area: clear deliverable under an L2 with a coherent acceptance target. Examples: History Backfill orchestrator, Workspace Attribution Review UI. L3 parent must be an existing L2 node.
5. L4 Implementation Task: module/API/state-machine/UI-flow/test-matrix work needed to finish an L3. Usually 1-3 days of implementation. L4 parent must be an existing L3 node.
6. L5 Action/Fix/Check: single fix, API endpoint, command, script, config change, validation, or regression check. L5 parent must be an existing L4 node. Most one-day diary-derived items belong here or only in evidence_ledger.
7. Do not promote one file edit, one command, one bug, one visual tweak, or one failed run above L5.
8. Prefer matching existing NT-* nodes before creating anything. Existing graph node references must use real NT-* ids from the active graph. Never invent NT-* ids.
9. Pending NTC-* ids are evidence locators only, not graph node ids.
10. If active graph already has a project root for actanara, TokenClock, or another workspace, do not create another L1 parent. Use matched_tasks or direct L2-L5 candidate_subtasks under the existing root/subsystem.
11. Treat graph root titles with suffixes like 系统, 项目开发, project development, or app as project anchors. For example, "actanara系统" is the actanara root and "Tokenclock项目开发" is the TokenClock root.
12. Never output candidate_subtasks whose proposed_title is the same as the proposed_parent_task_id node title. If a node already exists, emit matched_tasks or candidate_actions attach_existing instead.
13. Existing candidates may contain stale proposedParentNodeId values. Trust title, evidence, workspace attribution, and active graph over stale candidate parent hints.
14. Every candidate_subtask must include level_decision with chosen_level, layer, parent_level, why_not_higher, why_not_lower, matched_existing_node_id, and create_new_node. Despite the legacy field name, validated L2-L5 candidate_subtasks are materialized directly, not held for user review. Use candidate_subtasks only when matched_existing_node_id is empty and create_new_node is true; existing nodes belong in matched_tasks or reparent_hints.
15. The chosen_level must equal the resolved parent level plus one. If the correct L1 is missing, use unresolved or candidate_parent_tasks for L1; do not attach to a convenient but wrong parent. If an L1 exists but an intermediate L2/L3/L4 is missing, create that parent first in the same candidate_subtasks list and reference it with proposed_ref/proposed_parent_ref.
16. Never skip levels anywhere in the hierarchy. A candidate_subtasks item must be exactly one level below its resolved parent. If the only real parent is an L1 and the work is L3-like, first create an L2 workstream under the L1, then create the L3 deliverable under that L2 using proposed_parent_ref. If the only real parent is an L2 and the work is L4/L5-like, first create an L3 deliverable under the L2, then create the L4/L5 child under that proposed_ref. If the only real parent is an L3 and the work is L5-like, first create an L4 implementation task under the L3, then create the L5 action under that proposed_ref.
17. Use workspace attribution only to choose the L1 project anchor. A workspace root path is not enough to create L2-L5 unless the active graph has the matching NT-* parent.
18. Observed technical-report evidence creates evidence_ledger entries or agent-managed observed project_graph nodes. It must not be treated as planned work unless the source is explicit roadmap/RFC/PRD/user-approved intent.
19. For new candidate_subtasks, include status_decision as lifecycle evidence. Recon-created agent nodes use only automatic or settled; deterministic validation maps finished/validated work to settled and all other observed work to automatic.
20. Status state-machine transitions for existing nodes are allowed only for human-managed planned graph nodes or explicit user-approved non-L1 work. Observed completion for existing agent-managed observed nodes should normally be matched_tasks evidence or agent lifecycle, not human status_signals.
21. Keep output complete but consolidated: max 2 L1 candidate_parent_tasks, max 30 direct L2-L5 candidate_subtasks, max 20 matched_tasks, max 10 reparent_hints, max 25 legacy candidate_actions, max 12 unresolved.
22. Output exactly one YAML code block rooted at nova_task. No JSON.
23. The YAML must be complete and parseable. If there is too much work, output fewer high-confidence changes; never leave the YAML block truncated.
"""


PROMPT_TEMPLATE = """Review the technical report and Nova-Task evidence set, then produce the only structured Nova-Task work-graph payload for today.

Inputs:
- Active graph context:
{active_graph}

- Workspace attribution catalog:
{workspace_catalog}

- Routing hint inference instructions:
{routing_hint_instructions}

- Technical report for {business_date}:
{technical_report}

- Candidate evidence set, noise-filtered:
{candidate_inbox}

Task:
1. Read the technical report as the high-value engineering chronicle. Use it as the primary source for today's evidence_ledger entries.
2. Use candidate evidence only to consolidate, dedupe, route, and decide whether visible project_graph nodes are missing. Do not blindly recreate all candidates one-for-one.
3. First infer routing_hints from today's evidence and active graph. A routing_hint is a local, auditable semantic rule for this reconciliation only; it is not persistent product configuration and it is not authority.
4. Use routing_hints to explain why a cluster belongs under an existing node, why an L1 candidate is needed, or why an existing non-L1 node should be reparented. Cite routing_hint ids in evidence when useful.
5. Matching order: existing L1 root -> existing L2 subsystem/workstream -> existing L3/L4/L5 -> new L2-L5 only if material and future tracking is useful -> evidence_ledger only.
6. Identify missing L1 project anchors as candidate_parent_tasks only. L1 belongs to planning_overlay and requires manual approval.
7. Identify durable L2 workstreams/subsystems under existing project anchors. Use candidate_subtasks with proposed_parent_task_id set to a real NT-* L1 id and suggested_node_type: workstream; these validated L2 entries are written directly to the Project Graph.
8. Identify L3/L4/L5 items only when they have durable tracking value and a stable parent chain rooted at a real existing L1. Use suggested_node_type: deliverable for L3, subtask for L4, action for L5. If an intermediate parent is new in this same payload, give that parent a proposed_ref and set the child proposed_parent_ref to it. If you found an existing matched node, do not also create candidate_subtasks for it.
9. Emit matched_tasks for evidence_ledger progress that maps to existing NT-* nodes without needing a new node.
10. For each new candidate_subtasks item, set status_decision:
   - settled when the report says the implementation/fix/check was finished, validated, passed, landed, or otherwise closed;
   - automatic when it is observed work that should remain visible as agent-maintained engineering activity.
11. Emit status_signals only for planned graph nodes or clear non-L1 status changes. Observed diary evidence alone usually should not create a state-machine transition.
12. Put tiny one-off work in unresolved with reason tiny_work_evidence_only only when it should not become a visible L5 action under an existing parent.
13. Emit reparent_hints only when an existing non-L1 graph node is clearly under the wrong parent and both child_task_id and proposed_parent_task_id are real NT-* ids from the active graph. Do not use reparent_hints for speculative restructuring.
14. Use candidate_actions only as secondary cleanup for stale historical pending-review candidates:
   - attach_existing when a pending NTC-* is already represented by an existing NT-* node;
   - merge when a pending NTC-* should be folded into a better existing NTC-* candidate or NT-* node;
   - supersede when a pending NTC-* was replaced by a newer/better NTC-* candidate or deterministic NT-* graph binding;
   - reject when it is duplicate, noise, obsolete, or outside Nova-Task scope;
   - defer when evidence is plausible but still lacks a stable parent or materiality.
15. For this evaluation, cover the evidence set by grouping many NTC-* ids under fewer durable nodes or matched evidence entries. Each visible node should cite the NTC-* ids it consolidates.
16. candidate_subtasks.proposed_parent_task_id must be a real NT-* from the active graph unless proposed_parent_ref points to a previous candidate_subtasks.proposed_ref in the same YAML. Never use placeholder ids such as PENDING, TODO, proposed title text, or a not-yet-approved L1 candidate. If only a pending L1 candidate exists, emit candidate_parent_tasks and put its children in unresolved until that L1 is approved.
17. If the only real parent is higher than the work's chosen level, create every missing intermediate parent in order. Examples:
   - L1 -> proposed_ref new-l2-installer with proposed_level 2, then proposed_parent_ref new-l2-installer -> proposed_ref new-l3-wizard with proposed_level 3.
   - L2 -> proposed_ref new-l3-settings-audit with proposed_level 3, then proposed_parent_ref new-l3-settings-audit -> proposed_ref new-l4-secret-audit with proposed_level 4.
   - L2 -> proposed_ref new-l3-codex-fixtures with proposed_level 3, then L3 -> proposed_ref new-l4-fixture-coverage with proposed_level 4, then L4 -> proposed_ref new-l5-provider-case with proposed_level 5.
18. unresolved entries must be valid YAML mappings only. Put explanatory bullets in details: ["..."] and never write a bare list item directly under reason.
19. It is better to output 10 valid changes than 40 truncated changes. Always close the YAML code block.

Output exactly:

```yaml
nova_task:
  date: "{business_date}"
  routing_hints:
    - hint_id: "RH-..."
      boundary_type: l2_subsystem
      aliases: ["...", "..."]
      target_node_id: "NT-..."
      target_level: 2
      confidence: high
      reason: "..."
      evidence: ["technical:...", "graph:NT-..."]
      negative_rules:
        - "..."
  matched_tasks:
    - task_id: "NT-..."
      confidence: high
      event_type: progress
      summary: "..."
      evidence: ["technical:...", "candidate:NTC-..."]
  candidate_parent_tasks:
    - proposed_title: "..."
      suggested_node_type: track
      proposed_level: 1
      reason: "..."
      evidence: ["candidate:NTC-...", "workspace:..."]
  candidate_subtasks:
    - proposed_ref: "new-l2-or-empty"
      proposed_parent_task_id: "NT-..."
      proposed_parent_ref: ""
      suggested_node_type: workstream
      proposed_level: 2
      proposed_title: "..."
      reason: "..."
      level_decision:
        chosen_level: 2
        layer: project_graph
        parent_level: 1
        why_not_higher: "..."
        why_not_lower: "..."
        matched_existing_node_id: ""
        create_new_node: true
      status_decision:
        target_status: automatic
        source_type: observed_progress
        reason: "..."
      evidence: ["candidate:NTC-..."]
  status_signals:
    - task_id: "NT-..."
      confidence: medium
      target_status: blocked
      status_reason: "..."
      status_tags: ["waiting_external"]
      evidence: ["technical:..."]
  reparent_hints:
    - child_task_id: "NT-..."
      proposed_parent_task_id: "NT-..."
      confidence: high
      reason: "Existing non-L1 node is clearly under the wrong parent."
      evidence: ["technical:..."]
  candidate_actions:
    - candidate_id: "NTC-..."
      action: attach_existing
      target_node_id: "NT-..."
      reason: "Already represented by this graph node."
      confidence: high
      evidence: ["candidate:NTC-...", "technical:..."]
    - candidate_id: "NTC-..."
      action: reject
      reason: "Duplicate/noise/obsolete/out of scope."
      confidence: high
      evidence: ["candidate:NTC-..."]
    - candidate_id: "NTC-..."
      action: merge
      target_candidate_id: "NTC-..."
      target_node_id: ""
      reason: "Fold into the stronger existing candidate."
      confidence: high
      evidence: ["candidate:NTC-..."]
    - candidate_id: "NTC-..."
      action: supersede
      target_candidate_id: ""
      target_node_id: "NT-..."
      reason: "Replaced by deterministic graph binding."
      confidence: high
      evidence: ["candidate:NTC-..."]
    - candidate_id: "NTC-..."
      action: defer
      reason: "Plausible, but parent/materiality is not stable enough yet."
      confidence: medium
      evidence: ["candidate:NTC-..."]
  unresolved:
    - summary: "..."
      reason: no_active_task_match | insufficient_evidence | tiny_work_evidence_only | no_material_task_progress
      details: ["optional concise explanation"]
```
"""


@dataclass(frozen=True)
class NovaTaskWorkGraphReconciliationResult:
    business_date: str
    pending_before: int
    candidates_sent: int
    applied: bool
    artifact_path: str
    event_count: int
    candidate_count: int
    auto_confirmed_count: int
    action_count: int
    attached_count: int
    rejected_count: int
    deferred_count: int
    merged_count: int
    superseded_count: int
    pending_after: int
    response_preview: str
    evidence_ledger_event_count: int
    project_graph_write_count: int
    planning_overlay_proposal_count: int
    response_malformed: bool = False
    summary_path: str = ""


def noise_filtered_candidate_inbox(
    paths: RuntimePaths,
    *,
    limit: int = 120,
    include_reconciled_test_set: bool = False,
) -> list[dict[str, Any]]:
    """Return candidate evidence with volatile status/update fields removed."""
    if include_reconciled_test_set:
        items = _reconciled_candidate_test_set(paths, limit=limit)
    else:
        items = list_task_candidates(paths, status="pending_review", limit=limit)
    normalized = []
    for item in items:
        candidate_type = str(item.get("candidateType") or "")
        if candidate_type == "status_update" and not include_reconciled_test_set:
            continue
        metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
        normalized.append(
            {
                "candidateId": item.get("candidateId"),
                "candidateType": candidate_type,
                "currentStatus": item.get("status"),
                "proposedTitle": item.get("proposedTitle"),
                "proposedParentNodeId": item.get("proposedParentNodeId"),
                "matchedNodeId": item.get("matchedNodeId"),
                "reason": item.get("reason"),
                "evidence": (item.get("evidence") or [])[:5],
                "hintType": metadata.get("hint_type"),
                "candidateKind": metadata.get("candidateKind"),
                "workspace": metadata.get("workspace") if isinstance(metadata.get("workspace"), dict) else None,
            }
        )
    return normalized


def _reconciled_candidate_test_set(paths: RuntimePaths, *, limit: int = 120) -> list[dict[str, Any]]:
    """Return the old candidate backlog used for graph-classification evaluation.

    This intentionally includes candidates that were already attached/deferred by
    earlier reconciliation runs, because for evaluation they are input evidence,
    not the authority object being protected.
    """
    with connect(paths, read_only=True) as connection:
        rows = connection.execute(
            """
            WITH recon_confirmed AS (
                SELECT DISTINCT candidate_id
                FROM nova_task_reconciliation_decisions
                WHERE actor IN (?, ?)
                  AND decision_type IN ('attached', 'attach_as_subtask', 'merge', 'supersede')
            )
            SELECT *
            FROM nova_task_candidates
            WHERE status IN ('pending_review', 'pending', 'deferred')
               OR candidate_id IN (SELECT candidate_id FROM recon_confirmed)
            ORDER BY created_at, candidate_id
            LIMIT ?
            """,
            (ACTOR_WORK_GRAPH, LEGACY_ACTOR_RECONCILIATION, int(limit)),
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
                "metadata": metadata if isinstance(metadata, dict) else {},
            }
        )
    return candidates


def _bounded_text(text: str, *, max_chars: int = 30000) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n\n[... truncated for reconciliation prompt ...]"


def build_routing_hint_inference_instructions() -> str:
    """Return generic instructions for in-pass routing hint discovery."""
    instructions = {
        "policy": "Infer routing_hints inside this reconciliation pass. Do not rely on static product-specific rules.",
        "hintSemantics": [
            "A routing_hint is a temporary explanation for this YAML payload, not persistent authority.",
            "Derive aliases from repeated technical terms, file/module names, subsystem names, workspace names, and graph titles present in the inputs.",
            "Prefer specific co-occurring aliases over broad single words. For example, a lone generic noun is weak; a cluster of module + domain + failure mode is stronger.",
            "target_node_id must be a real NT-* id from the active graph when boundary_type routes to an existing node.",
            "For a missing long-lived project/product boundary, use boundary_type l1_candidate and leave target_node_id empty.",
            "If an existing non-L1 node clearly belongs under a better existing parent, explain that as a routing_hint and emit a reparent_hints item that cites it.",
            "If aliases are plausible but the target is not stable, emit unresolved instead of creating or reparenting graph nodes.",
        ],
        "allowedBoundaryTypes": [
            "existing_node",
            "l1_candidate",
            "l2_subsystem",
            "l3_deliverable",
            "evidence_only",
        ],
        "requiredFields": [
            "hint_id",
            "boundary_type",
            "aliases",
            "target_node_id",
            "target_level",
            "confidence",
            "reason",
            "evidence",
            "negative_rules",
        ],
    }
    return json.dumps(instructions, ensure_ascii=False, indent=2)


def build_reconciliation_prompt(
    paths: RuntimePaths,
    *,
    business_date: date,
    limit: int = 120,
    technical_report: str = "",
    include_reconciled_test_set: bool = False,
) -> tuple[str, list[dict[str, Any]]]:
    inbox = noise_filtered_candidate_inbox(
        paths,
        limit=limit,
        include_reconciled_test_set=include_reconciled_test_set,
    )
    catalog = build_workspace_attribution_catalog(paths)
    compact_catalog = {
        "projects": [
            {
                "displayName": item.get("display_name"),
                "rootPath": item.get("root_path"),
                "confidence": item.get("confidence"),
                "sources": item.get("sources"),
                "observationCount": item.get("observation_count"),
            }
            for item in (catalog.get("projects") or [])[:40]
            if isinstance(item, dict)
        ]
    }
    prompt = PROMPT_TEMPLATE.format(
        active_graph=render_task_graph_context(paths, max_nodes=120),
        workspace_catalog=json.dumps(compact_catalog, ensure_ascii=False, indent=2),
        routing_hint_instructions=build_routing_hint_inference_instructions(),
        technical_report=_bounded_text(technical_report) or "(technical report missing)",
        candidate_inbox=json.dumps(inbox, ensure_ascii=False, indent=2),
        business_date=business_date.isoformat(),
    )
    return prompt, inbox


def run_work_graph_reconciliation(
    paths: RuntimePaths | None = None,
    *,
    business_date: date | None = None,
    limit: int = 120,
    apply: bool = False,
    auto_confirm_non_l1: bool = False,
    actions_only: bool = False,
    technical_report: str = "",
    technical_report_path: Path | None = None,
    include_reconciled_test_set: bool = False,
    direct_graph_apply: bool = True,
    sender: LlmSender | None = None,
) -> NovaTaskWorkGraphReconciliationResult:
    selected = paths or load_paths()
    target_date = business_date or business_today(selected)
    pending_before = len(list_task_candidates(selected, status="pending_review", limit=200))
    report_text = technical_report
    if not report_text and technical_report_path is not None and technical_report_path.exists():
        report_text = technical_report_path.read_text(encoding="utf-8")
    prompt, inbox = build_reconciliation_prompt(
        selected,
        business_date=target_date,
        limit=limit,
        technical_report=report_text,
        include_reconciled_test_set=include_reconciled_test_set,
    )
    provider = resolve_llm_provider(selected, redact_secrets=False)
    resolved_sender = sender or (
        send_anthropic_message if provider.get("api") == "anthropic-messages" else send_openai_compatible_message
    )
    response = resolved_sender(
        endpoint=provider["endpoint"],
        api_key=provider["apiKey"],
        model=provider["model"],
        system=SYSTEM_PROMPT,
        prompt=prompt,
        temperature=0.05,
        max_tokens=16384,
        timeout=int(provider.get("timeoutSeconds") or 180),
        thinking_mode="off",
    )
    artifact = _write_reconciliation_artifact(selected, target_date, response, apply=apply)
    ingest = NovaTaskEvidenceIngest(0, 0, 0)
    auto_confirmed = 0
    action_counts = {"attached": 0, "rejected": 0, "deferred": 0, "merged": 0, "superseded": 0}
    if apply:
        before_candidate_ids = _candidate_ids(selected)
        if not actions_only:
            if direct_graph_apply:
                ingest, auto_confirmed = apply_reconciliation_graph_direct(
                    selected,
                    markdown=response,
                    business_date=target_date,
                    source_path=artifact,
                )
            else:
                ingest = ingest_nova_task_evidence(
                    selected,
                    markdown=response,
                    business_date=target_date,
                    source_path=artifact,
                    source_type=SOURCE_TYPE_WORK_GRAPH,
                )
        if auto_confirm_non_l1 and not actions_only and not direct_graph_apply:
            auto_confirmed = auto_confirm_reconciliation_candidates(
                selected,
                candidate_ids=sorted(_candidate_ids(selected) - before_candidate_ids),
            )
        action_counts = apply_candidate_actions_from_reconciliation(selected, response)
        export_task_board_markdown(selected)
    pending_after = len(list_task_candidates(selected, status="pending_review", limit=200))
    summary_path = _write_reconciliation_summary_artifact(
        selected,
        artifact,
        business_date=target_date,
        applied=apply,
        response=response,
        ingest=ingest,
        project_graph_write_count=auto_confirmed,
        action_counts=action_counts,
        pending_before=pending_before,
        pending_after=pending_after,
        candidates_sent=len(inbox),
    )
    return NovaTaskWorkGraphReconciliationResult(
        business_date=target_date.isoformat(),
        pending_before=pending_before,
        candidates_sent=len(inbox),
        applied=apply,
        artifact_path=str(artifact),
        event_count=ingest.event_count,
        candidate_count=ingest.candidate_count,
        auto_confirmed_count=auto_confirmed,
        action_count=sum(action_counts.values()),
        attached_count=action_counts["attached"],
        rejected_count=action_counts["rejected"],
        deferred_count=action_counts["deferred"],
        merged_count=action_counts["merged"],
        superseded_count=action_counts["superseded"],
        pending_after=pending_after,
        response_preview=str(response or "").strip()[:500],
        evidence_ledger_event_count=ingest.event_count,
        project_graph_write_count=auto_confirmed,
        planning_overlay_proposal_count=ingest.candidate_count,
        response_malformed=bool(ingest.malformed),
        summary_path=str(summary_path),
    )


NovaTaskCandidateReconciliationResult = NovaTaskWorkGraphReconciliationResult


def run_candidate_reconciliation(*args: Any, **kwargs: Any) -> NovaTaskWorkGraphReconciliationResult:
    """Compatibility alias for callers that still use the pre-work-graph name."""

    return run_work_graph_reconciliation(*args, **kwargs)


def apply_reconciliation_graph_direct(
    paths: RuntimePaths,
    *,
    markdown: str,
    business_date: date,
    source_path: Path | None,
) -> tuple[NovaTaskEvidenceIngest, int]:
    """Apply reconciliation output directly to the graph except Level-1 proposals.

    The LLM still only emits structured intent. This function performs the
    deterministic validation, dedupe, node creation, and status writes.
    """
    payload = _extract_nova_task_payload(markdown)
    if not isinstance(payload, dict):
        return NovaTaskEvidenceIngest(0, 0, pending_candidate_count(paths), malformed="nova_task:" in markdown), 0
    source_sha256 = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    import hashlib

    source_digest = hashlib.sha256(source_sha256.encode("utf-8")).hexdigest()
    inserted_events = 0
    l1_candidate_count = 0
    materialized = 0

    with connect(paths) as connection:
        for item in _validated_routing_hints(connection, payload):
            hint_id = str(item.get("hint_id") or "").strip()
            summary = str(item.get("reason") or hint_id or "LLM inferred routing hint")
            target_id = str(item.get("target_node_id") or "").strip() or None
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type="progress",
                summary=summary,
                evidence=_string_list(item.get("evidence")),
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=target_id if target_id and target_id.startswith("NT-") else None,
                metadata=layer_metadata(
                    LAYER_EVIDENCE_LEDGER,
                    raw=item,
                    directGraphApply=True,
                    hintEventType="routing_hint",
                    nonAuthority=True,
                    scope="current_reconciliation_only",
                ),
            )
            inserted_events += int(inserted)
            del event_id

        for item in _list_payload(payload, "matched_tasks"):
            task_id = str(item.get("task_id") or "") or None
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type=str(item.get("event_type") or "progress"),
                summary=str(item.get("summary") or ""),
                evidence=_string_list(item.get("evidence")),
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=task_id,
                metadata=layer_metadata(LAYER_EVIDENCE_LEDGER, raw=item, directGraphApply=True),
            )
            inserted_events += int(inserted)
            del event_id

        for item in _list_payload(payload, "unresolved"):
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type="unresolved",
                summary=str(item.get("summary") or ""),
                evidence=_string_list(item.get("evidence")),
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=None,
                metadata=layer_metadata(
                    LAYER_EVIDENCE_LEDGER,
                    raw=item,
                    reason=item.get("reason"),
                    directGraphApply=True,
                ),
            )
            inserted_events += int(inserted)
            del event_id

        for item in _list_payload(payload, "status_signals") + _list_payload(payload, "completion_signals"):
            evidence = _string_list(item.get("evidence"))
            task_id = str(item.get("task_id") or "") or None
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type="completion_signal",
                summary=str(item.get("status_reason") or item.get("summary") or item.get("target_status") or ""),
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=task_id,
                metadata=layer_metadata(LAYER_EVIDENCE_LEDGER, raw=item, directGraphApply=True),
            )
            inserted_events += int(inserted)
            depth = _node_depth(connection, task_id)
            tags = set(_string_list(item.get("status_tags")))
            node_metadata = {}
            if task_id:
                node_row = connection.execute(
                    "SELECT metadata_json FROM nova_task_nodes WHERE node_id = ?",
                    (task_id,),
                ).fetchone()
                if node_row is not None:
                    try:
                        parsed = json.loads(node_row["metadata_json"] or "{}")
                        node_metadata = parsed if isinstance(parsed, dict) else {}
                    except Exception:
                        node_metadata = {}
            if task_id and depth and depth > 1 and "needs_review" not in tags and allows_planned_state_machine(node_metadata):
                if _apply_node_status_from_signal(
                    connection,
                    node_id=task_id,
                    event_id=event_id,
                    item=item,
                    evidence=evidence,
                    business_date=business_date,
                    actor=ACTOR_WORK_GRAPH,
                ):
                    materialized += 1

        for item in _list_payload(payload, "reparent_hints"):
            evidence = _string_list(item.get("evidence"))
            child_id = str(item.get("child_task_id") or "").strip()
            parent_id = str(item.get("proposed_parent_task_id") or "").strip()
            reason = str(item.get("reason") or "LLM proposed deterministic graph reparent")
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type="progress",
                summary=reason,
                evidence=evidence,
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=child_id if child_id.startswith("NT-") else None,
                metadata=layer_metadata(
                    LAYER_EVIDENCE_LEDGER,
                    raw=item,
                    directGraphApply=True,
                    hintEventType="reparent_hint",
                    directGraphPolicy="direct_when_deterministic_no_candidate",
                ),
            )
            inserted_events += int(inserted)
            if _apply_direct_reparent_hint(
                connection,
                child_id=child_id,
                parent_id=parent_id,
                event_id=event_id,
                raw=item,
            ):
                materialized += 1

        parent_specs: list[dict[str, Any]] = []
        for item in _list_payload(payload, "candidate_parent_tasks"):
            title = str(item.get("proposed_title") or "").strip()
            if not title:
                continue
            event_id, inserted = _insert_event(
                connection,
                business_date=business_date,
                source_path=source_path,
                source_sha256=source_digest,
                source_type=SOURCE_TYPE_WORK_GRAPH,
                event_type="candidate_parent",
                summary=title,
                evidence=_string_list(item.get("evidence")),
                confidence=str(item.get("confidence") or "unknown"),
                matched_node_id=None,
                metadata=layer_metadata(
                    LAYER_PLANNING_OVERLAY,
                    raw=item,
                    directGraphApply=True,
                    manualRequired=True,
                ),
            )
            inserted_events += int(inserted)
            parent_specs.append(
                {
                    "candidate_type": "parent_task",
                    "proposed_title": title,
                    "reason": str(item.get("reason") or "LLM proposed Level-1 parent task"),
                    "evidence": _string_list(item.get("evidence")),
                    "source_event_id": event_id,
                    "confidence": str(item.get("confidence") or "unknown"),
                    "metadata": {
                        "novaTaskLayer": LAYER_PLANNING_OVERLAY,
                        "raw": item,
                        "suggestedNodeType": "track",
                        "reviewPolicy": "manual_required_for_level_1",
                        "ordinaryDailyEvidencePolicy": "review_only_no_l1_direct_graph_write",
                        "directGraphApply": True,
                    },
                }
            )

        node_specs, subtask_events = _materializable_candidate_subtask_specs(
            connection,
            payload,
            business_date=business_date,
            source_path=source_path,
            source_digest=source_digest,
        )
        inserted_events += subtask_events

    for spec in parent_specs:
        create_task_candidate(paths, **spec)
        l1_candidate_count += 1
    created_node_ids: list[str] = []
    for spec in node_specs:
        node_status = str(spec.pop("status", TASK_NODE_STATUS_AUTOMATIC) or TASK_NODE_STATUS_AUTOMATIC)
        node = create_task_node(paths, actor=ACTOR_WORK_GRAPH, status=node_status, **spec)
        created_node_ids.append(node.node_id)
        materialized += 1
    if created_node_ids:
        materialized += _auto_promote_planned_ancestors_for_observed_children(
            paths,
            node_ids=created_node_ids,
            business_date=business_date,
        )
    return NovaTaskEvidenceIngest(inserted_events, l1_candidate_count, pending_candidate_count(paths)), materialized


def _validated_routing_hints(connection: Any, payload: dict[str, Any]) -> list[dict[str, Any]]:
    hints: list[dict[str, Any]] = []
    for raw in _list_payload(payload, "routing_hints"):
        if not isinstance(raw, dict):
            continue
        hint_id = str(raw.get("hint_id") or "").strip()
        boundary_type = str(raw.get("boundary_type") or "").strip()
        confidence = str(raw.get("confidence") or "").strip().lower()
        evidence = _string_list(raw.get("evidence"))
        aliases = _string_list(raw.get("aliases"))
        if not hint_id or not boundary_type or not evidence or not aliases:
            continue
        if confidence not in {"high", "medium"}:
            continue
        target_id = str(raw.get("target_node_id") or "").strip()
        if target_id:
            if not target_id.startswith("NT-"):
                continue
            exists = connection.execute(
                "SELECT 1 FROM nova_task_nodes WHERE node_id = ? AND status != 'archived'",
                (target_id,),
            ).fetchone()
            if exists is None:
                continue
        hints.append(
            {
                **raw,
                "hint_id": hint_id,
                "boundary_type": boundary_type,
                "aliases": aliases,
                "evidence": evidence,
            }
        )
    return hints[:20]


def _materializable_candidate_subtask_specs(
    connection: Any,
    payload: dict[str, Any],
    *,
    business_date: date,
    source_path: Path | None,
    source_digest: str,
) -> tuple[list[dict[str, Any]], int]:
    node_specs: list[dict[str, Any]] = []
    inserted_events = 0
    created_ref_to_id: dict[str, str] = {}
    created_depth_by_ref: dict[str, int] = {}
    blocked_refs: set[str] = set()

    items = _list_payload(payload, "candidate_subtasks")
    for item in items:
        title = str(item.get("proposed_title") or "").strip()
        if not title:
            continue
        proposed_ref = str(item.get("proposed_ref") or "").strip()
        parent_ref = str(item.get("proposed_parent_ref") or "").strip()
        parent_id = str(item.get("proposed_parent_task_id") or "").strip()
        resolved_parent_id = ""
        resolved_parent_depth: int | None = None
        validation_reason = ""
        if parent_ref:
            if parent_ref in blocked_refs:
                validation_reason = "blocked_parent_ref"
            elif parent_ref in created_ref_to_id:
                resolved_parent_id = created_ref_to_id[parent_ref]
                resolved_parent_depth = created_depth_by_ref.get(parent_ref)
            else:
                validation_reason = "missing_parent_ref"
        elif parent_id.startswith("NT-"):
            resolved_parent_id = parent_id
            resolved_parent_depth = _node_depth(connection, resolved_parent_id)
            if resolved_parent_depth is None:
                validation_reason = "missing_real_parent_node"
        elif parent_id:
            validation_reason = "invalid_non_nt_parent_ref"
        else:
            validation_reason = "missing_parent_ref"

        suggested_type = _suggested_node_type_from_item(item, fallback="subtask")
        if suggested_type == "track":
            validation_reason = validation_reason or "level_1_candidate_subtasks_are_forbidden"
        if resolved_parent_depth is not None and resolved_parent_depth >= 5:
            validation_reason = validation_reason or "parent_depth_exceeds_graph_limit"
        level_valid = False
        equivalent = None
        if not validation_reason and resolved_parent_id:
            level_valid = _candidate_subtask_level_is_valid(
                connection,
                item=item,
                parent_id=resolved_parent_id,
                suggested_type=suggested_type,
                parent_depth_override=resolved_parent_depth,
            )
            if not level_valid:
                validation_reason = "level_contract_rejected"
            equivalent = _existing_equivalent_child_node_in_connection(
                connection,
                proposed_title=title,
                proposed_parent_node_id=resolved_parent_id,
            )

        event_id, inserted = _insert_event(
            connection,
            business_date=business_date,
            source_path=source_path,
            source_sha256=source_digest,
            source_type=SOURCE_TYPE_WORK_GRAPH,
            event_type="candidate_subtask",
            summary=title,
            evidence=_string_list(item.get("evidence")),
            confidence=str(item.get("confidence") or "unknown"),
            matched_node_id=equivalent or (resolved_parent_id if resolved_parent_id.startswith("NT-") else None),
            metadata={
                "novaTaskLayer": LAYER_EVIDENCE_LEDGER,
                "raw": item,
                "proposed_parent_ref": parent_ref or parent_id,
                "resolvedParentNodeId": resolved_parent_id,
                "directGraphApply": True,
                "dedupedToNodeId": equivalent,
                "levelValidation": "accepted" if level_valid else "rejected",
                "levelValidationReason": "" if level_valid else validation_reason,
            },
        )
        inserted_events += int(inserted)
        if not level_valid or equivalent:
            if proposed_ref:
                blocked_refs.add(proposed_ref)
            continue
        allocated_node_id = _new_id("NT")
        node_status = _validated_new_node_status(item, suggested_type=suggested_type)
        if proposed_ref:
            created_ref_to_id[proposed_ref] = allocated_node_id
            created_depth_by_ref[proposed_ref] = int(resolved_parent_depth or 0) + 1
        node_specs.append(
            {
                "node_id": allocated_node_id,
                "title": title,
                "node_type": suggested_type,
                "parent_node_id": resolved_parent_id,
                "status": node_status,
                "progress": 100 if node_status == TASK_NODE_STATUS_SETTLED else 0,
                "metadata": {
                    **project_graph_metadata(
                        origin=ORIGIN_OBSERVED,
                        state_authority=STATE_AUTHORITY_OBSERVED_SIGNAL,
                        created_by=NODE_CREATED_BY_AGENT,
                        managed_by=NODE_MANAGED_BY_AGENT,
                    ),
                    "createdFrom": SOURCE_TYPE_WORK_GRAPH,
                    "sourceEventId": event_id,
                    "rawCandidateSubtask": item,
                    "levelDecision": item.get("level_decision") if isinstance(item.get("level_decision"), dict) else {},
                    "statusDecision": item.get("status_decision") if isinstance(item.get("status_decision"), dict) else {},
                    "evidence": _string_list(item.get("evidence")),
                    "proposedRef": proposed_ref,
                    "proposedParentRef": parent_ref,
                },
            }
        )
    return node_specs, inserted_events


_NEW_NODE_STATUS_ALIASES = {
    "pending": TASK_NODE_STATUS_AUTOMATIC,
    "planned": TASK_NODE_STATUS_AUTOMATIC,
    "todo": TASK_NODE_STATUS_AUTOMATIC,
    "active": TASK_NODE_STATUS_AUTOMATIC,
    "ongoing": TASK_NODE_STATUS_AUTOMATIC,
    "in_progress": TASK_NODE_STATUS_AUTOMATIC,
    "progress": TASK_NODE_STATUS_AUTOMATIC,
    "automatic": TASK_NODE_STATUS_AUTOMATIC,
    "observed": TASK_NODE_STATUS_AUTOMATIC,
    "blocked": TASK_NODE_STATUS_AUTOMATIC,
    "paused": TASK_NODE_STATUS_AUTOMATIC,
    "completed": TASK_NODE_STATUS_SETTLED,
    "complete": TASK_NODE_STATUS_SETTLED,
    "done": TASK_NODE_STATUS_SETTLED,
    "settled": TASK_NODE_STATUS_SETTLED,
    "stale": TASK_NODE_STATUS_STALE,
}


def _validated_new_node_status(item: dict[str, Any], *, suggested_type: str) -> str:
    decision = item.get("status_decision") if isinstance(item.get("status_decision"), dict) else {}
    raw_status = (
        decision.get("target_status")
        or decision.get("status")
        or item.get("target_status")
        or item.get("suggested_status")
        or item.get("status")
    )
    target = _NEW_NODE_STATUS_ALIASES.get(str(raw_status or "").strip().lower())
    if target is None:
        return TASK_NODE_STATUS_AUTOMATIC
    if target == TASK_NODE_STATUS_SETTLED:
        level = level_for_node_type(suggested_type, fallback=3)
        if level <= 2:
            return TASK_NODE_STATUS_AUTOMATIC
        return TASK_NODE_STATUS_SETTLED if _status_decision_has_completion_evidence(decision, item) else TASK_NODE_STATUS_AUTOMATIC
    if target == TASK_NODE_STATUS_STALE:
        return TASK_NODE_STATUS_STALE
    return TASK_NODE_STATUS_AUTOMATIC


def _status_decision_blob(decision: dict[str, Any], item: dict[str, Any]) -> str:
    pieces = [
        decision.get("source_type"),
        decision.get("reason"),
        item.get("reason"),
        " ".join(_string_list(decision.get("evidence"))),
        " ".join(_string_list(item.get("evidence"))),
    ]
    return " ".join(str(piece or "").lower() for piece in pieces)


def _status_decision_is_future_intent(decision: dict[str, Any], item: dict[str, Any]) -> bool:
    blob = _status_decision_blob(decision, item)
    markers = (
        "future",
        "todo",
        "planned",
        "planning",
        "roadmap",
        "prd",
        "rfc",
        "后续",
        "待办",
        "计划",
        "规划",
        "下一步",
        "future_plan",
        "planning_overlay",
    )
    return any(marker in blob for marker in markers)


def _status_decision_has_completion_evidence(decision: dict[str, Any], item: dict[str, Any]) -> bool:
    blob = _status_decision_blob(decision, item)
    markers = (
        "completed",
        "complete",
        "done",
        "finished",
        "validated",
        "passed",
        "landed",
        "implemented",
        "fixed",
        "完成",
        "已完成",
        "通过",
        "落地",
        "修复",
        "验证",
    )
    return any(marker in blob for marker in markers)


def _status_decision_has_blocker_evidence(decision: dict[str, Any], item: dict[str, Any]) -> bool:
    blob = _status_decision_blob(decision, item)
    markers = ("blocked", "blocker", "waiting", "failed", "阻塞", "卡住", "等待", "失败")
    return any(marker in blob for marker in markers)


def _status_decision_has_pause_evidence(decision: dict[str, Any], item: dict[str, Any]) -> bool:
    blob = _status_decision_blob(decision, item)
    markers = ("paused", "deferred", "suspended", "暂停", "搁置", "延后")
    return any(marker in blob for marker in markers)


def _auto_promote_planned_l1_roots_for_observed_children(
    paths: RuntimePaths,
    *,
    node_ids: list[str],
    business_date: date,
) -> int:
    """Compatibility wrapper for earlier L1-only promotion tests/tools."""
    return _auto_promote_planned_ancestors_for_observed_children(
        paths,
        node_ids=node_ids,
        business_date=business_date,
    )


def _auto_promote_planned_ancestors_for_observed_children(
    paths: RuntimePaths,
    *,
    node_ids: list[str],
    business_date: date,
) -> int:
    promoted = 0
    with connect(paths) as connection:
        child_rows = [
            row
            for node_id in node_ids
            for row in [
                connection.execute(
                    """
                    SELECT node_id, title, status
                    FROM nova_task_nodes
                    WHERE node_id = ? AND status IN (?, ?, ?, ?, ?)
                    """,
                    (
                        node_id,
                        TASK_NODE_STATUS_ACTIVE,
                        TASK_NODE_STATUS_COMPLETED,
                        TASK_NODE_STATUS_BLOCKED,
                        TASK_NODE_STATUS_AUTOMATIC,
                        TASK_NODE_STATUS_SETTLED,
                    ),
                ).fetchone()
            ]
            if row is not None
        ]
        promoted_ancestors: set[str] = set()
        for child in child_rows:
            for ancestor_id in _ancestor_ids_for_node(connection, child["node_id"]):
                if ancestor_id in promoted_ancestors:
                    continue
                ancestor = connection.execute(
                    "SELECT * FROM nova_task_nodes WHERE node_id = ?",
                    (ancestor_id,),
                ).fetchone()
                if ancestor is None or ancestor["status"] != TASK_NODE_STATUS_PLANNED:
                    continue
                metadata = _json_loads(ancestor["metadata_json"], {})
                if not allows_planned_state_machine(metadata):
                    continue
                _promote_planned_ancestor_to_active(
                    connection,
                    ancestor=ancestor,
                    observed_child=child,
                    business_date=business_date,
                )
                promoted_ancestors.add(ancestor_id)
                promoted += 1
    return promoted


def _promote_planned_ancestor_to_active(
    connection: Any,
    *,
    ancestor: Any,
    observed_child: Any,
    business_date: date,
) -> None:
    now = datetime.now().astimezone().isoformat()
    before = dict(ancestor)
    metadata = _json_loads(ancestor["metadata_json"], {})
    if not isinstance(metadata, dict):
        metadata = {}
    metadata.update(
        {
            "statusReason": "Observed descendant work was materialized under this planned node.",
            "statusTags": [],
            "statusSourceNodeId": observed_child["node_id"],
            "statusSignal": {
                "source": SOURCE_TYPE_WORK_GRAPH,
                "reason": "planned_ancestor_has_observed_descendant",
                "childNodeId": observed_child["node_id"],
                "childStatus": observed_child["status"],
            },
            "statusBusinessDate": business_date.isoformat(),
            "statusUpdatedBy": ACTOR_WORK_GRAPH,
        }
    )
    after = {
        "nodeId": ancestor["node_id"],
        "status": TASK_NODE_STATUS_ACTIVE,
        "progress": int(ancestor["progress"] or 0),
        "completedAt": ancestor["completed_at"],
        "statusReason": metadata["statusReason"],
        "statusSourceNodeId": observed_child["node_id"],
    }
    connection.execute(
        """
        UPDATE nova_task_nodes
        SET status = ?, updated_at = ?, metadata_json = ?
        WHERE node_id = ?
        """,
        (TASK_NODE_STATUS_ACTIVE, now, _json_dumps(metadata), ancestor["node_id"]),
    )
    connection.execute(
        """
        INSERT INTO nova_task_audit_log(
            audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
        ) VALUES (?, ?, ?, 'auto_promote_planned_ancestor_to_active', ?, ?, ?, ?)
        """,
        (
            _new_id("NTA"),
            now,
            ACTOR_WORK_GRAPH,
            ancestor["node_id"],
            _json_dumps(before),
            _json_dumps(after),
            _json_dumps(
                {
                    "businessDate": business_date.isoformat(),
                    "sourceNodeId": observed_child["node_id"],
                }
            ),
        ),
    )
    if ancestor["parent_node_id"] is None:
        connection.execute(
            """
            INSERT INTO nova_task_audit_log(
                audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
            ) VALUES (?, ?, ?, 'auto_promote_planned_l1_to_active', ?, ?, ?, ?)
            """,
            (
                _new_id("NTA"),
                now,
                ACTOR_WORK_GRAPH,
                ancestor["node_id"],
                _json_dumps(before),
                _json_dumps(after),
                _json_dumps(
                    {
                        "businessDate": business_date.isoformat(),
                        "sourceNodeId": observed_child["node_id"],
                        "compatAction": "auto_promote_planned_ancestor_to_active",
                    }
                ),
            ),
        )


def _ancestor_ids_for_node(connection: Any, node_id: str | None) -> list[str]:
    current = str(node_id or "")
    ancestors: list[str] = []
    seen: set[str] = set()
    while current:
        if current in seen:
            return []
        seen.add(current)
        row = connection.execute(
            "SELECT parent_node_id FROM nova_task_nodes WHERE node_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return []
        parent = row["parent_node_id"]
        if not parent:
            return ancestors
        ancestors.append(parent)
        current = parent
    return ancestors


def _l1_root_id_for_node(connection: Any, node_id: str | None) -> str | None:
    current = str(node_id or "")
    if not current:
        return None
    seen: set[str] = set()
    root_id: str | None = None
    while current:
        if current in seen:
            return None
        seen.add(current)
        row = connection.execute(
            "SELECT node_id, parent_node_id FROM nova_task_nodes WHERE node_id = ?",
            (current,),
        ).fetchone()
        if row is None:
            return None
        root_id = row["node_id"]
        current = row["parent_node_id"]
    return root_id


def _json_loads(raw: str | None, fallback: Any) -> Any:
    try:
        value = json.loads(raw or "")
    except Exception:
        return fallback
    return value if value is not None else fallback


def _json_dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _apply_direct_reparent_hint(
    connection: Any,
    *,
    child_id: str,
    parent_id: str,
    event_id: str,
    raw: dict[str, Any],
) -> bool:
    if not child_id.startswith("NT-") or not parent_id.startswith("NT-") or child_id == parent_id:
        return False
    child = connection.execute(
        "SELECT * FROM nova_task_nodes WHERE node_id = ? AND status != 'archived'",
        (child_id,),
    ).fetchone()
    parent = connection.execute(
        "SELECT * FROM nova_task_nodes WHERE node_id = ? AND status != 'archived'",
        (parent_id,),
    ).fetchone()
    if child is None or parent is None:
        return False
    child_metadata = _json_loads(child["metadata_json"], {})
    parent_metadata = _json_loads(parent["metadata_json"], {})
    if _managed_by(child_metadata) != NODE_MANAGED_BY_AGENT:
        return False
    if _managed_by(parent_metadata) == NODE_MANAGED_BY_AGENT and _managed_by(child_metadata) != NODE_MANAGED_BY_AGENT:
        return False
    child_depth = _node_depth(connection, child_id)
    parent_depth = _node_depth(connection, parent_id)
    if child_depth is None or child_depth <= 1 or parent_depth is None or parent_depth >= 5:
        return False
    expected_child_level = parent_depth + 1
    if level_for_node_type(str(child["node_type"] or ""), fallback=expected_child_level) != expected_child_level:
        return False
    cursor = parent_id
    seen: set[str] = set()
    while cursor:
        if cursor == child_id:
            return False
        if cursor in seen:
            return False
        seen.add(cursor)
        row = connection.execute("SELECT parent_node_id FROM nova_task_nodes WHERE node_id = ?", (cursor,)).fetchone()
        cursor = row["parent_node_id"] if row is not None else None
    if str(child["parent_node_id"] or "") == parent_id:
        return False
    before = dict(child)
    now = datetime.now(timezone.utc).astimezone().isoformat()
    connection.execute(
        "UPDATE nova_task_nodes SET parent_node_id = ?, updated_at = ? WHERE node_id = ?",
        (parent_id, now, child_id),
    )
    after = {**before, "parent_node_id": parent_id}
    connection.execute(
        """
        INSERT INTO nova_task_audit_log(
            audit_id, occurred_at, actor, action, node_id, before_json, after_json, metadata_json
        ) VALUES (?, ?, ?, 'direct_reparent_node', ?, ?, ?, ?)
        """,
        (
            f"NTA-{uuid.uuid4().hex[:12]}",
            now,
            ACTOR_WORK_GRAPH,
            child_id,
            json.dumps(before, ensure_ascii=False, sort_keys=True),
            json.dumps(after, ensure_ascii=False, sort_keys=True),
            json.dumps({"sourceEventId": event_id, "raw": raw}, ensure_ascii=False, sort_keys=True),
        ),
    )
    return True


def _candidate_subtask_level_is_valid(
    connection: Any,
    *,
    item: dict[str, Any],
    parent_id: str,
    suggested_type: str,
    parent_depth_override: int | None = None,
) -> bool:
    level_decision = item.get("level_decision") if isinstance(item.get("level_decision"), dict) else {}
    if not level_decision:
        return False
    if level_decision.get("create_new_node") is not True:
        return False
    if not _string_list(item.get("evidence")):
        return False
    parent_depth = parent_depth_override if parent_depth_override is not None else _node_depth(connection, parent_id)
    if parent_depth is None or parent_depth < 1 or parent_depth >= 5:
        return False
    expected_level = parent_depth + 1
    try:
        proposed_level = int(item.get("proposed_level") or item.get("level") or 0)
    except (TypeError, ValueError):
        proposed_level = 0
    try:
        chosen_level = int(level_decision.get("chosen_level") or 0)
    except (TypeError, ValueError):
        chosen_level = 0
    if proposed_level and proposed_level != expected_level:
        return False
    if chosen_level and chosen_level != expected_level:
        return False
    if level_for_node_type(suggested_type, fallback=expected_level) != expected_level:
        return False
    parent_level = level_decision.get("parent_level")
    if parent_level not in (None, ""):
        try:
            if int(parent_level) != parent_depth:
                return False
        except (TypeError, ValueError):
            return False
    matched = str(level_decision.get("matched_existing_node_id") or "").strip()
    if matched.startswith("NT-"):
        return False
    return True


def apply_candidate_actions_from_reconciliation(paths: RuntimePaths, markdown: str) -> dict[str, int]:
    """Apply deterministic pending-candidate actions proposed by reconciliation YAML."""
    payload = _extract_nova_task_payload(markdown)
    counts = {"attached": 0, "rejected": 0, "deferred": 0, "merged": 0, "superseded": 0}
    if not isinstance(payload, dict):
        return counts
    actions = payload.get("candidate_actions")
    if not isinstance(actions, list):
        return counts
    for raw in actions:
        if not isinstance(raw, dict):
            continue
        candidate_id = str(raw.get("candidate_id") or "").strip()
        action = str(raw.get("action") or "").strip().lower()
        reason = str(raw.get("reason") or "Nova-Task reconciliation action")
        evidence = raw.get("evidence") if isinstance(raw.get("evidence"), list) else []
        metadata = {
            "raw": raw,
            "confidence": raw.get("confidence"),
            "evidence": evidence,
            "source": SOURCE_TYPE_WORK_GRAPH,
        }
        if not candidate_id.startswith("NTC-"):
            continue
        try:
            if action == "attach_existing":
                target_node_id = str(raw.get("target_node_id") or "").strip()
                if not target_node_id.startswith("NT-"):
                    continue
                attach_task_candidate_to_node(
                    paths,
                    candidate_id=candidate_id,
                    target_node_id=target_node_id,
                    actor=ACTOR_WORK_GRAPH,
                    reason=reason,
                    metadata=metadata,
                )
                counts["attached"] += 1
            elif action == "reject":
                reject_task_candidate(
                    paths,
                    candidate_id=candidate_id,
                    actor=ACTOR_WORK_GRAPH,
                    reason=reason,
                )
                counts["rejected"] += 1
            elif action == "defer":
                defer_task_candidate(
                    paths,
                    candidate_id=candidate_id,
                    actor=ACTOR_WORK_GRAPH,
                    reason=reason,
                )
                counts["deferred"] += 1
            elif action == "merge":
                target_candidate_id = str(raw.get("target_candidate_id") or "").strip() or None
                target_node_id = str(raw.get("target_node_id") or "").strip() or None
                if target_candidate_id and not target_candidate_id.startswith("NTC-"):
                    target_candidate_id = None
                if target_node_id and not target_node_id.startswith("NT-"):
                    target_node_id = None
                merge_task_candidate(
                    paths,
                    candidate_id=candidate_id,
                    actor=ACTOR_WORK_GRAPH,
                    reason=reason,
                    target_candidate_id=target_candidate_id,
                    target_node_id=target_node_id,
                    metadata=metadata,
                )
                counts["merged"] += 1
            elif action == "supersede":
                target_candidate_id = str(raw.get("target_candidate_id") or "").strip() or None
                target_node_id = str(raw.get("target_node_id") or "").strip() or None
                if target_candidate_id and not target_candidate_id.startswith("NTC-"):
                    target_candidate_id = None
                if target_node_id and not target_node_id.startswith("NT-"):
                    target_node_id = None
                supersede_task_candidate(
                    paths,
                    candidate_id=candidate_id,
                    actor=ACTOR_WORK_GRAPH,
                    reason=reason,
                    target_candidate_id=target_candidate_id,
                    target_node_id=target_node_id,
                    metadata=metadata,
                )
                counts["superseded"] += 1
        except ValueError:
            continue
    return counts


def _candidate_ids(paths: RuntimePaths) -> set[str]:
    with connect(paths, read_only=True) as connection:
        return {
            str(row["candidate_id"])
            for row in connection.execute("SELECT candidate_id FROM nova_task_candidates")
        }


def _normalized_graph_title(value: str | None) -> str:
    canonical = canonical_workspace_name(str(value or "")).lower()
    for suffix in ("系统", "项目开发", "project development", "app"):
        if canonical.endswith(suffix):
            canonical = canonical[: -len(suffix)].strip()
    return "".join(ch for ch in canonical if ch.isalnum())


def _existing_equivalent_child_node(
    paths: RuntimePaths,
    *,
    proposed_title: str,
    proposed_parent_node_id: str | None,
) -> str | None:
    if not proposed_title:
        return None
    proposed = _normalized_graph_title(proposed_title)
    if not proposed:
        return None
    with connect(paths, read_only=True) as connection:
        parent_title = None
        if proposed_parent_node_id:
            parent = connection.execute(
                "SELECT node_id, title FROM nova_task_nodes WHERE node_id = ?",
                (proposed_parent_node_id,),
            ).fetchone()
            if parent is not None:
                parent_title = str(parent["title"] or "")
                if _normalized_graph_title(parent_title) == proposed:
                    return str(parent["node_id"])
        rows = connection.execute(
            """
            SELECT node_id, title
            FROM nova_task_nodes
            WHERE COALESCE(parent_node_id, '') = COALESCE(?, '')
              AND status != 'archived'
            """,
            (proposed_parent_node_id,),
        ).fetchall()
        for row in rows:
            if _normalized_graph_title(str(row["title"] or "")) == proposed:
                return str(row["node_id"])
    return None


def _existing_equivalent_child_node_in_connection(
    connection: Any,
    *,
    proposed_title: str,
    proposed_parent_node_id: str | None,
) -> str | None:
    if not proposed_title:
        return None
    proposed = _normalized_graph_title(proposed_title)
    if not proposed:
        return None
    if proposed_parent_node_id:
        parent = connection.execute(
            "SELECT node_id, title FROM nova_task_nodes WHERE node_id = ?",
            (proposed_parent_node_id,),
        ).fetchone()
        if parent is not None and _normalized_graph_title(str(parent["title"] or "")) == proposed:
            return str(parent["node_id"])
    rows = connection.execute(
        """
        SELECT node_id, title
        FROM nova_task_nodes
        WHERE COALESCE(parent_node_id, '') = COALESCE(?, '')
          AND status != 'archived'
        """,
        (proposed_parent_node_id,),
    ).fetchall()
    for row in rows:
        if _normalized_graph_title(str(row["title"] or "")) == proposed:
            return str(row["node_id"])
    return None


def auto_confirm_reconciliation_candidates(paths: RuntimePaths, *, candidate_ids: list[str]) -> int:
    """Confirm newly generated non-Level-1 task candidates into the graph.

    Level 1 project anchors remain manual. Hierarchy adjustment hints remain
    review-only until a dedicated graph mutation API exists.
    """
    confirmed = 0
    with connect(paths, read_only=True) as connection:
        rows = [
            dict(row)
            for row in connection.execute(
                """
                SELECT candidate_id, candidate_type, proposed_parent_node_id, metadata_json
                FROM nova_task_candidates
                WHERE status IN ('pending_review', 'pending')
                  AND candidate_id IN ({})
                """.format(",".join("?" for _ in candidate_ids) or "''"),
                candidate_ids,
            )
        ]
    for row in rows:
        if row["candidate_type"] != "subtask":
            continue
        if not row["proposed_parent_node_id"]:
            continue
        with connect(paths, read_only=True) as connection:
            title_row = connection.execute(
                "SELECT proposed_title FROM nova_task_candidates WHERE candidate_id = ?",
                (row["candidate_id"],),
            ).fetchone()
        proposed_title = str(title_row["proposed_title"] if title_row is not None else "")
        equivalent_node_id = _existing_equivalent_child_node(
            paths,
            proposed_title=proposed_title,
            proposed_parent_node_id=row["proposed_parent_node_id"],
        )
        if equivalent_node_id:
            attach_task_candidate_to_node(
                paths,
                candidate_id=row["candidate_id"],
                target_node_id=equivalent_node_id,
                actor=ACTOR_WORK_GRAPH,
                reason="Auto-attached reconciliation candidate to an existing equivalent graph node.",
                metadata={
                    "source": SOURCE_TYPE_WORK_GRAPH,
                    "dedupe": "existing_equivalent_node",
                    "proposedTitle": proposed_title,
                    "proposedParentNodeId": row["proposed_parent_node_id"],
                },
            )
            confirmed += 1
            continue
        try:
            metadata = json.loads(row["metadata_json"] or "{}")
        except Exception:
            metadata = {}
        suggested_type = str(metadata.get("suggestedNodeType") or "")
        if suggested_type == "track":
            continue
        confirm_candidate_as_task(
            paths,
            candidate_id=row["candidate_id"],
            actor=ACTOR_WORK_GRAPH,
            reason="Auto-confirmed non-Level-1 reconciliation candidate",
        )
        confirmed += 1
    return confirmed


def _write_reconciliation_artifact(paths: RuntimePaths, business_date: date, response: str, *, apply: bool) -> Path:
    stamp = business_now(paths).strftime("%Y%m%d-%H%M%S")
    output = paths.state_dir / "nova-task" / "work-graph" / f"{business_date.isoformat()}-{stamp}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Nova-Task Work Graph Reconciliation\n\n"
        f"- businessDate: {business_date.isoformat()}\n"
        f"- applied: {str(apply).lower()}\n\n"
        + str(response or "").strip()
        + "\n",
        encoding="utf-8",
    )
    return output


def _write_reconciliation_summary_artifact(
    paths: RuntimePaths,
    artifact: Path,
    *,
    business_date: date,
    applied: bool,
    response: str,
    ingest: NovaTaskEvidenceIngest,
    project_graph_write_count: int,
    action_counts: dict[str, int],
    pending_before: int,
    pending_after: int,
    candidates_sent: int,
) -> Path:
    payload = _extract_nova_task_payload(response) or {}
    candidate_subtasks = _list_payload(payload, "candidate_subtasks") if isinstance(payload, dict) else []
    status_by_reason: dict[str, int] = {}
    accepted = 0
    rejected = 0
    for item in candidate_subtasks:
        level_decision = item.get("level_decision") if isinstance(item.get("level_decision"), dict) else {}
        reason = ""
        if level_decision and level_decision.get("create_new_node") is True:
            accepted += 1
        else:
            rejected += 1
            reason = "missing_or_non_create_level_decision"
        if reason:
            status_by_reason[reason] = status_by_reason.get(reason, 0) + 1
    summary = {
        "businessDate": business_date.isoformat(),
        "applied": applied,
        "artifactPath": str(artifact),
        "responseMalformed": bool(ingest.malformed),
        "candidatesSent": candidates_sent,
        "pendingBefore": pending_before,
        "pendingAfter": pending_after,
        "evidenceEventsInserted": ingest.event_count,
        "projectGraphWrites": project_graph_write_count,
        "planningOverlayProposals": ingest.candidate_count,
        "legacyActions": sum(action_counts.values()),
        "actionCounts": action_counts,
        "payloadCounts": {
            "routingHints": len(_list_payload(payload, "routing_hints")) if isinstance(payload, dict) else 0,
            "matchedTasks": len(_list_payload(payload, "matched_tasks")) if isinstance(payload, dict) else 0,
            "candidateParentTasks": len(_list_payload(payload, "candidate_parent_tasks")) if isinstance(payload, dict) else 0,
            "candidateSubtasks": len(candidate_subtasks),
            "statusSignals": len(_list_payload(payload, "status_signals")) if isinstance(payload, dict) else 0,
            "reparentHints": len(_list_payload(payload, "reparent_hints")) if isinstance(payload, dict) else 0,
            "unresolved": len(_list_payload(payload, "unresolved")) if isinstance(payload, dict) else 0,
        },
        "candidateSubtaskIntent": {
            "createNewNodeTrue": accepted,
            "notCreateNewNode": rejected,
            "notCreateReasons": status_by_reason,
        },
        "guardValidation": _guard_validation_summary(paths, artifact),
    }
    output = artifact.with_suffix(".summary.json")
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output


def _guard_validation_summary(paths: RuntimePaths, artifact: Path) -> dict[str, Any]:
    try:
        with connect(paths, read_only=True) as connection:
            rows = connection.execute(
                """
                SELECT
                    COALESCE(json_extract(metadata_json, '$.levelValidation'), '') AS validation,
                    COALESCE(json_extract(metadata_json, '$.levelValidationReason'), '') AS reason,
                    COUNT(*) AS count
                FROM nova_task_events
                WHERE source_path = ?
                  AND event_type = 'candidate_subtask'
                GROUP BY validation, reason
                ORDER BY validation, reason
                """,
                (str(artifact),),
            ).fetchall()
    except Exception:
        return {"available": False, "groups": []}
    groups = [
        {
            "validation": row["validation"] or "unknown",
            "reason": row["reason"] or "",
            "count": int(row["count"] or 0),
        }
        for row in rows
    ]
    return {
        "available": True,
        "accepted": sum(item["count"] for item in groups if item["validation"] == "accepted"),
        "rejected": sum(item["count"] for item in groups if item["validation"] == "rejected"),
        "groups": groups,
    }
