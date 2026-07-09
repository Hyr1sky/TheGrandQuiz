-- GKB-S2（ADR-0005）：知识模型收敛到全局 KB 干净终态——清库重来，不迁移旧数据。
-- 1) resources 内容寻址：resource_id = derive_id(url)，去 task_id 外键、加可空 topic 软标签列。
-- 2) LearningTask 消解：弃 tasks 表（不再有独立实体；语言归 Preference Memory）。
-- 直接落新形状（旧库归档 / 用户重新 ingest）：DROP 旧 resources / tasks 再建新 resources。
-- 靠 PRAGMA user_version 只跑一次（见 kernel/db.migrate），幂等；无时间戳 / 非确定性内容，
-- 保持 replay 逐字节一致。DROP TABLE 连带删掉 0001 建的 idx_resources_task 索引。
DROP TABLE IF EXISTS resources;
CREATE TABLE resources (
    resource_id  TEXT PRIMARY KEY,
    url          TEXT    NOT NULL,
    raw_content  TEXT,
    content_hash TEXT,
    trusted      INTEGER NOT NULL,
    status       TEXT    NOT NULL,
    topic        TEXT
);
DROP TABLE IF EXISTS tasks;
