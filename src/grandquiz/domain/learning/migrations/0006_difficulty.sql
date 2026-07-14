-- 难度台账（SE-S1）：锚定 item_id，为每个 KnowledgeItem 维护离散 5 档难度（1..5）。
-- 独立于 learning_memory 薄弱台账（决策 2）：薄弱台账"销账即删行"，而难度生命周期是
-- "只要考过就一直在"——从没薄弱过、一路顺畅的概念也需标难度并升档（User Story 12），故不能
-- 塞进薄弱表随销账丢失。item_id 唯一 → 每概念至多一行，set_tier 用 INSERT OR REPLACE 覆盖。
-- 无时间戳列（determinism 纪律）：seq 自增主键给插入序，难度无需时序、读取按 item_id 定位，
-- 排序不依赖墙上时间，保证 replay 逐字节一致。IF NOT EXISTS 兜底幂等。

CREATE TABLE IF NOT EXISTS difficulty (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    item_id TEXT    NOT NULL UNIQUE,
    tier    INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_difficulty_item ON difficulty (item_id);
