# 功能 RC 收口：把“能跑”变成“不会悄悄跑偏”

日期：2026-07-28

这一步没有增加新功能。它处理的是四类发布候选版风险：不可信材料的浏览器副作用、考核
trace 不闭合、并发 Chat 污染当前材料，以及已经失效的浏览器验收链。

## 1. Markdown 图片为什么不是普通排版问题

材料正文是不可信输入。旧实现把它直接交给 `react-markdown`：

```tsx
<Markdown>{node.content}</Markdown>
```

如果正文包含 `![图](https://example.invalid/pixel)`，浏览器会主动请求这个地址。请求发生在
用户看到页面时，绕开了后端 acquisition 的域名、大小和超时边界。

现在 `SafeMarkdown` 是唯一入口。图片语法只产生一个说明性占位，不产生 `<img src>`：

```tsx
const safeComponents = {
  img: ({ alt, src }) => (
    <span role="note">图片已阻止：{alt}（{src}）</span>
  ),
};
```

`App`、旧 `ArticleWorkspace` 和 Chat 都复用这个模块。安全策略与 GFM 配置因此只有一份，
以后不会出现“文章页修了，Chat 仍然会加载”的漂移。

2026-08-26 的体验补丁没有回退这条边界，而是把此前遗漏的“显式加载”补齐：材料阅读区的占位会提供
逐图加载按钮，只有用户点击后才创建限制为 `http(s)`、`no-referrer`、lazy loading 的 `<img>`；加载失败
回到可重试状态。Chat 继续使用默认硬拦截。Playwright 同时锁定“点击前零请求、点击后恰好一次请求、宽图
不撑破正文”，因此正常材料图片可读，但不可信 Markdown 仍不能在渲染时自动产生浏览器副作用。

## 2. Assessment trace 如何真正结束

原来 HTTP 会话的内存状态会变成 `completed`，但事件脊柱不知道这一点。Observatory 只能看到
出题、判卷等中间事件，于是可能一直判断为“运行中”。

现在每次 Web Assessment 都有一个根 span：

```text
web.assessment_run.started
  ├─ learning.question_asked
  ├─ learning.answer_judged
  └─ web.assessment_run.ended(status=completed|failed|cancelled)
```

异常还会先发一个脱敏 `error` 事件，所以 `error_count` 与终态一致。应用关闭时，正在等待
provider 或用户输入的任务会收到取消，并写入 `cancelled`，不会留下悬空 span。

第一次双轴审查还发现：“结束考核”当时只关闭 React 界面，后端仍在等待答案。现在关闭按钮会先
发送幂等的 `DELETE /api/v1/assessments/{session_id}`，等待任务写完 `cancelled` 终态后才回到
阅读页；即使第一题仍在生成，也会等 session 建立后再取消。

更新后的 Standards 复审又沿着 Chat 导航找到同一问题的旁路：`open_article` 或再次
`start_assessment` 会直接替换中间工作区。现在 `App` 在执行任何 Chat 工作区切换前，都会通过
`AssessmentPanel` 暴露的统一取消句柄关闭旧 session；只有取消成功才切换，开始新考核时还会创建
新的面板代次，避免复用旧题目与请求状态。

## 3. 为什么 Chat 选择“拒绝并发”

一个 Chat session 复用同一个 Runner、history 和当前材料上下文。如果两次消息并发进入，第二次
可能在第一轮仍运行时改写 `active_resource_id`。这会让“请结合当前材料”偶发引用另一篇文章。

RC 没有引入队列，而是采用最小、可解释的规则：

```text
session running + 新 message -> 409 turn_in_progress (retryable=true)
```

检查发生在改写材料 scope 之前。首轮一旦被接受，它看到的 exact resource 在整轮中保持不变。

## 4. 浏览器为什么只看八类事件

内部事件名会随着 runtime 深化而增加。如果前端直接消费它们，每次内部重构都会变成公开 API
变更。安全投影现在只提供：

```text
run / model / tool / assessment / approval / recovery / error / runtime
```

内部名字仍完整保存在 TraceStore，供审计和 replay 使用；浏览器只拿稳定类别、时间、token、
latency 和脱敏状态。新建 `TraceObservatory` 也会直接从已有 TraceStore 恢复历史，不依赖进程内
注册表。

## 5. Web Scenario Bot 怎样守住这条链

Playwright 使用 scripted provider 和一次性 SQLite，不调用真实模型。桌面与移动端固定检查：

- Markdown 图片零网络请求，宽表格和长代码只在阅读面内部滚动；
- 两轮 Chat 从上次 sequence 继续，且 exact resource 不变；
- Chat 工具导航到 Assessment，Evidence 悬停满三秒才显示；
- 提交答案后界面与 Observatory 都进入完成态。

失败时 CI 上传 screenshot、Playwright trace、HTML report、浏览器 console/network 错误和
`trace_id`。结束时 Python 审计临时 `trace.db`：每条 sequence 必须从 0 连续，Chat turn 与
Assessment run 的 started/ended 数量必须平衡。

这套 Bot 在落地时额外发现了一个真实问题：390px 视口会被材料选择器的最小宽度撑到 535px。
修复后，标题、文章、Chat 与页脚都保持在视口内，只有表格和代码容器拥有局部横向滚动。
