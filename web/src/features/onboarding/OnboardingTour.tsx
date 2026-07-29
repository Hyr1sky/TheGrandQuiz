import {
  ArrowLeftIcon,
  ArrowRightIcon,
  CheckIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useLayoutEffect, useState } from "react";
import "./onboarding-tour.css";

interface OnboardingTourProps {
  onComplete: () => void;
}

interface TourStep {
  target: string;
  title: string;
  body: string;
}

const STEPS: TourStep[] = [
  {
    target: "resource",
    title: "选择当前材料",
    body: "这里决定“本文”和“当前材料”具体指哪一份内容，Agent 不会把范围偷偷扩大到其他文章。",
  },
  {
    target: "sidebar",
    title: "浏览大纲与进度",
    body: "阅读时查看文章结构；进入考核后，可切换查看每一题的完成情况。",
  },
  {
    target: "chat",
    title: "用对话驱动学习",
    body: "可以让 Agent 结合当前材料解释、追问或发起考核。下方示例会帮你填好第一句话。",
  },
  {
    target: "observatory",
    title: "查看运行过程",
    body: "状态栏的罗盘会打开运行观测，展示模型、工具、事件与 trace，方便理解 Agent 做了什么。",
  },
];

export function OnboardingTour({
  onComplete,
}: OnboardingTourProps) {
  const [stepIndex, setStepIndex] = useState(0);
  const [position, setPosition] = useState({ top: 72, left: 16 });
  const step = STEPS[stepIndex] ?? STEPS[0];

  useLayoutEffect(() => {
    if (step === undefined) {
      return;
    }
    const target = document.querySelector<HTMLElement>(
      `[data-onboarding="${step.target}"]`,
    );
    if (target === null) {
      return;
    }

    const updatePosition = () => {
      const rect = target.getBoundingClientRect();
      const bubbleWidth = Math.min(336, window.innerWidth - 32);
      const estimatedHeight = 220;
      const left = Math.min(
        Math.max(16, rect.left),
        Math.max(16, window.innerWidth - bubbleWidth - 16),
      );
      const below = rect.bottom + 12;
      const top =
        below + estimatedHeight <= window.innerHeight
          ? below
          : Math.max(16, rect.top - estimatedHeight - 12);
      setPosition({ top, left });
    };

    target.setAttribute("data-onboarding-active", "true");
    updatePosition();
    window.addEventListener("resize", updatePosition);
    return () => {
      target.removeAttribute("data-onboarding-active");
      window.removeEventListener("resize", updatePosition);
    };
  }, [step]);

  if (step === undefined) {
    return null;
  }

  const lastStep = stepIndex === STEPS.length - 1;
  return (
    <div
      className="onboarding-tour"
      role="dialog"
      aria-label="正考级新手指南"
      style={{ top: position.top, left: position.left }}
    >
      <div className="onboarding-tour__meta">
        <span>
          {stepIndex + 1} / {STEPS.length}
        </span>
        <button
          type="button"
          className="onboarding-tour__icon-button"
          aria-label="跳过指南"
          onClick={onComplete}
        >
          <XIcon aria-hidden size={16} />
        </button>
      </div>
      <h2>{step.title}</h2>
      <p>{step.body}</p>
      <div className="onboarding-tour__actions">
        <button
          type="button"
          onClick={() =>
            setStepIndex((current) => Math.max(0, current - 1))
          }
          disabled={stepIndex === 0}
        >
          <ArrowLeftIcon aria-hidden size={15} />
          上一步
        </button>
        <button
          type="button"
          className="onboarding-tour__primary"
          onClick={() => {
            if (lastStep) {
              onComplete();
            } else {
              setStepIndex((current) => current + 1);
            }
          }}
        >
          {lastStep ? (
            <>
              完成
              <CheckIcon aria-hidden size={15} />
            </>
          ) : (
            <>
              下一步
              <ArrowRightIcon aria-hidden size={15} />
            </>
          )}
        </button>
      </div>
    </div>
  );
}
