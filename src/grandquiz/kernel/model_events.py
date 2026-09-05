"""Shared payload constructors for provider-neutral model lifecycle events."""

from grandquiz.providers.failure import provider_failure_payload


def model_failure_event_payload(
    exc: BaseException,
    /,
    **context: object,
) -> dict[str, object]:
    """Build the stable ``model.ended(ok=False)`` payload.

    Callers may add operation-specific context such as ``node_id``. Runtime-owned
    outcome fields and allowlisted provider facts cannot be overridden by callers.
    """

    return {
        **context,
        "ok": False,
        "error": repr(exc),
        **provider_failure_payload(exc),
    }
