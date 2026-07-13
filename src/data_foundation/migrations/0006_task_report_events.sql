CREATE TABLE IF NOT EXISTS task_report_sources (
    source_path TEXT PRIMARY KEY,
    content_sha256 TEXT NOT NULL,
    report_date TEXT,
    update_count INTEGER NOT NULL,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id)
);

CREATE TABLE IF NOT EXISTS task_report_update_events (
    event_key TEXT PRIMARY KEY,
    source_path TEXT NOT NULL REFERENCES task_report_sources(source_path),
    source_content_sha256 TEXT NOT NULL,
    event_ordinal INTEGER NOT NULL,
    report_date TEXT,
    source_task_id TEXT NOT NULL,
    source_project_id TEXT,
    title TEXT,
    status TEXT,
    progress_delta INTEGER NOT NULL,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id),
    UNIQUE(source_path, source_content_sha256, event_ordinal)
);

CREATE TABLE IF NOT EXISTS task_report_shadow_imports (
    run_id INTEGER PRIMARY KEY REFERENCES ingestion_runs(id),
    reports_root TEXT NOT NULL,
    source_file_count INTEGER NOT NULL,
    source_with_updates_count INTEGER NOT NULL,
    event_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    notes_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS task_board_observations (
    run_id INTEGER PRIMARY KEY REFERENCES ingestion_runs(id),
    board_path TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    in_progress_count INTEGER NOT NULL,
    completed_count INTEGER NOT NULL,
    identified_checkbox_count INTEGER NOT NULL,
    compared_at TEXT NOT NULL,
    details_json TEXT NOT NULL
);
