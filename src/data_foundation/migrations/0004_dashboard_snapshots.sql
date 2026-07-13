CREATE TABLE IF NOT EXISTS dashboard_snapshots (
    snapshot_key TEXT PRIMARY KEY,
    projection_type TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_run_id INTEGER REFERENCES ingestion_runs(id),
    status TEXT NOT NULL
);
