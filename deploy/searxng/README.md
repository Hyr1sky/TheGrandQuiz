# 可选本地 SearXNG

这是仅供个人本机使用的最小单容器配置：监听 `127.0.0.1:8080`，关闭公共实例才需要的 limiter，
不启动 Valkey 或反向代理，并显式开启 JSON Search API。

```bash
docker compose -f deploy/searxng/compose.yaml up -d
SEARXNG_URL=http://127.0.0.1:8080 uv run grandquiz search "MySQL 面试高频考点"
docker compose -f deploy/searxng/compose.yaml down
```

如需长期运行，可在 shell 或 Docker Compose 环境中设置随机的 `SEARXNG_SECRET`，并用
`SEARXNG_IMAGE` 固定经过验证的镜像 tag。默认使用官方 GHCR 镜像；如网络环境更适合 Docker Hub，可改为
`docker.io/searxng/searxng:latest`。该配置只绑定 loopback，不要未经限流、认证与反向代理加固就暴露到公网。
