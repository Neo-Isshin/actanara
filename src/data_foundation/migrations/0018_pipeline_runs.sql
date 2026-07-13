CREATE TABLE IF NOT EXISTS pipeline_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    business_date TEXT NOT NULL,
    run_kind TEXT NOT NULL,
    requested_by TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'completed', 'partial', 'failed', 'skipped', 'blocked')
    ),
    started_at TEXT,
    completed_at TEXT,
    source_trigger_id INTEGER REFERENCES ingestion_runs(id),
    provider_id TEXT,
    model TEXT,
    steps_json TEXT NOT NULL DEFAULT '[]',
    failure_class TEXT,
    error_summary TEXT,
    artifact_paths_json TEXT NOT NULL DEFAULT '{}',
    retry_of_run_id INTEGER REFERENCES pipeline_runs(id),
    metadata_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_business_date
    ON pipeline_runs(business_date, id DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_status
    ON pipeline_runs(status, updated_at DESC);

CREATE INDEX IF NOT EXISTS idx_pipeline_runs_kind_date
    ON pipeline_runs(run_kind, business_date, id DESC);
