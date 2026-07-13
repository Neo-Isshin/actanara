CREATE TABLE IF NOT EXISTS legacy_task_projects (
    source_project_id TEXT PRIMARY KEY,
    name TEXT,
    source_last_updated TEXT,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id)
);

CREATE TABLE IF NOT EXISTS legacy_tasks (
    source_task_id TEXT PRIMARY KEY,
    source_project_id TEXT,
    title TEXT,
    status TEXT,
    progress INTEGER NOT NULL DEFAULT 0,
    source_last_updated TEXT,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id)
);

CREATE TABLE IF NOT EXISTS legacy_task_updates (
    source_row_id INTEGER PRIMARY KEY,
    source_task_id TEXT,
    report_date TEXT,
    progress_delta INTEGER,
    status TEXT,
    report_file TEXT,
    source_run_id INTEGER NOT NULL REFERENCES ingestion_runs(id)
);

CREATE TABLE IF NOT EXISTS task_shadow_imports (
    run_id INTEGER PRIMARY KEY REFERENCES ingestion_runs(id),
    source_db_path TEXT NOT NULL,
    source_project_count INTEGER NOT NULL,
    source_task_count INTEGER NOT NULL,
    source_update_count INTEGER NOT NULL,
    imported_at TEXT NOT NULL,
    notes_json TEXT NOT NULL
);
