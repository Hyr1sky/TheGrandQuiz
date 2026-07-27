# 04 — 左栏上下文切换 + 视觉合规

**What to build:** 左栏根据主面板状态自动展示相关内容（文档大纲 / 考核进度 / 搜索候选），用户可手动覆盖。视觉对照 `docs/design/web-visual-language.md` 全面合规。

**Blocked by:** 03-navigation-tools-panel-switching

**Status:** ready-for-agent

- [ ] 左栏状态跟随主面板：`reading` → 文档大纲、`assessment` → 考核进度一览（第几题/判决状态）
- [ ] 左栏手动覆盖：用户可点击切换回其他视图，不被自动切换覆盖直到主面板状态再次变化
- [ ] 统一左栏字体：interface 区域全部使用 `--font-interface`，大纲序号不再单独用 `--font-reading`
- [ ] 收敛 neumorphism：只保留 ask/reveal/theme/compact run 控件的 raised/pressed 阴影，其他按钮回到平面样式
- [ ] 减少 hover 动画：去掉全局 `translateY(-2px)` + `shadow-hover`，改为 border-color 变化
- [ ] 三栏独立滚动验证：左栏、主面板、右栏各自 `overflow: auto`，不出现全页滚动
- [ ] 底栏罗盘导航：当前状态定位（阅读/考核/搜索）+ 运行轨迹，视觉呼应墨迹星图设计语言
- [ ] `html lang="zh-CN"` 已在之前修复，此处验证所有新增页面元素也正确
- [ ] 响应式断点对照设计稿：>= 1180px 三栏、760-1179px 紧凑双栏、< 760px 单列
- [ ] Vitest 测试：左栏自动切换逻辑、手动覆盖行为
