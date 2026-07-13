CREATE TABLE IF NOT EXISTS task_board_mutation_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    audit_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    occurred_at TEXT NOT NULL,
    mutation_source TEXT NOT NULL,
    board_path TEXT NOT NULL,
    requested_content TEXT NOT NULL,
    requested_done INTEGER NOT NULL CHECK (requested_done IN (0, 1)),
    identified_task_id TEXT,
    before_sha256 TEXT NOT NULL,
    after_sha256 TEXT NOT NULL,
    before_snapshot_json TEXT NOT NULL,
    after_snapshot_json TEXT NOT NULL
);
