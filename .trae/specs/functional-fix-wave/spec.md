# 功能修复波（Functional Fix Wave）· 方案与登记 · 2026-09-25 开波

> **开波依据**：用户 2026-09-24 拍板「64 条功能与安全缺陷 → 另开功能修复波」（`docs/决策台账.md` §5.10 拍板 10.1），
> 并于 2026-09-25 追加指令：**「将客户端与 Agent 接线纳入下一个波中。完成接线」** + **「先开始令牌波，再功能修复波」**。
> **本文件的定位**：本波的**入口与登记簿**（不复制缺陷清单——清单单一来源见 `refactor-ledger.md` §9.2，避免两处漂移）。

---

## 1. 范围（三段，来源各不相同，勿混）

| 段 | 内容 | 单一来源 | 状态 |
|---|---|---|---|
| **A. 功能与安全缺陷 64 条** | P0 10 / P1 36 / P2 18（含端云聚合同源、软删 30 天承诺、微信主链三簇） | `refactor-ledger.md` §9.2（+ §9.1 P0 清单） | ⏸ 待启动逐条 |
| **B. 能力缺口（非缺陷）** | ① 产品 Agent 曾"全 mock" ② 图搜**评测基线**缺失（RET-002；现有 `eval_image_search.py` 是 RET-001 文字搜图）③ RAG 负样本误召回 0.5714 | `docs/决策台账.md` §7.1/§7.2/§7.3 + `docs/审计_未关闭缺陷_20260923.md` | 🔄 ①见 §2 已闭环；②③ 待办 |
| **C. 施工期新增** | ① `TabIndex` 悬空面板（已裁决**不修**）② 令牌波**刻意留残**（尺寸同格变体/物理 px/无令牌散单） | `docs/决策台账.md` §4.15 15.4 / 令牌波 spec §4.3 | ✅ 均已裁决/分类，**非本波待办** |

## 2. 第 1 项：客户端 ↔ Agent 接线（**已完成** · 2026-09-25）

**为什么它在 B 段**：09-23 审计判定「产品 Agent 全 mock」＝能力缺口；用户指令要求**纳入本波并完成接线**。

### 2.1 接线链（逐段取证）

| 段 | 载体 | 状态 |
|---|---|---|
| 客户端 UI | `client/components/TabAi/TabAi.uvue` | ✅ `USE_MOCK_CHAT` **翻转为 false**（2026-09-25） |
| 客户端 API | `client/utils/chat_api.uts` | ✅ `sendChatMessage`（POST /chat/messages）+ **新增 `fetchChatHistory`**（GET /chat/conversations/{id}/messages） |
| 契约常量 | `client/utils/contract.uts` | ✅ 新增 `PATH_CHAT_CONVERSATION_MESSAGES`（**与调用点同时登记**，避免零调用常量） |
| 后端入口 | `backend/app/api/chat.py` | ✅ `POST /messages`、`GET /replies/{id}`、**`GET /conversations/{id}/messages`** 三个端点齐备 |
| 后端外呼 | `backend/app/services/external/agent.py` | ✅ `call_agent_chat` → `{AGENT_SERVICE_BASE_URL}/v1/chat`（`X-Agent-Token` 可选）+ `health()` |
| 外部 Agent 服务 | `agent/`（在库内，`src/main.py:150` 实现 `POST /v1/chat`） | ⚠️ **本机 Windows 起不来**（见 §2.3） |

### 2.2 本次补完的**真实缺口**（此前"接线"只做了一半）

- **缺口 1：开关未翻转**。`USE_MOCK_CHAT` 一直是 `true` ⇒ 产品实际运行在"样张对话"占位态
  （09-23 审计的原始问题）。已翻转，并加**门禁锁**：`audit_harness client` 断言该常量为 `false`
  （翻回即 CRITICAL）。**反向探针**：临时改回 true → `audit_harness client` **EXIT=1** 并精确点名；
  字节级还原后复绿。
- **缺口 2：历史从不回填**。后端 `GET /conversations/{id}/messages` 的 docstring 明确写着
  「客户端进入会话时回填」，但客户端**从未调用** ⇒ 重启后会话虽接续（`conversationId` 已落盘）、
  **界面空白**（用户以为历史丢了）。已补：`fetchChatHistory` + `TabAi.onMounted` 回填
  （role→user/ai 映射；形态用后端下发 `kind`（当前仅 `bubble`/`plain`），未知退回 `bubble`——
  **不猜 cards/confirm**；失败/空保持空列表，**不伪造历史**）。
- **缺口 3：翻开关后没有空态（我方翻开关引入的回归，自行修掉）**。原 mock 模式必有样张消息，
  而真链下全新用户 `messages` 为空 ⇒ 只剩输入框的一片空白。已补**诚实空态**
  （`v-if="messages.length == 0 && !USE_MOCK_CHAT"`）：文案「和你的回忆聊聊」+ 一句示例问法，
  **无假数据、不预填示例对话**；样式走令牌（带回退），故**不抬高硬编码棘轮**（实测 hex/rgb 未变）。

### 2.3 运行时依赖（**如实标注，未验证项**）

- `AGENT_SERVICE_BASE_URL` 默认 `http://127.0.0.1:8300`（`config.py`），模板项同名（`deploy/.env.production.template`）；
  `agent/.env` 的 `AGENT_HTTP_PORT=8300` 与之一致 ⇒ **端口口径对齐**。
- ⚠️ **本机无法端到端跑通**：`agent/pyproject.toml` 依赖 `dbus-python` / `PyGObject` / `pycairo`
  —— **Linux-only**，Windows 上 `uv sync` 必失败 ⇒ 服务起不来（实测 8300 端口拒绝、`agent.health()`
  返回 `reachable=false`）。⇒ **后端↔agent 的真实外呼未在本机验证**，需在 Linux devbox/部署环境或
  「已部署 agent 的后端 + 真机客户端」上验证。**不得以"编译通过"冒充运行验证。**
- 失败语义（设计如此，非缺陷）：agent 不可达 → 后端 502/504 → 客户端撤 typing 占位 + toast，
  **绝不补一句假 AI 文本**（`test_agent_unavailable_no_fake_reply` 覆盖）。

### 2.4 验收证据

| 项 | 证据 |
|---|---|
| 客户端编译 | ✅ `项目 client 编译成功`（59.7s） |
| 后端契约测试 | ✅ `pytest backend/tests/test_chat.py` → **7 passed**（含 happy path 落库对拍 / agent 不可用不伪造 / 超时映射 504 / IDOR 404 / **历史端点**） |
| 静态门禁 | ✅ `audit_harness all` **无 CRITICAL**；轴 4：新导出 `fetchChatHistory` **有 2 处引用**（非零调用） |
| 接线锁 | ✅ 反向探针（翻回 true → EXIT=1）+ 字节级还原 |

## 3. 本波工作纪律（沿用结构重构波的既有门禁）

- 每项独立提交；改动涉及客户端须**冷编译**、涉及后端须**跑对应 pytest**；完成声明前 `review_agent --full`。
- **禁止伪造**：能力不可用/数据缺失一律显式失败或诚实空态（本波已两次用到该口径：不伪造回复、不伪造历史）。
- 8 项"静默漂移"类门禁继续生效（轴 1–8）；本波新增/触碰的开关类断言须留**反向探针**证据。
- 需真机/凭证/合规的部分**显式挂账**，不冒充完成。

## 5. A 段进度（完成即登记）

| 缺陷 | 状态 | 证据 |
|---|---|---|
| **D04-1**（P0 · 云侧 L1 日界走 UTC，沪区 00:00–07:59 落前一天） | ✅ **已修 2026-09-25** | 见下 |
| **D04-2**（P0 · 预处理去重端云分叉 + 双跑门禁无鉴别力） | ✅ **已修 2026-09-25** | 见下 |
| （随附）**AGG-016 门禁真身**：云侧此前**从不消费夹具** | ✅ **已补 2026-09-25** | 见 D04-2 记录 |

### D04-2 + AGG-016 门禁修复记录（2026-09-25）

**三层根因（比审计原文更完整）**：
1. **契约缺字段**：端侧 `RawPhoto.phash` 早就有（`client/utils/agg/pipeline.uts:23`），
   云侧 `agg_types.RawPhoto` **没有 phash** ⇒ 云侧连"同哈希重复照片"这组输入都**无法表达**。
2. **实现缺步骤**：端侧 `preprocess` **首步**是 `dedup()`；云侧 `preprocess` 直接从排序开始
   （只做连拍折叠 + GPS 漂移）⇒ 同一输入两端结果分叉。
3. **门禁只跑一半（真身）**：`scripts/gen_agg_fixtures.py` **只产出端侧 UTS 夹具**
   （`client/utils/agg/fixtures.uts`），**云侧没有任何夹具消费方** —— 所谓"端云双跑/12 项零漂移"
   实际是**端侧单跑**；叠加 `run_validation` 那条常量自比的**恒真**断言（D04-14），
   于是"已校验"的错觉成立，D04-2 这类分叉长期无人发现。

**修法**：
- `agg_types.RawPhoto` 补 `phash: str = ""`（契约对齐端侧；空串＝按 id 兜底）。
- `agg_preprocess` 新增 `_dedup(photos)`（key＝phash 非空 ? phash : id；同 key 留**首张**、按输入序），
  并置于 `preprocess` **首步** —— 与端侧**逐字同语义**（端侧：先按输入序去重、再排序）。
- `services/events/aggregate._to_raw_photo` 补 `phash ← contents.perceptual_hash`（否则云侧去重
  只能按永远唯一的 id **空转**）。
- **新增 `backend/tests/test_agg_fixtures_parity.py`**：让**云侧真实实现**跑**同一份夹具**
  （直接 `from scripts.gen_agg_fixtures import build_cases`，含 phash 回贴），比对语义与端侧
  `agg_check.uts` 逐字对齐（簇＝集合语义、日卡＝`date|排序id|稀疏`）。⇒ AGG-016 从此是**真双跑**。

**验收证据**：
- `pytest backend/tests/test_agg_fixtures_parity.py test_agg_reference.py test_aggregation.py` → **33 passed**
  （13 个夹具用例在云侧实现上逐条对上）+ 一条"去重用例存在且有真重复哈希"的**反向保护**断言。
- **反向探针**：把云侧 `preprocess` 的去重撤掉（改回 `sorted(photos, …)`）⇒
  `[dedup-phash-duplicate] 簇与夹具期望不一致（端云分叉）` **失败** ⇒ 门禁**有鉴别力**；字节级还原一致。
- 后端相关面：`pytest backend/tests -k "agg or event"` → **137 passed**；另有 4 项
  `test_ab_scenarios::TestS3Aggregation` 报 **Redis 连接失败**——**环境性**（Docker Desktop 守护进程
  未运行 ⇒ `yishu-redis` 起不来，AGENTS 已记载"机器重启即眠"），与本次改动无关。

**如实标注（不过度声称）**：`uq_contents_user_hash(user_id, perceptual_hash)` 已使**库内**同用户
同哈希不可能并存 ⇒ 本项在当前生产路径上的收益主要是**契约与防线对齐**（对 `source='seed'`、
非库来源、哈希后补等路径有实义），并**顺带把 AGG-016 门禁从"端侧单跑"变成真双跑**。

**余项**：D04-14（`run_validation` 的 AGG-016 断言仍是常量自比恒真 —— 应改为与端侧
`agg_config.uts` 真实比对）、D04-15（夹具仍无 `approx`/`corrected` 分支用例）。

### D04-1 修复记录（2026-09-25）

**根因（比审计原文更深一层）**：不是"端云未对齐"这么简单，而是**云侧自己有两套日界口径**——
`services/echo.py::_local_now()` 用 `datetime.now().astimezone()`（其 docstring 记录它修过同一个
bug："原按 UTC 日界 ⇒ 本地 0:00-8:00 算前一天"），而 `event_aggregation/st_dbscan.py::l1_daily_aggregate`
的 `tz_offset_minutes` **默认 0＝UTC**；且 `deploy/` **未设 TZ** ⇒ 容器为 UTC ⇒ 连 echo 那套
"服务器本地"在容器里也等于 UTC（**等于没修**）。

**修法（最小且不动调用方）**：
1. 新增 `backend/app/core/timeutil.py` —— 云侧"本地自然日"的**单一来源**：
   `APP_LOCAL_TZ`（默认 `Asia/Shanghai`）→ `local_now()` / `local_utc_offset_minutes()`；
   **不读容器 TZ**。
2. `config.py` 加 `app_local_tz`（+ `env_template` 门禁要求的模板项 `APP_LOCAL_TZ` 已同步）。
3. `l1_daily_aggregate(tz_offset_minutes: int | None = None)`：**默认从 `0` 改为"应用本地口径"**，
   显式 `0` 仍＝UTC（测试/对照语义不变）。⇒ `pipeline.aggregate` / `incremental_aggregate`
   两处调用点**零改动**即自动走上正确口径。
4. `echo._local_now()` **改为委托** `timeutil.local_now()` ⇒ 云侧日界只剩一个来源。

**验收证据**：
- 新增 `test_day_boundary_defaults_to_app_local_tz_not_utc`（审计建议口径：同批照片在"默认"与
  "显式 0"下比较 `date`）＋ `test_cloud_day_boundary_has_single_source`（echo 与聚合偏移必须相等）。
- `pytest backend/tests/test_agg_reference.py backend/tests/test_aggregation.py` → **18 passed**。
- **反向探针**：把 `tz_offset_minutes = local_utc_offset_minutes()` 临时改成 `= 0` ⇒ 新用例**失败**
  且报错正是 D04-1 症状（`assert '2026-07-17' == '2026-07-18'`）⇒ 用例非空转；字节级还原一致。
- ruff 全过（含新文件）。

**未覆盖（如实标注，属产品决策）**：真实**跨时区用户**需 per-user 偏移（端侧已在
`aggregateToEvents(uploads, tzOffsetMin)` 传设备偏移，云侧无处可存）⇒ 已登记 `docs/决策台账.md` §5.10。
本次先把"云侧不再自相矛盾 + 不依赖容器 TZ"这一层收敛掉（MVP 面向单城）。

**余项**：D04-2（云侧生产无去重，夹具自造 `_dedup` 垫背）、D04-15（夹具未覆盖 `approx`/`corrected`
分支）⇒ 两者互相咬合，需同批做（先对齐去重口径，再重生成夹具 + 端侧复跑）。

## 6. 待办（下一步）

1. **A 段**：按 `refactor-ledger.md` §9.2 的三簇优先级排期（端云聚合同源 → 软删 30 天 → 微信主链），逐条立项。
2. **B 段**：② 图搜评测补建（RET-002）；③ RAG 负样本重校。
3. **运行时验证**：在可运行 agent 服务的环境上跑一次端到端（真机或 Linux devbox），回填 §2.3 的未验证项。