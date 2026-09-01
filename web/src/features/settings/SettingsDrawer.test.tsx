import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { ThemeProvider } from "../../app/ThemeProvider";
import { SettingsDrawer } from "./SettingsDrawer";

const settings = {
  schema_version: "settings.v1" as const,
  preferences: {
    question_language: "中文" as const,
    difficulty_mode: "adaptive" as const,
    asr_material_hints_enabled: false,
    asr_material_hints_source: "environment_default" as const,
  },
  difficulty: {
    default_tier: 3 as const,
    item_count: 12,
    average_tier: 3.25,
    tier_counts: { "1": 0, "2": 2, "3": 6, "4": 3, "5": 1 },
  },
  providers: [
    {
      role: "basic" as const,
      configured: true,
      model: "deepseek-v4-pro",
      endpoint_host: "api.deepseek.com",
      credential_source: "environment" as const,
      editable_in_web: false as const,
      required_env_vars: ["LLM_API_KEY", "LLM_BASE_URL", "LLM_MODEL"],
    },
    {
      role: "enrich" as const,
      configured: true,
      model: "qwen-plus",
      endpoint_host: "dashscope.aliyuncs.com",
      credential_source: "environment" as const,
      editable_in_web: false as const,
      required_env_vars: [
        "ENRICH_LLM_API_KEY",
        "ENRICH_LLM_BASE_URL",
        "ENRICH_LLM_MODEL",
      ],
    },
    {
      role: "speech" as const,
      configured: true,
      model: "qwen-audio-3.0-asr-flash",
      endpoint_host: "cn-beijing",
      credential_source: "environment" as const,
      editable_in_web: false as const,
      required_env_vars: ["DASHSCOPE_API_KEY", "DASHSCOPE_WORKSPACE_ID"],
    },
  ],
  data_locations: [
    {
      kind: "learning" as const,
      path: "/Users/test/.grandquiz/learning.db",
      read_only: true as const,
    },
    {
      kind: "trace" as const,
      path: "/Users/test/.grandquiz/trace.db",
      read_only: true as const,
    },
    {
      kind: "voice" as const,
      path: "/Users/test/.grandquiz/voice.db",
      read_only: true as const,
    },
  ],
};

afterEach(() => {
  vi.unstubAllGlobals();
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});
it("edits hot preferences while keeping provider secrets read-only", async () => {
  const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const request = input instanceof Request ? input : new Request(String(input), init);
    if (request.method === "GET") return Response.json(settings);
    const patch = (await request.json()) as Record<string, unknown>;
    return Response.json({
      ...settings,
      preferences: { ...settings.preferences, ...patch },
    });
  });
  vi.stubGlobal("fetch", fetchMock);
  const user = userEvent.setup();

  render(
    <ThemeProvider>
      <SettingsDrawer open onClose={() => undefined} />
    </ThemeProvider>,
  );

  expect(await screen.findByRole("dialog", { name: "应用设置" })).toHaveTextContent(
    "12 个知识点 · 平均 3.25 档",
  );
  expect(screen.getByText("deepseek-v4-pro")).toBeInTheDocument();
  expect(screen.getAllByText("由 .env 管理")).toHaveLength(3);
  expect(screen.queryByLabelText(/API Key/i)).not.toBeInTheDocument();
  expect(screen.getByText("/Users/test/.grandquiz/learning.db")).toBeInTheDocument();
  expect(screen.getByText("/Users/test/.grandquiz/trace.db")).toBeInTheDocument();
  expect(screen.getByText("/Users/test/.grandquiz/voice.db")).toBeInTheDocument();
  expect(screen.queryByRole("textbox", { name: /数据/ })).not.toBeInTheDocument();

  await user.click(screen.getByRole("switch", { name: "启用材料词表" }));
  await waitFor(() => {
    expect(fetchMock).toHaveBeenCalledWith(
      expect.objectContaining({ method: "PATCH" }),
    );
  });
  expect(screen.getByRole("switch", { name: "启用材料词表" })).toBeChecked();

  await user.click(screen.getByRole("radio", { name: "偏挑战" }));
  expect(screen.getByRole("radio", { name: "偏挑战" })).toBeChecked();
});
