# Open-source v0.5.0 发布检查清单

> 更新日期：2026-08-12
>
> 目标：发布可审查 Voice Interview、桌面工作区与上下文预算观测，不追加实时语音或数据飞轮。
>
> 本清单不自动授权 push、tag、GitHub Release 或 PyPI 上传；这些动作仍须仓库所有者明确批准。

## 1. 发布边界

v0.5.0 承诺桌面 Chromium 的完整录音上传、可编辑转写草稿、材料级有界术语、持久 VoiceRun 与唯一
Assessment 提交。它不承诺实时字幕、TTS、双工面试官、移动端、多用户、公网投产、自动语义清理或语音数据飞轮。

一并发布的非语音能力只有已经独立提交并通过同一回归门的桌面工作区收口与 `/status` 上下文预算观测；不再
趁发布增加新功能。

## 2. 版本、兼容与文档

- [x] `pyproject.toml`、`grandquiz.__version__` 与锁文件统一为 `0.5.0`。
- [x] README、文档索引、产品边界、Voice 设计契约与 Release Notes 描述同一组行为。
- [x] `.env.example` 只含安全示例，并明确 DashScope、ASR 模型与材料词表默认值。
- [x] 历史 v0.4.0 Release Notes/清单保持不变。
- [x] 实现与设计契约统一为“结束录音后立即上传，上传后回放并审查草稿”，没有虚构 `recorded` 阶段。

## 3. 代码与浏览器门

- [x] Ruff lint 与 format check。
- [x] Pyright 与 import-linter。
- [x] 全量 pytest（1076 passed）。
- [x] 离线 Eval 17/17，不读取真实 `.env` 或调用外部模型。
- [x] Web lint、typecheck、unit（69 passed）、OpenAPI、Sites worker 与 production package build。
- [x] Playwright 桌面/移动端（23 passed / 1 skipped；跳过项为本版明确不承诺的移动端语音）。
- [x] `v0.4.0...HEAD` Standards / Spec 双轴审查没有未解决 P0/P1；焦点可见性 P2 已修，设计漂移 P2 已收窄。

## 4. 仓库与供应链

- [x] 工作区只包含可解释的 v0.5 与 release-prep 变更。
- [x] `.env`、API Key、个人绝对路径、数据库、音频、Trace、`.scratch`、`localtemp` 与 Playwright artifact 未进入提交。
- [x] wheel/sdist metadata 为 0.5.0、MIT、Python 3.12+，项目链接正确。
- [x] wheel 包含最新 Web 静态资源、prompt、Eval fixtures、词表和 Voice Provider/迁移。
- [x] 从仓库外安装 wheel，运行 `grandquiz --help`、离线 `grandquiz report`，并验证 loopback Web health、首页与 SPA fallback。

## 5. 窄版人工 dogfood

- [x] owner 已完成桌面 Web 录音、转写草稿审查、设置开关和正式答案提交。
- [x] 四条固定音频 hints off/on 真实调用 8/8 成功；目标术语改善、负样本零插入、8/8 离线 replay。
- [x] 取消、重试、服务重启收敛、TTL、StrictMode 与旧题提交边界有确定性测试。
- [x] 原始音频不落盘，Trace/cassette 不保存音频 bytes、API Key、转写正文或术语正文。

真实证据见 [v0.5 Voice Interview 实现记录](devrecords/45-v050-voice-interview-implementation.md)。真实 Provider
dogfood 会产生费用并发送音频，后续重录仍必须由仓库所有者显式授权。

## 6. 已接受的非阻断项

- [x] Chat `/status` 与 Observatory 已共用同一个 Trace token usage projector。
- [ ] production Web 主 JavaScript chunk 约 546 kB，Vite 仅给出体积警告；local-first 桌面体验未阻断，后续按真实首屏指标再拆包。
- [ ] v0.5 真实 ASR 样本规模不足以给出通用准确率结论；继续使用显式材料词表开关和用户草稿审查兜底。

## 7. 最终 Go/No-Go

创建正式发布前必须满足：

- [x] release commit 已推送，且发布时 `origin/main == HEAD`；
- [ ] GitHub Actions 发布运行状态由发布会话单独核验；本地仓库不代替外部 CI 证据；
- [x] 本地质量门、安装产物 smoke、双轴审查和窄版 dogfood 通过，没有未解决 P0/P1；
- [x] Release Notes 已定稿，未通过门的自动判卷/自动澄清与未来实时语音没有被包装成已交付能力；
- [x] 仓库所有者已批准并创建 `v0.5.0` tag。
- [ ] GitHub Release 状态由发布会话单独核验；功能仓库不据本地 tag 推断外部发布状态。

历史发布命令：

```bash
git tag -a v0.5.0 -m "TheGrandQuiz v0.5.0"
git push origin v0.5.0
```

随后创建非 draft、非 prerelease 的 `v0.5.0` GitHub Release，粘贴
[`docs/releases/v0.5.0.md`](releases/v0.5.0.md) 并上传从 release commit/CI 构建的 sdist/wheel。PyPI 上传
继续作为独立决策。
