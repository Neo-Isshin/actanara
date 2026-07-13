CREATE TABLE IF NOT EXISTS diary_markdown_documents (
    document_key TEXT PRIMARY KEY,
    business_date TEXT NOT NULL,
    report_type TEXT NOT NULL,
    relative_path TEXT NOT NULL,
    title TEXT,
    embedded_json TEXT,
    content_sha256 TEXT NOT NULL,
    byte_size INTEGER NOT NULL,
    modified_at TEXT,
    parsed_at TEXT NOT NULL,
    source_run_id INTEGER REFERENCES ingestion_runs(id),
    status TEXT NOT NULL,
    UNIQUE(business_date, report_type, relative_path)
);

CREATE TABLE IF NOT EXISTS diary_markdown_sections (
    document_key TEXT NOT NULL REFERENCES diary_markdown_documents(document_key) ON DELETE CASCADE,
    ordinal INTEGER NOT NULL,
    heading_level INTEGER NOT NULL,
    heading TEXT NOT NULL,
    heading_path_json TEXT NOT NULL,
    body_markdown TEXT NOT NULL,
    PRIMARY KEY(document_key, ordinal)
);

CREATE INDEX IF NOT EXISTS diary_markdown_documents_date_idx
    ON diary_markdown_documents(business_date, report_type, status);
