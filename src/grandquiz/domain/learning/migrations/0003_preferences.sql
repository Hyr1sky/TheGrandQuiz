-- Preference Memory：显式设置的个人偏好（ADR-0003 的 M7 组成部分）。
-- 走 learning 的独立 db 文件（与 kernel 的 trace.db 分开），自有 PRAGMA user_version 与迁移序列。
-- **无任何时间戳列**（决策 2：偏好是显式设置、无时序含义），保证 replay 逐字节一致。
-- confidence 现恒 1.0（显式设置）；REAL 列为二期"从行为隐式推断偏好置信度"预留。
-- IF NOT EXISTS 保证幂等（迁移执行器已按 user_version 只跑一次，这里再兜底一层）。

CREATE TABLE IF NOT EXISTS preferences (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    confidence REAL NOT NULL
);
