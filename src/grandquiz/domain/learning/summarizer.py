"""历史摘要——context compression 增量 3：kernel ``Summarizer`` 协议的真 LLM 实现。

与 ``grading.py``/``question.py`` 的判卷/出题槽同源（同一套"LLM 只产内容、代码决定何时调用"
纪律），但**没有结构化输出契约与校验门**：摘要是自由文本、非机器解析的 JSON，PRD 定性为
"轻量 LLM 槽"——不值得上重试 + pydantic 校验那一整套。调用失败原样冒泡：
``kernel.context.SummarizingHistoryCompressor.prune`` 的调用方（``Runner._drain_pending_prune``）
已把这类失败当"非关键后台维护"隔离（发 ``ERROR`` 事件、不炸 turn），本模块无需重复兜底。
"""

from collections.abc import Sequence

from grandquiz.domain.learning.prompts import load_prompt
from grandquiz.kernel.events import EventEmitter, EventType
from grandquiz.providers.base import Completion, Message, Provider


class LLMSummarizer:
    """真 LLM 折叠老轮进滚动摘要（kernel ``Summarizer`` 协议，role=basic）。

    每次 ``summarize`` 调用自成一个根 span（``parent_span_id=None``）：调用发生在
    ``Runner._drain_pending_prune`` 里，跨越"上一轮"与"这一轮"之间，不天然从属于任何单个
    ``AGENT_TURN`` span，故不强行挂靠、老实当一条独立的后台维护 span 进 trace（同
    ``Runner`` 里 prune 失败 ``ERROR`` 事件的处理哲学一致）。
    """

    def __init__(self, provider: Provider, emitter: EventEmitter) -> None:
        self._provider = provider
        self._emitter = emitter

    async def summarize(self, prior_summary: str, messages: Sequence[Message]) -> str:
        prompt = load_prompt("summarize")
        rendered = "\n".join(f"{m.role}：{m.content}" for m in messages)
        call_messages = [
            Message(role="system", content=prompt.text),
            Message(
                role="user",
                content=f"此前摘要：{prior_summary or '（无）'}\n\n新增对话轮次：\n{rendered}",
            ),
        ]
        completion = await self._call_model(call_messages, prompt_version=prompt.version)
        return completion.text.strip()

    async def _call_model(self, messages: list[Message], *, prompt_version: str) -> Completion:
        # 照 grading._call_model 的一对 MODEL_STARTED/MODEL_ENDED 共享 span_id 模式，
        # 只是这里 parent_span_id 恒为 None（见类 docstring）。
        span_id = self._emitter.new_span_id()
        self._emitter.emit(
            EventType.MODEL_STARTED,
            span_id=span_id,
            payload={
                "messages": [m.model_dump() for m in messages],
                "prompt_version": prompt_version,
                "role": "basic",
            },
        )
        try:
            completion = await self._provider.complete(messages, role="basic")
        except Exception as exc:
            self._emitter.emit(
                EventType.MODEL_ENDED,
                span_id=span_id,
                payload={"ok": False, "error": repr(exc)},
            )
            raise
        self._emitter.emit(
            EventType.MODEL_ENDED,
            span_id=span_id,
            payload={"ok": True, "output": completion.text, "usage": completion.usage.model_dump()},
        )
        return completion
