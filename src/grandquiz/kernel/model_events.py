"""Shared payload constructors for provider-neutral model lifecycle events."""

from grandquiz.providers.base import Model
from grandquiz.providers.failure import provider_failure_payload
from grandquiz.providers.models import identity_of


def model_identity_event_payload(model: Model) -> dict[str, object]:
    """Execution facts belong to the bound call, never to current settings at export time."""
    identity = identity_of(model)
    return {} if identity is None else {"model_identity": identity.model_dump(mode="json")}


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
