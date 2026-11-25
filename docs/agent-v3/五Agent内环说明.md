# 五个 Agent 的内部 harness（每个都单独说清：进什么、跑几步、出什么、怎么失败）

> 共同约束：只依赖 `contracts` + `ports`；不互相 import；失败一律抛 `PortError`，
> 由 `orchestrator` 转成 `degraded/partial`，Agent 自己不许吞错。

## 0. 外层：一轮 turn 的编排 harness（`orchestrator.py`）

```
TurnRequest
  → plan(ctx)                   意图/调用表/预算（纯函数，可回放）
  → Policy.screen_input         危机 → 固定话术，直接返回（0 次 LLM）
  → MemoryStore.entries+corrections → Digest（三态）
  → build_frame                 九段，4200 字符，证据先裁
  → Librarian.retrieve          （仅 RECALL/REMEMBER，且检索未降级）
  → Muse.speak                  最多 2 次 LLM、3 次只读工具
  → Provenance.verify           规则判 pass/needs/mask/reject/handoff
  → MemoryStore.propose         （仅写策略=propose，且本轮无 LLM 故障）
  → TurnResult(reply,cites,verdict,receipts,status,cost,plan,trace)
```

失败分支：`llm` 失败 → 固定降级话术 + `partial`；`search` 降级 → 不进 Librarian；
`memory` 异常 → digest `unavailable`，不阻断回复。

## 1. Muse（唯一说话者，`may_write=False`）

- 输入：`Frame` + 用户原话 + locale。
- 内部：`LLMRequest(purpose="muse")` → 若 `finish=tool_calls` 且步数<2，执行只读工具（≤3）后重问 → 否则取文本。
- 输出：`(reply, cites)`；cites 只做提取，不做授权。
- 失败：空/超长/语言不符 → `PortError`（由上层降级），绝不返回半成品。
- 硬闸：工具表由组合根注入，Muse 无法自行获得写工具。

## 2. Librarian（只给证据，不给结论，`may_write=False`）

- 输入：`SearchQuery`（全系统唯一构造点 `resolve_query`）。
- 内部：keyword(+≤6 alternates) / dense / sparse / time 各成一路 → 事件端口（有窗口才查）
  → RRF(k=60) 融合 → 原文列 / 摘要列分开排序 → 分配 `[S#]/[W#]`。
- 输出：`SearchResult(hits,summaries,trace,status,gaps,clarify,event_hit_count)`。
- 失败：任一通道 `PortError` → `degraded=True` + `gaps` + `partial`；全空且歧义才给 `clarify`。
- 铁律：时间只投票不过滤；空结果必须先查过原文列才算 `empty`。

## 3. Provenance（规则为主，`may_write=False`）

- 输入：`ClaimBundle`（按句切）+ 本轮证据 ID + 用户修正文本。
- 内部四判：私密/数字/日期/姓名无引用 → needs；引用不在 held → needs；与修正相反 → reject；
  命中边界词 → mask（整句剔除并重建 `fixed_reply`）。
- 输出：`Verdict(status, claims, fixed_reply)`。
- 失败：危机标志 → handoff；任何 reject/mask 只会降低，不会被模型抬高。

## 4. Sculptor（唯一写入者，`may_write=True`，只走批次）

- 输入：episodes（文本/层/证据/日期/是否撤回/是否替换）。
- 内部：`propose_from_episodes` → 同文本转 `CAME_FROM`；有 `entry_id` 转 `UPDATE`；
  `retract` 转 `RETRACT`；否则 `ADD` → `validate`（19 个 issue code）→ 有任一 issue 整批拒绝
  → 干净批次 `commit`（快照 + 双时间 + 应用写 change log）。
- 输出：`CommitReceipt(committed, rejected, rolled_back, idempotent)`。
- 失败：目标缺失/修正冲突 → 抛出被 ledger 捕获 → 整批回滚，无部分成功。

## 5. Serendipity（去年今日，只读预热）

- 输入：`person_id` + topics + today + 注入的 `retrieve` 函数（不 import Librarian）。
- 内部：`year_ago`（闰日取 28 号）→ 构造 `SearchQuery(mode="overview", event_from/to)` → 命中才入缓存。
- 输出：`topic → SearchResult` 缓存；`take()` 供编排层读取。
- 失败：`PortError` 跳过该 topic；缓存为空即不展示，绝不用“无记录”冒充回响。

## 预算表（来自 `registry.py`，非口口相传）

| Agent | 可写 | 可说 | 预算 ms | 预算 chars |
|---|---|---|---|---|
| Muse | 否 | 是 | 3000 | 2200 |
| Librarian | 否 | 否 | 800 | 1600 |
| Provenance | 否 | 否 | 800 | 800 |
| Sculptor | 是 | 否 | 1000 | 1000 |
| Serendipity | 否 | 否 | 600 | 1000 |

## 每个 Agent 的“证明责任”

| Agent | 证明它没撒谎的测试 |
|---|---|
| Muse | `test_muse.py`：非法回复必抛错、工具≤3、步数≤2 |
| Librarian | `test_librarian.py`：通道故障=partial、两列不混、时间不过滤 |
| Provenance | `test_provenance.py`：无引用降级、悬空引用、边界剔除、修正优先 |
| Sculptor | `test_sculptor.py`：19 码、整批拒绝、干净批次落库 |
| Serendipity | `test_serendipity.py`：空结果不入缓存、闰日正确 |
