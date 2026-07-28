/**
 * Embedded assessment panel for the main content area.
 * Driven by navigation tool events from the chat panel.
 * Keeps AssessmentWorkspace.tsx untouched (its tests still pass separately).
 */

import {
  ArrowRightIcon,
  CheckCircleIcon,
  EyeIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import {
  useCallback,
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  getAssessment,
  nextRound,
  revealEvidence,
  startAssessment,
  submitAnswer,
  type AssessmentView,
} from "./api";
import "./assessment-panel.css";

interface AssessmentPanelProps {
  resourceId: string;
  rounds: number;
  questionType: string | null;
  onClose: () => void;
  onUpdate?: (view: AssessmentView) => void;
}

function commandId(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
}

export function AssessmentPanel({
  resourceId,
  rounds,
  questionType,
  onClose,
  onUpdate,
}: AssessmentPanelProps) {
  const [assessment, setAssessment] = useState<AssessmentView | null>(null);
  const [answer, setAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const revealRequested = useRef<string | null>(null);
  const answerCommand = useRef<{
    questionId: string;
    answer: string;
    requestId: string;
  } | null>(null);
  const nextCommand = useRef<{ roundIndex: number; requestId: string } | null>(
    null,
  );
  const startRequest = useRef<{
    key: string;
    promise: Promise<AssessmentView>;
  } | null>(null);

  // Notify parent of assessment state changes
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const notifyUpdate = useCallback((view: AssessmentView) => {
    setAssessment(view);
    onUpdateRef.current?.(view);
  }, []);

  // Start assessment on mount
  useEffect(() => {
    const key = `${resourceId}\u0000${rounds}\u0000${questionType ?? ""}`;
    let request = startRequest.current;
    if (request?.key !== key) {
      request = {
        key,
        promise: startAssessment(resourceId, rounds, questionType ?? ""),
      };
      startRequest.current = request;
    }
    let active = true;
    void (async () => {
      setBusy(true);
      try {
        const result = await request.promise;
        if (active) {
          notifyUpdate(result);
        }
      } catch (reason) {
        if (active) {
          setError(
            reason instanceof Error ? reason.message : "无法开始考核",
          );
        }
      } finally {
        if (active) {
          setBusy(false);
        }
      }
    })();
    return () => {
      active = false;
    };
  }, [resourceId, rounds, questionType]);

  // Poll for status changes
  const pollDelay = useRef(1000);
  useEffect(() => {
    if (
      assessment === null ||
      !["preparing", "grading"].includes(assessment.status)
    ) {
      pollDelay.current = 1000;
      return;
    }
    let active = true;
    const delay = pollDelay.current;
    const timer = window.setTimeout(() => {
      void getAssessment(assessment.session_id)
        .then((next) => {
          if (active) {
            pollDelay.current = Math.min(delay * 2, 4000);
            notifyUpdate(next);
          }
        })
        .catch((reason: unknown) => {
          if (active) {
            setError(
              reason instanceof Error
                ? reason.message
                : "无法刷新考核状态",
            );
          }
        });
    }, delay);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [assessment]);

  const reveal = async (interaction: "hover" | "click" | "keyboard") => {
    const question = assessment?.question;
    if (
      assessment === null ||
      question == null ||
      question.evidence_revealed ||
      revealRequested.current === question.question_id
    ) {
      return;
    }
    revealRequested.current = question.question_id;
    try {
      notifyUpdate(
        await revealEvidence(
          assessment.session_id,
          question.question_id,
          interaction,
        ),
      );
    } catch (reason) {
      revealRequested.current = null;
      setError(reason instanceof Error ? reason.message : "无法揭示证据");
    }
  };

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    const question = assessment?.question;
    if (assessment === null || question == null || answer.trim() === "") {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const existing = answerCommand.current;
      const command =
        existing?.questionId === question.question_id &&
        existing.answer === answer.trim()
          ? existing
          : {
              questionId: question.question_id,
              answer: answer.trim(),
              requestId: commandId("answer"),
            };
      answerCommand.current = command;
      notifyUpdate(
        await submitAnswer(
          assessment.session_id,
          question.question_id,
          answer.trim(),
          command.requestId,
        ),
      );
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法提交答案");
    } finally {
      setBusy(false);
    }
  };

  const advance = async () => {
    if (assessment === null) {
      return;
    }
    setBusy(true);
    try {
      const existing = nextCommand.current;
      const command =
        existing?.roundIndex === assessment.round_index
          ? existing
          : {
              roundIndex: assessment.round_index,
              requestId: commandId("next"),
            };
      nextCommand.current = command;
      const next = await nextRound(
        assessment.session_id,
        command.requestId,
      );
      setAnswer("");
      revealRequested.current = null;
      answerCommand.current = null;
      notifyUpdate(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法进入下一题");
    } finally {
      setBusy(false);
    }
  };

  // Loading / error state before first question
  if (assessment === null) {
    return (
      <section className="assessment-panel" aria-label="考核面板">
        <header className="assessment-panel__header">
          <h2>考核准备中</h2>
          <button
            type="button"
            className="assessment-panel__close"
            aria-label="结束考核"
            onClick={onClose}
          >
            <XCircleIcon aria-hidden size={19} />
            结束考核
          </button>
        </header>
        {busy ? (
          <p className="assessment-panel__status" role="status">
            正在从材料与薄弱状态生成第一题...
          </p>
        ) : null}
        {error !== null ? (
          <p className="assessment-panel__error" role="alert">
            {error}
          </p>
        ) : null}
      </section>
    );
  }

  // Refused / failed
  if (assessment.status === "refused" || assessment.status === "failed") {
    return (
      <section className="assessment-panel" aria-label="考核面板">
        <header className="assessment-panel__header">
          <h2>无法开始考核</h2>
          <button
            type="button"
            className="assessment-panel__close"
            aria-label="结束考核"
            onClick={onClose}
          >
            <XCircleIcon aria-hidden size={19} />
            返回阅读
          </button>
        </header>
        <p className="assessment-panel__error" role="alert">
          {assessment.error ?? "当前材料暂时无法开始考核。"}
        </p>
      </section>
    );
  }

  // Question view
  if (assessment.question) {
    const question = assessment.question;
    const waiting = assessment.status === "awaiting_answer";
    const judged =
      assessment.status === "judged" || assessment.status === "completed";

    return (
      <section className="assessment-panel" aria-label="考核面板">
        <header className="assessment-panel__header">
          <p>
            第 {assessment.round_index} / {assessment.rounds} 题
            <span className="assessment-panel__type">
              {question.question_type}
            </span>
          </p>
          <button
            type="button"
            className="assessment-panel__close"
            aria-label="结束考核"
            onClick={onClose}
          >
            <XCircleIcon aria-hidden size={19} />
            结束考核
          </button>
        </header>

        <h2 className="assessment-panel__question">{question.text}</h2>

        <form className="assessment-panel__answer" onSubmit={submit}>
          {question.options.length > 0 ? (
            <fieldset disabled={!waiting || busy}>
              <legend>选择一个答案</legend>
              {question.options.map((option) => (
                <label key={option} className="assessment-panel__option">
                  <input
                    type="radio"
                    name="panel-answer"
                    value={option}
                    checked={answer === option}
                    onChange={(event) => setAnswer(event.target.value)}
                  />
                  <span>{option}</span>
                </label>
              ))}
            </fieldset>
          ) : (
            <label className="assessment-panel__open">
              <span>你的回答</span>
              <textarea
                value={answer}
                disabled={!waiting || busy}
                onChange={(event) => setAnswer(event.target.value)}
                placeholder="先给出自己的理解..."
              />
            </label>
          )}

          <section
            className="assessment-panel__evidence"
            aria-label="本题材料证据"
          >
            <button
              type="button"
              aria-label="揭示本题材料证据"
              aria-expanded={question.evidence_revealed}
              onPointerEnter={() => void reveal("hover")}
              onFocus={() => void reveal("keyboard")}
              onClick={() => void reveal("click")}
            >
              <EyeIcon aria-hidden size={19} />
              {question.evidence_revealed
                ? "材料证据已揭示"
                : "想不起来？悬停或点击查看材料"}
            </button>
            {question.evidence_revealed ? (
              <blockquote>
                {question.evidence.map((quote, index) => (
                  <p key={index}>{quote}</p>
                ))}
              </blockquote>
            ) : (
              <div className="assessment-panel__veil" aria-hidden>
                Evidence hidden
              </div>
            )}
          </section>

          {waiting ? (
            <button
              className="assessment-panel__submit"
              type="submit"
              disabled={answer.trim() === "" || busy}
            >
              提交答案
              <ArrowRightIcon aria-hidden size={19} />
            </button>
          ) : null}
        </form>

        {assessment.status === "grading" ? (
          <p className="assessment-panel__status" role="status">
            正在依据原文判卷并更新薄弱状态...
          </p>
        ) : null}

        {judged && assessment.judgement ? (
          <section
            className="assessment-panel__judgement"
            aria-label="本题判决"
          >
            <p
              className={`assessment-panel__verdict assessment-panel__verdict--${assessment.judgement.verdict}`}
            >
              判断：{assessment.judgement.verdict}
            </p>
            {assessment.judgement.reason ? (
              <p>{assessment.judgement.reason}</p>
            ) : null}
            <p>
              概念状态：
              {assessment.judgement.concept_state ?? "未追踪"}
            </p>
            {assessment.judgement.correct_answer ? (
              <details>
                <summary>查看参考答案</summary>
                <p>{assessment.judgement.correct_answer}</p>
              </details>
            ) : null}
            {assessment.status === "judged" ? (
              <button
                type="button"
                disabled={busy}
                onClick={() => void advance()}
              >
                下一题
                <ArrowRightIcon aria-hidden size={19} />
              </button>
            ) : (
              <div className="assessment-panel__complete">
                <p className="assessment-panel__done">
                  <CheckCircleIcon aria-hidden size={20} />
                  本轮完成
                </p>
                <button type="button" onClick={onClose}>
                  返回阅读
                </button>
              </div>
            )}
          </section>
        ) : null}

        {error !== null ? (
          <p className="assessment-panel__error" role="alert">
            {error}
          </p>
        ) : null}

        <footer className="assessment-panel__trace">
          <code>trace_id: {assessment.trace_id}</code>
        </footer>
      </section>
    );
  }

  // Preparing state (no question yet)
  return (
    <section className="assessment-panel" aria-label="考核面板">
      <header className="assessment-panel__header">
        <h2>考核准备中</h2>
        <button
          type="button"
          className="assessment-panel__close"
          aria-label="结束考核"
          onClick={onClose}
        >
          <XCircleIcon aria-hidden size={19} />
          结束考核
        </button>
      </header>
      <p className="assessment-panel__status" role="status">
        正在从材料与薄弱状态生成第一题...
      </p>
    </section>
  );
}
