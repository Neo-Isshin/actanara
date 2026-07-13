PRAGMA foreign_keys=OFF;

CREATE TABLE nova_task_nodes_new (
    node_id TEXT PRIMARY KEY,
    parent_node_id TEXT REFERENCES nova_task_nodes(node_id),
    node_type TEXT NOT NULL CHECK (node_type IN ('track', 'workstream', 'task', 'subtask', 'step')),
    title TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN (
        'active', 'planned', 'blocked', 'paused', 'completed', 'done',
        'automatic', 'settled', 'stale', 'archived'
    )),
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

PRAGMA foreign_keys=ON;
