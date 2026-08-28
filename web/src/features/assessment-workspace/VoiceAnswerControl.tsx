import {
  ArrowClockwiseIcon,
  MicrophoneIcon,
  StopCircleIcon,
  XCircleIcon,
} from "@phosphor-icons/react";
import { useCallback, useEffect, useRef, useState } from "react";
import { ActivityIndicator } from "../../shared/components/ActivityIndicator";
import {
  cancelVoiceRun,
  cancelVoiceRequest,
  getVoiceRun,
  getVoiceRuntimeConfig,
  retryVoiceRun,
  startVoiceRun,
  type VoiceRunView,
  type VoiceRuntimeConfig,
} from "./api";

interface VoiceAnswerControlProps {
  assessmentSessionId: string;
  questionId: string;
  disabled: boolean;
  onCaptureStart: () => void;
  onReviewable: (voiceRun: VoiceRunView) => void;
  onReset: () => void;
}

type CapturePhase =
  | "idle"
  | "requesting_permission"
  | "recording"
  | "uploading"
  | "transcribing"
  | "reviewable"
  | "failed";

function requestId(prefix: string): string {
  return `${prefix}-${globalThis.crypto.randomUUID()}`;
}

function stopTracks(stream: MediaStream | null): void {
  stream?.getTracks().forEach((track) => track.stop());
}

function permissionMessage(reason: unknown): string {
  if (reason instanceof DOMException && reason.name === "NotAllowedError") {
    return "没有获得麦克风权限，请在浏览器地址栏允许后重试。";
  }
  if (reason instanceof DOMException && reason.name === "NotFoundError") {
    return "没有检测到可用麦克风。";
  }
  if (reason instanceof DOMException && reason.name === "NotReadableError") {
    return "麦克风正被其他应用占用，请关闭占用后重试。";
  }
  return reason instanceof Error ? reason.message : "无法启动录音。";
}

export function VoiceAnswerControl({
  assessmentSessionId,
  questionId,
  disabled,
  onCaptureStart,
  onReviewable,
  onReset,
}: VoiceAnswerControlProps) {
  const [config, setConfig] = useState<VoiceRuntimeConfig | null>(null);
  const [phase, setPhase] = useState<CapturePhase>("idle");
  const [elapsedMs, setElapsedMs] = useState(0);
  const [voiceRun, setVoiceRun] = useState<VoiceRunView | null>(null);
  const [audioBlob, setAudioBlob] = useState<Blob | null>(null);
  const [audioUrl, setAudioUrl] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const startedAtRef = useRef(0);
  const elapsedTimerRef = useRef<number | null>(null);
  const discardCaptureRef = useRef(false);
  const operationRef = useRef(0);
  const mountedRef = useRef(true);
  const voiceRunRef = useRef<VoiceRunView | null>(null);
  const pendingStartRef = useRef<{
    requestId: string;
    controller: AbortController;
  } | null>(null);
  const audioUrlRef = useRef<string | null>(null);
  const onReviewableRef = useRef(onReviewable);
  const onResetRef = useRef(onReset);
  const storageKey = `grandquiz.voice-run:${assessmentSessionId}:${questionId}`;

  useEffect(() => {
    onReviewableRef.current = onReviewable;
    onResetRef.current = onReset;
  }, [onReset, onReviewable]);

  useEffect(() => {
    voiceRunRef.current = voiceRun;
  }, [voiceRun]);

  useEffect(() => {
    audioUrlRef.current = audioUrl;
  }, [audioUrl]);

  useEffect(() => {
    let active = true;
    void getVoiceRuntimeConfig()
      .then((next) => {
        if (active) setConfig(next);
      })
      .catch(() => {
        if (active) setConfig(null);
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    mountedRef.current = true;
    return () => {
      mountedRef.current = false;
      operationRef.current += 1;
      if (elapsedTimerRef.current !== null) {
        window.clearInterval(elapsedTimerRef.current);
      }
      const recorder = recorderRef.current;
      if (recorder?.state === "recording") {
        discardCaptureRef.current = true;
        recorder.stop();
      }
      stopTracks(streamRef.current);
      if (audioUrlRef.current !== null) URL.revokeObjectURL(audioUrlRef.current);
    };
  }, []);

  const rememberVoiceRun = useCallback((run: VoiceRunView) => {
    try {
      globalThis.sessionStorage?.setItem(storageKey, run.voice_run_id);
    } catch {
      // Storage is an optional recovery aid; private mode must not break capture.
    }
  }, [storageKey]);

  const forgetVoiceRun = useCallback(() => {
    try {
      globalThis.sessionStorage?.removeItem(storageKey);
    } catch {
      // See rememberVoiceRun.
    }
  }, [storageKey]);

  const clearElapsedTimer = () => {
    if (elapsedTimerRef.current !== null) {
      window.clearInterval(elapsedTimerRef.current);
      elapsedTimerRef.current = null;
    }
  };

  const updateAudio = (blob: Blob) => {
    setAudioBlob(blob);
    setAudioUrl((current) => {
      if (current !== null) URL.revokeObjectURL(current);
      return URL.createObjectURL(blob);
    });
  };

  const pollVoiceRun = useCallback(async (initial: VoiceRunView, operation: number) => {
    let current = initial;
    while (
      operationRef.current === operation &&
      ["accepted", "transcribing"].includes(current.status)
    ) {
      current = await getVoiceRun(current.voice_run_id);
      if (["accepted", "transcribing"].includes(current.status)) {
        await new Promise((resolve) => window.setTimeout(resolve, 700));
      }
    }
    if (!mountedRef.current || operationRef.current !== operation) return;
    setVoiceRun(current);
    if (current.status === "reviewable") {
      rememberVoiceRun(current);
      setPhase("reviewable");
      onReviewableRef.current(current);
      return;
    }
    if (current.status === "failed") {
      rememberVoiceRun(current);
      setPhase("failed");
      setError(current.error?.reason ?? "语音识别失败，请重试。");
      return;
    }
    if (current.status === "cancelled" || current.status === "expired") {
      forgetVoiceRun();
      setPhase("idle");
      onResetRef.current();
    }
  }, [forgetVoiceRun, rememberVoiceRun]);

  const upload = async (blob: Blob, durationMs: number) => {
    const activeConfig = config;
    if (activeConfig === null) return;
    if (blob.size > activeConfig.max_audio_bytes) {
      setPhase("failed");
      setError("录音文件过大，请缩短回答后重试。");
      return;
    }
    const operation = operationRef.current + 1;
    operationRef.current = operation;
    setPhase("uploading");
    setError(null);
    const startRequestId = requestId("voice-start");
    const controller = new AbortController();
    pendingStartRef.current = { requestId: startRequestId, controller };
    try {
      const started = await startVoiceRun(
        assessmentSessionId,
        questionId,
        blob,
        Math.max(1, Math.min(durationMs, activeConfig.max_duration_ms)),
        startRequestId,
        controller.signal,
      );
      pendingStartRef.current = null;
      if (operationRef.current !== operation) {
        await cancelVoiceRun(started.voice_run_id).catch(() => undefined);
        return;
      }
      setVoiceRun(started);
      rememberVoiceRun(started);
      setPhase(started.status === "reviewable" ? "reviewable" : "transcribing");
      await pollVoiceRun(started, operation);
    } catch (reason) {
      pendingStartRef.current = null;
      if (operationRef.current !== operation) return;
      setPhase("failed");
      setError(reason instanceof Error ? reason.message : "上传或识别失败。");
    }
  };

  const beginRecording = async () => {
    if (config === null || !config.enabled || disabled) return;
    if (!globalThis.navigator.mediaDevices?.getUserMedia) {
      setError("当前浏览器不支持麦克风录音。");
      return;
    }
    const mimeType = config.mime_types[0];
    if (!mimeType || !MediaRecorder.isTypeSupported(mimeType)) {
      setError("当前浏览器不支持 WebM/Opus 录音。");
      return;
    }
    setPhase("requesting_permission");
    setError(null);
    setVoiceRun(null);
    onReset();
    onCaptureStart();
    try {
      const stream = await globalThis.navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;
      chunksRef.current = [];
      discardCaptureRef.current = false;
      const recorder = new MediaRecorder(stream, { mimeType });
      recorderRef.current = recorder;
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onstop = () => {
        clearElapsedTimer();
        stopTracks(streamRef.current);
        streamRef.current = null;
        if (discardCaptureRef.current) return;
        const blob = new Blob(chunksRef.current, { type: mimeType });
        const durationMs = Math.max(1, Date.now() - startedAtRef.current);
        updateAudio(blob);
        void upload(blob, durationMs);
      };
      startedAtRef.current = Date.now();
      setElapsedMs(0);
      recorder.start(250);
      setPhase("recording");
      elapsedTimerRef.current = window.setInterval(() => {
        const next = Date.now() - startedAtRef.current;
        setElapsedMs(next);
        if (next >= config.max_duration_ms && recorder.state === "recording") {
          recorder.stop();
        }
      }, 250);
    } catch (reason) {
      stopTracks(streamRef.current);
      streamRef.current = null;
      setPhase("idle");
      setError(permissionMessage(reason));
    }
  };

  const stopAndRecognize = () => {
    if (recorderRef.current?.state === "recording") {
      recorderRef.current.stop();
    }
  };

  const cancelCurrent = async () => {
    operationRef.current += 1;
    const pendingStart = pendingStartRef.current;
    if (pendingStart !== null) {
      pendingStart.controller.abort();
      try {
        await cancelVoiceRequest(pendingStart.requestId);
        pendingStartRef.current = null;
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法取消语音上传。");
        return;
      }
    }
    clearElapsedTimer();
    if (recorderRef.current?.state === "recording") {
      discardCaptureRef.current = true;
      recorderRef.current.stop();
    }
    stopTracks(streamRef.current);
    streamRef.current = null;
    if (voiceRun !== null && !["cancelled", "submitted", "expired"].includes(voiceRun.status)) {
      try {
        await cancelVoiceRun(voiceRun.voice_run_id);
      } catch (reason) {
        setError(reason instanceof Error ? reason.message : "无法取消语音识别。");
        return;
      }
    }
    setVoiceRun(null);
    forgetVoiceRun();
    setPhase("idle");
    setError(null);
    onReset();
  };

  useEffect(() => {
    if (config?.enabled !== true || voiceRunRef.current !== null) return;
    const storedVoiceRunId = (() => {
      try {
        return globalThis.sessionStorage?.getItem(storageKey) ?? null;
      } catch {
        return null;
      }
    })();
    if (storedVoiceRunId === null) return;
    const operation = operationRef.current + 1;
    operationRef.current = operation;
    void getVoiceRun(storedVoiceRunId)
      .then(async (restored) => {
        if (
          restored.assessment_session_id !== assessmentSessionId ||
          restored.question_id !== questionId
        ) {
          forgetVoiceRun();
          return;
        }
        if (!mountedRef.current || operationRef.current !== operation) return;
        setVoiceRun(restored);
        setPhase(
          restored.status === "reviewable"
            ? "reviewable"
            : restored.status === "failed"
              ? "failed"
              : "transcribing",
        );
        await pollVoiceRun(restored, operation);
      })
      .catch(() => forgetVoiceRun());
  }, [assessmentSessionId, config?.enabled, forgetVoiceRun, pollVoiceRun, questionId, storageKey]);

  const retry = async () => {
    if (voiceRun === null || audioBlob === null) return;
    const operation = operationRef.current + 1;
    operationRef.current = operation;
    setPhase("transcribing");
    setError(null);
    try {
      const next = await retryVoiceRun(
        voiceRun.voice_run_id,
        audioBlob,
        requestId("voice-retry"),
      );
      setVoiceRun(next);
      rememberVoiceRun(next);
      await pollVoiceRun(next, operation);
    } catch (reason) {
      if (operationRef.current !== operation) return;
      setPhase("failed");
      setError(reason instanceof Error ? reason.message : "语音识别重试失败。");
    }
  };

  if (config === null || !config.enabled) return null;

  return (
    <section className="voice-answer" aria-label="语音回答">
      <div className="voice-answer__toolbar">
        {phase === "idle" ? (
          <button type="button" disabled={disabled} onClick={() => void beginRecording()}>
            <MicrophoneIcon aria-hidden size={18} />
            开始语音回答
          </button>
        ) : null}
        {phase === "requesting_permission" ? (
          <ActivityIndicator label="正在请求麦克风权限..." tone="brass" />
        ) : null}
        {phase === "recording" ? (
          <>
            <p role="status">
              <span className="voice-answer__pulse" aria-hidden />
              正在录音 · {Math.ceil(elapsedMs / 1000)} / {config.max_duration_ms / 1000} 秒
            </p>
            <button type="button" onClick={stopAndRecognize} aria-label="结束录音并识别">
              <StopCircleIcon aria-hidden size={18} />
              结束录音并识别
            </button>
          </>
        ) : null}
        {phase === "uploading" || phase === "transcribing" ? (
          <>
            <ActivityIndicator
              label={phase === "uploading" ? "正在上传录音..." : "正在识别语音..."}
              detail={phase === "transcribing" ? "完成后会先生成可编辑草稿，不会直接提交答案。" : undefined}
            />
            <button type="button" onClick={() => void cancelCurrent()} aria-label="取消语音识别">
              <XCircleIcon aria-hidden size={18} />
              取消
            </button>
          </>
        ) : null}
        {phase === "reviewable" ? (
          <>
            <p role="status">已生成可编辑草稿</p>
            <button type="button" onClick={() => void cancelCurrent()}>
              重新录音
            </button>
          </>
        ) : null}
        {phase === "failed" ? (
          <>
            {voiceRun?.retryable && audioBlob !== null ? (
              <button type="button" onClick={() => void retry()}>
                <ArrowClockwiseIcon aria-hidden size={18} />
                重试识别
              </button>
            ) : null}
            <button type="button" onClick={() => void cancelCurrent()}>
              重新录音
            </button>
          </>
        ) : null}
      </div>
      {audioUrl !== null ? (
        <audio className="voice-answer__player" aria-label="录音回放" controls src={audioUrl} />
      ) : null}
      {voiceRun !== null ? (
        <p className="voice-answer__meta">
          识别尝试 {voiceRun.provider_attempt_count} / {config.max_provider_attempts}
          {config.hints_enabled && voiceRun.hints_applied && voiceRun.hint_count > 0
            ? ` · 已启用 ${voiceRun.hint_count} 个本题术语`
            : ""}
        </p>
      ) : null}
      <p className="voice-answer__meta">
        最长 {config.max_duration_ms / 1000} 秒 · 上传上限{" "}
        {(config.max_audio_bytes / 1024 / 1024).toFixed(1)} MiB
      </p>
      {error !== null ? (
        <p className="voice-answer__error" role="alert">
          {error}
        </p>
      ) : null}
    </section>
  );
}
