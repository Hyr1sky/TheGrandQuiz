"""Canonical identity for the exact system configuration under evaluation."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Literal

from grandquiz.providers.base import Role

_SECRET_NAME_FRAGMENTS = (
    "api_key",
    "apikey",
    "authorization",
    "password",
    "refresh_token",
    "secret",
    "access_token",
)
_SECRET_VALUE_PREFIXES = ("bearer ", "sk-", "token=")


@dataclass(frozen=True)
class ProviderIdentity:
    """Role-specific Provider configuration without credentials."""

    role: Role
    provider: str
    model: str
    thinking: str


@dataclass(frozen=True)
class ReplayEvidence:
    """Execution evidence linked to, but excluded from, subject identity."""

    owner: str
    cassette: str
    sha256: str


@dataclass(frozen=True)
class EvalSubjectSnapshot:
    """Immutable definition and replay evidence for one evaluated subject."""

    schema_version: Literal["eval-subject.v1"]
    subject_id: str
    prompts: tuple[tuple[str, str], ...]
    providers: tuple[ProviderIdentity, ...]
    tool_schemas: tuple[tuple[str, str], ...]
    policies: tuple[tuple[str, str], ...]
    replay_evidence: tuple[ReplayEvidence, ...]


def _sorted_items(values: Mapping[str, str]) -> tuple[tuple[str, str], ...]:
    return tuple(sorted(values.items()))


def _reject_secret(name: str, value: str) -> None:
    lowered_name = name.casefold()
    lowered_value = value.strip().casefold()
    if any(fragment in lowered_name for fragment in _SECRET_NAME_FRAGMENTS) or any(
        lowered_value.startswith(prefix) for prefix in _SECRET_VALUE_PREFIXES
    ):
        raise ValueError(f"secret-bearing subject fact is forbidden: {name}")


def snapshot_subject(
    *,
    prompts: Mapping[str, str],
    providers: Sequence[ProviderIdentity],
    tool_schemas: Mapping[str, str],
    policies: Mapping[str, str],
    replay_evidence: Sequence[ReplayEvidence] = (),
) -> EvalSubjectSnapshot:
    """Canonicalize explicit facts; never inspect environment or process state."""

    for group in (prompts, tool_schemas, policies):
        for name, value in group.items():
            _reject_secret(name, value)
    for provider in providers:
        _reject_secret("provider", provider.provider)
        _reject_secret("model", provider.model)
        _reject_secret("thinking", provider.thinking)
    for evidence in replay_evidence:
        _reject_secret("replay_owner", evidence.owner)
        _reject_secret("cassette", evidence.cassette)

    prompt_items = _sorted_items(prompts)
    provider_items = tuple(
        sorted(
            providers,
            key=lambda item: (item.role, item.provider, item.model, item.thinking),
        )
    )
    tool_items = _sorted_items(tool_schemas)
    policy_items = _sorted_items(policies)
    canonical = json.dumps(
        {
            "schema_version": "eval-subject.v1",
            "prompts": prompt_items,
            "providers": [asdict(provider) for provider in provider_items],
            "tool_schemas": tool_items,
            "policies": policy_items,
        },
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )
    return EvalSubjectSnapshot(
        schema_version="eval-subject.v1",
        subject_id=hashlib.sha256(canonical.encode("utf-8")).hexdigest(),
        prompts=prompt_items,
        providers=provider_items,
        tool_schemas=tool_items,
        policies=policy_items,
        replay_evidence=tuple(
            sorted(
                replay_evidence,
                key=lambda item: (item.owner, item.cassette, item.sha256),
            )
        ),
    )
