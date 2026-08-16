CREATE INDEX IF NOT EXISTS source_artifacts_cursor_identity_idx
    ON source_artifacts(
        tool_key,
        CAST(json_extract(
            CASE WHEN json_valid(cursor_json) THEN cursor_json ELSE '{}' END,
            '$.device'
        ) AS INTEGER),
        CAST(json_extract(
            CASE WHEN json_valid(cursor_json) THEN cursor_json ELSE '{}' END,
            '$.inode'
        ) AS INTEGER)
    );

CREATE INDEX IF NOT EXISTS source_artifacts_fingerprint_size_idx
    ON source_artifacts(tool_key, fingerprint, byte_size);

CREATE INDEX IF NOT EXISTS usage_events_source_artifact_business_date_idx
    ON usage_events(source_artifact_id, business_date);

CREATE INDEX IF NOT EXISTS activity_evidence_source_artifact_business_date_idx
    ON activity_evidence(source_artifact_id, business_date);
