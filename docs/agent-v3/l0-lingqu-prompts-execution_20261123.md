# L0 + Lingqu + 提示词/角色卡 执行报告

> 2026-11-23 · 工作分支 `feature/agent-v3-harness` · 测试环境 `agent/.venv312`（Python 3.12.16）

## 用户目标与完成口径

- L0 真接线（Postgres/Redis/Qdrant/MinIO + agent）
- 模型提供商小模块：5 家（DeepSeek/Qwen/Kimi/MiniMax/GLM），统一公司 Key，细致配置，默认 DeepSeek
- 复用旧提示词；10 段提示词 Bundle；角色卡系统；参考成熟角色卡实现
- 必须有审判 Agent，覆盖新增模块

## 交付清单

| 模块 | 文件 |
|---|---|
| 模型目录 | `agent/config/lingqu-catalog.json` |
| Lingqu | `agent/src/agentv3/lingqu/{contracts,catalog,credentials,errors,engine,router,telemetry,gateway,admin,cli}.py` |
| API | `agent/src/agentv3/api/routes.py`（models/cards/health） |
| V3 标签 | `agent/src/agentv3/domain/tagging.py`、`contracts.TagSet` |
| 13 工具 | `agent/src/agentv3/services/tools.py` |
| 提示词 | `agent/src/agentv3/prompts/bundle.py` + 8 个 `.md` 资产 |
| 角色卡 | `agent/src/agentv3/prompts/cards.py` |
| 迁移 | `backend/migrations/versions/b8c4d2f0a1e5_add_agent_v3_tags_and_links.py` |
| L0 | `deploy/compose.agent-v3.yml`、`deploy/Dockerfile.agentv3`、`scripts/dev/agent_v3_smoke.sh` |
| 审判 | `agent/src/agentv3/judge/{checks,mutation,probes}.py` 扩展 |
| 文档 | 本目录 4 篇 |

## 测试证据

```bash
cd agent
PYTHONPATH=src .venv312/bin/pytest tests/agentv3 tests/contract -q
```

结果：**249 passed**（基线 195 + 54 新增/改动；新增覆盖 Lingqu 17、标签 5、Sculptor 标签 4、工具 10、提示词 4、卡片 5、Lorebook 4、模型/卡 API 5，以及 PG 投影回归 3）。

```bash
PYTHONPATH=src .venv312/bin/python -m agentv3.lingqu.cli catalog-check
# {"catalog_version":"2026-11-23.1","profiles":13,"default_profile":"deepseek/deepseek-v4-flash"}
```

审判：`agent/src/agentv3/judge` 现有 **18 个变异 + 11 个探针**；新增 8 个变异（默认档、日志泄 key、400 回退、卡移动 guard、probability=0、预算失效、知识标签落 FACT、删除跨人）与 3 个探针。实测：**18/18 killed、11/11 probes ok、static P0=0、suite exit=0，verdict=pass**（`docs/agent-v3/judge-report.json`）。

## 未完成 / 风险（不伪称）

1. **Docker 未运行**：本机无 Docker；compose 只做 YAML/语法校验，未 `up`。smoke 未实际跑。
2. **真实厂商未调用**：无 `COMPANY_LLM_API_KEY`；所有 Lingqu 结果来自 `FakeEngine`。`LINGQU_LIVE=1` 才会发真实请求。
3. **13 工具中的 `fetch_url_content`** 仅注入 callable；没有生产 HTML 抽取适配器（旧 `yishu.fetch` 未接）。当前只有 SSRF 校验与接口。
4. **`update_memory_summary`** 是进程内摘要缓存，不落库；重启丢失。不是账本条目。
5. **角色卡只支持 JSON**，PNG/内嵌卡未做。
6. **Lorebook** 只做 `before_char` 文本注入，`position`/`depth` 字段保留未完全实现。
7. **PG 投影表** 需跑新迁移；未在真实 PG 上验证 upsert。
8. **OpenAPI golden** 已随 models/cards 路由更新；任何新路由都必须同步该文件。
