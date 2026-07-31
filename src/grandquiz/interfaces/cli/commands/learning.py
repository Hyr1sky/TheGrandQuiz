"""CLI adapter for deterministic learning review exports."""

from pathlib import Path

from grandquiz.domain.learning.learning_export import export_learning_review


def run_learning_export_cli(*, db_path: Path, out_dir: Path) -> None:
    result = export_learning_review(db_path=db_path, out_dir=out_dir)
    print(result.out_dir / "manifest.json")
