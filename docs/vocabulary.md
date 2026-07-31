# Learning Vocabulary 治理

Status: v1 foundation implemented（审核与候选已落地，尚不驱动选题）

受控词表的目的不是给每个知识点贴满标签，而是用少量、稳定、可解释的 key 支撑分类、选题、统计和
Eval，同时把模型自由发挥产生的噪声隔离在审核区。

## 三层结构

1. **封闭维度**：影响行为的枚举，由代码、数据契约与 Eval 共同版本化。
2. **受控增长词表**：领域、技术等可增长名词；具有稳定 key、alias、定义和生命周期。
3. **开放候选**：模型或用户提出的新词；在审核前不驱动产品行为。

```text
candidate: proposed ──→ approved term ──→ deprecated ──→ replaced
                    └─→ rejected
```

正式 term 不物理删除。显示名和别名可以演化，`namespace + key` 身份保持稳定。

## 初始 namespace

| namespace | 类型 | 用途 |
| --- | --- | --- |
| `kind` | 封闭 | 知识单元的主要形态 |
| `orientation` | 封闭 | 理论 / 实践倾向 |
| `question` | 封闭 | 题目形式 |
| `question_strategy` | 封闭 | 标准提问 / 追问深挖 |
| `input_modality` | 封闭 | 文本 / 语音输入媒介 |
| `answer_format` | 封闭 | 选项 / 自然语言 / 代码答案形态 |
| `demand` | 封闭 | 认知要求 |
| `error` | 封闭 | 错因 |
| `source` | 封闭 | 材料体裁 |
| `domain` | 受控增长 | RAG、Agent Runtime、评测等领域 |
| `technology` | 受控增长 | ROS 2、DDS、Fast DDS 等具体技术 |
| `role` | 预留 | CompetencyBlueprint 立项后再启用 |

## 分类流程

```text
Reader 产出 KnowledgeItem
→ 规范化候选字符串
→ exact / alias 匹配
→ 从 approved vocabulary 检索少量候选
→ 模型只能从候选中选择
→ 没有合适词时创建 TagCandidate
→ 审批只突出低置信度、冲突和新词
→ 用户修正同时形成 Eval 标签
```

初始机器词表位于 [vocabulary.v1.yaml](vocabulary.v1.yaml)。其中 managed seed 全部保持 `proposed`，
直到真实 KnowledgeItem 回放与人工去重通过；seed 的存在不代表已经批准。

## 规范化与冲突

- key 使用 ASCII `snake_case`。
- display label 与 alias 经过 NFKC、trim、casefold 后匹配。
- alias 只在同一 namespace 内解析。
- 同 namespace 的 alias 冲突 fail closed，进入人工审核。
- embedding 相似度只可提示候选，第一版不自动合并词。
- assignment 必须引用完整 `namespace + key`，不能只存显示名。

## 人工工作量控制

- 命中 approved term 且高置信度的 assignment 默认折叠。
- 新词、alias 冲突、低置信度和所有行为维度必须人工确认。
- 候选按批次处理，不在每次阅读时打断用户。
- 单次出现不能自动把模型候选升级为正式词。
- 用户纠正保留模型原建议，用于计算 correction rate 和 Replay 稳定性。

## v1 刻意不做

- 不维护 broader / narrower 词表层级；
- 不建立跨资源 CanonicalConcept；
- 不把 tag 当 KnowledgeRelation；
- 不让自由 tag 直接改变出题、判卷或 Learning Memory；
- 不以词表规模作为成功指标。

只有在明确消费者和 Eval 证明收益后，层级或关系才升格为正式领域对象。

## 质量指标

- approved tag coverage；
- user correction rate；
- new-term proposal rate；
- alias hit rate；
- duplicate candidate rate；
- low-use term rate；
- replay classification stability；
- unreviewed behavior-tag count（必须恒为 0）。

## 存储方向

- 仓库保存机器可读 seed，供安装包、测试和 Replay；
- `learning.db` 保存用户扩展 term、alias、candidate、assignment 与审核结果；
- 词表使用独立 tables、repository 和 migration 边界，但与知识入库共享数据库事务和备份；
- trace 只记录 taxonomy version、term key 与审核事件，不复制整份词表；
- Markdown 解释人类规则，但不是运行时唯一数据源。

需要多用户或跨设备共享时，优先增加显式 vocabulary export/import package；在出现真实同步消费者前
不拆第二个数据库。
