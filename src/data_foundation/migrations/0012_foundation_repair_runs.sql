CREATE TABLE IF NOT EXISTS foundation_repair_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    action_id TEXT NOT NULL,
    action_class TEXT NOT NULL,
    business_date TEXT NOT NULL,
    requested_at TEXT NOT NULL,
    started_at TEXT,
    completed_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('queued', 'running', 'completed', 'failed')),
    exit_code INTEGER,
    lock_key TEXT,
    command_digest TEXT NOT NULL,
    confirmation_digest TEXT,
    stdout_tail TEXT,
    stderr_tail TEXT,
    error_summary TEXT,
    qa_before_json TEXT,
    qa_after_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_foundation_repair_runs_date_status
    ON foundation_repair_runs(business_date, status, id);

CREATE INDEX IF NOT EXISTS idx_foundation_repair_runs_action_date
    ON foundation_repair_runs(action_id, business_date, id);
