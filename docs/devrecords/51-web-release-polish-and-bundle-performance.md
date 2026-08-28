# Web 发布收口与 Bundle 性能

日期：2026-08-28

## 为什么要优化

v0.5 工作台加入统一运行反馈、星图背景、设置与语音答题后，Vite 的生产构建把所有功能打进一个
`548.92 kB`（gzip `160.97 kB`）的主 JS。它不会明显拖慢 FastAPI 启动，但会增加浏览器首次下载、解析和执行
成本，低配置设备与冷缓存更容易感知。

这次没有调高警告阈值，也没有为了“文件看起来更小”强行拆 vendor。真正的加载边界是：

- 应用外壳、资源选择、Chat 输入和状态栏首屏可用；
- Markdown/GFM 只在文章或 Agent Markdown 回复出现时加载；
- 考核与语音答题组件只在进入 Assessment 后加载。

设置、入库、Eval 与 Observatory 抽屉暂不改卸载语义，避免为了 bundle 数字丢失用户尚未提交的面板状态。

## 实现

`App` 通过 `React.lazy + Suspense` 延迟加载 `ContinuousDocument` 与 `AssessmentPanel`，并用统一的
`ActivityIndicator` 告知当前加载阶段。`ChatPanel` 只在出现 Agent 回复时加载 `SafeMarkdown`；加载期间仍展示
原始文本，不阻塞已经到达的流式内容。

懒加载引入了真实异步边界，因此测试也不再假设 Markdown 和考核模块在同一个同步 tick 内完成。考核取消测试
先等待后端 Assessment 已创建，再验证切回阅读时发出取消命令。

## 结果

生产构建从单一主包变成以下关键 chunk：

| 产物 | 原始大小 | gzip |
| --- | ---: | ---: |
| 首屏主包 | 356.73 kB | 104.39 kB |
| SafeMarkdown | 158.80 kB | 48.21 kB |
| AssessmentPanel | 32.76 kB | 10.00 kB |
| ContinuousDocument | 1.56 kB | 0.77 kB |

首屏主包相较基线减少 `192.19 kB`，gzip 减少 `56.58 kB`，两者都约为 **35%**；Vite 的 500 kB chunk
警告消失。文章存在时 Markdown chunk 仍会随后加载，但应用外壳可以先解析和呈现；没有文章、尚未收到回复或
未进入考核时，不再为这些能力支付解析成本。

## 验收

- Python：`1140 passed`；
- Web unit：`76 passed`；
- Playwright：桌面/移动端 `23 passed, 1 skipped`，移动端语音按既有范围跳过；
- Ruff、格式、Pyright、import-linter、ESLint、TypeScript 与 OpenAPI drift 全部通过；
- 内置浏览器确认文章与设置交互正常，页面没有框架错误或 console warning/error。
