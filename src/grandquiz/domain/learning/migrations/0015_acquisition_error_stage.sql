CREATE TABLE IF NOT EXISTS acquisition_run_failures (
    run_id TEXT PRIMARY KEY
        REFERENCES acquisition_runs(run_id) ON DELETE CASCADE,
    error_stage TEXT NOT NULL
);
