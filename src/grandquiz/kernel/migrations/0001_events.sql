-- append-only 事件日志：trace = 事件的持久化。
-- 无任何时间戳列（ts 是注入 Clock 的确定性值，不是墙上时间），保证 replay 逐字节一致。
CREATE TABLE events (
    trace_id       TEXT    NOT NULL,
    seq            INTEGER NOT NULL,
    ts             REAL    NOT NULL,
    type           TEXT    NOT NULL,
    span_id        TEXT,
    parent_span_id TEXT,
    payload        TEXT    NOT NULL,
    PRIMARY KEY (trace_id, seq)
);
CREATE INDEX idx_events_span ON events (trace_id, span_id);
