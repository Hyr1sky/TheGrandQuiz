"""grandquiz CLI——argparse 子命令路由：``ingest``（喂材料入库）/ ``quiz``（逐题交互考核）。

CLI 是事件脊柱的消费者：``quiz`` 把 ``QuizEventPrinter`` 订阅到考核事件流做 Rich 呈现，不另起
渲染逻辑（呼应架构卖点）。两个子命令都用真 ``OpenAICompatProvider.from_env()`` + 持久化 SQLite
（``--db`` 默认 ``~/.grandquiz/learning.db``，自动建父目录；store / memory 同一 db 文件，薄弱点
跨会话留存）。真机交互试跑（``grandquiz quiz`` 的 tty 逐题）留给 human；``run_ingest`` /
``run_quiz`` 把 provider / responder / console 作参数注入，故可测的粘合（文件读取 / 存在性检查 /
空库分支 / 薄弱小结 / 事件呈现）都能用假件驱动断言，不碰真实 tty 或 LLM。

无子命令 → 打印帮助。
"""

import argparse
import asyncio
import contextlib
import sys
import time
import uuid
from collections.abc import Callable, Iterable, Iterator, Sequence
from pathlib import Path
from urllib.parse import urlparse

from dotenv import load_dotenv
from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.approval import ScriptedApprovalGate
from grandquiz.domain.learning.assessment import assess_once
from grandquiz.domain.learning.context import learner_context_provider
from grandquiz.domain.learning.fetch import FetchError
from grandquiz.domain.learning.ingest import IngestResult, ingest_resource
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.models import LearningTask
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY, SqlitePreferenceMemory
from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.domain.learning.tools import register_learning_tools
from grandquiz.interfaces.cli.interactive import InteractiveResponder
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import SystemClock, new_rng
from grandquiz.kernel.context import ContextBuilder, Partition
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.recovery import Decision, RecoveryPolicy
from grandquiz.kernel.report import render_trace_html
from grandquiz.kernel.runner import Runner
from grandquiz.kernel.tools import ToolRegistry
from grandquiz.kernel.trace import Span, TraceStore, build_span_tree
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

# ReAct 系统提示的版本化模板名（load_prompt 读 prompts/react_system.md，版本号进 trace）。
_REACT_PROMPT_NAME = "react_system"

# --db 默认库路径：跨会话薄弱点留存的持久 SQLite。
_DEFAULT_DB = Path.home() / ".grandquiz" / "learning.db"
# 独立 trace 库文件名：与 learning.db 分开、同目录（各自 user_version / 迁移序列，互不串号）。
_TRACE_DB_NAME = "trace.db"
# 本地材料的占位 URL host（fetch 域名白名单放行它；真机远程抓取才走真实域名 + 注入防护）。
_LOCAL_HOST = "local"
_DEFAULT_MAX_BYTES = 8 * 1024 * 1024
_DEFAULT_ROUNDS = 5


def _ensure_parent(db_path: Path) -> None:
    """自动建 db 文件的父目录（``~/.grandquiz`` 首次运行时不存在）。"""
    db_path.parent.mkdir(parents=True, exist_ok=True)


def _resolve_trace_db(db_path: Path, trace_db_path: Path | None) -> Path:
    """独立 trace 库路径：显式传入优先，否则默认与 learning.db 同目录的 ``trace.db``。

    两库各自 ``user_version``——同路径会让 learning 迁移先占版本号、TraceStore 跳过建 ``events`` 表、
    ``record`` 被隔离静默吞掉 → trace 静默为空（违背"大声失败"），故拒绝同路径。
    """
    resolved = trace_db_path if trace_db_path is not None else db_path.parent / _TRACE_DB_NAME
    if resolved == db_path:
        raise ValueError(f"trace 库不能与 learning 库同路径（{db_path}）——请分开")
    return resolved


def _print_trace_location(console: Console, trace_id: str, trace_db_path: Path) -> None:
    """会话结束打印 ``trace_id`` + 独立 trace 库位置（便于随手 ``grandquiz trace <id>`` 复盘）。"""
    console.print(
        f"[dim]本次会话 trace：[bold]{trace_id}[/]（存于 {escape(str(trace_db_path))}）[/]"
    )


async def run_ingest(
    *,
    title: str,
    material_path: Path,
    db_path: Path,
    provider: Provider,
    console: Console,
    trace_db_path: Path | None = None,
) -> IngestResult:
    """读本地材料 → 真 Reader 深读 → 审批（MVP keep-all）→ 入 SQLite。返回 ``IngestResult``。

    ``source`` 注入文件内容、``url`` 用本地占位（域名白名单只放行 ``_LOCAL_HOST``）；``provider`` /
    ``console`` 作参数注入以便测试用假件驱动。``max_bytes`` 取内容实际字节与默认上限的较大者
    （本地材料不受远程大小上限约束，但仍走同一守卫路径）。

    本次会话生成一个 ``trace_id``，并把发射的 AgentEvent 流经 ``EventSink.register`` 注册的
    ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的 ``trace.db``）——落 trace 纯
    经"注册 processor"实现，``ingest_resource`` 签名逻辑一行不改（可观测是脊柱投影、非业务耦合）。
    会话结束打印 ``trace_id`` + 库位置。
    """
    content = material_path.read_text(encoding="utf-8")
    _ensure_parent(db_path)
    resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
    _ensure_parent(resolved_trace_db)
    store = SqliteLearningStore(db_path)
    trace_store: TraceStore | None = None  # try 内构造 + None-guard 关闭，建失败不泄漏 store
    task = LearningTask.create(title)
    url = f"file://{_LOCAL_HOST}/{material_path.name}"
    trace_id = uuid.uuid4().hex
    try:
        sink = EventSink()
        trace_store = TraceStore(resolved_trace_db)
        sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
        result = await ingest_resource(
            task,
            url,
            source=lambda _url: content,
            provider=provider,
            store=store,
            approval=ScriptedApprovalGate(keep=lambda _item: True),
            emitter=emitter,
            max_bytes=max(_DEFAULT_MAX_BYTES, len(content.encode("utf-8")) + 1),
            allowed_domains={_LOCAL_HOST},
        )
    finally:
        store.close()
        if trace_store is not None:
            trace_store.close()
    _print_ingest_result(console, title, result)
    _print_trace_location(console, trace_id, resolved_trace_db)
    return result


def _print_ingest_result(console: Console, title: str, result: IngestResult) -> None:
    if result.status == "failed":
        console.print(f"[red]深读失败：材料未能入库（任务「{title}」）。[/]")
        return
    if not result.items:
        console.print(f"[yellow]深读完成但没有抽出知识点（任务「{title}」）。[/]")
        return
    console.print(f"[bold green]已入库 {len(result.items)} 个知识点（任务「{title}」）：[/]")
    for item in result.items:
        console.print(f"  · [bold]{item.concept}[/] — {item.summary}")


async def run_quiz(
    *,
    title: str,
    rounds: int,
    db_path: Path,
    provider: Provider,
    responder: Responder,
    console: Console,
    seed: int,
    trace_db_path: Path | None = None,
    prefer_lang: str | None = None,
) -> None:
    """对 ``title`` 任务跑 ``rounds`` 轮逐题考核；空库 → 提示先 ingest。会话结束打印薄弱点小结。

    ``QuizEventPrinter`` 订阅事件流做 Rich 呈现（CLI = 事件脊柱的投影）。``responder`` 取消作答
    （``InteractiveResponder`` 抛 ``KeyboardInterrupt``）→ 优雅退出本次会话、仍打印已积累的薄弱点。
    某轮失败由 kernel ``RecoveryPolicy`` 统一裁决：``DEGRADED``（出题 / 判卷重试用尽）→ 跳过该轮
    继续下一轮；其余（``ReplayMiss`` 等 ``FATAL`` / 未知异常）→ 原样冒泡（绝不静默吞，保 eval /
    replay 契约）。裁决经异常自带的 ``error_class`` 标做出、并发 ``RECOVERY_DECIDED`` 上脊柱。
    ``rng`` 用可变种子（CLI 非 replay）：每轮 ``new_rng(seed + 轮次)``。

    ``prefer_lang``：非 None 时先显式把 ``question_language`` 偏好写进持久 SQLite（跨会话留存），
    再下传 Preference Memory 给 ``assess_once``——出题语言按 **偏好 > task 默认 > 中文** 覆盖。
    偏好台账**每次会话都构造并下传**（哪怕本次未设），故上次会话设过的语言偏好本次仍生效。

    **每次会话一个 ``trace_id``**（一个 ``EventEmitter`` 贯穿全部轮次，故 ``seq`` / span id 跨轮
    唯一、落库后是一条 trace、每轮一棵 assessment 根 span）；发射的 AgentEvent 流经
    ``EventSink.register`` 注册的 ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的
    ``trace.db``）——落 trace 纯经"注册 processor"实现，``assess_once`` 签名逻辑一行不改（可观测
    是脊柱投影、非业务耦合）。会话结束打印 ``trace_id`` + 库位置。
    """
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    preferences = SqlitePreferenceMemory(db_path)  # 偏好与 store / memory 共用同一 learning db
    if prefer_lang is not None:
        # 显式设置出题语言偏好（confidence 恒 1.0），跨会话留存、后续覆盖 task 默认语言。
        preferences.set_preference(QUESTION_LANGUAGE_KEY, prefer_lang)
    trace_store: TraceStore | None = None  # 空库分支不落 trace（无会话）；在 finally 里择机关闭
    try:
        task = LearningTask.create(title)
        if not store.items_for_task(task.task_id):
            _print_needs_ingest(console, title)
            return

        resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
        _ensure_parent(resolved_trace_db)
        trace_id = uuid.uuid4().hex
        sink = EventSink()
        sink.subscribe(QuizEventPrinter(console))
        trace_store = TraceStore(resolved_trace_db)
        sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
        # 一个 emitter 贯穿全会话：跨轮共享 trace_id，seq / span id 单调唯一（不逐轮重置、不撞号）。
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)
        policy = RecoveryPolicy(emitter)  # 每轮失败统一裁决（读异常 error_class 标、发事件上脊柱）
        # SKELETON: 会话内进程内"已问过"台账（item_id → 已问过的题目文本），跨轮累积、经 assess_once
        # 下传出题函数做去重——复考同一薄弱概念时每轮换角度、不逐字重问。正式版是与 Learning Memory
        # 并列的跨会话 SQLite 去重表（跨会话持久），见 docs/skeleton-ledger.md #8。
        recently_asked: dict[str, list[str]] = {}
        console.print(f"[bold]开始考核「{title}」——共 {rounds} 轮（Ctrl+C 随时退出）[/]")
        try:
            for round_index in range(rounds):
                console.rule(f"第 {round_index + 1} / {rounds} 轮")
                try:
                    await assess_once(
                        task,
                        store=store,
                        provider=provider,
                        responder=responder,
                        memory=memory,
                        emitter=emitter,
                        rng=new_rng(seed + round_index),
                        recently_asked=recently_asked,
                        preferences=preferences,
                    )
                except Exception as exc:
                    # 统一裁决：assess_once 按契约原样冒泡一切异常（保 eval / replay——不吞
                    # ReplayMiss 等 harness 错误）；本界面把裁决权交给 kernel RecoveryPolicy——
                    # DEGRADED（出题 / 判卷重试用尽）→ 跳过本轮继续；其余（FATAL / 未知）→ 冒泡。
                    # 分类只读异常自带的 error_class 标（kernel 领域无关），未带标 → FATAL 冒泡。
                    if policy.decide(exc) is Decision.SKIP:
                        console.print(f"[yellow]本轮跳过（{escape(str(exc))}）[/]")
                        continue
                    raise
        except KeyboardInterrupt:
            console.print("\n[dim]已退出本次考核会话。[/]")

        _print_weak_summary(console, store, memory, task)
        _print_trace_location(console, trace_id, resolved_trace_db)
    finally:
        store.close()
        memory.close()
        preferences.close()
        if trace_store is not None:
            trace_store.close()


def _print_needs_ingest(console: Console, title: str) -> None:
    console.print(
        f"[yellow]任务「{title}」还没有知识库。先运行 "
        f"[bold]grandquiz ingest <材料文件> --task {title}[/] 喂材料再来考核。[/]"
    )


def _print_weak_summary(
    console: Console,
    store: SqliteLearningStore,
    memory: SqliteLearningMemory,
    task: LearningTask,
) -> None:
    weak_ids = memory.weak_item_ids()
    if not weak_ids:
        console.print("[green]本次考核后没有遗留薄弱点，全部掌握。[/]")
        return
    concept_by_id = {item.item_id: item.concept for item in store.items_for_task(task.task_id)}
    console.print("[bold]薄弱点小结（已跨会话留存，下次优先考）：[/]")
    for item_id in sorted(weak_ids):
        state = memory.state_of(item_id)
        console.print(f"  · {concept_by_id.get(item_id, item_id)} — {state}")


# --- react 子命令（真机 ReAct 对话 agent）----------------------------------------------------
#
# 复用现有装配件：register_learning_tools（ingest / query_weak / start_quiz）+ kernel
# Runner.run_agent_turn（有界 tool-calling 循环）+ QuizEventPrinter（事件脊柱的终端投影）+
# 独立 trace 库。考官内核 / ingest 编排一行不改——react 只是新增命令 + 组装。
# R1-S6：交互考核硬化为受控子流程——LLM 只触发 start_quiz(count)，逐题一问一答 + MC 选择器逐字提交
# 都在工具内部的 assess_once 循环里跑（LLM 不进逐题循环、不复述题目、不自己判卷）。


def _file_source(materials_dir: Path) -> Callable[[str], str]:
    """建**文件式** fetch 源（复用现有 ingest 那套读本地材料，非 httpx——真远程抓取仍缓办）。

    把 ``file://local/<相对路径>`` 的 url 映射到 ``materials_dir/<相对路径>`` 读取；文件不存在等 IO
    异常由 ``fetch_resource`` 归一成 ``FetchError`` → ingest 走优雅失败分支（不炸整条会话）。url 的
    ``local`` host 必在 ingest 的域名白名单里（见 ``run_react`` 的 ``allowed_domains``）。

    **路径穿越守卫**：url 的 path 是不可信输入——``file://local/../../etc/passwd`` 之类含
    ``..`` / 绝对路径的 payload 会逃出 ``materials_dir`` 读任意文件。故解析后 ``resolve()`` 归一，
    再校验最终路径仍在 ``materials_dir`` 内（``is_relative_to``）；越界一律**拒**（抛
    ``FetchError``、报清楚哪个文件不在材料目录内）——``fetch_resource`` 原样透传 ``FetchError``，
    ingest 走优雅失败分支、不炸会话。正常的 ``file://local/<名>`` / 目录内嵌套相对路径不受影响。
    """
    base = materials_dir.resolve()

    def source(url: str) -> str:
        relative = urlparse(url).path.lstrip("/")
        target = (base / relative).resolve()
        if not target.is_relative_to(base):
            raise FetchError(f"文件 {relative} 不在材料目录 {base} 内")
        return target.read_text(encoding="utf-8")

    return source


async def run_react(
    *,
    title: str,
    db_path: Path,
    materials_dir: Path,
    provider: Provider,
    responder: Responder,
    console: Console,
    user_messages: Iterable[str],
    seed: int,
    trace_db_path: Path | None = None,
    max_iterations: int = 8,
) -> str:
    """真机 ReAct 会话循环：逐条用户消息跑一次 ``run_agent_turn``，多回合共享同一 agent / 会话态。

    组装：``provider`` + ``ToolRegistry``（经 ``register_learning_tools`` 注入真依赖：SQLite
    store/memory/preferences + 文件式 fetch 源 + keep-all 审批门 + 注入的 ``responder`` +
    ``quiz_seed=seed``）+ **ContextBuilder 分区装配**（M5）：system 前言区（版本化 ReAct 系统提示，
    ``load_prompt`` 读 name@digest，进 trace）+ 学情注入分区（``learner_context_provider`` 闭包，
    每回合 build 现取最新薄弱点 + 偏好 → agent 不调工具即知学情、更聪明编排）。**一个 ``Runner``
    贯穿全部回合**——``run_agent_turn`` 的历史裁剪（只留 user + final assistant）跨回合累积，学情
    分区随之逐回合刷新。R1-S6：考核走**受控子流程** ``start_quiz(count)``——LLM 只触发它、拿结构化
    小结，逐题一问一答 + MC 选择器逐字提交都在工具内部的 ``assess_once`` 循环里（``responder`` 逐题
    作答），LLM
    不进逐题循环、不复述题目、不自己判卷。``preferences`` 透传给 ``start_quiz`` → ``assess_once``
    解析出题语言（偏好 > task 默认 > 中文；跨会话留存，可由 ``quiz --prefer-lang`` 预先设定）。

    **一个 ``EventEmitter`` / ``trace_id`` 贯穿全会话**：``QuizEventPrinter`` 订阅做 Rich 呈现、
    ``TraceStore`` 经 ``register`` 落**独立 trace 库**（默认与 learning.db 同目录 ``trace.db``）。
    会话结束打印 ``trace_id`` + 库位置。返回 ``trace_id``。真机模型 dogfood 属人机边界、不在 AFK。
    """
    _ensure_parent(db_path)
    resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
    _ensure_parent(resolved_trace_db)
    store = SqliteLearningStore(db_path)
    memory = SqliteLearningMemory(db_path)
    preferences = SqlitePreferenceMemory(db_path)  # 与 store / memory 共用同一 learning db
    trace_store: TraceStore | None = None
    trace_id = uuid.uuid4().hex
    try:
        task = LearningTask.create(title)
        sink = EventSink()
        sink.subscribe(QuizEventPrinter(console))
        trace_store = TraceStore(resolved_trace_db)
        sink.register(trace_store)  # 消费者即 processor：真机事件流落独立 trace 库
        emitter = EventEmitter(sink, SystemClock(), trace_id=trace_id)

        registry = ToolRegistry()
        register_learning_tools(
            registry,
            task=task,
            source=_file_source(materials_dir),
            provider=provider,
            store=store,
            approval=ScriptedApprovalGate(keep=lambda _item: True),  # MVP keep-all（同 run_ingest）
            memory=memory,
            max_bytes=_DEFAULT_MAX_BYTES,
            allowed_domains={_LOCAL_HOST},
            responder=responder,  # start_quiz 逐题作答（真机 InteractiveResponder）
            preferences=preferences,  # 出题语言偏好透传给 assess_once
            quiz_seed=seed,
        )
        prompt = load_prompt(_REACT_PROMPT_NAME)
        # ContextBuilder（M5）分区装配：system 前言区（版本化 react 系统提示）+ 学情注入分区
        # （domain provider，闭包捕获 store/memory/preferences/task → 每回合 build 现取最新薄弱
        # 点 + 偏好）。domain→kernel 合法：ContextBuilder 只认名字 + 字符串 provider。学情分区内容
        # 为空（无薄弱、无偏好）时 build 自动跳过、不注入空块。预算 / 压缩接缝已在 ContextBuilder
        # 留好（下一程接 context compression），本处不设 budget。
        context_builder = ContextBuilder(
            [
                Partition(name="system", provider=prompt.text),
                Partition(
                    name="memory",
                    provider=learner_context_provider(
                        store=store, memory=memory, preferences=preferences, task=task
                    ),
                ),
            ]
        )
        runner = Runner(
            provider=provider,
            emitter=emitter,
            prompt_version=prompt.version,  # prompt 版本号进 trace（架构约束）
            tools=registry,
            max_iterations=max_iterations,
            context_builder=context_builder,
        )

        console.print(f"[bold]ReAct 学习助手「{title}」——输入消息与我对话（Ctrl+D 退出）[/]")
        for message in user_messages:
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
        store.close()
        memory.close()
        preferences.close()
        if trace_store is not None:
            trace_store.close()


# --- 导出子命令（report / trace → 自包含 HTML）------------------------------------------------
#
# 复用 issue 03 的 kernel.report.render_trace_html——两命令共用同一渲染器，绝不重实现渲染。


def _sum_span_tokens(spans: Iterable[Span]) -> int:
    """递归汇总 span 森林的 token 用量（复用 ``Span.tokens``，其底层是 ``Usage.total_tokens``）。"""
    total = 0
    for span in spans:
        if span.tokens is not None:
            total += span.tokens
        total += _sum_span_tokens(span.children)
    return total


def export_trace_html(
    trace_id: str,
    *,
    trace_db_path: Path,
    out_path: Path | None = None,
    console: Console | None = None,
) -> Path:
    """从独立 trace 库按 ``trace_id`` 读出某次会话 → ``render_trace_html`` → 写自包含 HTML 文件。

    读不到该 ``trace_id`` → **大声报错**（抛 ``ValueError``），绝不静默产出空报告。默认输出路径为
    trace 库同目录的 ``trace-<id>.html``。返回产出文件路径。
    """
    store = TraceStore(trace_db_path)
    try:
        events = store.events(trace_id)
    finally:
        store.close()
    if not events:
        raise ValueError(
            f"trace 未找到：{trace_id}（库 {trace_db_path}）"
            "——确认 trace_id 与 --trace-db 正确（每次会话结束会打印 trace_id + 库位置）"
        )
    spans = build_span_tree(events)
    meta: dict[str, object] = {
        "trace_id": trace_id,
        "event_count": len(events),
        "total_tokens": _sum_span_tokens(spans),
    }
    document = render_trace_html(events, spans, meta=meta, title=f"Trace {trace_id}")
    resolved_out = (
        out_path if out_path is not None else trace_db_path.parent / f"trace-{trace_id}.html"
    )
    _ensure_parent(resolved_out)
    resolved_out.write_text(document, encoding="utf-8")
    if console is not None:
        console.print(f"[bold green]trace HTML 已导出：[/]{escape(str(resolved_out))}")
    return resolved_out


async def _run_ingest_cli(*, title: str, material_path: Path, db_path: Path) -> None:
    provider = OpenAICompatProvider.from_env()
    try:
        await run_ingest(
            title=title,
            material_path=material_path,
            db_path=db_path,
            provider=provider,
            console=Console(),
        )
    finally:
        await provider.aclose()


async def _run_quiz_cli(
    *, title: str, rounds: int, db_path: Path, prefer_lang: str | None = None
) -> None:
    # 先查库（不构造 provider）：空库 / 错任务直接给指引——无需 LLM key，也免去无谓 HTTP 客户端。
    console = Console()
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    try:
        has_items = bool(store.items_for_task(LearningTask.create(title).task_id))
    finally:
        store.close()
    if not has_items:
        _print_needs_ingest(console, title)
        return

    provider = OpenAICompatProvider.from_env()
    try:
        await run_quiz(
            title=title,
            rounds=rounds,
            db_path=db_path,
            provider=provider,
            responder=InteractiveResponder(),
            console=console,
            seed=int(time.time()),  # CLI 非 replay：可变种子（每次会话不同选题次序）
            prefer_lang=prefer_lang,
        )
    finally:
        await provider.aclose()


def _stdin_messages(console: Console) -> Iterator[str]:
    """从 stdin 逐行读用户消息（交互会话循环的输入源）：空行跳过，``exit`` / ``quit`` 或 EOF 退出。

    做成生成器（而非一次读全部）让会话真正逐回合交互：``run_react`` 每 ``next()`` 拿一条消息、跑一
    回合、打印回复，再回来取下一条。真机试跑（tty 逐回合对话）留给 human。
    """
    while True:
        console.print("[bold]你[/]：", end="")
        try:
            line = sys.stdin.readline()
        except KeyboardInterrupt:
            break
        if not line:  # EOF（Ctrl+D）
            break
        message = line.strip()
        if not message:
            continue
        if message in {"exit", "quit", ":q"}:
            break
        yield message


async def _run_react_cli(*, title: str, db_path: Path, materials_dir: Path) -> None:
    console = Console()
    provider = OpenAICompatProvider.from_env()
    try:
        await run_react(
            title=title,
            db_path=db_path,
            materials_dir=materials_dir,
            provider=provider,
            responder=InteractiveResponder(),  # start_quiz 逐题作答：questionary 选择器 / 文本输入
            console=console,
            user_messages=_stdin_messages(console),
            seed=int(time.time()),  # CLI 非 replay：可变种子（每次会话不同选题次序）
        )
    finally:
        await provider.aclose()


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="grandquiz", description="考核驱动的个人学习工具")
    sub = parser.add_subparsers(dest="command")

    p_ingest = sub.add_parser("ingest", help="读本地材料 → 深读 → 入库")
    p_ingest.add_argument("material_file", type=Path, help="本地材料文件路径")
    p_ingest.add_argument("--task", required=True, help="学习任务标题（考核范围）")
    p_ingest.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")

    p_quiz = sub.add_parser("quiz", help="对某任务逐题交互考核")
    p_quiz.add_argument("title", help="学习任务标题")
    p_quiz.add_argument("--rounds", type=int, default=_DEFAULT_ROUNDS, help="考核轮数")
    p_quiz.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")
    p_quiz.add_argument(
        "--prefer-lang",
        default=None,
        help="显式设出题语言偏好（如 英文 / en），跨会话留存并覆盖任务默认语言",
    )

    p_react = sub.add_parser("react", help="真机 ReAct 对话——学材料 / 出题 / 判卷全经工具")
    p_react.add_argument("title", help="学习任务标题（考核范围）")
    p_react.add_argument("--db", type=Path, default=_DEFAULT_DB, help="SQLite 库路径")
    p_react.add_argument(
        "--materials-dir",
        type=Path,
        default=Path.cwd(),
        help="本地材料目录（ingest 的 file://local/<文件名> 相对此目录解析，默认当前目录）",
    )

    p_report = sub.add_parser("report", help="跑 eval harness → 导出自包含 HTML 报告")
    p_report.add_argument(
        "--out", type=Path, default=None, help="报告输出目录（默认 ~/.grandquiz/eval-report）"
    )

    p_trace = sub.add_parser("trace", help="按 trace_id 从 trace 库导出自包含 HTML")
    p_trace.add_argument("trace_id", help="要导出的会话 trace_id（会话结束时打印过）")
    p_trace.add_argument(
        "--db", type=Path, default=_DEFAULT_DB, help="learning 库路径（派生默认 trace 库位置）"
    )
    p_trace.add_argument(
        "--trace-db", type=Path, default=None, help="独立 trace 库路径（默认同目录 trace.db）"
    )
    p_trace.add_argument(
        "--out", type=Path, default=None, help="输出 HTML 文件（默认 trace-<id>.html）"
    )

    return parser


def main(argv: Sequence[str] | None = None) -> None:
    """子命令路由入口（``[project.scripts] grandquiz``）。无子命令 → 打印帮助。

    启动即 ``load_dotenv()``（从 cwd 向上找 ``.env``）：让 ``grandquiz`` 在仓库里开箱可用、
    无需每次 ``--env-file``；已在环境里的变量不覆盖（``uv run --env-file .env`` 仍兼容）。
    """
    load_dotenv()
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.command == "ingest":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_ingest_cli(title=args.task, material_path=args.material_file, db_path=args.db)
            )
    elif args.command == "quiz":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_quiz_cli(
                    title=args.title,
                    rounds=args.rounds,
                    db_path=args.db,
                    prefer_lang=args.prefer_lang,
                )
            )
    elif args.command == "react":
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(
                _run_react_cli(title=args.title, db_path=args.db, materials_dir=args.materials_dir)
            )
    elif args.command == "report":
        # 报告不碰 provider / learning 库：全确定性假件驱动 harness，纯导出 HTML。
        from grandquiz.evals.harness import export_html_report

        console = Console()
        out_dir = args.out if args.out is not None else _DEFAULT_DB.parent / "eval-report"
        index_path = asyncio.run(export_html_report(out_dir))
        console.print(f"[bold green]eval 报告已导出：[/]{escape(str(index_path))}（浏览器打开）")
    elif args.command == "trace":
        console = Console()
        trace_db = _resolve_trace_db(args.db, args.trace_db)
        try:
            export_trace_html(
                args.trace_id, trace_db_path=trace_db, out_path=args.out, console=console
            )
        except (ValueError, OSError) as exc:  # 读不到 id / 库路径问题 → 大声报错 + 非零退出
            console.print(f"[red]{escape(str(exc))}[/]")
            raise SystemExit(1) from exc
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
