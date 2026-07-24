"""Deterministically export the local API contract without opening production state."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from pathlib import Path

from grandquiz.interfaces.api.app import ApiSettings, create_app
from grandquiz.providers.base import Completion, Message, Role, ToolSpec


class _ContractProvider:
    async def complete(
        self,
        messages: Sequence[Message],
        *,
        role: Role = "basic",
        tools: Sequence[ToolSpec] | None = None,
    ) -> Completion:
        raise AssertionError("OpenAPI export must not call the provider")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    app = create_app(
        settings=ApiSettings(
            learning_db_path=Path("/tmp/grandquiz-openapi-learning.db"),
            trace_db_path=Path("/tmp/grandquiz-openapi-trace.db"),
        ),
        provider=_ContractProvider(),
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(app.openapi(), ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
