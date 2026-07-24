import { expect, test } from "@playwright/test";

test("reads, asks, traces, reveals evidence, and changes theme", async ({ page }) => {
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
    page.getByText("失败后继续当前 turn 会让后续副作用依赖不完整状态"),
  ).toBeVisible();

  await page.getByRole("button", { name: "切换至亮色模式" }).click();
  await expect(page.locator("html")).toHaveAttribute("data-theme", "light");
});
