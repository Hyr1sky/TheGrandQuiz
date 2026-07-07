-- tasks 表补 language 列：出题 / 判卷所用语言（LearningTask.language）。
-- 0001 建表时漏了此列，导致 language 跨 SQLite 往返被静默丢弃、退回默认"中文"（真 bug）。
-- NOT NULL DEFAULT '中文'：0001 已存在的旧行无该列值，DEFAULT 让它们回填为默认中文
-- （与模型默认一致，不破坏既有数据）。ALTER TABLE ADD COLUMN 是幂等迁移的自然单位——
-- 靠 PRAGMA user_version 只跑一次（见 kernel/db.migrate），不会重复加列报错。
-- 无时间戳 / 非确定性内容，保持 replay 逐字节一致。
ALTER TABLE tasks ADD COLUMN language TEXT NOT NULL DEFAULT '中文';
