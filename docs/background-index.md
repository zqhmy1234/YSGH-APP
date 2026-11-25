# 背景资料索引（每个模块看哪一节）

> 规则来自 `docs/任务卡_B5记忆域_B7有人味域_20261123.md` §0：
> **任务卡没有 `background.docs` 或章节不存在 → 审判直接 `FAIL: missing_background`。**

| 模块 | 背景文档 · 章节 | 解决哪个设计点 |
|---|---|---|
| `agent/src/harness/contracts.py` | `docs/架构重构总计划_v3_20261123.md` §6.2；`docs/agent-v3/五Agent内环说明.md` §0 | TurnPlan / 六类意图 / frozen DTO |
| `agent/src/harness/plan.py` | 同上 §6.1/§6.4；45 §4.3 | 纯函数路由、MIXED、TRUST 0 LLM |
| `agent/src/harness/orchestrator.py` | 同上 §6.3；45 §3.2/§4.4 | 固定管线、危机短路、降级不编造 |
| `agent/src/harness/context.py` | 45 §4.4；`docs/设计_B5记忆域_B7有人味域_深度设计_20261123.md` §7 | 九段顺序 / 预算 / 裁剪 / 受保护段 |
| `agent/src/prompts/role_card.py` | 设计 §13；`docs/agent-v3/prompt-and-cards.md` | 内部 RoleCard、V2 只做导入导出、12 卡 |
| `agent/src/harness/empathy.py` | 设计 §12.1；45 §4.5/§5.7；需求 35/45 | 共情三轴、禁语单一来源、fail-open/fail-closed |
| `agent/src/harness/persona.py` | 设计 §12.2；45 §7.2 | 五维封闭枚举、下一轮生效、无全局单例 |
| `agent/src/harness/emotion.py` | 设计 §12.3；45 §5.7；需求 39/40 | 无 confidence、not_measured、用户纠正 |
| `agent/src/harness/crisis.py` | 设计 §12.4；45 §5.6；需求 37 | T3 不进 Agent、无资源号、无"已联络" |
| `agent/src/ports/__init__.py` | 计划 §3.3/§3.4；45 §6.2 | LLM/Backend/Checkpoint/Clock/Trace 五端口 |
| `agent/src/adapters/backend_http.py` | 计划 §3.3；`docs/OpenAPI契约.md` | 六端点、错误映射、不直连 DB |
| `shared/contracts/` | 计划 §3.1；45 §6.3 | 跨进程 DTO、字段一致 |
| `backend/app/ports/` | 计划 §3.2；45 §6.2；架构总图 §二 | 15 端口、三态、Protocol-only |
| `backend/app/domain/memory/` | 设计 §5.3；45 §5.5 | 冲突优先级 / 两阶段写入 |
| `backend/app/domain/profile/` | 设计 §6.2；45 §4.4 | 8 段投影、≤600、三态 |
| `backend/app/domain/person/` | 设计 §4.2 E；45 §7.2 | 用户确认人物、name_norm |
| `backend/app/domain/guard/` | 设计 §12.1；45 §5.7 | 与 Agent 共用禁语表 |
| `backend/app/domain/crisis/` | 设计 §12.4；45 §5.6 | T1/T2/T3、固定话术、12356 前置门 |
| `backend/app/api/agent_profile.py` | 计划 §3.3/§7.1；45 §6.1 | L3 agent digest/persons/echo |
| `backend/app/db/models/{person,persona,crisis,echo_pref}.py` | 计划 §4.1/§4.2；设计 §4.2/§12.2/§12.4 | 表定义、约束、down/upgrade 对称 |
| `backend/migrations/versions/d4e5f6a7b8c9_*` | 计划 §4.2；设计 §8 | 合并双 head、对称回滚 |

## 任务卡模板（复制后填章节）

```yaml
id: B5-XX
goal: ...
background:
  docs:
    - docs/设计_B5记忆域_B7有人味域_深度设计_20261123.md#§7-上下文组装
    - docs/架构重构总计划_v3_20261123.md#§6.3-固定管线
  decisions: [ADR-003]
allowed_paths: [...]
forbidden_paths: [...]
acceptance: [...]
evidence: [pytest.log, judge-report.json]
```
