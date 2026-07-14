"""``grandquiz quiz``——对**全局 KB** 逐题交互考核；空库提示先 ingest，会话结束打印薄弱点小结。"""

import time
import uuid
from pathlib import Path

from rich.console import Console
from rich.markup import escape

from grandquiz.domain.learning.assessment.engine import assess_once
from grandquiz.domain.learning.memory import SqliteLearningMemory
from grandquiz.domain.learning.preference import QUESTION_LANGUAGE_KEY
from grandquiz.domain.learning.responder import Responder
from grandquiz.domain.learning.store import SqliteLearningStore
from grandquiz.interfaces.cli.commands import _print_trace_location
from grandquiz.interfaces.cli.composition import (
    _ensure_parent,
    _resolve_trace_db,
    build_event_backbone,
    build_learning_stores,
)
from grandquiz.interfaces.cli.interactive import InteractiveResponder
from grandquiz.interfaces.cli.printer import QuizEventPrinter
from grandquiz.kernel.clock import new_rng
from grandquiz.kernel.recovery import Decision, RecoveryPolicy
from grandquiz.kernel.trace import TraceStore
from grandquiz.providers.base import Provider
from grandquiz.providers.llm import OpenAICompatProvider

__all__ = ["_run_quiz_cli", "run_quiz"]


async def run_quiz(
    *,
    title: str | None = None,
    rounds: int,
    db_path: Path,
    provider: Provider,
    responder: Responder,
    console: Console,
    seed: int,
    trace_db_path: Path | None = None,
    prefer_lang: str | None = None,
) -> None:
    """对**全局 KB** 跑 ``rounds`` 轮逐题考核；空库 → 提示先 ingest。会话结束打印薄弱点小结。

    ``title`` 是**可选横幅**（只用于打印开场白 / 空库提示，不进任何派生 / 分区）——``LearningTask``
    已消解（ADR-0005），会话不再绑标题，选题候选池恒为全库。

    ``QuizEventPrinter`` 订阅事件流做 Rich 呈现（CLI = 事件脊柱的投影）。``responder`` 取消作答
    （``InteractiveResponder`` 抛 ``KeyboardInterrupt``）→ 优雅退出本次会话、仍打印已积累的薄弱点。
    某轮失败由 kernel ``RecoveryPolicy`` 统一裁决：``DEGRADED``（出题 / 判卷重试用尽）→ 跳过该轮
    继续下一轮；其余（``ReplayMiss`` 等 ``FATAL`` / 未知异常）→ 原样冒泡（绝不静默吞，保 eval /
    replay 契约）。裁决经异常自带的 ``error_class`` 标做出、并发 ``RECOVERY_DECIDED`` 上脊柱。
    ``rng`` 用可变种子（CLI 非 replay）：每轮 ``new_rng(seed + 轮次)``。

    ``prefer_lang``：非 None 时先显式把 ``question_language`` 偏好写进持久 SQLite（跨会话留存），
    再下传 Preference Memory 给 ``assess_once``——出题语言按 **偏好 > 中文** 解析。偏好台账**每次
    会话都构造并下传**（哪怕本次未设），故上次会话设过的语言偏好本次仍生效。

    **每次会话一个 ``trace_id``**（一个 ``EventEmitter`` 贯穿全部轮次，故 ``seq`` / span id 跨轮
    唯一、落库后是一条 trace、每轮一棵 assessment 根 span）；发射的 AgentEvent 流经
    ``EventSink.register`` 注册的 ``TraceStore`` 落进**独立 trace 库**（默认与 learning.db 同目录的
    ``trace.db``）——落 trace 纯经"注册 processor"实现，``assess_once`` 签名逻辑一行不改（可观测
    是脊柱投影、非业务耦合）。会话结束打印 ``trace_id`` + 库位置。
    """
    _ensure_parent(db_path)
    store, memory, preferences, asked_questions = build_learning_stores(db_path)
    if prefer_lang is not None:
        # 显式设置出题语言偏好（confidence 恒 1.0），跨会话留存、后续覆盖 task 默认语言。
        preferences.set_preference(QUESTION_LANGUAGE_KEY, prefer_lang)
    trace_store: TraceStore | None = None  # 空库分支不落 trace（无会话）；在 finally 里择机关闭
    try:
        # 全库预检（全局 KB，同 _run_quiz_cli 的预检口径）：库里有知识即放行。这是 _run_quiz_cli
        # 那道预检的内层同一逻辑，须同源切读。
        if not store.all_items():
            _print_needs_ingest(console, title)
            return

        resolved_trace_db = _resolve_trace_db(db_path, trace_db_path)
        _ensure_parent(resolved_trace_db)
        trace_id = uuid.uuid4().hex
        # 一个 emitter 贯穿全会话：跨轮共享 trace_id，seq / span id 单调唯一（不逐轮重置、不撞号）。
        emitter, trace_store = build_event_backbone(
            resolved_trace_db, trace_id=trace_id, subscribers=[QuizEventPrinter(console)]
        )
        policy = RecoveryPolicy(emitter)  # 每轮失败统一裁决（读异常 error_class 标、发事件上脊柱）
        # 会话内进程内"已问过"台账（item_id → 已问过的题目文本），跨轮累积、经 assess_once 下传出题
        # 函数做去重——复考同一薄弱概念时每轮换角度、不逐字重问。与 asked_questions（跨会话持久，
        # skeleton-ledger.md #8 已修）互补：前者管本会话覆盖优先选题，后者管跨会话去重记忆。
        recently_asked: dict[str, list[str]] = {}
        banner = f"「{title}」" if title else ""
        console.print(f"[bold]开始考核{banner}——共 {rounds} 轮（Ctrl+C 随时退出）[/]")
        try:
            for round_index in range(rounds):
                console.rule(f"第 {round_index + 1} / {rounds} 轮")
                try:
                    await assess_once(
                        store=store,
                        provider=provider,
                        responder=responder,
                        memory=memory,
                        emitter=emitter,
                        rng=new_rng(seed + round_index),
                        recently_asked=recently_asked,
                        asked_questions=asked_questions,
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

        _print_weak_summary(console, store, memory)
        _print_trace_location(console, trace_id, resolved_trace_db)
    finally:
        store.close()
        memory.close()
        preferences.close()
        asked_questions.close()
        if trace_store is not None:
            trace_store.close()


def _print_needs_ingest(console: Console, title: str | None = None) -> None:
    console.print(
        "[yellow]知识库还是空的。先运行 [bold]grandquiz ingest <材料文件>[/] 喂材料再来考核。[/]"
    )


def _print_weak_summary(
    console: Console,
    store: SqliteLearningStore,
    memory: SqliteLearningMemory,
) -> None:
    weak_ids = memory.weak_item_ids()
    if not weak_ids:
        console.print("[green]本次考核后没有遗留薄弱点，全部掌握。[/]")
        return
    # 全库读（同 _weak_concepts / _render_weak 口径）：薄弱 item 可能源自其他标题下 ingest 的知识，
    # 须全局解析概念名，否则退回裸 item_id 显示。
    concept_by_id = {item.item_id: item.concept for item in store.all_items()}
    console.print("[bold]薄弱点小结（已跨会话留存，下次优先考）：[/]")
    for item_id in sorted(weak_ids):
        state = memory.state_of(item_id)
        console.print(f"  · {concept_by_id.get(item_id, item_id)} — {state}")


async def _run_quiz_cli(
    *, title: str | None, rounds: int, db_path: Path, prefer_lang: str | None = None
) -> None:
    # 先查库（不构造 provider）：空库 / 错任务直接给指引——无需 LLM key，也免去无谓 HTTP 客户端。
    console = Console()
    _ensure_parent(db_path)
    store = SqliteLearningStore(db_path)
    try:
        # 全库预检（全局 KB）：只要库里有知识就放行——换标题也能考到此前 ingest 的知识（修 #2）。
        has_items = bool(store.all_items())
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
