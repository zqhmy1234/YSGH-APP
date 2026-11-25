# L5 Apache APISIX（旁路，未切流）

拓扑（计划 §8.2）：

```
Internet → nginx(TLS) → APISIX 127.0.0.1:9080
  → backend   127.0.0.1:8010
  → agent-v2  127.0.0.1:8400（staging 旧服务保持 8300）
  → LiteLLM   127.0.0.1:4000（可选 /model/v1）
APISIX Admin 127.0.0.1:9180，不对公网
```

## 路由草案

| 路由 | 上游 | 鉴权 | 限流 |
|---|---|---|---|
| `/api/v1/*` | backend | JWT | 60/min/user |
| `/agent/v1/*` | agent-v2 | key-auth + IP allowlist | 20/min |
| `/wechat/callback` | backend | 应用内验签 | 30/min/IP |
| `/model/v1/*` | LiteLLM | key-auth | 按 consumer |
| `/health` | 网关 | 无 | 无 |

## 状态

**proposed（2026-11-23）**：本目录只有路由契约，没有可运切的 `config.yaml`。
理由：ADR-007 尚未在 staging 验证 401/429/413/504 与 100 并发 p95 增量；
按计划 §8.4，验证通过后才允许 5%→100% 切流。不要在没有这份证据时改 nginx upstream。
