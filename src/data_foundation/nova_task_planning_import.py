"""Import user-supplied planning documents into Nova-Task.

Planning documents are a trusted intent source: RFCs, PRDs, and roadmaps may
define a planned L1 project anchor before any workspace path exists. The LLM
extracts structure; deterministic validation owns graph writes.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

from .db import connect
from .llm_execution import execute_llm_message
from .nova_task import (
    _extract_nova_task_payload,
    _node_depth,
    _suggested_node_type_from_item,
    create_task_node,
    export_task_board_markdown,
    normalized_l1_anchor_title,
    render_task_graph_context,
)
from .nova_task_layers import (
    ORIGIN_PLANNED,
    STATE_AUTHORITY_PLANNED_STATE_MACHINE,
    level_for_node_type,
    node_type_for_level,
    project_graph_metadata,
)
from .paths import RuntimePaths, load_paths
from .settings import resolve_llm_provider
from .time import business_now
from .workspace_attribution import build_workspace_attribution_catalog

LlmSender = Callable[..., str]

SOURCE_TYPE_PLANNING_IMPORT = "nova_task_planning_import"
ACTOR_PLANNING_IMPORT = "nova-task-planning-import"


SYSTEM_PROMPT = """You are a Nova-Task planning import analyst.
The user supplied an engineering planning document such as an RFC, PRD, or roadmap.
This document is a planned-intent source, not observed daily evidence.

Rules:
1. Extract one planned L1 project/product root from the document when the document names a coherent project, product, or initiative.
2. If an active graph L1 already matches the document, use that existing NT-* id as matched_existing_node_id.
3. A workspace catalog item is useful for matching an existing/path-backed project, but it is not required. Planning documents may create pathless planned L1 anchors.
4. Generate L2-L5 children only when the document grounds them. Do not invent work outside the document.
5. L2 parent is L1, L3 parent is L2, L4 parent is L3, L5 parent is L4.
6. Use suggested_node_type: track for L1, workstream for L2, deliverable for L3, subtask for L4, action for L5.
7. Keep the tree concise: max 1 root, max 8 L2 items, max 40 total descendants.
8. Output exactly one YAML code block rooted at nova_task. No JSON.
"""


PROMPT_TEMPLATE = """Import this planning document into Nova-Task planned project graph intent.

Inputs:
- Existing active graph:
{active_graph}

- Workspace attribution catalog:
{workspace_catalog}

- Document title:
{document_title}

- Document body:
{document_body}

Output exactly:

```yaml
nova_task:
  planning_import:
    document_title: "{document_title}"
    project:
      proposed_title: "..."
      suggested_node_type: track
      proposed_level: 1
      matched_existing_node_id: ""
      workspace_root_path: ""
      reason: "..."
      children:
        - proposed_title: "..."
          suggested_node_type: workstream
          proposed_level: 2
          reason: "..."
          children:
            - proposed_title: "..."
              suggested_node_type: deliverable
              proposed_level: 3
              reason: "..."
              children: []
```
"""


@dataclass(frozen=True)
class NovaTaskPlanningImportResult:
    document_title: str
    applied: bool
    artifact_path: str
    root_node_id: str | None
    root_created: bool
    node_created_count: int
    node_reused_count: int
    skipped_count: int
    response_preview: str
    preview_tree: dict[str, Any]
    validation_report: dict[str, Any]


def import_planning_document(
    paths: RuntimePaths | None = None,
    *,
    document_title: str,
    document_text: str,
    apply: bool = False,
    sender: LlmSender | None = None,
) -> NovaTaskPlanningImportResult:
    selected = paths or load_paths()
    title = str(document_title or "").strip() or "Untitled planning document"
    body = _bounded_text(document_text, max_chars=50000)
    if not body:
        raise ValueError("document_text is required")
    prompt = build_planning_import_prompt(selected, document_title=title, document_text=body)
    if sender is None:
        response = execute_llm_message(
            paths=selected,
            system=SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.05,
            max_tokens=12000,
            thinking_mode="off",
            pass_id="nova-task-planning-import",
            label="Nova-Task planning import",
        ).text
    else:
        provider = resolve_llm_provider(selected, redact_secrets=False)
        response = sender(
            endpoint=provider["endpoint"],
            api_key=provider["apiKey"],
            model=provider["model"],
            system=SYSTEM_PROMPT,
            prompt=prompt,
            temperature=0.05,
            max_tokens=12000,
            timeout=int(provider.get("timeoutSeconds") or 180),
            thinking_mode="off",
        )
    if not str(response or "").strip():
        raise ValueError("Nova-Task planning import returned empty content")
    artifact = _write_planning_import_artifact(selected, title, response, apply=apply)
    root_node_id = None
    root_created = False
    created = 0
    reused = 0
    skipped = 0
    validation_report = planning_import_validation_report(selected, response)
    if apply:
        root_node_id, root_created, created, reused, skipped = apply_planning_import_tree(
            selected,
            markdown=response,
            source_path=artifact,
            document_title=title,
        )
        export_task_board_markdown(selected)
    else:
        created = int(validation_report.get("summary", {}).get("create", 0))
        reused = int(validation_report.get("summary", {}).get("reuse", 0))
        skipped = int(validation_report.get("summary", {}).get("skip", 0))
    return NovaTaskPlanningImportResult(
        document_title=title,
        applied=apply,
        artifact_path=str(artifact),
        root_node_id=root_node_id,
        root_created=root_created,
        node_created_count=created,
        node_reused_count=reused,
        skipped_count=skipped,
        response_preview=str(response or "").strip()[:500],
        preview_tree=validation_report.get("tree") if isinstance(validation_report.get("tree"), dict) else {},
        validation_report=validation_report,
    )


def apply_planning_import_artifact(
    paths: RuntimePaths | None = None,
    *,
    artifact_path: str | Path,
) -> NovaTaskPlanningImportResult:
    selected = paths or load_paths()
    artifact = _safe_planning_import_artifact_path(selected, artifact_path)
    markdown = artifact.read_text(encoding="utf-8")
    if _artifact_applied(markdown):
        raise ValueError("planning import artifact has already been applied")
    title = _artifact_document_title(markdown) or artifact.stem
    validation_report = planning_import_validation_report(selected, markdown)
    root_node_id, root_created, created, reused, skipped = apply_planning_import_tree(
        selected,
        markdown=markdown,
        source_path=artifact,
        document_title=title,
    )
    _mark_planning_import_artifact_applied(artifact)
    export_task_board_markdown(selected)
    return NovaTaskPlanningImportResult(
        document_title=title,
        applied=True,
        artifact_path=str(artifact),
        root_node_id=root_node_id,
        root_created=root_created,
        node_created_count=created,
        node_reused_count=reused,
        skipped_count=skipped,
        response_preview=markdown.strip()[:500],
        preview_tree=validation_report.get("tree") if isinstance(validation_report.get("tree"), dict) else {},
        validation_report=validation_report,
    )


def planning_import_preview_tree(markdown: str) -> dict[str, Any]:
    return planning_import_validation_report(None, markdown).get("tree", {})


def planning_import_validation_report(paths: RuntimePaths | None, markdown: str) -> dict[str, Any]:
    payload = _extract_nova_task_payload(markdown)
    planning = payload.get("planning_import") if isinstance(payload, dict) else None
    project = planning.get("project") if isinstance(planning, dict) else None
    if not isinstance(project, dict):
        return {"summary": {"create": 0, "reuse": 0, "skip": 1}, "tree": {}, "issues": ["missing planning_import.project"]}
    selected = paths or load_paths()
    tree = _validation_node(
        selected,
        project,
        expected_level=1,
        parent_id=None,
        parent_action="root",
    )
    summary = {"create": 0, "reuse": 0, "skip": 0}
    _summarize_validation_tree(tree, summary)
    return {"summary": summary, "tree": tree, "issues": []}


def build_planning_import_prompt(paths: RuntimePaths, *, document_title: str, document_text: str) -> str:
    catalog = build_workspace_attribution_catalog(paths)
    compact_catalog = {
        "projects": [
            {
                "displayName": item.get("display_name"),
                "rootPath": item.get("root_path"),
                "confidence": item.get("confidence"),
                "evidence": item.get("evidence"),
                "sources": item.get("sources"),
                "observationCount": item.get("observation_count"),
            }
            for item in (catalog.get("projects") or [])[:40]
            if isinstance(item, dict)
        ]
    }
    return PROMPT_TEMPLATE.format(
        active_graph=render_task_graph_context(paths, max_nodes=160),
        workspace_catalog=json.dumps(compact_catalog, ensure_ascii=False, indent=2),
        document_title=str(document_title or "").strip() or "Untitled planning document",
        document_body=_bounded_text(document_text, max_chars=50000),
    )


def apply_planning_import_tree(
    paths: RuntimePaths,
    *,
    markdown: str,
    source_path: Path | None,
    document_title: str,
) -> tuple[str | None, bool, int, int, int]:
    payload = _extract_nova_task_payload(markdown)
    planning = payload.get("planning_import") if isinstance(payload, dict) else None
    project = planning.get("project") if isinstance(planning, dict) else None
    if not isinstance(project, dict):
        return None, False, 0, 0, 1
    root_title = str(project.get("proposed_title") or "").strip()
    if not root_title:
        return None, False, 0, 0, 1
    root_path = str(project.get("workspace_root_path") or "").strip()
    matched = str(project.get("matched_existing_node_id") or "").strip()
    root_id = _existing_root_id(paths, node_id=matched, title=root_title, root_path=root_path)
    root_created = False
    created = 0
    reused = 0
    skipped = 0
    if root_id:
        reused += 1
    else:
        node = create_task_node(
            paths,
            title=root_title,
            node_type="track",
            status="planned",
            actor=ACTOR_PLANNING_IMPORT,
            metadata=project_graph_metadata(
                origin=ORIGIN_PLANNED,
                state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                createdFrom=SOURCE_TYPE_PLANNING_IMPORT,
                sourceDocumentTitle=document_title,
                sourcePath=str(source_path) if source_path else None,
                workspace={"rootPath": root_path} if root_path else {},
                rawPlanningRoot=_redacted_raw(project),
            ),
        )
        root_id = node.node_id
        root_created = True
        created += 1
    child_created, child_reused, child_skipped = _apply_children(
        paths,
        parent_id=root_id,
        parent_level=1,
        children=project.get("children"),
        source_path=source_path,
        document_title=document_title,
    )
    return root_id, root_created, created + child_created, reused + child_reused, skipped + child_skipped


def _apply_children(
    paths: RuntimePaths,
    *,
    parent_id: str,
    parent_level: int,
    children: Any,
    source_path: Path | None,
    document_title: str,
) -> tuple[int, int, int]:
    if not isinstance(children, list):
        return 0, 0, 0
    created = 0
    reused = 0
    skipped = 0
    for raw in children:
        if not isinstance(raw, dict):
            skipped += 1
            continue
        title = str(raw.get("proposed_title") or "").strip()
        if not title:
            skipped += 1
            continue
        expected_level = parent_level + 1
        if expected_level > 5:
            skipped += 1
            continue
        try:
            proposed_level = int(raw.get("proposed_level") or expected_level)
        except (TypeError, ValueError):
            proposed_level = expected_level
        node_type = _suggested_node_type_from_item(raw, fallback=node_type_for_level(expected_level))
        if proposed_level != expected_level or level_for_node_type(node_type, fallback=expected_level) != expected_level:
            skipped += 1
            continue
        existing = _existing_child_id(paths, parent_id=parent_id, title=title)
        if existing:
            node_id = existing
            reused += 1
        else:
            node = create_task_node(
                paths,
                title=title,
                node_type=node_type,
                parent_node_id=parent_id,
                status="planned",
                actor=ACTOR_PLANNING_IMPORT,
                metadata=project_graph_metadata(
                    origin=ORIGIN_PLANNED,
                    state_authority=STATE_AUTHORITY_PLANNED_STATE_MACHINE,
                    createdFrom=SOURCE_TYPE_PLANNING_IMPORT,
                    sourceDocumentTitle=document_title,
                    sourcePath=str(source_path) if source_path else None,
                    rawPlanningNode=_redacted_raw(raw),
                ),
            )
            node_id = node.node_id
            created += 1
        child_created, child_reused, child_skipped = _apply_children(
            paths,
            parent_id=node_id,
            parent_level=expected_level,
            children=raw.get("children"),
            source_path=source_path,
            document_title=document_title,
        )
        created += child_created
        reused += child_reused
        skipped += child_skipped
    return created, reused, skipped


def _existing_root_id(paths: RuntimePaths, *, node_id: str, title: str, root_path: str) -> str | None:
    normalized = _normalized_title(title)
    with connect(paths, read_only=True) as connection:
        if node_id.startswith("NT-"):
            row = connection.execute(
                "SELECT node_id FROM nova_task_nodes WHERE node_id = ? AND parent_node_id IS NULL AND status != 'archived'",
                (node_id,),
            ).fetchone()
            if row is not None:
                return str(row["node_id"])
        rows = connection.execute(
            """
            SELECT node_id, title, metadata_json
            FROM nova_task_nodes
            WHERE parent_node_id IS NULL
              AND status != 'archived'
            """
        ).fetchall()
    for row in rows:
        metadata = _json_obj(row["metadata_json"])
        workspace = metadata.get("workspace") if isinstance(metadata, dict) else {}
        if root_path and isinstance(workspace, dict) and str(workspace.get("rootPath") or "") == root_path:
            return str(row["node_id"])
        if _normalized_title(str(row["title"] or "")) == normalized:
            return str(row["node_id"])
    return None


def _existing_child_id(paths: RuntimePaths, *, parent_id: str, title: str) -> str | None:
    normalized = _normalized_title(title)
    with connect(paths, read_only=True) as connection:
        parent_depth = _node_depth(connection, parent_id)
        if parent_depth is None:
            return None
        rows = connection.execute(
            """
            SELECT node_id, title
            FROM nova_task_nodes
            WHERE parent_node_id = ?
              AND status != 'archived'
            """,
            (parent_id,),
        ).fetchall()
    for row in rows:
        if _normalized_title(str(row["title"] or "")) == normalized:
            return str(row["node_id"])
    return None


def _normalized_title(value: str) -> str:
    return normalized_l1_anchor_title(value)


def _bounded_text(text: str, *, max_chars: int) -> str:
    value = str(text or "").strip()
    if len(value) <= max_chars:
        return value
    return value[:max_chars] + "\n\n[... truncated for planning import prompt ...]"


def _write_planning_import_artifact(paths: RuntimePaths, document_title: str, response: str, *, apply: bool) -> Path:
    stamp = business_now(paths).strftime("%Y%m%d-%H%M%S")
    digest = hashlib.sha256(str(document_title or "").encode("utf-8")).hexdigest()[:10]
    output = paths.state_dir / "nova-task" / "planning-import" / f"{stamp}-{digest}.md"
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        "# Nova-Task Planning Import\n\n"
        f"- documentTitle: {document_title}\n"
        f"- applied: {str(apply).lower()}\n\n"
        + str(response or "").strip()
        + "\n",
        encoding="utf-8",
    )
    return output


def _safe_planning_import_artifact_path(paths: RuntimePaths, artifact_path: str | Path) -> Path:
    root = (paths.state_dir / "nova-task" / "planning-import").resolve()
    candidate = Path(str(artifact_path or "")).expanduser().resolve()
    if root != candidate and root not in candidate.parents:
        raise ValueError("planning import artifact must be inside the planning-import state directory")
    if not candidate.exists() or not candidate.is_file():
        raise ValueError("planning import artifact not found")
    return candidate


def _artifact_document_title(markdown: str) -> str:
    for line in str(markdown or "").splitlines()[:20]:
        if line.startswith("- documentTitle:"):
            return line.split(":", 1)[1].strip()
    return ""


def _mark_planning_import_artifact_applied(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    if "- applied: false" in text:
        path.write_text(text.replace("- applied: false", "- applied: true", 1), encoding="utf-8")


def _artifact_applied(markdown: str) -> bool:
    return "- applied: true" in str(markdown or "").split("```", 1)[0]


def _preview_node(raw: dict[str, Any], *, expected_level: int) -> dict[str, Any]:
    title = str(raw.get("proposed_title") or "").strip()
    if not title:
        return {}
    children = []
    if expected_level < 5 and isinstance(raw.get("children"), list):
        for child in raw.get("children") or []:
            if isinstance(child, dict):
                preview = _preview_node(child, expected_level=expected_level + 1)
                if preview:
                    children.append(preview)
    return {
        "title": title,
        "level": expected_level,
        "nodeType": _suggested_node_type_from_item(raw, fallback=node_type_for_level(expected_level)),
        "matchedExistingNodeId": str(raw.get("matched_existing_node_id") or ""),
        "workspaceRootPath": str(raw.get("workspace_root_path") or ""),
        "reason": str(raw.get("reason") or ""),
        "children": children,
    }


def _validation_node(
    paths: RuntimePaths,
    raw: dict[str, Any],
    *,
    expected_level: int,
    parent_id: str | None,
    parent_action: str,
) -> dict[str, Any]:
    title = str(raw.get("proposed_title") or "").strip()
    if not title:
        return _skip_node(raw, expected_level=expected_level, reason="missing title")
    try:
        proposed_level = int(raw.get("proposed_level") or expected_level)
    except (TypeError, ValueError):
        proposed_level = expected_level
    node_type = _suggested_node_type_from_item(raw, fallback=node_type_for_level(expected_level))
    if proposed_level != expected_level:
        return _skip_node(raw, expected_level=expected_level, reason=f"proposed level {proposed_level} does not match expected L{expected_level}")
    if level_for_node_type(node_type, fallback=expected_level) != expected_level:
        return _skip_node(raw, expected_level=expected_level, reason=f"node type {node_type} does not match expected L{expected_level}")
    matched_id = ""
    action = "create"
    reason = "Will create planned graph node."
    if expected_level == 1:
        matched_id = _existing_root_id(
            paths,
            node_id=str(raw.get("matched_existing_node_id") or "").strip(),
            title=title,
            root_path=str(raw.get("workspace_root_path") or "").strip(),
        ) or ""
        if matched_id:
            action = "reuse"
            reason = "Existing L1 root matches by NT id, title, or workspace path."
    else:
        if parent_action == "skip" or not parent_id:
            return _skip_node(raw, expected_level=expected_level, reason="parent is not available for this level")
        matched_id = _existing_child_id(paths, parent_id=parent_id, title=title) or ""
        if matched_id:
            action = "reuse"
            reason = "Existing sibling node matches this title."
    children = []
    next_parent_id = matched_id if matched_id else ("__planned_new_parent__" if action == "create" else None)
    if expected_level < 5 and isinstance(raw.get("children"), list):
        for child in raw.get("children") or []:
            if not isinstance(child, dict):
                children.append(_skip_node({}, expected_level=expected_level + 1, reason="child is not an object"))
                continue
            children.append(
                _validation_node(
                    paths,
                    child,
                    expected_level=expected_level + 1,
                    parent_id=next_parent_id,
                    parent_action=action,
                )
            )
    return {
        "title": title,
        "level": expected_level,
        "nodeType": node_type,
        "action": action,
        "validationReason": reason,
        "matchedExistingNodeId": matched_id,
        "workspaceRootPath": str(raw.get("workspace_root_path") or ""),
        "reason": str(raw.get("reason") or ""),
        "children": children,
    }


def _skip_node(raw: dict[str, Any], *, expected_level: int, reason: str) -> dict[str, Any]:
    return {
        "title": str(raw.get("proposed_title") or ""),
        "level": expected_level,
        "nodeType": str(raw.get("suggested_node_type") or raw.get("node_type") or node_type_for_level(expected_level)),
        "action": "skip",
        "validationReason": reason,
        "matchedExistingNodeId": "",
        "workspaceRootPath": str(raw.get("workspace_root_path") or ""),
        "reason": str(raw.get("reason") or ""),
        "children": [],
    }


def _summarize_validation_tree(node: dict[str, Any], summary: dict[str, int]) -> None:
    action = str(node.get("action") or "skip")
    if action in summary:
        summary[action] += 1
    for child in node.get("children") if isinstance(node.get("children"), list) else []:
        if isinstance(child, dict):
            _summarize_validation_tree(child, summary)


def _json_obj(raw: str | None) -> dict[str, Any]:
    try:
        data = json.loads(raw or "{}")
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _redacted_raw(value: dict[str, Any]) -> dict[str, Any]:
    result = dict(value)
    if isinstance(result.get("children"), list):
        result["childrenCount"] = len(result["children"])
        result.pop("children", None)
    return result
