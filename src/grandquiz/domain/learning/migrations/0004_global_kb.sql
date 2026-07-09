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
-- 清库重来须清净全部知识数据（用户确认历史 dogfood 数据不重要）：旧 knowledge_items 的
-- resource_id 来自旧派生 derive_id(task_id, url)、其 resources 已随本迁移 DROP → 成孤儿；
-- in-place 升级若不清，all_items()（读全表、不 join resources）会把孤儿 item 读进全库选题池、
-- 污染考核。锚定其上的 learning_memory 薄弱账同样成孤儿，一并清空。preferences（question_language
-- 等个人设置）属跨库设置、非知识数据，**保留不清**。fresh db 下两表本空 → DELETE 为 no-op。
DELETE FROM knowledge_items;
DELETE FROM learning_memory;
