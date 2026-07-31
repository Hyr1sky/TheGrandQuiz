"""Deliver committed learning facts back onto the operational event spine."""

from grandquiz.domain.learning.learning_facts import LearningFactJournal
from grandquiz.kernel.clock import Clock, SystemClock
from grandquiz.kernel.events import EventEmitter, EventSink
from grandquiz.kernel.trace import TraceStore


def publish_pending_learning_facts(
    journal: LearningFactJournal,
    trace_store: TraceStore,
    *,
    clock: Clock | None = None,
) -> int:
    """Idempotently recover outbox delivery after a process interruption."""

    published = 0
    for fact in journal.pending():
        existing = trace_store.events(fact.trace_id)
        already_present = any(
            event.type == fact.event_type and event.payload.get("event_id") == fact.event_id
            for event in existing
        )
        if not already_present:
            next_seq = max((event.seq for event in existing), default=-1) + 1
            span_numbers = [
                int(span_id.rsplit(":s", 1)[1])
                for event in existing
                if (span_id := event.span_id) is not None
                and span_id.startswith(f"{fact.trace_id}:s")
                and span_id.rsplit(":s", 1)[1].isdigit()
            ]
            sink = EventSink()
            sink.register_durable(trace_store)
            emitter = EventEmitter(
                sink,
                clock or SystemClock(),
                trace_id=fact.trace_id,
                initial_seq=next_seq,
                initial_span_counter=max(span_numbers, default=-1) + 1,
            )
            emitter.emit(fact.event_type, payload=fact.model_dump(mode="json"))
        journal.mark_published(fact.event_id)
        published += 1
    return published
