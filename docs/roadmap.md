# Learning Digital Human Development Roadmap

> 初始路线图（2026-06 起草），记录基于 ScholarMate 数字人 demo 改造学习型数字人的架构讨论。
> 执行顺序已按依赖关系调整，见 [architecture.md](architecture.md)。原始文档中已过时的章节
> （指向旧仓库的代码树、日期已过的甘特图、已拍板的 Next Discussion Topics）已裁去或并入对应文档。

This document records the initial architecture discussion for building a learning-oriented digital human based on the current ScholarMate digital human demo.

## Overall Direction

The learning digital human should not be treated as a simple reskin of the scholar digital human. The current repository is best used as a reference implementation for an Agent Runtime: a manually written ReAct loop, dynamic tool mounting, progressive context disclosure, subagent execution, task persistence, references, and conversation history.

The learning scenario should get its own domain layer.

```text
Learning goal input
  -> resource discovery
  -> user approval
  -> initial knowledge base construction
  -> user learning activity tracking
  -> agent dispatches skills, tools, and subagents
  -> quizzes, interview questions, summaries, route updates
  -> eval, trace, memory, and recovery improve the system over time
```

The core product is not only a chatbot. It should be an observable, recoverable, and evaluable learning Agent Runtime.

## Recommended Architecture

```mermaid
flowchart TD
    U[User] --> UI[Learning UI]

    UI --> API[Backend API]
    API --> LS[Learning Service]
    API --> CS[Conversation Service]
    API --> ES[Event Service]

    LS --> RT[Learning Agent Runtime]
    CS --> RT
    ES --> MEM[Memory System]

    RT --> RUN[AgentRunner / ReAct Loop]
    RT --> CTX[Learning Context Builder]
    RT --> TR[Tool Registry]
    RT --> HK[Hook Manager]
    RT --> ER[Error Recovery]

    TR --> T1[Resource Discovery Tools]
    TR --> T2[Resource Reading Tools]
    TR --> T3[Knowledge Base Tools]
    TR --> T4[Learning Skill Tools]
    TR --> T5[User Activity Tools]

    RT --> SA1[Discovery Subagent]
    RT --> SA2[Reader Subagent]
    RT --> SA3[Quiz Subagent]
    RT --> SA4[Interview Subagent]
    RT --> SA5[Summarizer Subagent]

    SA1 --> KB[(Knowledge Store)]
    SA2 --> KB
    SA3 --> KB
    SA4 --> KB
    SA5 --> KB

    MEM --> CTX
    KB --> CTX

    RT --> TRACE[Trace Store]
    TRACE --> EVAL[Eval Harness]
```

## Core Domain Model

Do not start with a complex vector database. First make the structured model clear.

```mermaid
classDiagram
    class LearningTask {
      id
      user_id
      topic
      goal
      level
      status
      created_at
    }

    class ResourceCandidate {
      id
      task_id
      title
      url
      source_type
      summary
      reason
      quality_score
      status
    }

    class LearningResource {
      id
      task_id
      candidate_id
      title
      url
      content_status
      approved_at
      notes
    }

    class KnowledgeItem {
      id
      resource_id
      concept
      summary
      evidence
      confidence
    }

    class ActivityEvent {
      id
      user_id
      task_id
      event_type
      target_id
      payload
      created_at
    }

    class MemoryRecord {
      id
      user_id
      scope
      kind
      content
      confidence
      updated_at
    }

    LearningTask --> ResourceCandidate
    ResourceCandidate --> LearningResource
    LearningResource --> KnowledgeItem
    LearningTask --> ActivityEvent
    ActivityEvent --> MemoryRecord
```

## Subagent Plan

Do not add subagents only for the sake of being multi-agent. Introduce a subagent when the task boundary is clear, the context can be isolated, and the output can be verified.

| Subagent | Responsibility | Trigger | Output |
| --- | --- | --- | --- |
| Discovery Subagent | Finds blogs, official docs, repos, courses | After user creates a learning task | `ResourceCandidate[]` |
| Reader Subagent | Deep reads approved resources | After approval or when user asks detailed questions | Summary, concepts, evidence, citations |
| Quiz Subagent | Generates exercises from read material | After user studies or asks for quiz mode | Questions, answers, source evidence |
| Interview Subagent | Runs interview-style questioning | When interview skill is enabled | Question sequence, scoring, follow-ups |
| Summarizer Subagent | Produces learning notes/articles | After resource reading or user request | Summary article, concept tree |
| Planner Subagent | Adjusts learning route | When progress or gaps change | Next-step learning recommendation |

The main agent should handle conversation and orchestration. Subagents should handle isolated, verifiable work.

> 2026-06-13 收敛（架构审视）：判据硬化为"隔离大上下文 + 输出可结构化验证"。按此判据，
> **MVP 唯一 subagent 是 Reader**；出题（generate_quiz）、判卷（grade_answer）是带 schema 的
> **工具**，不是 subagent。上表 Discovery / Quiz / Interview / Summarizer / Planner 降为二期候选，
> 逐个按判据再立项。核心考核循环是确定性 workflow（见 architecture.md 核心设计判断二），
> 不是多 subagent 编排。

## Tool Plan

```mermaid
mindmap
  root((Learning Tools))
    Discovery
      search_web
      search_docs
      search_github_repos
      rank_resource_candidates
    Reading
      fetch_web_page
      read_document
      read_code_repo
      extract_key_concepts
    Knowledge
      create_knowledge_item
      list_approved_resources
      query_learning_kb
      attach_source_evidence
    Activity
      get_recent_activity
      summarize_user_progress
      detect_learning_gap
    Skills
      generate_quiz
      grade_answer
      generate_interview_question
      summarize_topic
      build_learning_plan
```

MVP tools can be much smaller:

```text
search_learning_resources
approve_resource
read_resource_deep
list_learning_resources
generate_quiz
generate_summary
get_recent_activity
```

> 2026-06-13 按考核竖切收敛 MVP 实际工具集：`approve_resource`、`read_resource_deep`（经 Reader
> subagent）、`list_knowledge_items`、`generate_quiz`（出题，工具）、`grade_answer`（判卷，工具）、
> `get_recent_activity`。`search_learning_resources` / `generate_summary` 随发现 / 总结环节移入二期。

Later expansions can add GitHub repository reading, official documentation priority ranking, coding exercise generation, and source quality scoring.

## Hook System

The hook system is important for making the runtime solid. Avoid scattering callbacks across the codebase. Define an explicit lifecycle.

```mermaid
sequenceDiagram
    participant User
    participant Runtime
    participant Hook
    participant Tool
    participant Trace
    participant Memory

    User->>Runtime: send message
    Runtime->>Hook: before_turn
    Runtime->>Hook: before_model_call
    Runtime->>Tool: execute tool
    Runtime->>Hook: after_tool_call
    Runtime->>Memory: update short-term memory
    Runtime->>Trace: persist trace
    Runtime->>Hook: after_turn
    Runtime-->>User: response
```

Recommended hook points:

```text
before_turn
after_turn
before_model_call
after_model_call
before_tool_call
after_tool_call
on_tool_error
on_subagent_start
on_subagent_done
on_memory_update
on_eval_sample
```

Early hooks can be no-op implementations. The important part is to stabilize the interface early.

## Memory System

Do not merge all memory into one generic table. Separate memory by purpose.

```text
1. Session Memory
   Short-term context from the current conversation and JSONL history.

2. Learning Memory
   User progress inside a learning task: mastered concepts, weak concepts, completed resources.

3. Preference Memory
   User preferences: interview-style learning, quiz preference, summary length, language preference.

4. Resource Memory
   Resource summaries, concepts, citations, and quality judgments.
```

> 2026-06-13 收敛（ADR-0003）：上述四分库收为两类——MVP 只实现 **Learning + Preference**；
> Resource Memory 并入 KnowledgeItem（不重复造实体），Session Memory 归 kernel 会话历史（非 domain 记忆）。

SQLite plus JSON payload is enough for the first version. A vector database can be introduced later when resource volume and retrieval requirements justify it.

## Error Recovery

Learning agents depend on many external operations: web fetching, search, repository reading, model tool calls, and subagent tasks. Add a `RecoveryPolicy` early.

```mermaid
flowchart TD
    ERR[Error] --> TYPE{Error Type}

    TYPE -->|Tool param invalid| FIX1[Ask model to repair args]
    TYPE -->|Network failed| FIX2[Retry with backoff]
    TYPE -->|Resource unreadable| FIX3[Mark resource failed + suggest replacement]
    TYPE -->|Subagent timeout| FIX4[Return partial result]
    TYPE -->|Low confidence| FIX5[Ask user approval / clarification]
    TYPE -->|Model max iterations| FIX6[Stop with trace + recovery message]
```

Errors should be written into trace records. They should not only be returned as strings to the model.

## Eval Harness

Eval should be planned early, even if the first version is simple. The key capability is deterministic replay.

```mermaid
flowchart LR
    CASE[Eval Case] --> RUN[Run Agent]
    RUN --> TRACE[Trace]
    TRACE --> ASSERT[Assertions]
    ASSERT --> REPORT[Eval Report]

    ASSERT --> A1[Tool Order]
    ASSERT --> A2[Grounding]
    ASSERT --> A3[Approval Gate]
    ASSERT --> A4[Source Citation]
    ASSERT --> A5[Skill Behavior]
```

Initial eval cases（2026-06-12 改为考核竖切 8 例，全部跑在 trace 上）：

| # | Case | Assertion |
| --- | --- | --- |
| 1 | 深读产出未经审批 | 未审批的 KnowledgeItem 不得入库 |
| 2 | 空库时"考我" | Agent 拒绝出题并引导用户先喂资源，不凭空编题 |
| 3 | 出题 | 每道题锚定存在的 KnowledgeItem，且其 evidence 非空 |
| 4 | 答错一道题 | 对应薄弱概念按 item id 写入 Learning Memory |
| 5 | 复考选题 | 出的题 ∈ 代码构造的"薄弱优先"候选集（候选集按薄弱优先构造，LLM 在集内自由挑；有薄弱概念时新概念不进集） |
| 6 | 答对薄弱题 | 第一次答对 → 薄弱转"观察中"（仍在表内）；连续第二次答对 → 销账移出 |
| 7 | 深读 fetch 失败 | 资源标记失败，不产生幽灵 KnowledgeItem |
| 8 | 题型路由 | 首次接触概念出选择题，薄弱概念复考走追问 |

> 旧用例中 discovery（"先发现再建库"）、interview skill、换源建议三例随发现/面试环节移入二期。
> "拒绝资源不入上下文"并入用例 1（审批语义）。

## MVP Scope（已确认，2026-06-12 修订为考核竖切）

> 修订背景：产品定位明确为"考核驱动的个人学习工具"（见根目录 CONTEXT.md），
> 核心循环是考核，路线规划与总结降为配角——原"路线 + quiz + 总结三件套"出 MVP。

MVP 是一条穿透考核循环的竖切：

```text
手动喂 URL → 审批 → Reader 深读入库（带出处）→ "考我" → 出题带证据
→ 判卷 → 薄弱概念入 Learning Memory → 复考时优先薄弱点
```

这条竖切已穿透全部 runtime 能力：审批门、subagent、grounding、结构化输出、记忆、trace/replay。

For stable development and eval, the first version uses manually supplied URLs (mock resource
provider). Real search/discovery, learning-route planning, and summaries are second-phase.

The main recommendation is unchanged: build the Agent Runtime skeleton, approval gate, trace, and
eval extension points before adding many tools.
