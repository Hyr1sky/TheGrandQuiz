# Security Policy

TheGrandQuiz 是 local-first、单用户软件。当前版本没有账号、鉴权或公网部署安全承诺。

## Supported versions

安全修复只面向最新的 `main` 和最新发布版本。当前由个人维护，不提供响应时限承诺。

## Report a vulnerability

请优先使用 GitHub 仓库的 **Security → Report a vulnerability** 私密报告入口，并提供：

- 受影响的 commit/tag 和操作系统；
- 最小复现步骤；
- 预期影响；
- 可安全分享的 trace_id 或脱敏日志。

不要在公开 issue 中粘贴 API Key、`.env`、私人材料、完整 prompt、模型输出或数据库文件。如果私密报告
入口不可用，请只开一个不含漏洞细节的 issue，请求维护者建立私密沟通渠道。

## Security boundaries

### Local Web

`grandquiz-web` 默认只监听 `127.0.0.1`，没有用户系统或鉴权。不要通过 `0.0.0.0`、反向代理、端口转发
或隧道直接暴露到局域网/公网。若自行改变绑定范围，认证、TLS、CSRF、速率限制和访问控制均由部署者负责。

### Secrets

凭证只应放在被 gitignore 的 `.env` 中。不要提交 key，不要把 `.env` 作为 bug 附件。怀疑泄漏时应立即在
供应商侧撤销/轮换，并检查 Git 历史和 CI artifact；从最新提交删除并不能清除历史泄漏。

### External LLMs

真实调用会向配置的 OpenAI-compatible 服务发送 system prompt、用户消息、选定材料节点和工具上下文。
只处理你有权发送给该供应商的内容，并阅读供应商的数据保留、训练和地域政策。

### Web content and prompt injection

搜索结果和抓取正文始终是不可信输入。Search 不自动 Fetch，Fetch 不自动入库；系统使用域名/大小/超时/
质量限制、untrusted 标记、工具契约和人工审批降低风险。任何绕过这些边界、让网页指令获得系统权限的行为
都应视为安全问题。

### Local data and traces

`~/.grandquiz/learning.db` 包含材料结构、知识点和学习状态；`~/.grandquiz/trace.db` 可能包含用户消息、
工具参数、模型输出、引用片段和错误。它们不应上传到公开 issue。备份和清除方法见 README。

### Rendered content

动态 Markdown 统一经过安全 renderer；远程图片默认不会自动加载。材料阅读区只有在用户逐图点击确认后，
才允许 `http(s)` 图片以 `no-referrer` 方式加载；Chat 等其他 Markdown 投影继续保持硬拦截。发现浏览器在
未确认时请求材料中的远程 URL、执行脚本或突破内容 containment，请按安全漏洞报告。
