import {
  ArrowRightIcon,
  CheckCircleIcon,
  CompassIcon,
  EyeIcon,
} from "@phosphor-icons/react";
import {
  useEffect,
  useRef,
  useState,
  type FormEvent,
} from "react";
import {
  listResources,
  type ResourceSummary,
} from "../../shared/api/resources";
import {
  getAssessment,
  nextRound,
  revealEvidence,
  startAssessment,
  submitAnswer,
  type AssessmentView,
} from "./api";
import "./assessment-workspace.css";

const SESSION_STORAGE_KEY = "grandquiz.assessment.session_id";

function commandId(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
}

export function AssessmentWorkspace() {
  const [resources, setResources] = useState<ResourceSummary[]>([]);
  const [resourceId, setResourceId] = useState("");
  const [rounds, setRounds] = useState("3");
  const [questionType, setQuestionType] = useState("");
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

  useEffect(() => {
    let active = true;
    void listResources()
      .then((items) => {
        if (!active) {
          return;
        }
        setResources(items);
        setResourceId(items[0]?.resource_id ?? "");
      })
      .catch((reason: unknown) => {
        if (active) {
          setError(reason instanceof Error ? reason.message : "无法读取考核材料");
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    const sessionId = window.sessionStorage.getItem(SESSION_STORAGE_KEY);
    if (sessionId === null) {
      return;
    }
    let active = true;
    void getAssessment(sessionId)
      .then((current) => {
        if (active) {
          setAssessment(current);
        }
      })
      .catch(() => {
        window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
      });
    return () => {
      active = false;
    };
  }, []);

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
            setAssessment(next);
          }
        })
        .catch((reason: unknown) => {
          if (active) {
            setError(reason instanceof Error ? reason.message : "无法刷新考核状态");
          }
        });
    }, delay);
    return () => {
      active = false;
      window.clearTimeout(timer);
    };
  }, [assessment]);

  const start = async (event: FormEvent) => {
    event.preventDefault();
    if (resourceId === "") {
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const requestedRounds = Number.parseInt(rounds, 10);
      const normalizedType = questionType === "" ? null : questionType;
      const started = await startAssessment(
        resourceId,
        { rounds: requestedRounds, questionType: normalizedType },
      );
      window.sessionStorage.setItem(SESSION_STORAGE_KEY, started.session_id);
      setAssessment(started);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法开始考核");
    } finally {
      setBusy(false);
    }
  };

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
      setAssessment(
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
      setAssessment(
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
      setAssessment(next);
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : "无法进入下一题");
    } finally {
      setBusy(false);
    }
  };

  const resetAssessment = () => {
    window.sessionStorage.removeItem(SESSION_STORAGE_KEY);
    revealRequested.current = null;
    answerCommand.current = null;
    nextCommand.current = null;
    setAnswer("");
    setAssessment(null);
    setError(null);
  };

  if (assessment?.question) {
    const question = assessment.question;
    const waiting = assessment.status === "awaiting_answer";
    const judged =
      assessment.status === "judged" || assessment.status === "completed";
    return (
      <main className="assessment-workspace" aria-label="TheGrandQuiz 考核工作台">
        <section className="quiz-sheet">
          <header className="quiz-sheet__header">
            <p>
              第 {assessment.round_index} / {assessment.rounds} 题
            </p>
            <span>{question.question_type}</span>
          </header>
          <h1>{question.text}</h1>

          <form className="quiz-answer" onSubmit={submit}>
            {question.options.length > 0 ? (
              <fieldset disabled={!waiting || busy}>
                <legend>选择一个答案</legend>
                {question.options.map((option) => (
                  <label key={option} className="quiz-option">
                    <input
                      type="radio"
                      name="assessment-answer"
                      value={option}
                      checked={answer === option}
                      onChange={(event) => setAnswer(event.target.value)}
                    />
                    <span>{option}</span>
                  </label>
                ))}
              </fieldset>
            ) : (
              <label className="quiz-answer__open">
                <span>你的回答</span>
                <textarea
                  value={answer}
                  disabled={!waiting || busy}
                  onChange={(event) => setAnswer(event.target.value)}
                  placeholder="先给出自己的理解，再决定是否查看材料…"
                />
              </label>
            )}

            <section className="quiz-evidence" aria-label="本题材料证据">
              <button
                type="button"
                aria-label="揭示本题材料证据"
                aria-expanded={question.evidence_revealed}
                onPointerEnter={() => void reveal("hover")}
                onFocus={() => void reveal("keyboard")}
                onClick={() => void reveal("click")}
              >
                <EyeIcon aria-hidden size={19} />
                {question.evidence_revealed ? "材料证据已揭示" : "想不起来？悬停或点击查看材料"}
              </button>
              {question.evidence_revealed ? (
                <blockquote>
                  {question.evidence.map((quote, index) => (
                    <p key={index}>{quote}</p>
                  ))}
                </blockquote>
              ) : (
                <div className="quiz-evidence__veil" aria-hidden>
                  Evidence hidden
                </div>
              )}
            </section>

            {waiting ? (
              <button
                className="quiz-submit"
                type="submit"
                disabled={answer.trim() === "" || busy}
              >
                提交答案
                <ArrowRightIcon aria-hidden size={19} />
              </button>
            ) : null}
          </form>

          {assessment.status === "grading" ? (
            <p className="quiz-status" role="status">
              正在依据原文判卷并更新薄弱状态…
            </p>
          ) : null}
          {judged && assessment.judgement ? (
            <section className="quiz-judgement" aria-label="本题判决">
              <p className={`quiz-verdict quiz-verdict--${assessment.judgement.verdict}`}>
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
                <button type="button" disabled={busy} onClick={() => void advance()}>
                  下一题
                  <ArrowRightIcon aria-hidden size={19} />
                </button>
              ) : (
                <div className="quiz-complete-actions">
                  <p className="quiz-complete">
                    <CheckCircleIcon aria-hidden size={20} />
                    本轮完成
                  </p>
                  <button type="button" onClick={resetAssessment}>
                    开始新一轮
                  </button>
                </div>
              )}
            </section>
          ) : null}
          {error ? <p role="alert">{error}</p> : null}
          <footer>
            <code>trace_id: {assessment.trace_id}</code>
          </footer>
        </section>
      </main>
    );
  }

  return (
    <main className="assessment-workspace" aria-label="TheGrandQuiz 考核工作台">
      <section className="assessment-setup">
        <div className="assessment-setup__intro">
          <p className="assessment-setup__eyebrow">Assessment · 一题一步</p>
          <CompassIcon aria-hidden size={34} weight="duotone" />
          <h1>开始一轮考核</h1>
          <p>题目从明确选择的材料生成。每答完一题，代码才更新薄弱状态。</p>
        </div>

        <form className="assessment-setup__form" onSubmit={start}>
          <label>
            <span>考核材料</span>
            <select
              aria-label="考核材料"
              value={resourceId}
              onChange={(event) => setResourceId(event.target.value)}
            >
              {resources.map((resource) => (
                <option key={resource.resource_id} value={resource.resource_id}>
                  {resource.topic ?? resource.url}
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>题目数量</span>
            <select
              aria-label="题目数量"
              value={rounds}
              onChange={(event) => setRounds(event.target.value)}
            >
              {[1, 3, 5, 10].map((count) => (
                <option key={count} value={count}>
                  {count} 题
                </option>
              ))}
            </select>
          </label>
          <label>
            <span>题型</span>
            <select
              aria-label="题型"
              value={questionType}
              onChange={(event) => setQuestionType(event.target.value)}
            >
              <option value="">自适应</option>
              <option value="选择题">选择题</option>
              <option value="简答题">简答题</option>
            </select>
          </label>
          {assessment?.status === "preparing" ? (
            <p role="status">正在从材料与薄弱状态生成第一题…</p>
          ) : null}
          {assessment?.status === "refused" || assessment?.status === "failed" ? (
            <p role="alert">
              {assessment.error ?? "当前材料暂时无法开始考核，请重新选择。"}
            </p>
          ) : null}
          {error ? <p role="alert">{error}</p> : null}
          <button type="submit" disabled={resourceId === "" || busy}>
            生成第一题
            <ArrowRightIcon aria-hidden size={19} />
          </button>
        </form>
      </section>
    </main>
  );
}
