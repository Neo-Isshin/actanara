from fastapi import APIRouter
from fastapi.responses import JSONResponse
from app.services import foundation, nova_task_review
from app.services.dashboard_state import attach_dashboard_state, dashboard_failure
import logging

logger = logging.getLogger(__name__)
router = APIRouter()


def _nova_task_disabled_response(**extra):
    return attach_dashboard_state(
        {
            "enabled": False,
            "reason": "Nova-Task subsystem is disabled by settings.",
            **extra,
        },
        status="unavailable",
    )


def _nova_task_failure(**fallback):
    return dashboard_failure("nova-task-operation", fallback=fallback)


def _nova_task_enabled() -> bool:
    return foundation.nova_task_enabled()


@router.get("/tasks/candidates/status")
async def api_task_candidate_status():
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(
                pendingReviewCount=0,
                hasPendingReview=False,
                pendingCount=0,
                hasPending=False,
            )
        return nova_task_review.candidate_status()
    except Exception as e:
        logger.exception("GET /api/tasks/candidates/status failed")
        return _nova_task_failure()


@router.get("/tasks/l1-review/status")
async def api_task_l1_review_status():
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(
                l1ReviewCount=0,
                hasL1Review=False,
                pendingReviewCount=0,
                hasPendingReview=False,
                pendingCount=0,
                hasPending=False,
            )
        return nova_task_review.l1_review_status()
    except Exception as e:
        logger.exception("GET /api/tasks/l1-review/status failed")
        return _nova_task_failure()


@router.get("/tasks/candidates")
async def api_task_candidates(status: str = "pending_review", limit: int = 50):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(
                candidates=[],
                count=0,
                pendingReviewCount=0,
                hasPendingReview=False,
                pendingCount=0,
                hasPending=False,
            )
        return nova_task_review.candidates(status=status, limit=limit)
    except Exception as e:
        logger.exception("GET /api/tasks/candidates failed")
        return _nova_task_failure()


@router.get("/tasks/l1-review")
async def api_task_l1_review_items(status: str = "pending_review", limit: int = 50):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(
                items=[],
                candidates=[],
                count=0,
                l1ReviewCount=0,
                hasL1Review=False,
                pendingReviewCount=0,
                hasPendingReview=False,
                pendingCount=0,
                hasPending=False,
            )
        return nova_task_review.l1_review_items(status=status, limit=limit)
    except Exception as e:
        logger.exception("GET /api/tasks/l1-review failed")
        return _nova_task_failure()


@router.post("/tasks/planning-import")
async def api_task_planning_import(payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.planning_import(
            title=data.get("title") or "Planning document",
            content=data.get("content") or "",
            apply=bool(data.get("apply", True)),
        )
    except Exception as e:
        logger.exception("POST /api/tasks/planning-import failed")
        return _nova_task_failure()


@router.post("/tasks/planning-import/apply")
async def api_apply_task_planning_import(payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.apply_planning_import(
            artifact_path=data.get("artifactPath") or "",
        )
    except Exception as e:
        logger.exception("POST /api/tasks/planning-import/apply failed")
        return _nova_task_failure()


@router.get("/tasks/nodes")
async def api_task_nodes():
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(nodes=[], count=0)
        return nova_task_review.nodes()
    except Exception as e:
        logger.exception("GET /api/tasks/nodes failed")
        return _nova_task_failure()


@router.get("/tasks/tree")
async def api_task_tree():
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(roots=[], nodes=[], count=0)
        return nova_task_review.tree()
    except Exception as e:
        logger.exception("GET /api/tasks/tree failed")
        return _nova_task_failure()


@router.get("/tasks/direct-writes/recent")
async def api_recent_task_direct_writes(limit: int = 20):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(
                audits=[],
                events=[],
                deferredByGuard=[],
                routingHints=[],
                auditCount=0,
                eventCount=0,
                deferredByGuardCount=0,
                routingHintCount=0,
            )
        return nova_task_review.recent_direct_writes(limit=limit)
    except Exception as e:
        logger.exception("GET /api/tasks/direct-writes/recent failed")
        return _nova_task_failure()


@router.post("/tasks/nodes")
async def api_create_task_node(payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.create_node(
            title=data.get("title") or "",
            status=data.get("status") or "planned",
            parent_node_id=data.get("parentNodeId") or None,
            node_type=data.get("nodeType"),
        )
    except Exception as e:
        logger.exception("POST /api/tasks/nodes failed")
        return _nova_task_failure()


@router.patch("/tasks/nodes/{node_id}")
async def api_update_task_node(node_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        parent_marker = ...
        if "parentNodeId" in data:
            parent_marker = data.get("parentNodeId") or None
        return nova_task_review.update_node(
            node_id,
            title=data.get("title"),
            status=data.get("status"),
            parent_node_id=parent_marker,
            progress=data.get("progress"),
            completion_method=data.get("completionMethod"),
            managed_by=data.get("managedBy"),
        )
    except Exception as e:
        logger.exception("PATCH /api/tasks/nodes/%s failed", node_id)
        return _nova_task_failure()


@router.post("/tasks/candidates/{candidate_id}/confirm")
async def api_confirm_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.confirm_candidate(
            candidate_id,
            title=data.get("title"),
            reason=data.get("reason"),
            parent_node_id=data.get("parentNodeId"),
            node_type=data.get("nodeType"),
        )
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/confirm failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/l1-review/{candidate_id}/confirm")
async def api_confirm_task_l1_review_item(candidate_id: str, payload: dict | None = None):
    return await api_confirm_task_candidate(candidate_id, payload)


@router.post("/tasks/candidates/{candidate_id}/reject")
async def api_reject_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.reject_candidate(candidate_id, reason=data.get("reason"))
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/reject failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/l1-review/{candidate_id}/reject")
async def api_reject_task_l1_review_item(candidate_id: str, payload: dict | None = None):
    return await api_reject_task_candidate(candidate_id, payload)


@router.post("/tasks/candidates/{candidate_id}/defer")
async def api_defer_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.defer_candidate(candidate_id, reason=data.get("reason"))
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/defer failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/l1-review/{candidate_id}/defer")
async def api_defer_task_l1_review_item(candidate_id: str, payload: dict | None = None):
    return await api_defer_task_candidate(candidate_id, payload)


@router.post("/tasks/candidates/{candidate_id}/merge")
async def api_merge_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.merge_candidate(
            candidate_id,
            reason=data.get("reason"),
            target_candidate_id=data.get("targetCandidateId"),
            target_node_id=data.get("targetNodeId"),
        )
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/merge failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/candidates/{candidate_id}/supersede")
async def api_supersede_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.supersede_candidate(
            candidate_id,
            reason=data.get("reason"),
            target_candidate_id=data.get("targetCandidateId"),
            target_node_id=data.get("targetNodeId"),
        )
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/supersede failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/candidates/{candidate_id}/delete")
async def api_delete_task_candidate(candidate_id: str, payload: dict | None = None):
    try:
        if not _nova_task_enabled():
            return _nova_task_disabled_response(error="Nova-Task disabled")
        data = payload or {}
        return nova_task_review.delete_candidate(candidate_id, reason=data.get("reason"))
    except Exception as e:
        logger.exception("POST /api/tasks/candidates/%s/delete failed", candidate_id)
        return _nova_task_failure()


@router.post("/tasks/l1-review/{candidate_id}/delete")
async def api_delete_task_l1_review_item(candidate_id: str, payload: dict | None = None):
    return await api_delete_task_candidate(candidate_id, payload)


@router.get("/tasks")
async def api_tasks():
    try:
        return nova_task_review.task_board_payload(enabled=_nova_task_enabled())
    except Exception as e:
        logger.exception("GET /api/tasks failed")
        return dashboard_failure(
            "nova-task-board",
            fallback={"tasks": [], "grouped": {}, "tree": [], "nodes": [], "lastModified": None},
        )
