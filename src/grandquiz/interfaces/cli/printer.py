"""考核事件流的 Rich 呈现器——CLI 是事件脊柱的**消费者**（不另起渲染逻辑）。

呼应架构卖点："hook / trace / 流式输出 / eval replay 是同一条 AgentEvent 事件流的四个消费者"——
终端呈现就是事件流的一个投影。``QuizEventPrinter`` 订阅 ``EventSink``，按事件类型渲染：

- ``QUESTION_ASKED`` → ``Panel``（标题=题型，题干 + 选项列表）
- ``ANSWER_JUDGED`` → 判决着色（对=green / 勉强=yellow / 错=red）+ 回显作答
- ``FOLLOWUP_GIVEN`` → ``Panel``（标题="正解"）呈现 correct_answer
- ``CONCEPT_STATE_CHANGED`` → 一行状态转移

只读 ``event.payload``（脊柱契约：consumer 视 payload 为只读），不认识的事件类型静默略过。

所有动态文本（作答 / LLM 题干选项 / 证据引文正解）插入前一律 ``rich.markup.escape``——真实内容常
含 ``[...]`` 等 markup 元字符，未转义会让 Rich 抛 ``MarkupError``、经 EventSink 冒泡炸掉整轮考核
（EventSink 不隔离订阅者异常，那是 M4 HookManager 的职责）。
"""

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.kernel.events import AgentEvent

# 判决三值 → 着色（与 grading.VerdictLabel 一致）；未知判决回退 white。
_VERDICT_STYLE: dict[str, str] = {"对": "green", "勉强": "yellow", "错": "red"}


class QuizEventPrinter:
    """把考核事件渲染到给定 ``Console``——作为 ``EventSink`` 的订阅者（``__call__`` 收单个事件）。"""

    def __init__(self, console: Console) -> None:
        self._console = console

    def __call__(self, event: AgentEvent) -> None:
        if event.type == LearningEvent.QUESTION_ASKED:
            self._render_question(event)
        elif event.type == LearningEvent.ANSWER_JUDGED:
            self._render_verdict(event)
        elif event.type == LearningEvent.FOLLOWUP_GIVEN:
            self._render_followup(event)
        elif event.type == LearningEvent.CONCEPT_STATE_CHANGED:
            self._render_state_change(event)

    def _render_question(self, event: AgentEvent) -> None:
        payload = event.payload
        question_type = str(payload.get("question_type", "题目"))
        lines: list[str] = [escape(str(payload.get("question", "")))]
        options = payload.get("options")
        if options:
            lines.append("")
            for index, option in enumerate(options):
                lines.append(f"  {index + 1}. {escape(str(option))}")
        self._console.print(Panel("\n".join(lines), title=escape(question_type)))

    def _render_verdict(self, event: AgentEvent) -> None:
        verdict = str(event.payload.get("verdict", ""))
        answer = str(event.payload.get("answer", ""))
        style = _VERDICT_STYLE.get(verdict, "white")
        self._console.print(f"[{style}]判决：{escape(verdict)}[/]（你的作答：{escape(answer)}）")

    def _render_followup(self, event: AgentEvent) -> None:
        correct_answer = str(event.payload.get("correct_answer", ""))
        self._console.print(Panel(escape(correct_answer), title="正解"))

    def _render_state_change(self, event: AgentEvent) -> None:
        from_state = event.payload.get("from_state") or "未追踪"
        to_state = event.payload.get("to_state") or "未追踪"
        consecutive_correct = event.payload.get("consecutive_correct", 0)
        self._console.print(
            f"  · 概念状态：{from_state} → {to_state}（连对 {consecutive_correct}）"
        )
