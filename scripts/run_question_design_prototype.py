#!/usr/bin/env python3
"""PROTOTYPE — inspect the question-design state before production integration.

Question: when Evidence is narrow, does making affordance/task/format explicit
produce a more honest design outcome than generating first and judging later?

Run the interactive prototype:
    uv run python scripts/run_question_design_prototype.py

Export a read-only owner-labelling intake from the real local knowledge base:
    uv run python scripts/run_question_design_prototype.py export \
      --db ~/.grandquiz/learning.db \
      --out .scratch/question-design-baseline/real-intake.jsonl
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any, cast

from grandquiz.evals.question_design_experiment import (
    EvidenceAffordanceV0,
    QuestionDesignCandidateV0,
    QuestionDesignSampleV0,
    RequestedChallengeV0,
    RequestedQuestionFormatV0,
    compile_question_design_dataset,
    plan_question_design,
    select_balanced_candidates,
)

_FORMATS: tuple[RequestedQuestionFormatV0, ...] = (
    "auto",
    "multiple_choice",
    "open_response",
)
_CHALLENGES: tuple[RequestedChallengeV0, ...] = (
    "foundation",
    "adaptive",
    "challenge",
)
_AFFORDANCES: tuple[EvidenceAffordanceV0, ...] = (
    "narrow_proposition",
    "definition_attributes",
    "rule_conditions",
    "process",
    "contrast_set",
    "insufficient",
)


def _worked_examples() -> list[QuestionDesignSampleV0]:
    rows: tuple[tuple[str, EvidenceAffordanceV0, str, str, str], ...] = (
        (
            "narrow",
            "narrow_proposition",
            "向量库与 Markdown 互补",
            "向量库和 Markdown 也不是二选一。",
            "学习者能准确解释两者并非绝对互斥",
        ),
        (
            "definition",
            "definition_attributes",
            "AgentEvent",
            "AgentEvent 是包含 type、元数据和不透明 payload 的事件信封。",
            "学习者能识别 AgentEvent 的定义性属性",
        ),
        (
            "rule",
            "rule_conditions",
            "审批门",
            "只有 eligible 且 approved 的候选才能进入 Acquisition。",
            "学习者能在给定状态下应用审批规则",
        ),
        (
            "process",
            "process",
            "薄弱状态销账",
            "答错进入薄弱；薄弱下答对进入观察中；观察中再答对才销账。",
            "学习者能诊断状态转移中的缺步或错误顺序",
        ),
        (
            "contrast",
            "contrast_set",
            "Trace 与 LearningFact",
            "Trace 保存完整运行审计；LearningFact 只保存白名单长期学习事实。",
            "学习者能区分完整运行审计与长期学习事实",
        ),
        (
            "insufficient",
            "insufficient",
            "材料外模型选型",
            "本段只说明事件信封结构。",
            "学习者能选择某厂商最合适的模型",
        ),
    )
    return [
        QuestionDesignSampleV0(
            sample_id=sample_id,
            resource_id="worked-example",
            item_id=f"worked-{sample_id}",
            concept=concept,
            summary=claim,
            evidence_quotes=(evidence,),
            evidence_affordance=affordance,
            assessment_claim=claim,
        )
        for sample_id, affordance, concept, evidence, claim in rows
    ]


def _next[T](values: tuple[T, ...], current: T) -> T:
    return values[(values.index(current) + 1) % len(values)]


def _render(sample: QuestionDesignSampleV0, index: int, total: int) -> None:
    target = plan_question_design(sample)
    print("\033[2J\033[H", end="")
    print("\033[1mQuestion Design Prototype\033[0m")
    print(f"\033[2m情景 {index + 1}/{total} · PROTOTYPE，不修改生产考核\033[0m\n")
    state = {
        "concept": sample.concept,
        "assessment_claim": sample.assessment_claim,
        "evidence": list(sample.evidence_quotes),
        "evidence_affordance": sample.evidence_affordance,
        "requested_format": sample.requested_format,
        "requested_challenge": sample.requested_challenge,
        "mode": target.mode,
        "cognitive_task": target.cognitive_task,
        "selected_format": target.selected_format,
        "option_count_range": target.option_count_range,
        "format_fit": target.format_fit,
        "outcome": target.outcome,
        "rationale": target.rationale,
    }
    for key, value in state.items():
        print(f"\033[1m{key}:\033[0m {json.dumps(value, ensure_ascii=False)}")
    print("\n\033[1m[n]\033[0m 下一情景  \033[1m[p]\033[0m 上一情景  ", end="")
    print("\033[1m[f]\033[0m 切换题型  \033[1m[c]\033[0m 切换挑战  \033[1m[q]\033[0m 退出")


def run_interactive() -> None:
    samples = _worked_examples()
    index = 0
    while True:
        sample = samples[index]
        _render(sample, index, len(samples))
        action = input("> ").strip().lower()
        if action == "q":
            return
        if action == "n":
            index = (index + 1) % len(samples)
        elif action == "p":
            index = (index - 1) % len(samples)
        elif action == "f":
            samples[index] = sample.model_copy(
                update={"requested_format": _next(_FORMATS, sample.requested_format)}
            )
        elif action == "c":
            samples[index] = sample.model_copy(
                update={"requested_challenge": _next(_CHALLENGES, sample.requested_challenge)}
            )


def _read_candidates(db_path: Path) -> list[QuestionDesignCandidateV0]:
    uri = f"file:{db_path.expanduser().resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as connection:
        connection.row_factory = sqlite3.Row
        rows = connection.execute(
            """
            SELECT r.resource_id,
                   COALESCE(r.topic, r.url) AS resource_label,
                   k.item_id, k.concept, k.summary,
                   e.ordinal, e.quote
            FROM resources AS r
            JOIN knowledge_items AS k ON k.resource_id = r.resource_id
            JOIN knowledge_item_evidence AS e ON e.item_id = k.item_id
            WHERE r.status = 'read' AND e.resolved = 1
            ORDER BY r.resource_id, k.item_id, e.ordinal
            """
        ).fetchall()
    grouped: dict[str, list[sqlite3.Row]] = defaultdict(list)
    for row in rows:
        grouped[cast(str, row["item_id"])].append(row)
    return [
        QuestionDesignCandidateV0(
            resource_id=cast(str, item_rows[0]["resource_id"]),
            resource_label=cast(str, item_rows[0]["resource_label"]),
            item_id=item_id,
            concept=cast(str, item_rows[0]["concept"]),
            summary=cast(str, item_rows[0]["summary"]),
            evidence_quotes=tuple(cast(str, row["quote"]) for row in item_rows),
        )
        for item_id, item_rows in sorted(grouped.items())
    ]


def export_intake(db_path: Path, out_path: Path, sample_count: int, min_resources: int) -> None:
    selected = select_balanced_candidates(
        _read_candidates(db_path),
        sample_count=sample_count,
        min_resources=min_resources,
    )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("w", encoding="utf-8") as stream:
        for ordinal, candidate in enumerate(selected, start=1):
            payload: dict[str, Any] = candidate.model_dump(mode="json")
            payload.update(
                {
                    "sample_id": f"real-{ordinal:02d}",
                    "evidence_extent_sampling_hint": candidate.evidence_extent,
                    "human_evidence_affordance": None,
                    "human_assessment_claim": None,
                    "requested_format": "auto",
                    "requested_challenge": "adaptive",
                    "include": None,
                    "review_note": None,
                }
            )
            stream.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
    print(f"已只读抽取 {len(selected)} 个候选：{out_path}")
    print("evidence_extent 仅用于平衡采样，不是人工 EvidenceAffordance 标签。")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    return [
        cast(dict[str, Any], json.loads(line))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")


def _render_review(row: dict[str, Any], index: int, total: int, out_path: Path) -> None:
    print("\033[2J\033[H", end="")
    print("\033[1mQuestion Design Owner Review\033[0m")
    print(f"\033[2m候选 {index + 1}/{total} · 自动保存到 {out_path}\033[0m\n")
    for key in (
        "resource_label",
        "concept",
        "summary",
        "evidence_quotes",
        "evidence_extent_sampling_hint",
        "human_evidence_affordance",
        "human_assessment_claim",
        "include",
        "review_note",
    ):
        print(f"\033[1m{key}:\033[0m {json.dumps(row.get(key), ensure_ascii=False)}")
    print("\nEvidenceAffordance：")
    for label_index, affordance in enumerate(_AFFORDANCES, start=1):
        print(f"  \033[1m[{label_index}]\033[0m {affordance}")
    print("\n\033[1m[e]\033[0m 编辑 Assessment Claim  ", end="")
    print("\033[1m[r]\033[0m 编辑备注  \033[1m[x]\033[0m 排除  ", end="")
    print("\033[1m[n/p]\033[0m 前后  \033[1m[q]\033[0m 保存退出")


def review_intake(input_path: Path, out_path: Path) -> None:
    rows = _load_jsonl(out_path if out_path.exists() else input_path)
    if not rows:
        raise ValueError("review intake 不能为空")
    index = 0
    while True:
        row = rows[index]
        _render_review(row, index, len(rows), out_path)
        action = input("> ").strip().lower()
        if action == "q":
            _write_jsonl(out_path, rows)
            print(f"已保存：{out_path}")
            return
        if action == "n":
            index = (index + 1) % len(rows)
        elif action == "p":
            index = (index - 1) % len(rows)
        elif action == "x":
            row["include"] = False
        elif action == "e":
            row["human_assessment_claim"] = input("Assessment Claim：").strip() or None
        elif action == "r":
            row["review_note"] = input("Review note：").strip() or None
        elif action.isdigit() and 1 <= int(action) <= len(_AFFORDANCES):
            row["human_evidence_affordance"] = _AFFORDANCES[int(action) - 1]
            row["include"] = True
        _write_jsonl(out_path, rows)


def compile_reviewed_dataset(input_path: Path, out_path: Path) -> None:
    reviewed = _load_jsonl(input_path)
    included = [row for row in reviewed if row.get("include") is True]
    incomplete = [
        cast(str, row.get("sample_id", "unknown"))
        for row in included
        if not row.get("human_evidence_affordance") or not row.get("human_assessment_claim")
    ]
    if incomplete:
        raise ValueError(f"以下 included 样本尚未完成 owner 标注：{incomplete}")
    samples = [
        QuestionDesignSampleV0(
            sample_id=cast(str, row["sample_id"]),
            resource_id=cast(str, row["resource_id"]),
            item_id=cast(str, row["item_id"]),
            concept=cast(str, row["concept"]),
            summary=cast(str, row["summary"]),
            evidence_quotes=tuple(cast(list[str], row["evidence_quotes"])),
            evidence_affordance=cast(EvidenceAffordanceV0, row["human_evidence_affordance"]),
            assessment_claim=cast(str, row["human_assessment_claim"]),
            requested_format=cast(RequestedQuestionFormatV0, row["requested_format"]),
            requested_challenge=cast(RequestedChallengeV0, row["requested_challenge"]),
        )
        for row in included
    ]
    dataset = compile_question_design_dataset(samples, min_samples=20, min_resources=3)
    payload = dataset.model_dump(mode="json")
    payload["candidate_targets"] = [
        plan_question_design(sample).model_dump(mode="json") for sample in dataset.samples
    ]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"已冻结 Dataset Snapshot：{dataset.dataset_id}")
    print(f"样本数：{len(dataset.samples)}；分层：{dataset.stratum_counts}")
    print(f"实验包：{out_path}")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command")
    export = subparsers.add_parser("export", help="从真实 learning.db 只读导出人工标注 intake")
    export.add_argument("--db", type=Path, required=True)
    export.add_argument("--out", type=Path, required=True)
    export.add_argument("--sample-count", type=int, default=24)
    export.add_argument("--min-resources", type=int, default=3)
    review = subparsers.add_parser(
        "review", help="逐条标注真实 Evidence affordance 与 Assessment Claim"
    )
    review.add_argument("--input", type=Path, required=True)
    review.add_argument("--out", type=Path, required=True)
    compile_command = subparsers.add_parser(
        "compile", help="冻结已完成 owner 标注的数据集与规划结果"
    )
    compile_command.add_argument("--input", type=Path, required=True)
    compile_command.add_argument("--out", type=Path, required=True)
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.command == "export":
        export_intake(args.db, args.out, args.sample_count, args.min_resources)
        return
    if args.command == "review":
        review_intake(args.input, args.out)
        return
    if args.command == "compile":
        compile_reviewed_dataset(args.input, args.out)
        return
    run_interactive()


if __name__ == "__main__":
    main()
