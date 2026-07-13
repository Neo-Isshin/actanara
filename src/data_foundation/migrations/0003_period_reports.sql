CREATE TABLE IF NOT EXISTS period_reports (
    report_key TEXT PRIMARY KEY,
    period_type TEXT NOT NULL,
    start_date TEXT NOT NULL,
    end_date TEXT NOT NULL,
    projection_type TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    generated_at TEXT NOT NULL,
    source_run_id INTEGER REFERENCES ingestion_runs(id),
    status TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS period_reports_period_idx
    ON period_reports(projection_type, start_date, end_date, status);
