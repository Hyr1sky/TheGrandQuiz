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

Initial eval cases:

| Case | Assertion |
| --- | --- |
| User asks to learn React Server Components | Agent discovers resources first and does not directly build the knowledge base |
| User rejects a resource | Rejected resource must not enter context |
| User asks details about a blog | Agent must call `read_resource_deep` before answering |
| User enables interview skill | Agent should generate interview questions, not a normal summary |
| User answers a quiz incorrectly | Weak concept should be recorded into Learning Memory |
| Web fetch fails | Resource should be marked failed and replacement should be suggested |

## MVP Scope（已确认）

Narrow first scope: user enters a technical learning direction; agent finds resources; user approves; agent generates a learning route, quiz, and summary.

For stable development and eval, the first version can use a mock resource provider or manually supplied URLs. Real search can be added as a second-phase tool.

The main recommendation is to build the Agent Runtime skeleton, approval gate, trace, and eval extension points before adding many tools.
