"""学习域工具——把确定性考官 / 记忆编排包成 kernel ReAct 循环可调的 ``Tool``（R1-S2）。

**住 domain 层**：import kernel 的 ``Tool`` / ``ToolContext`` + domain 的编排函数（``domain→kernel``
合法；``kernel↛domain`` 由 import-linter 守）。工具是 **wrap 不是改写**——``ingest_resource`` /
``Memory`` / ``Store`` 的签名逻辑一行不动，只是被薄薄一层包起来注册进 ``ToolRegistry``。

三个工具各自一个文件（``ingest_tool.py`` / ``query_weak_tool.py`` / ``start_quiz_tool.py``），
共用的 ``ScopedEmitter`` 包装抽在 ``_scoped_emitter.py``：

- ``ingest(url)``：wrap ``ingest_resource`` → 返回结构化结果（入库知识点数 + 概念名列表）。内部
  span（fetch / Reader model / item_created）经 ``ScopedEmitter`` **重挂在本次 TOOL_CALL 之下**、
  进 trace；ReAct 消息上下文**只收结构化结果字符串**、看不到考官内部 model 调用 / 消息（隔离在
  工具边界）。
- ``query_weak_concepts()``：**只读**——读 Learning Memory（薄弱 / 观察中 item）+ store（概念名，
  全库读）→ 返回薄弱概念摘要。无 LLM、确定性（context-free 工具，不需要 ctx）。
- ``start_quiz(count?, focus?)``：**受控一问一答子流程**——内部跑 ``assess_once × count``
  （``assess_once`` 一行不改），用**注入的 Responder** 逐题作答（MC 走 ``questionary.select`` 逐字
  选项文本 → 确定性逐字判卷），共享 emitter（内部 assess_once span 嵌 TOOL_CALL 之下），返回结构化
  小结（考几题 / 每题判决 / 暴露哪些薄弱点）。**LLM 只触发它、拿小结，不进逐题循环、不复述题目、
  不自己判卷**——
  取代 S2b 的软工具 ``next_question`` / ``submit_answer``（那套把逐轮编排压给 LLM，deepseek 守不住：
  编题 / 把 MC 答案加 "B. " 前缀毁逐字判卷 / 题目双重渲染 / confabulate）。

组装点 ``register_learning_tools`` 把三者一并注册（``start_quiz`` 仅当注入了 ``responder`` 时
注册——无 responder 无从逐题作答）；工具的领域依赖（source / provider / store / approval / memory /
responder / preferences …）在此闭包捕获，per-call 只多收工具入参与（context-aware 工具才用的）
``ToolContext``。``LearningTask`` 已消解（ADR-0005）——知识进全局 KB 单池、无 task 线程，工具不再收
task。三个工具各自的结果 / 参数类型（``IngestToolResult``、``StartQuizResult`` 等）走精确子模块路径
导入，本包顶层只公开这一个组装入口。
"""

from collections.abc import Collection
from typing import Literal

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.asked_questions import AskedQuestionsLedger
from grandquiz.domain.learning.difficulty import DifficultyLedger
from grandquiz.domain.learning.ingest.fetch import FetchSource
from grandquiz.domain.learning.memory import Memory
from grandquiz.domain.learning.preference import PreferenceMemory
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import Store
from grandquiz.domain.learning.tools.document_search_tools import make_document_search_tools
from grandquiz.domain.learning.tools.grounded_answer_tool import make_grounded_answer_tool
from grandquiz.domain.learning.tools.ingest_tool import make_ingest_tool
from grandquiz.domain.learning.tools.query_weak_tool import make_query_weak_concepts_tool
from grandquiz.domain.learning.tools.start_quiz_tool import make_start_quiz_tool
from grandquiz.kernel.tools import ToolRegistry
from grandquiz.providers.base import Provider

__all__ = ["register_learning_tools"]


def register_learning_tools(
    registry: ToolRegistry,
    *,
    source: FetchSource,
    provider: Provider,
    store: Store,
    approval: ApprovalGate,
    memory: Memory,
    max_bytes: int,
    allowed_domains: Collection[str] | Literal["*"],
    responder: Responder | None = None,
    preferences: PreferenceMemory | None = None,
    quiz_seed: int = 0,
    asked_questions: AskedQuestionsLedger | None = None,
    difficulty: DifficultyLedger | None = None,
) -> None:
    """组装点：注册 ``ingest`` / ``query_weak_concepts`` /（有 responder 时）``start_quiz``。

    领域依赖在此注入并被各工具闭包捕获；注册后 ReAct 主体（``run_agent_turn``）即可按名调它们，
    kernel 侧 registry / dispatch 完全不认识这些工具的领域语义（kernel 领域无关）。

    ``responder`` 为 ``None`` 时**不注册** ``start_quiz``——受控考核无从逐题作答（如 S2 的 ingest /
    query 单测装配无需交互作答）；真机 react 装配注入 ``InteractiveResponder`` 后即可考核。
    ``preferences`` 透传给 ``start_quiz`` → ``assess_once`` 解析出题语言；``quiz_seed`` 给选题种子
    （replay 传固定值 → 可复现，CLI 可传可变值）；``asked_questions`` 透传跨会话去重台账
    （skeleton-ledger.md #8），``None`` 时行为不变（向后兼容）。``difficulty`` 透传难度台账
    （SE-S3），``None`` 时不接难度自适应（向后兼容）。
    """
    registry.register(
        make_ingest_tool(
            source=source,
            provider=provider,
            store=store,
            approval=approval,
            max_bytes=max_bytes,
            allowed_domains=allowed_domains,
        )
    )
    registry.register(make_query_weak_concepts_tool(store=store, memory=memory))
    registry.register(make_grounded_answer_tool(store=store, provider=provider))
    for tool in make_document_search_tools(store=store):
        registry.register(tool)
    if responder is not None:
        registry.register(
            make_start_quiz_tool(
                provider=provider,
                store=store,
                memory=memory,
                responder=responder,
                preferences=preferences,
                quiz_seed=quiz_seed,
                asked_questions=asked_questions,
                difficulty=difficulty,
            )
        )
