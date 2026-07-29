CREATE TABLE IF NOT EXISTS acquisition_runs (
    run_id TEXT PRIMARY KEY,
    trace_id TEXT NOT NULL UNIQUE,
    kind TEXT NOT NULL CHECK (kind IN ('upload', 'url')),
    locator TEXT NOT NULL,
    display_name TEXT NOT NULL,
    status TEXT NOT NULL CHECK (
        status IN ('queued', 'running', 'needs_input', 'succeeded', 'failed', 'cancelled')
    ),
    request_payload TEXT NOT NULL,
    prepared_payload TEXT,
    token_hash TEXT NOT NULL,
    token_expires_at REAL NOT NULL,
    token_used_at REAL,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL,
    resource_id TEXT,
    error_code TEXT,
    error_message TEXT
);

CREATE INDEX IF NOT EXISTS idx_acquisition_runs_updated_at
ON acquisition_runs(updated_at DESC);
