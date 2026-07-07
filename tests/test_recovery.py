"""kernel RecoveryPolicy + ErrorClass 的确定性裁决（architecture step 6，M6）。

确定性核心走 TDD：
- 分类读 ``exc.error_class``、未带标 → FATAL（大声失败）。
- ``DEGRADED`` → ``SKIP``；其余（FATAL / RESOURCE_UNREADABLE / 未知）→ ``PROPAGATE``。
- ``ReplayMiss`` 必 ``PROPAGATE``、**绝不 ``SKIP``**（决策 6：eval / replay 契约不可破）——
  mutation：把它误标 DEGRADED，``test_replay_miss_propagates_never_skip`` 必红。
- ``decide`` 确定（无墙上时钟 / random，同一异常反复裁决同一 Decision）。
- 每次裁决发 ``RECOVERY_DECIDED`` 上脊柱，payload 含 error / error_class / decision。

kernel 领域无关：本模块禁止 import domain（``lint-imports`` 门），故不 ``isinstance(exc, ...)``——
分类只读异常自带的 ``error_class`` 标（domain / providers 各自打标，domain→kernel 合法方向）。
"""

from grandquiz.domain.learning.fetch import FetchError
from grandquiz.domain.learning.grading import GradingError
from grandquiz.domain.learning.question import QuestionError
from grandquiz.domain.learning.reader import ReaderError
from grandquiz.kernel.clock import ManualClock
from grandquiz.kernel.events import AgentEvent, EventEmitter, EventSink, EventType
from grandquiz.kernel.recovery import Decision, ErrorClass, RecoveryPolicy, classify
from grandquiz.providers.replay import ReplayMiss


def _policy_with_collector() -> tuple[RecoveryPolicy, list[AgentEvent]]:
    events: list[AgentEvent] = []
    sink = EventSink()
    sink.subscribe(events.append)
    emitter = EventEmitter(sink, ManualClock(), trace_id="rec-test")
    return RecoveryPolicy(emitter), events


# --- 分类（classify 纯函数）------------------------------------------------


def test_classify_untagged_defaults_fatal() -> None:
    # 未带 error_class 标的普通异常 → 默认 FATAL（大声失败，不静默降级）。
    assert classify(RuntimeError("no tag")) is ErrorClass.FATAL


def test_classify_replay_miss_is_fatal() -> None:
    assert classify(ReplayMiss("no cassette")) is ErrorClass.FATAL


def test_classify_question_and_grading_are_degraded() -> None:
    assert classify(QuestionError("boom")) is ErrorClass.DEGRADED
    assert classify(GradingError("boom")) is ErrorClass.DEGRADED


def test_classify_fetch_and_reader_are_resource_unreadable() -> None:
    assert classify(FetchError("x")) is ErrorClass.RESOURCE_UNREADABLE
    assert classify(ReaderError("x")) is ErrorClass.RESOURCE_UNREADABLE


# --- 裁决（RecoveryPolicy.decide）------------------------------------------


def test_replay_miss_propagates_never_skip() -> None:
    # 决策 6 命门：ReplayMiss（cassette 缺录 = harness bug）必冒泡，绝不静默跳过。
    # mutation：ReplayMiss 若被误标 DEGRADED → decide 返回 SKIP → 本测试红。
    policy, _ = _policy_with_collector()
    assert policy.decide(ReplayMiss("no cassette")) is Decision.PROPAGATE


def test_question_error_skips() -> None:
    policy, _ = _policy_with_collector()
    assert policy.decide(QuestionError("重试用尽")) is Decision.SKIP


def test_grading_error_skips() -> None:
    policy, _ = _policy_with_collector()
    assert policy.decide(GradingError("重试用尽")) is Decision.SKIP


def test_unknown_exception_propagates() -> None:
    # 未带标的未知异常 → FATAL → propagate（不吞不掩盖）。
    policy, _ = _policy_with_collector()
    assert policy.decide(RuntimeError("未知")) is Decision.PROPAGATE


def test_resource_unreadable_propagates() -> None:
    # RESOURCE_UNREADABLE 非 DEGRADED → 走默认 propagate（ingest 已在内部各自降级；
    # 此处只保证 policy 不把它当"本轮可跳过"静默吞）。
    policy, _ = _policy_with_collector()
    assert policy.decide(FetchError("域名不在白名单")) is Decision.PROPAGATE


def test_decide_is_deterministic() -> None:
    # 无墙上时钟 / random：同一异常反复裁决恒同一 Decision。
    policy, _ = _policy_with_collector()
    exc = QuestionError("same")
    assert [policy.decide(exc) for _ in range(5)] == [Decision.SKIP] * 5


# --- 事件上脊柱 ------------------------------------------------------------


def test_decide_emits_recovery_decided_on_spine() -> None:
    policy, events = _policy_with_collector()
    policy.decide(QuestionError("boom"))
    recovery = [e for e in events if e.type == EventType.RECOVERY_DECIDED]
    assert len(recovery) == 1
    payload = recovery[0].payload
    assert payload["error_class"] == ErrorClass.DEGRADED.value
    assert payload["decision"] == Decision.SKIP.value
    assert "boom" in str(payload["error"])


def test_decide_emits_for_propagated_error_too() -> None:
    # 即使裁决 propagate 也留痕（可观测 ≥ 旧的静默冒泡）。
    policy, events = _policy_with_collector()
    policy.decide(ReplayMiss("no cassette"))
    recovery = [e for e in events if e.type == EventType.RECOVERY_DECIDED]
    assert len(recovery) == 1
    assert recovery[0].payload["decision"] == Decision.PROPAGATE.value
    assert recovery[0].payload["error_class"] == ErrorClass.FATAL.value
