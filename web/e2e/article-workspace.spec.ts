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

test("blocks Markdown network images and contains truly wide content", async ({
  page,
}, testInfo) => {
  const remoteRequests: string[] = [];
  page.on("request", (request) => {
    if (request.url().includes("attacker.invalid")) {
      remoteRequests.push(request.url());
    }
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
  await expect(page.getByRole("img")).toHaveCount(0);
  expect(remoteRequests).toEqual([]);

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
  expect((await snapshot.json()).summary.status).toBe("cancelled");
});
