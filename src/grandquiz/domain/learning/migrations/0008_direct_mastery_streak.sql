-- SH-S8：未进入薄弱账、直接连续答对的持久掌握证据。
-- 两次直答即升档并清零，因此持久态只允许 0/1；脏值在写入点大声失败。
ALTER TABLE difficulty ADD COLUMN correct_streak INTEGER NOT NULL DEFAULT 0
    CHECK (correct_streak BETWEEN 0 AND 1);
