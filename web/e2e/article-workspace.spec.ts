import { expect, test } from "@playwright/test";

const ASSESSMENT_QUESTION =
  /durable processor|事件历史的因果一致性|完整事件记录|执行边界/;

let browserErrors: string[] = [];
let observedTraceIds: string[] = [];

async function dismissOnboarding(
  page: import("@playwright/test").Page,
) {
  const tour = page.getByRole("dialog", {
    name: "正考级新手指南",
  });
  if (await tour.isVisible()) {
    await page.getByRole("button", { name: "跳过指南" }).click();
  }
}

async function openEvalData(page: import("@playwright/test").Page) {
  await page.getByRole("button", { name: "打开管理菜单" }).click();
  await page.getByRole("menuitem", { name: "Eval 数据" }).click();
}

test.beforeEach(async ({ page }) => {
  browserErrors = [];
  observedTraceIds = [];
  page.on("console", (message) => {
    if (message.type() === "error") {
      browserErrors.push(`console: ${message.text()}`);
    }
  });
  page.on("requestfailed", (request) => {
    browserErrors.push(
      `network: ${request.method()} ${request.url()} ${request.failure()?.errorText ?? ""}`,
    );
  });
  page.on("response", (response) => {
    if (!response.url().includes("/api/v1/assessments")) {
      return;
    }
    void response
      .json()
      .then((payload: unknown) => {
        if (
          typeof payload === "object" &&
          payload !== null &&
          "trace_id" in payload &&
          typeof payload.trace_id === "string"
        ) {
          observedTraceIds.push(payload.trace_id);
        }
      })
      .catch(() => undefined);
  });
});

test.afterEach(async ({ page: _page }, testInfo) => {
  if (testInfo.status === testInfo.expectedStatus) {
    return;
  }
  await testInfo.attach("browser-errors-and-trace-id", {
    body: JSON.stringify({ traceIds: observedTraceIds, browserErrors }, null, 2),
    contentType: "application/json",
  });
});

test("guides the first run and can be reopened", async ({ page }) => {
  await page.goto("/");
  const tour = page.getByRole("dialog", {
    name: "正考级新手指南",
  });
  await expect(tour).toContainText("1 / 4");
  await expect(tour).toContainText("选择当前材料");
  await page.getByRole("button", { name: "下一步" }).click();
  await expect(tour).toContainText("2 / 4");
  await page.getByRole("button", { name: "跳过指南" }).click();
  await expect(tour).toBeHidden();

  await page.reload();
  await expect(tour).toBeHidden();

  await page.getByRole("button", { name: "打开新手指南" }).click();
  await expect(tour).toContainText("1 / 4");
  await page.getByRole("button", { name: "跳过指南" }).click();
});

test("changes local runtime preferences from the unified settings drawer", async ({
  page,
}) => {
  const reset = await page.request.patch("/api/v1/settings", {
    data: {
      asr_material_hints_enabled: false,
      difficulty_mode: "adaptive",
    },
  });
  expect(reset.ok()).toBe(true);
  await page.goto("/");
  await dismissOnboarding(page);
  await page.getByRole("button", { name: "打开应用设置" }).click();
  const drawer = page.getByRole("dialog", { name: "应用设置" });
  await expect(drawer).toBeVisible();
  await expect(drawer.getByText("Provider 与密钥")).toBeVisible();

  const materialHints = drawer.getByRole("switch", { name: "启用材料词表" });
  await expect(materialHints).not.toBeChecked();
  await materialHints.click();
  await expect(materialHints).toBeChecked();
  await drawer.getByRole("radio", { name: "偏挑战" }).click();
  await expect(drawer.getByRole("radio", { name: "偏挑战" })).toBeChecked();
  await expect(drawer.getByRole("status")).toHaveText("设置已保存");

  await drawer.getByRole("button", { name: "关闭应用设置" }).click();
  await page.getByRole("button", { name: "打开应用设置" }).click();
  await expect(page.getByRole("switch", { name: "启用材料词表" })).toBeChecked();
  await expect(page.getByRole("radio", { name: "偏挑战" })).toBeChecked();
  const restore = await page.request.patch("/api/v1/settings", {
    data: {
      asr_material_hints_enabled: false,
      difficulty_mode: "adaptive",
    },
  });
  expect(restore.ok()).toBe(true);
});

test("uploads, approves, and switches to a new material", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  await page.getByRole("button", { name: "添加与管理材料" }).click();
  const drawer = page.getByRole("dialog", { name: "添加与管理材料" });
  await drawer
    .locator('input[type="file"]')
    .setInputFiles("e2e/fixtures/event-spine.md");
  await drawer.getByRole("button", { name: "开始解析" }).click();

  await expect(drawer.getByText("事件事实源", { exact: true })).toBeVisible();
  await expect(
    drawer.getByText("事件是系统唯一的事实来源", { exact: true }),
  ).toBeVisible();
  await drawer.getByRole("button", { name: "批准 1 个知识点" }).click();
  await expect(drawer.getByText("材料已经进入知识星图")).toBeVisible();
  await drawer.getByRole("button", { name: "关闭材料管理" }).click();

  await expect(page.getByRole("combobox", { name: "当前材料" })).toHaveValue(
    /.+/,
  );
  await expect(page.getByRole("heading", { name: "上传材料：事件脊柱" })).toBeVisible();
});

test("discovers candidates and enters Acquisition only after human approval", async ({ page }, testInfo) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const resourceCount = await page.getByRole("combobox", { name: "当前材料" }).locator("option").count();
  await page.getByRole("button", { name: "添加与管理材料" }).click();
  const drawer = page.getByRole("dialog", { name: "添加与管理材料" });
  await drawer.getByRole("tab", { name: "发现材料" }).click();
  await drawer.getByRole("searchbox").fill(`Agent Memory ${testInfo.project.name}`);
  await drawer.getByRole("button", { name: "搜索候选" }).click();

  await expect(drawer.getByText("Agent Memory 工程指南")).toBeVisible();
  await expect(drawer.getByText("只搜索候选；批准后才会抓取")).toBeVisible();
  await expect(page.getByRole("combobox", { name: "当前材料" }).locator("option")).toHaveCount(resourceCount);
  await drawer.getByRole("button", { name: "批准并深读" }).click();
  await expect(drawer.getByText("事件事实源", { exact: true })).toBeVisible();
  await drawer.getByRole("button", { name: "批准 1 个知识点" }).click();
  await expect(drawer.getByText("材料已经进入知识星图")).toBeVisible();
  await drawer.getByRole("button", { name: "关闭材料管理" }).click();
  await expect(page.getByRole("combobox", { name: "当前材料" }).locator("option")).toHaveCount(resourceCount + 1);
});

test("reviews blind labels before creating a dataset snapshot", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  await openEvalData(page);
  const drawer = page.getByRole("dialog", { name: "Eval 数据管理" });
  await drawer
    .locator('input[type="file"]')
    .setInputFiles("e2e/fixtures/blind-samples.json");

  await expect(drawer.getByText("盲标样本 · fixture-blind-1")).toBeVisible();
  await expect(drawer.getByText("可计入发布门")).toBeVisible();
  const privacyReview = drawer.getByRole("button", { name: "隐私检查通过" });
  if (await privacyReview.isVisible()) {
    await expect(drawer.getByRole("button", { name: "生成快照" })).toBeDisabled();
    await privacyReview.click();
  } else {
    // The second viewport deliberately reuses SQLite and proves review survives restart/readback.
    await expect(drawer.getByText("纳入下一份快照")).toBeVisible();
  }
  await drawer.getByRole("button", { name: "生成快照" }).click();
  await expect(drawer.getByText("快照已固定")).toBeVisible();
  await expect(drawer.getByText("1 条 · 发布门 1 · 探索 0")).toBeVisible();
  await drawer.getByRole("button", { name: "关闭 Eval 数据管理" }).click();
  await page.reload();
  await openEvalData(page);
  const restored = page.getByRole("dialog", { name: "Eval 数据管理" });
  await expect(
    restored.getByRole("region", { name: "数据集快照历史" }),
  ).toContainText("1 条 · 发布门 1");
});

test("loads Markdown images only after consent and contains truly wide content", async ({
  page,
}, testInfo) => {
  const remoteRequests: string[] = [];
  const remoteReferrers: Array<string | undefined> = [];
  await page.route(/https:\/\/attacker\.invalid\/should-not-load\.png.*/, async (route) => {
    const request = route.request();
    remoteRequests.push(request.url());
    remoteReferrers.push(request.headers().referer);
    await route.fulfill({
      status: 200,
      contentType: "image/svg+xml",
      headers: { "cache-control": "no-store" },
      body: '<svg xmlns="http://www.w3.org/2000/svg" width="1600" height="400"><rect width="1600" height="400" fill="#d8c7a5"/></svg>',
    });
  });

  await page.goto("/");
  await dismissOnboarding(page);
  const durableNode = page
    .locator(".outline__item")
    .filter({ hasText: "Durable processors" });
  if (testInfo.project.name === "mobile") {
    await durableNode.evaluate((element: HTMLElement) => element.click());
  } else {
    await durableNode.click();
  }

  await expect(page.getByRole("note")).toContainText("不可信远程图片");
  const remoteImageUrl = await page.getByRole("note").locator("code").innerText();
  await expect(page.getByRole("img")).toHaveCount(0);
  expect(remoteRequests).toEqual([]);

  await page
    .getByRole("button", { name: "加载图片：不可信远程图片" })
    .click();
  const materialImage = page.getByRole("img", { name: "不可信远程图片" });
  await expect(materialImage).toBeVisible();
  await expect.poll(() => remoteRequests).toEqual([remoteImageUrl]);
  expect(remoteReferrers).toEqual([undefined]);
  expect(
    await materialImage.evaluate(
      (element) => element.getBoundingClientRect().width <= element.parentElement!.getBoundingClientRect().width,
    ),
  ).toBe(true);

  const table = page.getByRole("table");
  const code = page.locator(".reading-markdown pre");
  await expect(table).toBeVisible();
  await expect(code).toBeVisible();
  expect(
    await table.evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBe(true);
  expect(
    await code.evaluate((element) => element.scrollWidth > element.clientWidth),
  ).toBe(true);
  const viewportOverflow = await page.evaluate(() => {
    const viewportWidth = document.documentElement.clientWidth;
    return Array.from(document.querySelectorAll<HTMLElement>("body *"))
      .filter((element) => {
        if (element.closest(".star-map-backdrop") !== null) {
          return false;
        }
        const scrollOwner = element.closest(
          ".reading-markdown table, .reading-markdown pre",
        );
        return scrollOwner === null || scrollOwner === element;
      })
      .map((element) => {
        const rect = element.getBoundingClientRect();
        return {
          selector:
            element.id ||
            element.className ||
            element.tagName.toLowerCase(),
          left: Math.round(rect.left),
          right: Math.round(rect.right),
          width: Math.round(rect.width),
        };
      })
      .filter(
        ({ left, right, width }) =>
          width > 0 && (left < -1 || right > viewportWidth + 1),
      );
  });
  expect(viewportOverflow).toEqual([]);
});

test("keeps the exact material across two chat cursors", async ({ page }) => {
  const eventCursors: number[] = [];
  page.on("request", (request) => {
    if (!request.url().includes("/api/v1/chat/sessions/") || !request.url().includes("/events")) {
      return;
    }
    eventCursors.push(Number(new URL(request.url()).searchParams.get("after") ?? "0"));
  });
  await page.goto("/");
  await dismissOnboarding(page);
  const resourceId = await page.getByRole("combobox", { name: "当前材料" }).inputValue();
  const composer = page.getByRole("textbox", { name: "发送消息" });

  await composer.fill("第一轮");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.locator(".chat-bubble--agent")).toHaveText(
    "（fixture",
  );
  await expect(page.getByText(new RegExp(`active_resource_id=${resourceId}.*第一轮`))).toBeVisible();

  await composer.fill("第二轮");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByText(new RegExp(`active_resource_id=${resourceId}.*第二轮`))).toBeVisible();
  await expect(page.locator(".chat-bubble--agent")).toHaveCount(2);
  await expect(composer).toHaveValue("");
  await composer.press("ArrowUp");
  await expect(composer).toHaveValue("第二轮");
  expect(eventCursors).toContain(0);
  expect(eventCursors.some((cursor) => cursor > 0)).toBe(true);
});

test("stops an active Chat turn at the backend", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const composer = page.getByRole("textbox", { name: "发送消息" });
  await composer.fill("请保持生成，等待我停止");
  await page.getByRole("button", { name: "发送" }).click();

  await page.getByRole("button", { name: "停止生成" }).click();

  await expect(page.getByText("已停止生成。")).toBeVisible();
  await expect(
    page.getByRole("button", { name: "停止生成" }),
  ).toBeHidden();
  await expect(page.getByRole("button", { name: "发送" })).toBeVisible();
});

test("navigates from Chat to Assessment and closes the trace", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const composer = page.getByRole("textbox", { name: "发送消息" });
  await composer.fill("请结合当前材料考我一题");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(
    page.getByRole("heading", {
      name: ASSESSMENT_QUESTION,
    }),
  ).toBeVisible();
  await expect(
    page.getByText(/收到：请结合当前材料考我一题/),
  ).toBeVisible();

  const evidence = page.getByRole("button", { name: "揭示本题材料证据" });
  await evidence.hover();
  await page.waitForTimeout(2500);
  await expect(evidence).toHaveAttribute("aria-expanded", "false");
  await expect(
    page.getByText("失败后继续当前 turn 会让后续副作用依赖不完整状态", {
      exact: true,
    }),
  ).toHaveCount(0);
  await expect(evidence).toHaveAttribute("aria-expanded", "true", {
    timeout: 1500,
  });

  await page
    .getByRole("radio", { name: "避免后续副作用依赖不完整状态" })
    .check();
  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(page.getByText("判断：对")).toBeVisible();
  await expect(page.getByText("本轮完成")).toBeVisible();

  await page.getByRole("button", { name: "打开运行观测" }).click();
  await expect(page.getByRole("dialog", { name: "运行观测" })).toContainText(
    "已完成",
  );
});

test("appeals an open-answer verdict without replacing the original answer", async ({
  page,
}) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const composer = page.getByRole("textbox", { name: "发送消息" });
  await composer.fill("请用简答题考我一题，我要测试判卷申诉");
  await page.getByRole("button", { name: "发送" }).click();

  await expect(
    page.getByRole("heading", {
      name: /durable processor|不完整状态/,
    }),
  ).toBeVisible();
  const originalAnswer = "我觉得主要是为了让系统运行得更快。";
  await page.getByRole("textbox", { name: "你的回答" }).fill(originalAnswer);
  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(page.getByText("判断：错")).toBeVisible();

  await page
    .getByRole("button", { name: "补充说明 / 判卷有异议" })
    .click();
  await page
    .getByRole("textbox", { name: "补充说明" })
    .fill("继续执行会让后续副作用依赖不完整状态，所以必须阻断当前 turn。");
  await page.getByRole("button", { name: "提交补充并重判" }).click();

  await expect(page.getByText("判断：对")).toBeVisible();
  await expect(page.getByText("原判：错；重判：对")).toBeVisible();
  await expect(
    page.getByText("结合补充说明，已覆盖阻断原因。"),
  ).toBeVisible();
  await expect(page.getByRole("textbox", { name: "你的回答" })).toHaveValue(
    originalAnswer,
  );
});

test("records a voice answer, reviews the transcript, and submits it once", async ({
  page,
}, testInfo) => {
  test.skip(testInfo.project.name === "mobile", "v0.5 voice capture targets desktop Chromium");
  await page.addInitScript(() => {
    const track = { stop: () => undefined };
    Object.defineProperty(navigator, "mediaDevices", {
      configurable: true,
      value: { getUserMedia: async () => ({ getTracks: () => [track] }) },
    });
    class FixtureMediaRecorder {
      static isTypeSupported() {
        return true;
      }
      state = "inactive";
      ondataavailable: ((event: { data: Blob }) => void) | null = null;
      onstop: (() => void) | null = null;
      constructor(
        readonly stream: unknown,
        readonly options: { mimeType?: string } = {},
      ) {}
      start() {
        this.state = "recording";
      }
      stop() {
        this.state = "inactive";
        this.ondataavailable?.({
          data: new Blob(["fixture-voice"], {
            type: this.options.mimeType,
          }),
        });
        this.onstop?.();
      }
    }
    Object.defineProperty(globalThis, "MediaRecorder", {
      configurable: true,
      value: FixtureMediaRecorder,
    });
  });

  await page.goto("/");
  await dismissOnboarding(page);
  await page.getByRole("textbox", { name: "发送消息" }).fill("请用简答题考我一题");
  await page.getByRole("button", { name: "发送" }).click();
  await expect(page.getByRole("heading", { name: /durable processor|不完整状态/ })).toBeVisible();

  const answerBox = page.getByRole("textbox", { name: "你的回答" });
  await answerBox.fill("我先写下的文字草稿。");
  await page.getByRole("button", { name: "开始语音回答" }).click();
  await expect(page.getByRole("status")).toContainText("正在录音");
  await page.getByRole("button", { name: "结束录音并识别" }).click();

  const transcript = "继续执行会让后续副作用依赖不完整状态，所以必须阻断当前 turn。";
  await expect(page.getByText("请选择如何使用识别草稿")).toBeVisible();
  const draft = page.getByRole("textbox", {
    name: "识别草稿（请确认或修改后提交）",
  });
  await expect(draft).toHaveValue("我先写下的文字草稿。");
  await page.getByRole("button", { name: "追加到回答" }).click();
  await expect(draft).toHaveValue(`我先写下的文字草稿。\n\n${transcript}`);
  await draft.fill(`${transcript} 这是我确认后的表述。`);
  await page.getByRole("button", { name: "提交答案" }).click();

  await expect(page.getByText("判断：对")).toBeVisible();
  await expect(page.getByText("本轮完成")).toBeVisible();
});

test("cancels an abandoned Assessment before returning to reading", async ({
  page,
}) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const assessmentStarted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/assessments"),
  );
  await page
    .getByRole("textbox", { name: "发送消息" })
    .fill("请结合当前材料考我一题");
  await page.getByRole("button", { name: "发送" }).click();
  const started = (await (await assessmentStarted).json()) as {
    trace_id: string;
  };
  await expect(
    page.getByRole("heading", {
      name: ASSESSMENT_QUESTION,
    }),
  ).toBeVisible();

  await page.getByRole("button", { name: "结束考核" }).click();
  await expect(page.getByRole("main", { name: "文章内容" })).toBeVisible();

  const snapshot = await page.request.get(
    `/api/v1/observability/traces/${started.trace_id}`,
  );
  expect(snapshot.ok()).toBe(true);
  expect((await snapshot.json()).status).toBe("cancelled");
});

test("opens the exact generation-degraded trace and keeps it after returning to reading", async ({
  page,
}) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const assessmentStarted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/assessments"),
  );
  await page
    .getByRole("textbox", { name: "发送消息" })
    .fill("请触发生成降级并考我一题");
  await page.getByRole("button", { name: "发送" }).click();
  const started = (await (await assessmentStarted).json()) as {
    trace_id: string;
  };
  await expect(
    page.getByRole("heading", { name: "本题暂时无法生成" }),
  ).toBeVisible();

  const firstTraceRead = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(
        `/api/v1/observability/traces/${started.trace_id}`,
      ),
  );
  await page.getByRole("button", { name: "查看本次运行" }).click();
  expect((await firstTraceRead).ok()).toBe(true);
  await expect(page.getByRole("dialog", { name: "运行观测" })).toBeVisible();

  await page
    .getByRole("dialog", { name: "运行观测" })
    .getByRole("button", { name: "关闭运行观测" })
    .click();
  await page.getByRole("button", { name: "结束考核" }).click();
  await expect(page.getByRole("main", { name: "文章内容" })).toBeVisible();

  const secondTraceRead = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(
        `/api/v1/observability/traces/${started.trace_id}`,
      ),
  );
  await page.getByRole("button", { name: "打开运行观测" }).click();
  expect((await secondTraceRead).ok()).toBe(true);
});

test("opens the exact grading-degraded trace", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const assessmentStarted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/assessments"),
  );
  await page
    .getByRole("textbox", { name: "发送消息" })
    .fill("请触发判卷降级，用简答题考我一题");
  await page.getByRole("button", { name: "发送" }).click();
  const started = (await (await assessmentStarted).json()) as {
    trace_id: string;
  };
  await expect(
    page.getByRole("heading", { name: ASSESSMENT_QUESTION }),
  ).toBeVisible();
  await page
    .getByRole("textbox", { name: "你的回答" })
    .fill("继续会依赖不完整状态。");
  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(
    page.getByRole("heading", { name: "本题暂时无法判卷" }),
  ).toBeVisible();

  const traceRead = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(
        `/api/v1/observability/traces/${started.trace_id}`,
      ),
  );
  await page.getByRole("button", { name: "查看本次运行" }).click();
  expect((await traceRead).ok()).toBe(true);
  const drawer = page.getByRole("dialog", { name: "运行观测" });
  await expect(drawer).toBeVisible();
  await drawer.getByRole("button", { name: "关闭运行观测" }).click();
  await page.getByRole("button", { name: "结束考核" }).click();
  await expect(page.getByRole("main", { name: "文章内容" })).toBeVisible();
});

test("opens the exact fatal assessment trace", async ({ page }) => {
  await page.goto("/");
  await dismissOnboarding(page);
  const assessmentStarted = page.waitForResponse(
    (response) =>
      response.request().method() === "POST" &&
      response.url().endsWith("/api/v1/assessments"),
  );
  await page
    .getByRole("textbox", { name: "发送消息" })
    .fill("请触发致命失败并考我一题");
  await page.getByRole("button", { name: "发送" }).click();
  const started = (await (await assessmentStarted).json()) as {
    trace_id: string;
  };
  await expect(
    page.getByRole("heading", { name: "无法开始考核" }),
  ).toBeVisible();
  await expect(page.getByRole("alert")).toContainText(
    "本轮考核失败，请通过 trace_id 查看详情",
  );

  const traceRead = page.waitForResponse(
    (response) =>
      response.request().method() === "GET" &&
      response.url().endsWith(
        `/api/v1/observability/traces/${started.trace_id}`,
      ),
  );
  await page.getByRole("button", { name: "查看本次运行" }).click();
  expect((await traceRead).ok()).toBe(true);
  await expect(page.getByRole("dialog", { name: "运行观测" })).toBeVisible();
  await expect(page.getByText("失败", { exact: true })).toBeVisible();
});
