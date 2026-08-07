/**
 * Embedded assessment panel for the main content area.
 * Driven by navigation tool events from the chat panel.
 * Keeps AssessmentWorkspace.tsx untouched (its tests still pass separately).
 */

import {
  ArrowRightIcon,
  ChatCircleDotsIcon,
  CheckCircleIcon,
  EyeIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  cancelAssessment,
  getAssessment,
  nextRound,
  revealEvidence,
  startAssessment,
  submitAppeal,
  submitAnswer,
  type AssessmentView,
} from "./api";
import "./assessment-panel.css";

interface AssessmentPanelProps {
  resourceId: string;
  questionTypePlan: Array<string | null>;
  onClose: () => void;
  onUpdate?: (view: AssessmentView) => void;
}

export interface AssessmentPanelHandle {
  cancel: () => Promise<boolean>;
}

function commandId(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
}

export const AssessmentPanel = forwardRef<
  AssessmentPanelHandle,
  AssessmentPanelProps
>(function AssessmentPanel(
  { resourceId, questionTypePlan, onClose, onUpdate },
  ref,
) {
  const [assessment, setAssessment] = useState<AssessmentView | null>(null);
  const assessmentRef = useRef<AssessmentView | null>(null);
  const [answer, setAnswer] = useState("");
  const [appealOpen, setAppealOpen] = useState(false);
  const [supplementalAnswer, setSupplementalAnswer] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [hoverCountdown, setHoverCountdown] = useState<number | null>(null);
  const revealRequested = useRef<string | null>(null);
  const hoverRevealTimer = useRef<number | null>(null);
  const hoverCountdownTimer = useRef<number | null>(null);
  const answerCommand = useRef<{
    questionId: string;
    answer: string;
    requestId: string;
  } | null>(null);
  const nextCommand = useRef<{ roundIndex: number; requestId: string } | null>(
    null,
  );
  const appealCommand = useRef<{
    questionId: string;
    supplementalAnswer: string;
    requestId: string;
  } | null>(null);
  const startRequest = useRef<{
    key: string;
    promise: Promise<AssessmentView>;
  } | null>(null);
  const closeRequested = useRef(false);

  // Notify parent of assessment state changes
  const onUpdateRef = useRef(onUpdate);
  onUpdateRef.current = onUpdate;
  const notifyUpdate = useCallback((view: AssessmentView) => {
    assessmentRef.current = view;
    setAssessment(view);
    onUpdateRef.current?.(view);
  }, []);

  // Start assessment on mount
  useEffect(() => {
    const key = `${resourceId}\u0000${JSON.stringify(questionTypePlan)}`;
    let request = startRequest.current;
    if (request?.key !== key) {
      request = {
        key,
        promise: startAssessment(resourceId, { questionTypePlan }),
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
  }, [resourceId, questionTypePlan, notifyUpdate]);

  // Poll for status changes
  const pollDelay = useRef(1000);
  useEffect(() => {
    if (
      assessment === null ||
      (!["preparing", "grading"].includes(assessment.status) &&
        assessment.appeal?.status !== "grading")
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
  }, [assessment, notifyUpdate]);

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

  const cancelHoverReveal = useCallback(() => {
    if (hoverRevealTimer.current !== null) {
      window.clearTimeout(hoverRevealTimer.current);
      hoverRevealTimer.current = null;
    }
    if (hoverCountdownTimer.current !== null) {
      window.clearInterval(hoverCountdownTimer.current);
      hoverCountdownTimer.current = null;
    }
    setHoverCountdown(null);
  }, []);

  const beginHoverReveal = () => {
    const question = assessment?.question;
    if (
      question == null ||
      question.evidence_revealed ||
      revealRequested.current === question.question_id ||
      hoverRevealTimer.current !== null
    ) {
      return;
    }
    setHoverCountdown(3);
    hoverCountdownTimer.current = window.setInterval(() => {
      setHoverCountdown((current) =>
        current === null ? null : Math.max(1, current - 1),
      );
    }, 1000);
    hoverRevealTimer.current = window.setTimeout(() => {
      if (hoverCountdownTimer.current !== null) {
        window.clearInterval(hoverCountdownTimer.current);
        hoverCountdownTimer.current = null;
      }
      hoverRevealTimer.current = null;
      setHoverCountdown(null);
      void reveal("hover");
    }, 3000);
  };

  useEffect(() => {
    return () => {
      if (hoverRevealTimer.current !== null) {
        window.clearTimeout(hoverRevealTimer.current);
      }
      if (hoverCountdownTimer.current !== null) {
        window.clearInterval(hoverCountdownTimer.current);
      }
    };
  }, []);

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
      setAppealOpen(false);
      setSupplementalAnswer("");
      revealRequested.current = null;
      answerCommand.current = null;
      appealCommand.current = null;
      notifyUpdate(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法进入下一题");
    } finally {
      setBusy(false);
    }
  };

  const submitSupplement = async (event?: FormEvent) => {
    event?.preventDefault();
    const question = assessment?.question;
    const supplement = supplementalAnswer.trim();
    if (assessment === null || question == null || supplement === "") {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const existing = appealCommand.current;
      const command =
        existing?.questionId === question.question_id &&
        existing.supplementalAnswer === supplement
          ? existing
          : {
              questionId: question.question_id,
              supplementalAnswer: supplement,
              requestId: commandId("appeal"),
            };
      appealCommand.current = command;
      notifyUpdate(
        await submitAppeal(
          assessment.session_id,
          question.question_id,
          supplement,
          command.requestId,
        ),
      );
      setAppealOpen(false);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法提交补充说明");
    } finally {
      setBusy(false);
    }
  };

  const cancelCurrent = useCallback(async (): Promise<boolean> => {
    let current = assessmentRef.current;
    if (current === null && startRequest.current !== null) {
      try {
        current = await startRequest.current.promise;
      } catch {
        return true;
      }
    }
    if (
      current !== null &&
      (current.appeal?.status === "grading" ||
        !["completed", "refused", "failed", "cancelled"].includes(
          current.status,
        ))
    ) {
      try {
        notifyUpdate(await cancelAssessment(current.session_id));
      } catch (reason) {
        setError(
          reason instanceof Error ? reason.message : "无法结束考核",
        );
        return false;
      }
    }
    return true;
  }, [notifyUpdate]);

  useImperativeHandle(ref, () => ({ cancel: cancelCurrent }), [cancelCurrent]);

  const close = async () => {
    if (closeRequested.current) {
      return;
    }
    closeRequested.current = true;
    if (!(await cancelCurrent())) {
      closeRequested.current = false;
      return;
    }
    onClose();
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
            onClick={() => void close()}
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
            onClick={() => void close()}
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
    const matchedPoints = assessment.judgement?.matched_points ?? [];
    const missingPoints = assessment.judgement?.missing_points ?? [];

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
            onClick={() => void close()}
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
              onPointerEnter={beginHoverReveal}
              onPointerLeave={cancelHoverReveal}
              onKeyDown={(event) => {
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  cancelHoverReveal();
                  void reveal("keyboard");
                }
              }}
              onClick={() => {
                cancelHoverReveal();
                void reveal("click");
              }}
            >
              <EyeIcon aria-hidden size={19} />
              {question.evidence_revealed
                ? "材料证据已揭示"
                : hoverCountdown === null
                  ? "想不起来？悬停 3 秒或点击查看材料"
                  : `继续悬停 ${hoverCountdown} 秒查看材料`}
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
            {matchedPoints.length > 0 || missingPoints.length > 0 ? (
              <div className="assessment-panel__point-feedback">
                {matchedPoints.length > 0 ? (
                  <section>
                    <h3>答到了</h3>
                    <ul>
                      {matchedPoints.map((point) => (
                        <li key={point.point_id}>{point.description}</li>
                      ))}
                    </ul>
                  </section>
                ) : null}
                {missingPoints.length > 0 ? (
                  <section>
                    <h3>还缺</h3>
                    <ul>
                      {missingPoints.map((point) => (
                        <li key={point.point_id}>{point.description}</li>
                      ))}
                    </ul>
                  </section>
                ) : null}
              </div>
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
            {assessment.appeal?.status === "available" ? (
              appealOpen ? (
                <form
                  className="assessment-panel__appeal"
                  onSubmit={submitSupplement}
                >
                  <label>
                    <span>补充说明</span>
                    <textarea
                      aria-label="补充说明"
                      value={supplementalAnswer}
                      disabled={busy}
                      onChange={(event) =>
                        setSupplementalAnswer(event.target.value)
                      }
                      placeholder="只补充你认为原判遗漏的表达；原回答会完整保留。"
                    />
                  </label>
                  <div>
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => setAppealOpen(false)}
                    >
                      取消
                    </button>
                    <button
                      type="submit"
                      disabled={busy || supplementalAnswer.trim() === ""}
                    >
                      提交补充并重判
                    </button>
                  </div>
                </form>
              ) : (
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => setAppealOpen(true)}
                >
                  <ChatCircleDotsIcon aria-hidden size={19} />
                  补充说明 / 判卷有异议
                </button>
              )
            ) : null}
            {assessment.appeal?.status === "grading" ? (
              <p className="assessment-panel__appeal-status" role="status">
                正在结合原回答与补充说明重新判卷...
              </p>
            ) : null}
            {assessment.appeal?.status === "resolved" ? (
              <p className="assessment-panel__appeal-result">
                原判：{assessment.appeal.original_verdict}；重判：
                {assessment.appeal.final_verdict}
              </p>
            ) : null}
            {assessment.appeal?.status === "failed" ? (
              <div>
                <p className="assessment-panel__error" role="alert">
                  {assessment.appeal.reason}
                </p>
                <button
                  type="button"
                  disabled={busy}
                  onClick={() => void submitSupplement()}
                >
                  重新重判
                </button>
              </div>
            ) : null}
            {assessment.appeal?.status === "cancelled" ? (
              <p className="assessment-panel__appeal-status" role="status">
                补充说明重判已取消
              </p>
            ) : null}
            {assessment.status === "judged" ? (
              <button
                type="button"
                disabled={busy || assessment.appeal?.status === "grading"}
                onClick={() => void advance()}
              >
                下一题
                <ArrowRightIcon aria-hidden size={19} />
              </button>
            ) : assessment.appeal?.status !== "grading" ? (
              <div className="assessment-panel__complete">
                <p className="assessment-panel__done">
                  <CheckCircleIcon aria-hidden size={20} />
                  本轮完成
                </p>
                <button type="button" onClick={() => void close()}>
                  返回阅读
                </button>
              </div>
            ) : null}
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
          onClick={() => void close()}
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
});
