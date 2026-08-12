"""``grandquiz react``——真机 ReAct 对话：学材料 / 出题 / 判卷全经工具，多回合共享同一 agent。

复用现有装配件：``register_learning_tools``（ingest / query_weak / start_quiz）+ kernel
``Runner.run_agent_turn``（有界 tool-calling 循环）+ ``QuizEventPrinter``（事件脊柱的终端投影）+
独立 trace 库。考官内核 / ingest 编排一行不改——react 只是新增命令 + 组装（组装本身已剥到
``composition.build_react_runner``，供 CLI / Web 复用）。R1-S6：交互考核硬化为受控子流程——LLM 只
触发 ``start_quiz(count)``，逐题一问一答 + MC 选择器逐字提交都由 ``AssessmentSession`` 组合单题
workflow（LLM 不进逐题循环、不复述题目、不自己判卷）。
"""

import contextlib
import importlib
import time
import uuid
from collections.abc import Iterable, Iterator
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.approval import ApprovalGate
from grandquiz.domain.learning.preference import (
    QUESTION_LANGUAGE_KEY,
    detect_language,
    record_inferred_preference,
)
from grandquiz.domain.learning.responder import Responder
from grandquiz.interfaces.cli.approval import CliApprovalGate
from grandquiz.interfaces.cli.commands import _print_trace_location
from grandquiz.interfaces.cli.composition import (
    _ensure_parent,
    _resolve_trace_db,
    build_event_backbone,
    build_learning_persistence,
    build_react_runner,
    search_provider_from_env,
)
from grandquiz.interfaces.cli.interactive import InteractiveResponder
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.interfaces.learning_outbox import publish_pending_learning_facts
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

__all__ = ["_run_react_cli", "run_react"]

# DS-S4 的合法开放查询可能需要 search → outline/expand → 多个 bounded read →
# citation 参数回灌修正 → final。真机已出现第 8 次模型调用成功铸出 citation、却没有第 9 次
# finalization 机会的路径。提高 CLI 编排上限不放宽正文读取或总上下文预算；两者仍分别由
# DocumentSearch.turn_read_budget 与 ContextBuilder.total_budget 强制。Runner 通用默认保持 8。
_DEFAULT_REACT_MAX_ITERATIONS = 12


async def run_react(
    *,
    title: str | None = None,
    db_path: Path,
    materials_dir: Path,
    provider: Provider,
    responder: Responder,
    approval: ApprovalGate,
    console: Console,
    user_messages: Iterable[str],
    seed: int,
    trace_db_path: Path | None = None,
    max_iterations: int = _DEFAULT_REACT_MAX_ITERATIONS,
) -> str:
    """真机 ReAct 会话循环：逐条用户消息跑一次 ``run_agent_turn``，多回合共享同一 agent / 会话态。

    ``title`` 是**可选横幅**（只用于打印开场白，不进任何派生 / 分区）；会话操作持久全局 KB
    单池，不按标题分区（ADR-0005）。

    组装（经 ``composition.build_react_runner``）：``provider`` + ``ToolRegistry``（注入真依赖：
    SQLite store/memory/preferences + 本地文件/真实网页路由式 fetch 源 + 注入的审批门 /
    ``responder`` + ``quiz_seed=seed``）+ **ContextBuilder 分区装配**（M5）：system 前言区
    （版本化 ReAct 系统提示，
    ``load_prompt`` 读 name@digest，进 trace）+ 学情注入分区（``learner_context_provider`` 闭包，
    每回合 build 现取最新薄弱点 + 偏好 → agent 不调工具即知学情、更聪明编排）。**一个 ``Runner``
    贯穿全部回合**——``run_agent_turn`` 的历史裁剪（只留 user + final assistant）跨回合累积，学情
    分区随之逐回合刷新。R1-S6：考核走**受控子流程** ``start_quiz(count)``——LLM 只触发它、拿结构化
    小结，逐题一问一答 + MC 选择器逐字提交都由工具内部的 ``AssessmentSession`` 组合单题 workflow
    （``responder`` 逐题作答），LLM 不进逐题循环、不复述题目、不自己判卷。``preferences`` 透传给
    ``start_quiz``，出题语言按 **偏好 > 中文** 解析；跨会话留存，可由 ``quiz
    --prefer-lang`` 预先设定。

    **一个 ``EventEmitter`` / ``trace_id`` 贯穿全会话**：``QuizEventPrinter`` 订阅做 Rich 呈现、
    ``TraceStore`` 经 ``register`` 落**独立 trace 库**（默认与 learning.db 同目录 ``trace.db``）。
    会话结束打印 ``trace_id`` + 库位置。返回 ``trace_id``。真机模型 dogfood 属人机边界、不在 AFK。
    """
    _ensure_parent(db_path)
    resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
    _ensure_parent(resolved_trace_db)
    persistence = build_learning_persistence(db_path)
    store = persistence.store
    memory = persistence.memory
    preferences = persistence.preferences
    asked_questions = persistence.asked_questions
    difficulty = persistence.difficulty
    trace_store: TraceStore | None = None
    runner: Runner | None = None
    trace_id = uuid.uuid4().hex
    try:
        emitter, trace_store = build_event_backbone(
            resolved_trace_db, trace_id=trace_id, subscribers=[QuizEventPrinter(console)]
        )
        publish_pending_learning_facts(persistence.learning_facts, trace_store)
        runner = build_react_runner(
            provider=provider,
            emitter=emitter,
            store=store,
            memory=memory,
            preferences=preferences,
            asked_questions=asked_questions,
            difficulty=difficulty,
            approval=approval,
            materials_dir=materials_dir,
            responder=responder,
            seed=seed,
            max_iterations=max_iterations,
            search_provider=search_provider_from_env(),
            learning_facts=persistence.learning_facts,
            classifications=persistence.classifications,
            lexicons=persistence.recognition_lexicons,
        )

        banner = f"「{title}」" if title else ""
        console.print(f"[bold]ReAct 学习助手{banner}——输入消息与我对话（Ctrl+D 退出）[/]")
        for message in user_messages:
            # 自进化第一个具体能力：从这一轮用户原文推断出题语言偏好（纯确定性字符分类，不调
            # LLM）。与 run_agent_turn 是否成功无关——语言信号来自用户自己怎么打字，不来自这一轮
            # 有没有正常回复；显式设置（--prefer-lang / 用户明说"用英文出题"）永不被推断覆盖
            # （record_inferred_preference 自己的规则），故先做这步不影响既有显式偏好行为。
            detected_language = detect_language(message)
            if detected_language is not None:
                record_inferred_preference(preferences, QUESTION_LANGUAGE_KEY, detected_language)

            # 单轮兜底（dogfood "神了" 的会话级鲁棒）：run_agent_turn 内部已把可恢复的坏 tool_call
            # 走 M6 DEGRADED 回灌自愈；但若仍冒出未预期异常（FATAL 工具错 / MaxIterations / provider
            # 炸），只兜**这一轮**——打印友好提示后继续下一条消息，不让一轮坏 turn 杀整场会话。历史只
            # 在成功轮提交，失败轮不留孤儿 user 消息，故下一轮上下文干净。KeyboardInterrupt 是
            # BaseException、不被这里捕获，照旧优雅退出（main 的 suppress + _stdin_messages 处理）。
            try:
                reply = await runner.run_agent_turn(message)
            except Exception as exc:
                # 会话级兜底：一轮坏 turn 不杀整场（同 run_quiz 逐轮兜底的形状）。
                console.print(
                    f"[yellow]这一轮出了点问题，已跳过（{escape(str(exc))}）。继续对话。[/]"
                )
                continue
            console.print(f"[bold cyan]助手[/]：{escape(reply)}")

        _print_trace_location(console, trace_id, resolved_trace_db)
        return trace_id
    finally:
        # C-wire 增量 2：会话最后一轮排的历史折叠后台任务没有"下一轮"帮它收口——显式在此收尾，
        # 否则 asyncio.run 退出时会直接取消掉它，最后几轮的老轮摘要就永久丢了。须在 store/memory
        # 关闭前跑（真 Summarizer 若将来要读 KB，避免用到已关闭的连接）。
        if runner is not None:
            await runner.aclose()
        persistence.close()
        if trace_store is not None:
            trace_store.close()


def _stdin_messages() -> Iterator[str]:
    """从 stdin 逐行读用户消息（交互会话循环的输入源）：空行跳过，``exit`` / ``quit`` 或 EOF 退出。

    做成生成器（而非一次读全部）让会话真正逐回合交互：``run_react`` 每 ``next()`` 拿一条消息、跑一
    回合、打印回复，再回来取下一条。真机试跑（tty 逐回合对话）留给 human。

    读入走内置 ``input()`` + ``import readline``：GNU readline 按 locale 宽度做行编辑，修掉裸
    ``readline()`` cooked-mode 下 backspace 只删半个 CJK 字符宽、渲染与实际缓冲不符的问题（dogfood
    反馈）。``input()`` 是阻塞同步读、**不起嵌套事件循环**（不与 ``run_react`` 的 asyncio loop 冲突
    ——刻意不用 prompt_toolkit 同步 prompt，那会在运行中的 loop 里崩，正是 responder 走 ``ask_async``
    的原因）。管道 / 测试（非 tty）``input()`` 照常逐行读、EOF 抛 ``EOFError`` 退出。
    """
    # 仅为副作用导入 readline：让内置 input() 走 GNU readline 行编辑（CJK 宽度正确）；无 readline
    # 平台（如 Windows）跳过。用 import_module 而非 `import readline`——后者的裸名字绑定会被 ruff
    # F401 / pyright reportUnusedImport 双双判为未使用（它确实只为副作用、从不被引用）。
    with contextlib.suppress(ImportError):
        importlib.import_module("readline")
    while True:
        try:
            message = input("你：").strip()
        except (EOFError, KeyboardInterrupt):  # Ctrl+D / Ctrl+C：优雅退出会话
            break
        if not message:
            continue
        if message in {"exit", "quit", ":q"}:
            break
        yield message


async def _run_react_cli(*, title: str | None, db_path: Path, materials_dir: Path) -> None:
    console = Console()
    provider = OpenAICompatProvider.from_env()
    try:
        await run_react(
            title=title,
            db_path=db_path,
            materials_dir=materials_dir,
            provider=provider,
            responder=InteractiveResponder(),  # start_quiz 逐题作答：questionary 选择器 / 文本输入
            approval=CliApprovalGate(console=console),
            console=console,
            user_messages=_stdin_messages(),
            seed=int(time.time()),  # CLI 非 replay：可变种子（每次会话不同选题次序）
        )
    finally:
        await provider.aclose()
