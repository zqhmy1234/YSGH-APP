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

## 4. 第 2 批～第 8 批（2026-09-25 · 本会话续执行）

> 每批＝独立提交 + 快速门禁 + 受影响测试；批次顺序按「P0 优先 → 后果排序」。
> 全部为**行为修复**（本波允许改行为），门禁轴（1–8）与 `review_agent` 全程无 CRITICAL。

| 批次 | 提交 | 内容 | 缺陷 | 验收证据 |
|---|---|---|---|---|
| 1 | `da8b318` | **存储键命名空间单一来源**（新增 `services/storage_keys.py`，5 处字面量全部改为派生 + 补 `wechat/`） | D02-2 / D09-2（P0）· 顺带 D09-14 | `tests/test_storage_keys.py` **11 例**：微信原件 200 下发（修复前恒 401）/ `.amr` → `audio/amr` / 白名单外仍 401 / **源码级反漂移断言**（本地字面量必须消失）/ monkeypatch 反向探针（拿掉唯一来源立即 401） |
| 2 | `2c1dc05` | **护栏 fail-safe 收口**：fail-closed 分支补 `action`；`verdict_action`（pass 优先、缺键亦判 reject）；托管空响应改走 chat 兜底；4 条主链改走策略入口；`moderate_managed` 退化为薄委托；回流词进硬规则表；ASR 回传打码文本 | D10-1/2（P0）+ D10-3/4/5/12（P1） | `tests/test_guard_failsafe.py` **16 例**（含端到端 422、五例参数化空响应、源码级防漂移、硬/软回流分流）+ `test_moderate_selector`/`test_content_safety` 语义同步更新 |
| 3 | `b1f2488` | **检索过滤器键集单一来源 + 未知键 fail-closed**（新增 `services/search_filters.py`；Qdrant/PG 两侧均先归一 + 覆盖自检；PG 隔离值以过滤器为准） | D05-3（P0） | `tests/test_search_filters.py` **16 例**（未知键两侧抛错 / 每个声明键都被真正消费 / `user_id` 真生成条件 / 跨用户隔离行为对拍） |
| 4 | `adfd4e4` | **画像枚举集随镜像分发 + 启动自检**（Dockerfile COPY + `PROFILE_ENUM_DIR`；缺件报可执行错误；`lifespan` fail-fast） | D07-1（P0） | **真实镜像构建取证**（同一 COPY 指令，redis:7-alpine 基座，两文件 127401/430244B 齐备，随即清理）+ `tests/test_profile_enum_distribution.py` **5 例** |
| 5 | `5f5c37c` | **微信主链收口**：绑定表 ORM + 幂等重建迁移 + `from_user` 解析 + 绑定/解绑端点 + 应用 Secret 独立项 + `WECOM_*` 别名 + 回调 offload 线程池 + 缺 MsgId 403 + 微信「删掉」走全局软删 | D09-1（P0）+ D09-3/4/7/8/12（P1/P2） | `tests/test_wechat_binding.py` **14 例**（绑定前后行为对比 / 图片媒体落 `wechat/` 键 / 409 防劫持 / 软删链路含审计日志 / gettoken corpsecret 断言 / 线程池防漂移） |
| 6 | `634b4b4` | **一次性生成链标记 + 路径可移植**（两脚本） | D12-5（P0 · 结构） | 原 `ROOT_DIR` 指向**已不存在**的工作树 ⇒ 改 env + 默认仓库根 + 缺输入显式报错；头注声明"一次性件/手写生成边界" |
| 7 | `b7a6e04` | **新建内容写变更日志**（`OP_CREATE` 单一出口，4 条新建路径统一调用） | D08-18（P1 · 簇② 遗留） | `tests/test_sync_create_log.py` **3 例**（含"另一设备 pull 能看到本端新建"验收口径）+ 修 `test_wechat_media` 手工 teardown（改用公共 `db_user`） |

**如实标注（未做，勿当已完成）**：

| 批 | 提交 | 内容 | 验证 |
|---|---|---|---|
| 8 | `000309f` | **客户端护栏默认值由放行改拒发**（D10-10）：原 `?? true` / 缺 `guardrail` `: true` 是客户端唯一 fail-open 默认值 ⇒ 字段漂移时"未审核"显示成"已通过"；现缺失/取不到一律按**未通过**并给可读原因 | HBuilderX **冷编译 ✅ 63224ms**（基线 `--cleanCache`）+ **增量 ✅ 34432ms**；`audit_harness client` 无 CRITICAL；⚠️ 无真机 ⇒ 提示渲染未目视 |
| 9 | `3137132` | **D02-9 部分收口**：补 `.heif → image/heif`（受理白名单含 `.heif` 而 MIME 表无映射 ⇒ 下发 MIME 与字节不符）+ 新增"受理白名单 ⊆ MIME 表"机器断言（加扩展名忘补 MIME 即红） | `pytest tests/test_storage_keys.py` → **12 passed**；`.gif` 方向**刻意排除**在断言外（是否受理动图属产品决策） |

**产品决策项（须拍板后才能做，本会话一律未动）**：

- `D10-8`（生成态 LLM 输出是否过护栏：成本/延迟 vs 上架合规）
- `D10-9`（软敏感 fail-open：LLM 故障时敏感话题是否停止"主动提及"）
- `D07-12`（画像人工确认/修改/锁定/清除**端点缺失** ⇒「用户操作优先」无载体）+ 连带的 `D07-10`（清除文案承诺与能力不符）/`D07-11`（`locked` 无真实语义）
- `D02-9 剩余`（是否放开 `.gif` 受理 / 是否收紧 `.heif` 受理）

> 详细选项与建议见 `docs/决策台账.md` §5.11（本节为唯一决策入口，拍板后回填结论再动代码）。

**仍需专用窗口的项**：

- **客户端侧**（`D08-4` 客户端镜像丢弃 `value` ⇒ 字段级收敛不可达）
  —— 正确修法＝本地字段值库（`sync_local` 注释自认"随 XView 自定义基座落地"）；仅存值不接消费方会**新增零调用导出**（撞轴 4），故不半做。
- **客户端结构项**（`D07-7/8/9`、`D06-*` 等 P1/P2）—— 需逐项评估 + 编译门窗口。
- **凭证/实网项**：微信客服 `openid → unionid` 自动解析（需客服 Secret）、`D09-5` 微信侧「找」回复装配、
  `D09-4` 真凭证 40001 复验 —— 代码侧载体已就位（绑定表/端点/服务），**实网未验**。
- **产品决策项**：`D10-8`（生成态 LLM 输出是否过护栏 = 成本/延迟）、`D10-9`（软敏感 fail-open 处置）、
  `D07-12`（画像人工确认/锁定端点缺失）、`D02-9`（照片扩展名三表收敛）。
- **候审项**：审计标注"候审（待验）"的条目未在本会话落地（需运行时/人工复核）。

## 5. A 段进度（完成即登记）

| 缺陷 | 状态 | 证据 |
|---|---|---|
| **D04-1**（P0 · 云侧 L1 日界走 UTC，沪区 00:00–07:59 落前一天） | ✅ **已修 2026-09-25** | 见下 |
| **D04-2**（P0 · 预处理去重端云分叉 + 双跑门禁无鉴别力） | ✅ **已修 2026-09-25** | 见下 |
| **D04-14**（P1 · AGG-016 断言恒真「假校验」） | ✅ **已修 2026-09-25** | 见下 |
| **D04-15**（P0 · 夹具未覆盖 GPS 漂移三态） | ✅ **已修 2026-09-25** | 见下 ⇒ **簇① 收口** |
| （随附）**AGG-016 门禁真身**：云侧此前**从不消费夹具** | ✅ **已补 2026-09-25** | 见 D04-2 记录 |
| **D08-1**（P0 · 离线删不投影 `contents.deleted_at` ⇒ REST 仍存活/"突然消失"） | ✅ **已修 2026-09-25** | 见下 |
| **D08-2**（P0 · REST 删不写 `deleted_logs` ⇒ 30 天清理永不选中） | ✅ **已修 2026-09-25** | 见下 |
| **D08-3**（P0 · 离线字段写只落 SFV，从不投影权威表） | ✅ **已修 2026-09-25** | 见下 |
| **D08-5**（P1 · 冲突败方值穿透写进变更日志） | ✅ **已修 2026-09-25**（同批顺手） | 见下 |
| （随附）**D08-1 更深一层**：无 SFV 行的内容离线删被误判"entity 不存在" | ✅ **已修 2026-09-25** | 见下 ⇒ **簇② 收口** |

### 簇② 修复记录（2026-09-25）——软删 30 天承诺 + 离线字段写（D08-1/2/3/5）

**先拍板契约**（grill 三问定案）：① **单一软删写路径**；② **双向写穿**（REST 编辑也写
SFV + 变更日志）；③ D08-3 投影白名单**仅 `remark`**。设计前已按要求先梳理输入分布
（谁在什么状态进这两条链）再调研业界口径（"单一权威表示 + 删除是一等事件 + soft
delete now/purge later + 恢复须同时清墓碑"），两件事做完才给意见。

**三条承诺在任何一条用户路径上都不同时成立（缺陷原貌）**：

| 用户路径 | 现状 | 立即隐藏 | 可恢复 | 30 天清除 |
|---|---|---|---|---|
| 在线 `DELETE /contents/{id}` | 只置 `deleted_at`，不写 `deleted_logs` | ✅ | ✅ | ❌ **永不清理** |
| 离线删除（sync `delete`） | 只写 SFV 墓碑 + `deleted_logs`，不置 `deleted_at` | ❌ **REST 仍存活** | ❌ **回收站看不到** | ✅（且是"突然消失"） |
| 离线改备注（sync `upsert_field`） | 只写 SFV，从不投影权威表 | — | — | —（**用户永不可见**） |

**根因（比审计原文更完整）**：同一业务语义（软删 / 字段写）在 REST 与 sync **两处入口
各自实现**，权威表、同步账本、审计日志三者的写入分别散落、无人统管；且既有门禁
（`test_sync` 只断言"墓碑 + `deleted_logs`"）把分歧**固化成"预期行为"**。

**修法**：新增 [sync_writes.py](file:///d:/GuangH-App/backend/app/services/sync_writes.py)
作为**单一写入语义路径**——`finalize_soft_delete`（软删对权威表/审计/他端做什么，只此
一处定义）、`project_field`（白名单投影）、`restore_content`（反向清墓碑 + 中和审计
日志）、`hard_delete_records`（硬删清尾）。REST `DELETE`/`PATCH` 与 `push_ops` 两处
**同调这些原语**；唯一差异是 SFV 墓碑写法（`push_ops` 必须走批内映射以保 R2#6
"批内同键不 500"）。

**D08-1 更深一层（审计未列，实现中发现）**：`push_ops` 判"实体存在"**只认 SFV 行**，
而照片经 REST/分片链路上云（`photo_content`/`register`）**从不写 SFV 行** ⇒ 这类内容的
离线删除被误判 `entity 不存在` 而**静默丢弃**（客户端只看到 `rejected`，用户以为删了、
云端其实没删）。已改为"**权威行存在即实体存在**"（归属仍由既有权校验把关）。

**D08-5（同批顺手）**：冲突分支原无 `continue`，控制流穿透到变更日志写入 ⇒ 把**被判败的
客户端旧值**原样下发他端 replay。已改为"只在真正应用到权威表时写日志"。

**验收证据**：
- 新增 `backend/tests/test_content_lifecycle_sync.py`（**13 用例**，含 D08-1 三条 + D08-1
  深层 + D08-2 两条（含端到端"超期被真的物理清除"）+ D08-3 三条（投影/白名单/类型守卫）
  + D08-5 + 双向写穿 + 恢复两条）⇒ **13 passed**。
- 无回归：`test_sync + test_reconcile + test_cleanup_job` → **24 passed**；
  `test_contents + test_content_upload + test_photo_content + test_content_safety +
  test_data_chains_12 + test_event_sync` → **104 passed**。
- **7 条反向探针全部真红**（关掉即失败、字节级复原、`grep PROBE` 无残留）：
  ① 撤 `deleted_at` 投影 → 2 用例红；② 撤"权威行即存在" → 复现 `entity 不存在`；
  ③ 撤 `deleted_logs` → 2 用例红（含端到端永不清理）；④ 撤字段投影 → 投影用例红；
  ⑤ 撤白名单 → 非白名单字段被静默 applied；⑥ 冲突补写日志 → 日志数 2≠1；
  ⑦ 撤审计日志中和 → `pending`≠`restored`。
- 端点 docstring 变更 ⇒ `gen_openapi.py` 重导（240153 字符）；`review_agent` ✅；lessons 已登记。

**如实标注 / 未做（挂账）**：
- §2.3 同款诚实：客户端镜像**丢弃 `value`**（D08-4/P1 未修）⇒「他端**自动收敛**」要等
  D08-4 落地；本批只保证**云端**权威版本与 **LWW 基准**正确（离线旧值不再盖掉在线新值）。
- **新发现（未修，登记后续，勿与簇②混淆）**：REST **创建**内容不写变更日志 ⇒ 他端 pull不到
  新内容。本次拍板范围仅"改备注/删除"（Q2 原话），**创建同步不在本批**，已登记待办。
- 白名单收窄**只对存在权威行的 content** 生效；无权威行的合成/历史实体沿用通用 SFV
  账本语义（避免误伤扩展面与既有门禁语义）。

### D04-15 修复记录（2026-09-25）——并向"真双跑"再补一刀

**问题**：夹具 13 例中只有"超驾车上限且**无**众数"被间接触达，`approx`（中速移动）与
`corrected`（众数拉回）**完全没有覆盖** ⇒ 端云双跑全绿也不代表这三态同源。

**修法**：新增 3 个用例，均按「**关掉该分支 ⇒ 结果就变**」设计（不是"只改标签"）：
- `drift-corrected-single-point`：5 张在 A（速度 0）+ 第 6 张跳 ~8km 且距上一张 3min（≈163km/h
  > 120km/h 上限）⇒ 众数支持 5 ⇒ **corrected** 拉回 ⇒ **1 簇 6 张**（不拉回则 1 簇 5 张 + 1 散片）。
- `drift-approx-moving`：5 张在 A + 3 张在 ~1.1km 外（≈9.5km/h，落步行~驾车间）⇒ **approx**
  （保留坐标）⇒ **2 簇**（若误拉回则并成 1 簇 8 张）。
- `drift-degraded-no-mode`：p1 跳 ~8km（超上限）且**全批任一网格只有 1 张**（众数支持 <2）
  ⇒ **degraded**（坐标置空）⇒ p1 按**时间**被吸收 ⇒ **1 簇 3 张**（若保留坐标则 p1 离 p2/p3
  约 8km ⇒ **0 簇**）。

**又抓到我自己门禁的"自证"缺陷（关键）**：第一版云侧门禁用 `build_cases()` **现场算期望**，
而生成器的期望正是**调用云侧实现**算出来的 ⇒ 关掉分支时"输入侧"与"期望侧"**一起变**，
三个分支探针**全绿**却什么都没抓到。修法：生成器额外落盘**冻结快照**
[`backend/tests/data/agg_fixtures.json`](file:///d:/GuangH-App/backend/tests/data/agg_fixtures.json)
（输入 + 期望都冻结），云侧门禁跑**冻结输入**、比**冻结期望**。

**验收证据**：
- 夹具 17 用例 / 95 照片 / 19 期望簇 / 24 期望日卡片；`pytest`（parity+reference+aggregation+
  generator+cli）→ **59 passed**；云侧冻结门禁 **19 passed**；
  端侧 UTS 夹具重生成后**冷编译 ✅ `项目 client 编译成功`**。
- **四个分支探针全部真红**（关掉即失败、字节级还原一致）：`corrected`（不拉回）、
  `approx`（阈值改 WALK）、`degraded`（保留坐标）、`dedup`（撤去重）。
- 另加两条**反向保护**断言：快照新鲜度（`build_cases()` 必须逐字等于冻结快照）+ 三态用例
  与"真实重复哈希"用例的存在性。

> 🛠 三点踩坑留证：① **金标准不得与被测实现同源**（否则门禁自证——与 D04-14 的恒真断言同族）；
> ② **用例必须证明"不同走法 ⇒ 不同结果"**（第一版 degraded 用例里 p1 原坐标离 p2/p3 太近，
> 保留坐标也会成同一簇 ⇒ 无鉴别力，探针实测未抓到，遂重设计）；
> ③ **单位坑**：`DRIVE_SPEED_MS = 120000/3600 = 33.3 **m/s**（＝120km/h）`，误当 33.3km/h 会把
> 48km/h 的用例错送进 `approx`；且时间偏移是**逐张累积**的（`t0+4*300000+180000` 才是"距上一张 3min"）。

### D04-14 修复记录（2026-09-25）

**问题比审计描述更糟**：`run_validation` 的 AGG-016 有**两条**假校验，不止一条 ——
① `AGG_CONFIG["l0"]["eps_s_m"] == 500.0`（常量与字面量自比，恒真）；
② `aggregate(photos)` 与 `aggregate(photos)` 对比 —— **自己跟自己跑**（确定性函数 ⇒ 恒真），
却命名为"同参双跑结果一致（端云同一配置源）"，**端侧根本没参与**。

**修法**：
- 新增 [`end_constants.py`](file:///d:/GuangH-App/backend/app/services/event_aggregation/end_constants.py)：
  读**端侧真源** `client/utils/agg/agg_config.uts`（含 `6000.0 / 3600.0` 这类表达式解析，
  **不用 eval**），与云侧 `agg_types` 常量**逐项**比对（含缺项检测）；生产镜像可能不含
  `client/`（同类问题见 D07-1）⇒ 读不到时返回 `None`，调用方**显式标注"未比对"**，**不得**当作通过。
- `run_validation`：把②改名为它真正的语义**「聚合幂等」**；把①换成真比对 + 一条
  **防空转**断言（端侧解析项数 ≥6，否则"0 项一致"也是绿的）。
- 新增 CI 硬门禁 `test_cloud_and_end_constants_match_via_source`（读端侧文件比对，不一致即失败）。

**验收证据**：
- 端侧解析 **7 项**全部正确（`WALK_SPEED_MS=1.6667`、`DRIVE_SPEED_MS=33.333` 等），
  比对结果 **`不一致 = []`**（端云当前逐项一致 —— 首次有**真实**结论）。
- `python -m app.services.event_aggregation.run_validation` → **18 项全绿**，其中
  「AGG-016: 端云常量逐项一致（7 项）」+「防空转（解析 7 项）」+「聚合幂等」三条**真通过**。
- **反向探针**：把云侧 `L0_EPS_S_M` 由 500 改成 600 ⇒ 门禁失败并**精确点名**
  `L0_EPS_S_M(600.0) ≠ end.L0_EPS_S_M(500.0)` ⇒ 有鉴别力；字节级还原一致。
- 测试面：`pytest`（generator/reference/parity/aggregation/cli）→ **55 passed**。

> 🛠 **修门禁过程中被自己的"防空转"断言抓住一个 bug**：正则把端侧行尾注释符 `//` 也吞进数值
> 表达式 ⇒ 端侧解析出 **0 项**、门禁自己**空转**却报绿。已改为"截到行尾再手工去注释"。
> 教训：**门禁的解析器本身也必须有"防空转"断言**，否则"0 项一致"就是新的假绿。

**D04-1/D04-2 的两处连带修复（同日，同属本簇）**：
- `scripts/agg_generate_photos.py` 锚点由 **UTC 正午**改为**本地正午**（`_local_base()`）：
  D04-1 改日界后，场景 3「一日游」在 UTC 锚定下会越过本地午夜被切成多张日卡片 ——
  那是**数据摆放**问题（场景意图是"单日"），故修数据而非改期望。
- 同文件场景 9（同哈希重复 3 张）**补 `phash`**：`RawPhoto` 已有该字段 ⇒ 这组数据**真正**走
  感知去重（此前只能用 `ocr_text` 打标记、聚合时按 id 全保留，与原型意图不符）。

**余项**：D04-15（夹具仍无 `approx`/`corrected`/`degraded` 分支用例 —— 这三态目前只在
`run_validation` 场景 11/12/17 被间接覆盖，未进端云双跑夹具）。

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

> **2026-09-25 会话续执行后的口径**：A 段 **17 条 P0 已全部处置**（簇① D04-1/2/14/15、簇② D08-1/2/3/5、
> 簇③ D09-1/2、散点 D02-2 / D05-3 / D07-1 / D10-1/2 / D11-1 / D12-1(令牌波) / D12-5 / D14-1）。
> 剩余＝**P1/P2 清单**（ledger §9.2）+ **客户端编译门项** + **凭证/实网项** + **产品决策项**，见 §4 末「如实标注」。

1. **A 段 · 簇①（端云聚合同源）已收口**：D04-1（日界单一来源）、D04-2（云侧去重+契约 phash）、
   D04-14（AGG-016 真比对）、D04-15（漂移三态夹具）四项全部完成 —— 且**顺带把 AGG-016 门禁从
   "端侧单跑 + 恒真断言 + 现场算期望"修成"云侧跑冻结快照 + 四项分支探针真红"**。
2. **A 段 · 簇②（软删 30 天承诺 + 离线字段写）已收口**：D08-1（离线删投影权威表 +
   "权威行即存在"深层）、D08-2（REST 删写审计日志，30 天清理真的生效）、D08-3（离线
   字段写投影权威表 + 白名单收敛）、D08-5（冲突不污染变更日志）全部完成 ⇒ "立即隐藏 /
   可恢复 / 30 天彻底清除"三承诺首次在**所有**用户路径上同时成立；且顺带把"单一写路径"
   抽出（`sync_writes`）。
3. **A 段 · 簇③（微信主链 D09-1/2）**：需企微/微信凭证（部分挂账）。
4. **散点 P0**：D07-1（镜像缺 `docs/` 致画像首调 500）、D10-1/2（护栏 fail-open）、D05-3（过滤器静默丢弃）。
5. **A 段其余**（按 ledger §9.2 的 P1/P2 清单）与本波 B 段（图搜评测 RET-002 补建、RAG 负样本重校）。
6. **运行时验证**：在可运行 agent 服务的环境上跑一次端到端（真机或 Linux devbox），回填 §2.3 的未验证项。
7. **簇② 新发现（未修，登记待办）**：REST **创建**内容不写变更日志 ⇒ 他端 pull 不到新内容；
   与 D08-4（客户端镜像丢弃 `value` ⇒ 字段级收敛不可达）同属"端间一致性"族，建议与
   D08-4 合批处理（勿与已收口的簇②混淆）。