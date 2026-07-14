-- 已问过去重台账（skeleton-ledger.md #8 修复）：跨会话持久化"item_id → 已问过的题目文本"，
-- 替换此前进程内 dict 假件（重开 CLI 就丢，复考同一薄弱概念可能被跨会话逐字重问旧题）。
-- 无时间戳列（决策 2）：题目的先后靠 seq 自增主键给出的插入序，不靠墙上时间，保证 replay
-- 逐字节一致。IF NOT EXISTS 兜底幂等（迁移执行器已按 user_version 只跑一次）。

CREATE TABLE IF NOT EXISTS asked_questions (
    seq      INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id  TEXT NOT NULL,
    question TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_asked_questions_item ON asked_questions (item_id);
