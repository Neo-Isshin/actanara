CREATE VIEW IF NOT EXISTS nova_task_l1_review_items AS
SELECT
    candidate_id AS review_id,
    candidate_id,
    candidate_type,
    proposed_title,
    proposed_parent_node_id,
    matched_node_id,
    status,
    confidence,
    reason,
    evidence_json,
    source_event_id,
    source_fingerprint,
    metadata_json,
    created_at,
    updated_at,
    decided_at
FROM nova_task_candidates
WHERE candidate_type = 'parent_task';
