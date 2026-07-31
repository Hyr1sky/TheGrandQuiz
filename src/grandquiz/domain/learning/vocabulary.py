"""Versioned controlled vocabulary loaded from the repository's single YAML truth."""

from functools import lru_cache
from pathlib import Path
from typing import Any, cast

import yaml
from pydantic import BaseModel, model_validator


class ClosedValue(BaseModel):
    key: str
    label_zh: str
    definition: str | None = None
    use_when: str | None = None
    avoid_when: str | None = None


class ManagedTerm(BaseModel):
    term_id: str
    namespace: str
    key: str
    label_zh: str
    aliases: tuple[str, ...] = ()
    status: str


class VocabularyCatalog(BaseModel):
    schema_version: str
    status: str
    closed_dimensions: dict[str, tuple[ClosedValue, ...]]
    default_orientation_by_kind: dict[str, tuple[str, ...]]
    managed_seed_terms: tuple[ManagedTerm, ...]
    _term_lookup: dict[str, ManagedTerm] = {}

    @model_validator(mode="after")
    def _validate_and_index(self) -> "VocabularyCatalog":
        lookup: dict[str, ManagedTerm] = {}
        for term in self.managed_seed_terms:
            for value in (term.term_id, term.key, *term.aliases):
                normalized = _normalize(value)
                previous = lookup.get(normalized)
                if previous is not None and previous.term_id != term.term_id:
                    raise ValueError(
                        f"managed vocabulary alias collision: {value} "
                        f"({previous.term_id}, {term.term_id})"
                    )
                lookup[normalized] = term
        object.__setattr__(self, "_term_lookup", lookup)
        return self

    def keys(self, dimension: str) -> frozenset[str]:
        return frozenset(value.key for value in self.closed_dimensions.get(dimension, ()))

    def default_orientations(self, knowledge_kind: str) -> frozenset[str]:
        return frozenset(self.default_orientation_by_kind.get(knowledge_kind, ()))

    def resolve_managed_term(self, value: str) -> ManagedTerm | None:
        return self._term_lookup.get(_normalize(value))


def _normalize(value: str) -> str:
    return value.strip().casefold().replace("-", "_").replace(" ", "_")


def _default_vocabulary_path() -> Path:
    packaged = Path(__file__).with_name("vocabulary.v1.yaml")
    if packaged.exists():
        return packaged
    repository = Path(__file__).parents[4] / "docs" / "vocabulary.v1.yaml"
    if repository.exists():
        return repository
    raise FileNotFoundError("vocabulary.v1.yaml is missing from package and repository")


@lru_cache(maxsize=4)
def load_vocabulary(path: Path | None = None) -> VocabularyCatalog:
    source = path or _default_vocabulary_path()
    raw_object: object = yaml.safe_load(source.read_text(encoding="utf-8"))
    if not isinstance(raw_object, dict):
        raise ValueError("vocabulary root must be a mapping")
    data = dict(cast("dict[str, object]", raw_object))
    managed: list[dict[str, Any]] = []
    entries = data.get("managed_seed_terms", [])
    if not isinstance(entries, list):
        raise ValueError("managed_seed_terms must be a list")
    for entry_object in cast("list[object]", entries):
        if not isinstance(entry_object, dict):
            raise ValueError("managed vocabulary term must be a mapping")
        item = dict(cast("dict[str, Any]", entry_object))
        item["term_id"] = f"{item['namespace']}:{item['key']}"
        managed.append(item)
    data["managed_seed_terms"] = managed
    return VocabularyCatalog.model_validate(data)
