import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cancelVoiceRequest,
  cancelVoiceRun,
  getVoiceRun,
  getVoiceRuntimeConfig,
  startVoiceRun,
} from "./api";
import { VoiceAnswerControl } from "./VoiceAnswerControl";

vi.mock("./api", () => ({
  cancelVoiceRequest: vi.fn(),
  cancelVoiceRun: vi.fn(),
  getVoiceRun: vi.fn(),
  getVoiceRuntimeConfig: vi.fn(),
  retryVoiceRun: vi.fn(),
  startVoiceRun: vi.fn(),
}));

class FakeMediaRecorder {
  static isTypeSupported = vi.fn(() => true);
  state: RecordingState = "inactive";
  ondataavailable: ((event: BlobEvent) => void) | null = null;
  onstop: (() => void) | null = null;

  constructor(
    readonly stream: MediaStream,
    readonly options?: MediaRecorderOptions,
  ) {}

  start() {
    this.state = "recording";
  }

  stop() {
    this.state = "inactive";
    this.ondataavailable?.({ data: new Blob(["voice"], { type: this.options?.mimeType }) } as BlobEvent);
    this.onstop?.();
  }
}

const config = {
  enabled: true,
  mime_types: ["audio/webm;codecs=opus"],
  max_duration_ms: 90_000,
  max_audio_bytes: 7_000_000,
  max_provider_attempts: 2,
  review_ttl_seconds: 1_800,
  max_hint_entries: 50,
  hints_enabled: true,
};

function voiceRun(status: "transcribing" | "reviewable" | "cancelled") {
  return {
    schema_version: "voice-run.v1" as const,
    voice_run_id: "voice-1",
    request_id: "request-1",
    assessment_session_id: "assessment-1",
    question_id: "question-1",
    item_id: "item-1",
    status,
    version: 2,
    mime_type: "audio/webm;codecs=opus",
    byte_count: 5,
    client_duration_ms: 1_000,
    audio_sha256: "hash",
    hint_set_id: "hints-1",
    hint_count: 2,
    hints_applied: true,
    provider_attempt_count: 1,
    active_provider_attempt_id: "attempt-1",
    reviewable_transcript: status === "reviewable" ? "ReAct 是推理与动作交替。" : null,
    retryable: false,
    error: null,
    trace_id: "voice-trace-1",
    created_at: 1,
    updated_at: 2,
    expires_at: status === "reviewable" ? 100 : null,
  };
}

afterEach(() => {
  globalThis.sessionStorage.clear();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

describe("VoiceAnswerControl", () => {
  it("records, releases the microphone and returns a reviewable transcript", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.mocked(startVoiceRun).mockResolvedValue(voiceRun("transcribing"));
    vi.mocked(getVoiceRun).mockResolvedValue(voiceRun("reviewable"));
    vi.mocked(cancelVoiceRun).mockResolvedValue(voiceRun("cancelled"));
    const stopTrack = vi.fn();
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: stopTrack }],
    });
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:voice"),
      revokeObjectURL: vi.fn(),
    });
    const onReviewable = vi.fn();

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={onReviewable}
        onReset={() => undefined}
      />,
    );

    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));
    await waitFor(() =>
      expect(screen.getByRole("status")).toHaveTextContent("正在录音"),
    );
    fireEvent.click(screen.getByRole("button", { name: "结束录音并识别" }));

    await waitFor(() => expect(startVoiceRun).toHaveBeenCalledTimes(1));
    await waitFor(() => expect(onReviewable).toHaveBeenCalledWith(voiceRun("reviewable")));
    expect(stopTrack).toHaveBeenCalledTimes(1);
    expect(screen.getByText("已生成可编辑草稿")).toBeInTheDocument();
    expect(screen.getByLabelText("录音回放")).toBeInTheDocument();
    expect(screen.getByText(/最长 90 秒 · 上传上限 6\.7 MiB/)).toBeInTheDocument();
  });

  it("shows a permission error without creating a VoiceRun", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockRejectedValue(new DOMException("denied", "NotAllowedError")),
      },
    });

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("没有获得麦克风权限");
    expect(startVoiceRun).not.toHaveBeenCalled();
  });

  it("distinguishes an occupied microphone from denied permission", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi
          .fn()
          .mockRejectedValue(new DOMException("busy", "NotReadableError")),
      },
    });

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("正被其他应用占用");
    expect(startVoiceRun).not.toHaveBeenCalled();
  });

  it("rejects an oversized recording before upload", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue({
      ...config,
      max_audio_bytes: 1,
    });
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:voice"),
      revokeObjectURL: vi.fn(),
    });

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));
    fireEvent.click(await screen.findByRole("button", { name: "结束录音并识别" }));

    expect(await screen.findByRole("alert")).toHaveTextContent("录音文件过大");
    expect(startVoiceRun).not.toHaveBeenCalled();
  });

  it("cancels an in-flight server VoiceRun", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.mocked(startVoiceRun).mockResolvedValue(voiceRun("transcribing"));
    vi.mocked(getVoiceRun).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(cancelVoiceRun).mockResolvedValue(voiceRun("cancelled"));
    const getUserMedia = vi.fn().mockResolvedValue({
      getTracks: () => [{ stop: vi.fn() }],
    });
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", { mediaDevices: { getUserMedia } });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:voice"),
      revokeObjectURL: vi.fn(),
    });

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));
    fireEvent.click(await screen.findByRole("button", { name: "结束录音并识别" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消语音识别" }));

    await waitFor(() => expect(cancelVoiceRun).toHaveBeenCalledWith("voice-1"));
    expect(screen.getByRole("button", { name: "开始语音回答" })).toBeInTheDocument();
  });

  it("reserves cancellation while the upload has not returned a VoiceRun ID", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.mocked(startVoiceRun).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(cancelVoiceRequest).mockResolvedValue();
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:voice"),
      revokeObjectURL: vi.fn(),
    });

    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));
    fireEvent.click(await screen.findByRole("button", { name: "结束录音并识别" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消语音识别" }));

    await waitFor(() => expect(cancelVoiceRequest).toHaveBeenCalledTimes(1));
    expect(cancelVoiceRun).not.toHaveBeenCalled();
  });

  it("keeps a failed upload cancellation available for an explicit retry", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.mocked(startVoiceRun).mockImplementation(() => new Promise(() => undefined));
    vi.mocked(cancelVoiceRequest)
      .mockRejectedValueOnce(new Error("取消请求失败"))
      .mockResolvedValueOnce();
    vi.stubGlobal("MediaRecorder", FakeMediaRecorder);
    vi.stubGlobal("navigator", {
      mediaDevices: {
        getUserMedia: vi.fn().mockResolvedValue({
          getTracks: () => [{ stop: vi.fn() }],
        }),
      },
    });
    vi.stubGlobal("URL", {
      createObjectURL: vi.fn(() => "blob:voice"),
      revokeObjectURL: vi.fn(),
    });
    render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={() => undefined}
        onReset={() => undefined}
      />,
    );
    fireEvent.click(await screen.findByRole("button", { name: "开始语音回答" }));
    fireEvent.click(await screen.findByRole("button", { name: "结束录音并识别" }));
    fireEvent.click(await screen.findByRole("button", { name: "取消语音识别" }));
    expect(await screen.findByRole("alert")).toHaveTextContent("取消请求失败");

    fireEvent.click(screen.getByRole("button", { name: "取消语音识别" }));
    await waitFor(() => expect(cancelVoiceRequest).toHaveBeenCalledTimes(2));
  });

  it("restores a reviewable run after remount without cancelling it on cleanup", async () => {
    vi.mocked(getVoiceRuntimeConfig).mockResolvedValue(config);
    vi.mocked(getVoiceRun).mockResolvedValue(voiceRun("reviewable"));
    globalThis.sessionStorage.setItem(
      "grandquiz.voice-run:assessment-1:question-1",
      "voice-1",
    );
    const onReviewable = vi.fn();

    const view = render(
      <VoiceAnswerControl
        assessmentSessionId="assessment-1"
        questionId="question-1"
        disabled={false}
        onCaptureStart={() => undefined}
        onReviewable={onReviewable}
        onReset={() => undefined}
      />,
    );

    await waitFor(() => expect(onReviewable).toHaveBeenCalledWith(voiceRun("reviewable")));
    view.unmount();
    expect(cancelVoiceRun).not.toHaveBeenCalled();
  });
});
