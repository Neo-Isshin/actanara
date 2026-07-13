CREATE TABLE IF NOT EXISTS task_board_snapshots (
    snapshot_key TEXT PRIMARY KEY,
    board_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    projected_at TEXT NOT NULL,
    source_run_id INTEGER REFERENCES ingestion_runs(id),
    status TEXT NOT NULL,
    details_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_board_projects (
    snapshot_key TEXT NOT NULL REFERENCES task_board_snapshots(snapshot_key) ON DELETE CASCADE,
    project_ordinal INTEGER NOT NULL,
    section TEXT NOT NULL,
    project TEXT NOT NULL,
    PRIMARY KEY(snapshot_key, project_ordinal)
);

CREATE TABLE IF NOT EXISTS task_board_items (
    snapshot_key TEXT NOT NULL REFERENCES task_board_snapshots(snapshot_key) ON DELETE CASCADE,
    item_key TEXT NOT NULL,
    project_ordinal INTEGER NOT NULL,
    item_ordinal INTEGER NOT NULL,
    section TEXT NOT NULL,
    project TEXT NOT NULL,
    done INTEGER NOT NULL CHECK (done IN (0, 1)),
    content TEXT NOT NULL,
    agent TEXT,
    identified_task_id TEXT,
    source_line INTEGER NOT NULL,
    raw_line TEXT NOT NULL,
    PRIMARY KEY(snapshot_key, item_key),
    FOREIGN KEY(snapshot_key, project_ordinal) REFERENCES task_board_projects(snapshot_key, project_ordinal)
);

CREATE INDEX IF NOT EXISTS task_board_items_snapshot_idx
    ON task_board_items(snapshot_key, section, done);
