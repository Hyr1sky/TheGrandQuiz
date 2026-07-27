/**
 * Compact assessment progress panel for the sidebar.
 * Shows current round, total rounds, and per-round verdict status.
 */

import {
  CheckCircleIcon,
  CircleDashedIcon,
  CircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import type { AssessmentView } from "./api";
import "./assessment-progress.css";

export interface RoundRecord {
  roundIndex: number;
  verdict: string | null;
}

export interface AssessmentProgressProps {
  assessment: AssessmentView | null;
  history: RoundRecord[];
}

function verdictIcon(verdict: string | null) {
  if (verdict === null) {
    return <CircleDashedIcon aria-hidden size={14} />;
  }
  switch (verdict) {
    case "对":
      return <CheckCircleIcon aria-hidden size={14} />;
    case "勉强":
      return <CircleIcon aria-hidden size={14} />;
    case "错":
      return <XCircleIcon aria-hidden size={14} />;
    default:
      return <CircleIcon aria-hidden size={14} />;
  }
}

function verdictLabel(verdict: string | null): string {
  if (verdict === null) {
    return "未作答";
  }
  return verdict;
}

export function AssessmentProgress({
  assessment,
  history,
}: AssessmentProgressProps) {
  if (assessment === null) {
    return (
      <div className="assessment-progress" aria-label="考核进度">
        <p className="assessment-progress__empty">考核准备中...</p>
      </div>
    );
  }

  const currentRound = assessment.round_index;
  const totalRounds = assessment.rounds;

  return (
    <div className="assessment-progress" aria-label="考核进度">
      <p className="assessment-progress__summary">
        第 {currentRound} / {totalRounds} 题
      </p>
      <ul className="assessment-progress__list">
        {history.map((record) => (
          <li
            key={record.roundIndex}
            className={`assessment-progress__item${record.verdict !== null ? ` assessment-progress__item--${record.verdict}` : ""}`}
            aria-current={
              record.roundIndex === currentRound ? "step" : undefined
            }
          >
            {verdictIcon(record.verdict)}
            <span className="assessment-progress__round">
              第 {record.roundIndex} 题
            </span>
            <span className="assessment-progress__verdict">
              {verdictLabel(record.verdict)}
            </span>
          </li>
        ))}
      </ul>
    </div>
  );
}
