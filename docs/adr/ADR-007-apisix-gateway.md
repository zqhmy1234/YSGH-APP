# ADR-007: L5 用 Apache APISIX，不把业务规则搬进网关

- 状态：proposed（待 staging 验证）
- 背景：计划 §8
- 决策：
  1. Internet → nginx(TLS) → APISIX 127.0.0.1:9080 → backend / agent-v2 / LiteLLM。
  2. 网关只做鉴权、限流、CORS、request-id、body size、timeout、canary、access log。
  3. 参数归一 / 结果裁剪留在 L3；`/api/v1/*` 与 `/agent/v1/*` 分路由、分鉴权。
- 后果：
  - 好处：成熟插件、可观测性、灰度能力。
  - 代价：多一个进程/配置面；需验证 401/429/413/504 与 100 并发 p95。
- 门禁（待建）：
  - `deploy/apisix/*` 配置 + staging 安全矩阵
  - 5%→100% 影子对比
