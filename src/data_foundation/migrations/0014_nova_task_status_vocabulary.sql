PRAGMA foreign_keys=OFF;

CREATE TABLE nova_task_nodes_new (
    node_id TEXT PRIMARY KEY,
    parent_node_id TEXT REFERENCES nova_task_nodes(node_id),
    node_type TEXT NOT NULL CHECK (node_type IN ('track', 'workstream', 'task', 'subtask', 'step')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('active', 'planned', 'blocked', 'paused', 'completed', 'archived')),
    progress INTEGER NOT NULL DEFAULT 0 CHECK (progress >= 0 AND progress <= 100),
    sort_order INTEGER NOT NULL DEFAULT 0,
    scope_json TEXT NOT NULL DEFAULT '{}',
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    completed_at TEXT
);

INSERT INTO nova_task_nodes_new(
    node_id, parent_node_id, node_type, title, status, progress, sort_order,
    scope_json, metadata_json, created_at, updated_at, completed_at
)
SELECT
    node_id, parent_node_id, node_type, title, status, progress, sort_order,
    scope_json, metadata_json, created_at, updated_at, completed_at
FROM nova_task_nodes;

DROP TABLE nova_task_nodes;
ALTER TABLE nova_task_nodes_new RENAME TO nova_task_nodes;

CREATE INDEX IF NOT EXISTS idx_nova_task_nodes_parent
    ON nova_task_nodes(parent_node_id, sort_order);

CREATE INDEX IF NOT EXISTS idx_nova_task_nodes_status
    ON nova_task_nodes(status);

CREATE TABLE nova_task_candidates_new (
    candidate_id TEXT PRIMARY KEY,
    candidate_type TEXT NOT NULL CHECK (candidate_type IN ('parent_task', 'subtask', 'status_update', 'scope_alias')),
    proposed_title TEXT NOT NULL,
    proposed_parent_node_id TEXT REFERENCES nova_task_nodes(node_id),
    matched_node_id TEXT REFERENCES nova_task_nodes(node_id),
    status TEXT NOT NULL CHECK (status IN ('pending_review', 'confirmed', 'merged', 'superseded', 'rejected', 'deferred')),
    confidence TEXT NOT NULL CHECK (confidence IN ('high', 'medium', 'low', 'unknown')),
    reason TEXT NOT NULL,
    evidence_json TEXT NOT NULL DEFAULT '[]',
    source_event_id TEXT REFERENCES nova_task_events(event_id),
    source_fingerprint TEXT NOT NULL,
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    decided_at TEXT
);

INSERT INTO nova_task_candidates_new(
    candidate_id, candidate_type, proposed_title, proposed_parent_node_id,
    matched_node_id, status, confidence, reason, evidence_json, source_event_id,
    source_fingerprint, metadata_json, created_at, updated_at, decided_at
)
SELECT
    candidate_id, candidate_type, proposed_title, proposed_parent_node_id,
    matched_node_id,
    CASE WHEN status = 'pending' THEN 'pending_review' ELSE status END,
    confidence, reason, evidence_json, source_event_id,
    source_fingerprint, metadata_json, created_at, updated_at, decided_at
FROM nova_task_candidates;

DROP TABLE nova_task_candidates;
ALTER TABLE nova_task_candidates_new RENAME TO nova_task_candidates;

CREATE INDEX IF NOT EXISTS idx_nova_task_candidates_status
    ON nova_task_candidates(status, updated_at);

CREATE UNIQUE INDEX IF NOT EXISTS idx_nova_task_candidates_identity
    ON nova_task_candidates(candidate_type, proposed_title, COALESCE(proposed_parent_node_id, ''), source_fingerprint);

CREATE TABLE nova_task_reconciliation_decisions_new (
    decision_id TEXT PRIMARY KEY,
    candidate_id TEXT REFERENCES nova_task_candidates(candidate_id),
    decision_type TEXT NOT NULL CHECK (decision_type IN ('confirm', 'rename_confirm', 'merge', 'attached', 'attach_as_subtask', 'reject', 'defer', 'status_update', 'supersede')),
    actor TEXT NOT NULL,
    reason TEXT,
    before_json TEXT NOT NULL DEFAULT '{}',
    after_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);

INSERT INTO nova_task_reconciliation_decisions_new(
    decision_id, candidate_id, decision_type, actor, reason, before_json, after_json, created_at
)
SELECT decision_id, candidate_id, decision_type, actor, reason, before_json, after_json, created_at
FROM nova_task_reconciliation_decisions;

DROP TABLE nova_task_reconciliation_decisions;
ALTER TABLE nova_task_reconciliation_decisions_new RENAME TO nova_task_reconciliation_decisions;

PRAGMA foreign_keys=ON;
