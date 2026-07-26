import { expect, test } from "@playwright/test";

test("reads, asks, traces, reveals evidence, and changes theme", async ({ page }) => {
  await page.addInitScript(() => {
    window.localStorage.setItem("grandquiz-theme", "dark");
  });
  await page.goto("/");

  await expect(
    page.getByRole("heading", { name: "Agent Runtime：事件总线与可恢复执行" }),
  ).toBeVisible();
  await page.getByRole("button", { name: /Durable processors/ }).click();
  await expect(
    page.getByText(/durable processor 订阅事件并执行有状态逻辑/),
  ).toBeVisible();

  await page
    .getByRole("textbox", { name: "针对当前材料的问题" })
    .fill("为什么 durable processor 失败必须阻断当前 turn？");
  await page.getByRole("button", { name: "向材料提问" }).click();

  await expect(page.getByText(/破坏事件历史的因果一致性与可重放性/)).toBeVisible();
  await expect(page.getByRole("button", { name: /Runtime > Durable processors/ })).toBeVisible();
  await page.getByRole("button", { name: "揭示证据" }).click();
  await expect(
    page.getByText("失败后继续当前 turn 会让后续副作用依赖不完整状态", {
      exact: true,
    }),
  ).toBeVisible();

  await page.getByRole("button", { name: "切换至亮色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});

test("starts a scoped assessment, reveals evidence, and judges one answer", async ({
  page,
}) => {
  await page.goto("/");
  await page.getByRole("button", { name: "进入考核模式" }).click();

  await expect(
    page.getByRole("heading", { name: "开始一轮考核" }),
  ).toBeVisible();
  await page.getByRole("combobox", { name: "题目数量" }).selectOption("1");
  await page.getByRole("combobox", { name: "题型" }).selectOption("选择题");
  await page.getByRole("button", { name: "生成第一题" }).click();
  // Keep the pointer away while the question replaces the setup form; otherwise the
  // evidence veil can legitimately reveal as it renders underneath the last click.
  await page.mouse.move(1, 1);

  await expect(
    page.getByRole("heading", {
      name: /durable processor 失败后为什么要阻断当前 turn？/,
    }),
  ).toBeVisible();

  const evidence = page.getByRole("button", { name: "揭示本题材料证据" });
  await expect(evidence).toHaveAttribute("aria-expanded", "false");
  await evidence.hover();
  await expect(evidence).toHaveAttribute("aria-expanded", "true");
  await expect(
    page.getByText("失败后继续当前 turn 会让后续副作用依赖不完整状态", {
      exact: true,
    }),
  ).toBeVisible();

  await page
    .getByRole("radio", { name: "避免后续副作用依赖不完整状态" })
    .check();

  await page.getByRole("button", { name: "提交答案" }).click();
  await expect(page.getByText("判断：对")).toBeVisible();
  await expect(page.getByText("本轮完成")).toBeVisible();
  await expect(page.getByText(/trace_id:/)).toBeVisible();
});
