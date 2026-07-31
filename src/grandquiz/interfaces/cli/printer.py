"""考核事件流的 Rich 呈现器——CLI 是事件脊柱的**消费者**（不另起渲染逻辑）。

呼应架构卖点："hook / trace / 流式输出 / eval replay 是同一条 AgentEvent 事件流的四个消费者"——
终端呈现就是事件流的一个投影。``QuizEventPrinter`` 订阅 ``EventSink``，按事件类型渲染：

- ``QUESTION_ASKED`` → ``Panel``（标题=题型，题干 + 选项列表）
- ``ANSWER_JUDGED`` → 判决着色（对=green / 勉强=yellow / 错=red）+ 回显作答
- ``FOLLOWUP_GIVEN`` → ``Panel``（标题="正解"）呈现 correct_answer
- ``CONCEPT_STATE_CHANGED`` → 一行状态转移
- ``DIFFICULTY_TIER_CHANGED`` → 一行难度跨档（"难度：3 → 4 档（原因）"，SE-S3 透明展示）

R1-S4 起还投影 ReAct 骨架的 kernel 级事件（``grandquiz react`` 的对话循环用）：

- ``AGENT_TURN_STARTED`` → 一行回显本回合用户消息（dim）
- ``TOOL_CALL_STARTED`` → 一行"调用工具 <name>"（dim）
- ``TOOL_CALL_ENDED`` → 仅失败时一行提示（成功不噪声，避免与领域事件重复）

只读 ``event.payload``（脊柱契约：consumer 视 payload 为只读），不认识的事件类型静默略过。

所有动态文本（作答 / LLM 题干选项 / 证据引文正解 / 用户消息 / 工具名）插入前一律
``rich.markup.escape``——真实内容常含 ``[...]`` 等 markup 元字符，未转义会让 Rich 抛
``MarkupError``。``EventSink.publish`` 现已隔离订阅者异常（一个坏订阅者不再炸整轮），但转义仍是本
消费者该做的正确防御：别把渲染搞乱、也别只靠隔离兜底。
"""

from typing import cast

from rich.console import Console
from rich.markup import escape
from rich.panel import Panel

from grandquiz.domain.learning.events import LearningEvent
from grandquiz.kernel.events import AgentEvent, EventType

# 判决三值 → 着色（与 grading.VerdictLabel 一致）；未知判决回退 white。
_VERDICT_STYLE: dict[str, str] = {"对": "green", "勉强": "yellow", "错": "red"}
# 需展示判官诊断（reason）的判决——错 / 勉强（对不展示"问题："，见 _render_verdict）。
_WEAK_VERDICTS: frozenset[str] = frozenset({"勉强", "错"})


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
        elif event.type == LearningEvent.DIFFICULTY_TIER_CHANGED:
            self._render_difficulty_change(event)
        elif event.type == EventType.AGENT_TURN_STARTED:
            self._render_agent_turn_started(event)
        elif event.type == EventType.TOOL_CALL_STARTED:
            self._render_tool_call_started(event)
        elif event.type == EventType.TOOL_CALL_ENDED:
            self._render_tool_call_ended(event)

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
        reason = str(event.payload.get("reason", ""))
        style = _VERDICT_STYLE.get(verdict, "white")
        self._console.print(f"[{style}]判决：{escape(verdict)}[/]（你的作答：{escape(answer)}）")
        # 判官诊断（reason）——只在错 / 勉强且有诊断时呈现"问题：…"，指出缺 / 偏了哪点（修 dogfood
        # 的"答错看不出问题所在"）；判"对"或无诊断（MC 代码判卷）不打此行。reason 是 LLM 动态文本、
        # 一律 escape。
        if verdict in _WEAK_VERDICTS and reason:
            self._console.print(f"  [dim]问题：{escape(reason)}[/]")
        for label, key in (("答到了", "matched_points"), ("还缺", "missing_points")):
            raw_points = event.payload.get(key)
            if not isinstance(raw_points, list):
                continue
            descriptions: list[str] = []
            for raw_point in cast("list[object]", raw_points):
                if not isinstance(raw_point, dict):
                    continue
                point = cast("dict[str, object]", raw_point)
                description = point.get("description")
                if isinstance(description, str):
                    descriptions.append(description)
            if descriptions:
                self._console.print(f"  [dim]{label}：{escape('；'.join(descriptions))}[/]")

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

    def _render_difficulty_change(self, event: AgentEvent) -> None:
        # SE-S3 透明展示：难度跨档一行呈现（照 _render_state_change 的一行式风格）。reason 由代码
        # 确定性产出、理论上不含 markup，但仍 escape（同本消费者对所有动态文本的防御约定）。
        from_tier = event.payload.get("from_tier", "?")
        to_tier = event.payload.get("to_tier", "?")
        reason = str(event.payload.get("reason", ""))
        self._console.print(f"  · 难度：{from_tier} → {to_tier} 档（{escape(reason)}）")

    def _render_agent_turn_started(self, event: AgentEvent) -> None:
        user_message = str(event.payload.get("user_message", ""))
        self._console.print(f"[dim]› {escape(user_message)}[/]")

    def _render_tool_call_started(self, event: AgentEvent) -> None:
        tool_name = str(event.payload.get("tool_name", "?"))
        self._console.print(f"[dim]· 调用工具 {escape(tool_name)}[/]")

    def _render_tool_call_ended(self, event: AgentEvent) -> None:
        # 成功不打印（避免与领域事件如 QUESTION_ASKED / ANSWER_JUDGED 重复）；失败给一行提示。
        if event.payload.get("ok"):
            return
        error = str(event.payload.get("error", ""))
        vetoed = event.payload.get("vetoed")
        label = "工具被安全门阻断" if vetoed else "工具调用失败"
        self._console.print(f"[yellow]{label}：{escape(error)}[/]")
