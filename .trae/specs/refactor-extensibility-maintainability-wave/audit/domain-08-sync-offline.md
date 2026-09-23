# 域⑧ · 同步与离线 深度审计

> 审计范围：字段级 LWW 同步、软删除 30 天、离线队列、WiFi 原图/蜂窝缩略图、断点续传
> 性质：**只读审计，未修改任何业务文件**。本波为严格行为等价，缺陷只登记不修。
> 排除：`agent/` 目录（按要求排除）；`client/uni_modules/` 仅作依赖边界记录，未深审。

## 覆盖范围与实读清单

**实读文件（路径 : 行数）**

后端：
- `backend/app/services/sync.py` : 313（push_ops / push_ops_safe / _push_ops_per_op / pull_changes / _max_version）
- `backend/app/services/sync_common.py` : 36（TOMBSTONE_FIELD / parse_ts / lww_wins）
- `backend/app/services/reconcile.py` : 102（_cloud_entities / reconcile_snapshot）
- `backend/app/services/events/sync.py` : 163（sync_client_events / _safe）
- `backend/app/schemas/sync.py` : 92（SyncOp / SyncPushRequest / SyncReconcile*）
- `backend/app/schemas/event.py` : 146（读 112-146：ClientEventItem 幂等键）
- `backend/app/db/models/sync.py` : 82（SyncState / OfflineQueue / DeletedLog / SyncFieldVersion）
- `backend/app/api/sync.py` : 73（/push /pull /reconcile）
- `backend/app/api/deps.py` : 读 83-102（load_alive_content 软删过滤）
- `backend/app/api/contents.py` : 读 555-815（DELETE /contents/{id} + trash_router 三端点）
- `backend/app/workers/cleanup_job.py` : 240（run_cleanup / run_upload_reaper）

客户端：
- `client/utils/sync_client.uts` : **705**（与任务书「705 行」一致）
- `client/utils/queue_store.uts` : 228
- `client/utils/retry.uts` : 44
- `client/utils/event_sync.uts` : 101
- `client/utils/pause_controller.uts` : 72
- `client/utils/uploader.uts` : 628（流量约束 resolveMode / held·failed 队列 / 并发池 / 断点 upload_id）
- `client/utils/upload_pipeline.uts` : 190（init→status→chunk→complete 状态机）
- `client/utils/time.uts` : 157（isoLocal 硬编码 +08:00 / parseIsoMs）
- `client/utils/play.uts` : 653（读 375-490 / 628-653：deleteContent / updateContentRemark / trash*）
- `client/utils/event_ops.uts` : 492（读 1-45 / 370-470：enqueueOp / flushOpQueue / flushNext）
- `client/utils/contract.uts` : 路径常量段（PATH_SYNC_* / FIELD_HAS_MORE）

**未读到 / 未覆盖（明确声明）**
- `client/uni_modules/yishu-background-tasks`（WorkManager 两段式插件本体，`sync_client.uts:43` 的唯一外部原生依赖）——未读，其降级分支正确性未验证。
- `client/utils/upload_protocol.uts`（162 行，仅确认被 upload_pipeline 消费，未逐行读）。
- 后端 `upload/*` 分片协议实现（本域只到客户端协议调用面）。
- 未运行任何测试（只读审计，无写权限）。

**`git status --short`（审计时点）**
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
→ 工作区**干净**：无在途修改的业务文件。本次审计新增的只有本报告文件。

---

## LWW 端云一致性对照表（本域重点，逐字段核对）

| 规则项 | 服务端实现 | 服务端位置 | 客户端实现 | 客户端位置 | 是否一致 |
|---|---|---|---|---|---|
| 时间戳来源 | 取客户端传入 `updated_at`（**设备时钟**）；缺失才回退服务端 `_utcnow()` | `backend/app/services/sync.py:126`、`sync.py:29-30` | 入队时 `isoNow()` = `isoLocal(Date.now())`（**设备时钟**，硬编码 `+08:00` 后缀） | `client/utils/sync_client.uts:124`、`client/utils/time.uts:30-34` | ⚠️ 来源一致（同为设备时钟），但客户端硬编码 `+08:00` ⇒ **非 +08 时区设备的时间戳整体偏移**（`time.uts:11-13` 自注为「历史行为」） |
| tie-break | 严格大于才更新：`client_ts > server_ts`；**相等 → 云端胜** + 写 `conflicts` | `backend/app/services/sync_common.py:31-36`；`sync.py:196-205` | **无客户端 LWW 判定**：`applyChanges` 盲信服务端下发的 `updated_at`，直接覆盖本地镜像 | `client/utils/sync_client.uts:240-254` | ❌ 客户端零判定（服务端单向权威）；服务端 tie-break 与 `reconcile` 的 `newer` 判定自洽（`reconcile.py:74`） |
| 字段粒度 | **每 `(entity_type, entity_id, field)` 一行**独立比较（`field="*"` 为实体级墓碑） | `backend/app/db/models/sync.py:74-76`；`sync.py:191-220` | **实体级**：镜像行格式 `entity_id\|updatedAt\|deleted`，无字段维度；`value` 被丢弃不落本地 | `client/utils/sync_client.uts:201-217`、`220-236`、`243-252` | ❌ 服务端字段级 / 客户端实体级；`pull` 回来的 `field`/`value` 在客户端**无人消费** |
| 软删 30 天 | **两条互不相通的轨道**：<br>① REST 删除 → `contents.deleted_at`（**无任何清理任务消费**）<br>② 同步 delete → `sync_field_versions.deleted` + `deleted_logs` → `cleanup_job` 30 天后物理清理 | ① `backend/app/api/contents.py:620-638`、`trash_list:723-758`、`trash_clear:783-807`<br>② `sync.py:155-185`、`cleanup_job.py:74-124` | 无本地 30 天逻辑；仅 UI 文案「保留 30 天」，`days_left` 全部依赖后端 | `client/pages/storage/trash.uvue:16`、`client/pages/storage/storage.uvue:128` | ❌ 两端口径分裂；且服务端内部**自身就是双轨**（见 D08-1 / D08-2） |

**补充（同表内结论）**
- 服务端 `parse_ts` 把 naive 时间统一按 UTC 解释（`sync_common.py:26-27`），客户端永远带 `+08:00` 后缀 ⇒ 语法上不会走到 naive 分支；但**偏移值本身是硬编码的**，不是设备真实时区。
- `push_ops` 的 LWW 比较基准是「上一次客户端写入时落库的 `updated_at`」，即**设备时钟 vs 设备时钟**（跨设备时钟偏差无保护），服务端时钟不参与判定。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D08-1 | `backend/app/services/sync.py:155-185` × `backend/app/api/contents.py:620-638` | 口径分裂（软删双轨） | P0 | 高 | 高 | 行为等价替换 | 新增测试：push delete 后 `GET /contents/{id}` 应 404 | 批次1（需先定契约） | 确认 |
| D08-2 | `backend/app/api/contents.py:626-638` × `backend/app/workers/cleanup_job.py:87-95` | 承诺未落地（30 天清理漏轨） | P0 | 高 | 中 | 行为等价替换 | `run_cleanup` 前后比对 `contents` 行数（REST 软删条目） | 批次1 | 确认 |
| D08-3 | `backend/app/services/sync.py:206-220` × `client/utils/play.uts:647` | 写入不投影权威表 | P0 | 高 | 高 | 行为等价替换 | push `field=remark` 后读 `contents.remark` 应等值 | 批次1 | 确认 |
| D08-4 | `client/utils/sync_client.uts:240-254` | 能力缺口（字段级无本地载体） | P1 | 高 | 中 | 新增工具 | `applyChanges` 单测：changes 含 `field/value` 时应落本地字段库 | 批次2 | 确认 |
| D08-5 | `backend/app/services/sync.py:196-205` + `222-240` | 幂等/变更日志污染 | P1 | 中 | 中 | 行为等价替换 | 冲突用例：pull 回来的 `changes[].value` 应为云端权威值 | 批次1 | 确认 |
| D08-6 | `client/utils/sync_client.uts:451,464-471,556-560` | 分页未消费 | P1 | 中 | 低 | 行为等价替换 | 造 >200 条变更，单轮 `runSyncChain` 后 `pending` 游标应追平 | 批次2 | 确认 |
| D08-7 | `client/utils/play.uts:444-448`、`646-650` | 丢操作窗口 | P1 | 中 | 低 | 行为等价替换 | 蜂窝网络 + 服务端 500 时删除应仍入队 | 批次2 | 确认 |
| D08-8 | `client/utils/event_ops.uts:381` | op_id 碰撞 | P1 | 中 | 低 | 行为等价替换 | 同毫秒连调 `enqueueOp` 两次 → 两行 op_id 应不同 | 批次2 | 确认 |
| D08-9 | `backend/app/schemas/sync.py:17` × `backend/app/services/sync.py:135-153` | 注册表覆盖度（校验静默跳过） | P1 | 中 | 中 | 新增工具 | 扩 pattern 加类型但不加分支 → 越权 op 应被拒 | 批次2 | 确认 |
| D08-10 | `client/utils/sync_client.uts:584`（及 `:64,82,617`） | 死码/零调用导出 | P2 | 低 | 低 | 删码(需证死) | 全仓引用搜索（见证据） | 批次3 | 确认 |
| D08-11 | `client/utils/retry.uts:26-32` × `client/utils/uploader.uts:430`、`client/utils/sync_client.uts:373` | 死参数（不可达分支） | P2 | 低 | 低 | 行为等价替换 | 桩测：`isFatal` 回调应从未被以非 null 调用 | 批次3 | 确认 |
| D08-12 | `client/utils/uploader.uts:119-135,252-269` × `client/utils/queue_store.uts:61-79` | 重复逻辑（行分隔存储读写） | P2 | 中 | 低 | 纯移动+兼容再导出 | 抽取后行为快照对比 | 批次3 | 确认 |
| D08-13 | `backend/app/workers/cleanup_job.py:65-71` | 越界删除风险 | P2 | 低 | 中 | 行为等价替换 | 同 entity_id 不同类型墓碑用例 | 批次3 | 候审 |
| D08-14 | `backend/app/services/sync.py:184` + `cleanup_job.py:48-62` | 语义复用（content_id 承载任意实体） | P2 | 中 | 中 | 行为等价替换 | event 删除后 `cleanup_job` 不应删 content 行 | 批次3 | 确认 |
| D08-15 | `client/utils/sync_client.uts:220-236,482-487` × `backend/app/schemas/sync.py:84` | 上限未对齐 | P2 | 低 | 低 | 新增工具 | 造 >5000 镜像行 → `reconcileNow` 应分片而非返回 null | 批次3 | 确认 |
| D08-16 | `client/utils/event_ops.uts:29` | 反向依赖 | P2 | 中 | 低 | 纯移动+兼容再导出 | 移出 `getNetKind` 后编译通过 | 批次3 | 确认 |
| D08-17 | `backend/app/services/sync.py:243` × `client/utils/sync_client.uts:326-335` | 零消费字段 | P2 | 低 | 低 | 删码(需证死) | 客户端全仓搜 `server_version` = 0 命中 | 批次3 | 确认 |

**D08-1 证据**：`push_ops` 的 `OP_DELETE` 分支（`sync.py:155-185`）只做三件事——写/更新 `SyncFieldVersion(field="*").deleted=True`、`db.add(DeletedLog(content_id=entity_id, deleted_by=user_id))`、`applied.append(...)`。**全函数不触碰 `contents.deleted_at`**。而 `GET /contents/{id}`（`deps.py:93-99` `load_alive_content`，条件含 `Content.deleted_at.is_(None)`）与列表/详情全靠 `contents.deleted_at` 过滤。客户端离线删除路径 `play.uts:444-448`（`getNetKind()=='none'` 时 `enqueueDeleteOp('content', contentId)`）上云后正是走这个分支 ⇒ **用户离线删除后，条目在服务端仍是「存活」，列表与详情照常可见**；而 30 天后 `cleanup_job` 又会按 `DeletedLog` 把它连同 COS 原件**物理删除**（`cleanup_job.py:107-113`）。即：先「删了还在」，后「突然永久消失」。`backend/tests/test_sync.py:97-118` 的既有用例只断言墓碑 + `deleted_logs`，**未断言 `contents.deleted_at`**，把该分歧固化成了「预期行为」。

**D08-2 证据**：`DELETE /api/v1/contents/{id}`（`contents.py:620-638`）只设 `row.deleted_at / row.deleted_by`，**不写 `DeletedLog`**。`run_cleanup` 的选取条件为 `DeletedLog.cleanup_status=="pending" AND DeletedLog.deleted_at <= cutoff`（`cleanup_job.py:87-95`）——即清理任务**只认 `deleted_logs` 表**。全仓 `DeletedLog` 的唯一写入点是 `sync.py:184`（同步 delete 分支）。⇒ **UI 实际使用的删除路径（REST DELETE）永远不会被 30 天清理任务选中**，只有用户在回收站手动「清空」（`trash_clear`，`contents.py:783-807`）才硬删。设计承诺「30 天后彻底清除」（`client/pages/storage/trash.uvue:16`、`client/pages/privacy/privacy.uvue:87`）在用户可见路径上**未落地**。

**D08-3 证据**：`SyncFieldVersion` 的全部读取点仅 `reconcile.py:25-33`（对账快照）与 `cleanup_job.py:66-71`（清墓碑）；写入点仅 `sync.py:174-181,208-214`。**没有任何代码把 SFV 的 `value` 投影回 `contents` / `events` 权威表**。客户端离线改备注走 `play.uts:647` `enqueueFieldOp('content', contentId, 'remark', remark)` → `push_ops` 把 `field="remark"` 写进 SFV（`sync.py:206-220`）→ 而 `PATCH /contents/{id}`（`contents.py:597-617`）与详情出参读的是 `contents.remark`。⇒ **离线修改的备注上云成功（applied 计数 +1）但用户永远看不到**。「云端主库」的注释（`sync.py:4`）与实际数据落点不符。

**D08-5 证据**：冲突分支（`sync.py:196-205`）只 `conflicts.append(...)`，**没有 `continue`**，控制流继续落到 `sync.py:222-240` 的 `OfflineQueue` 写入——写入的 `payload.value` 取自 `op.get("value")`，即**被判定为败方的客户端旧值**，`updated_at` 也是客户端旧时间戳。该行随后被 `pull_changes`（`sync.py:316-323`）原样下发给所有端。客户端侧对 `conflicts` 只 `console.warn`（`sync_client.uts:402-404`），不触发任何回拉。⇒ 冲突结果既**污染变更日志**（他端 replay 会拿到败方值），客户端也**不收敛到云端权威值**。

**D08-6 证据**：`pullIncremental` 固定 `limit=200`（`sync_client.uts:451`），解析出 `has_more` 后仅打印（`:464-470`）并塞进 `PullOutcome.hasMore`（`:471`）；`PullOutcome.hasMore` 的全部消费点只有 `sync_client.uts:556-560` 的 `runSyncChain`——那里只取 `pull.changes`，**不读 `hasMore`、不续拉**。⇒ 单轮同步最多消费 200 条变更，超出的要等下一次触发（前台 2h 定时 / 网络恢复）。服务端 `MAX_PULL_LIMIT=500`（`api/sync.py:31`）的能力被客户端自限到 200。

**D08-7 证据**：`deleteContent`（`play.uts:433-451`）与 `updateContentRemark`（`play.uts:636-653`）的入队条件都是 `if (getNetKind() == 'none')`。当网络**可达但请求失败**（4xx 越权/404、5xx、超时）时 `res == null`，条件不成立 ⇒ **既不重试也不入队**，直接 `resolve(false)`，用户操作丢失。同一份代码在 `uploader`（`uploadBatch`）里对失败是「入 failed 队列 + 可重试」，两条离线兜底口径不一致。

**D08-8 证据**：`event_ops.uts:381` `op_id: 'op_' + Date.now().toString()` —— 无序列后缀。对比 `sync_client.uts:87-91` 的 `nextOpId()` 显式注释「同毫秒并发防碰撞」并追加 `_opSeq`。`removeByIds`（`queue_store.uts:175-200`）按 `op_id` **相等即删**，同毫秒两条同 id 时会**连带删除另一条未发送的操作**。

**D08-9 证据**：`schemas/sync.py:17` 用正则 `^(content|event|profile)$` 限定 `entity_type`；`sync.py:135-153` 的归属校验是 `if content / elif event / elif profile` 的**互斥链**——若只在 schema 放开新类型（如 `tag`）而忘记加分支，`entity_type="tag"` 会**不经过任何归属校验**直达 `sync.py:155` 的墓碑/字段写入（`sync.py:157-172` 的墓碑归属回退分支也只在 `row`/`entity_rows` 存在时才拦）。此外 `sync.py:71-95` 的 owner 预取映射（`content_owner` / `event_owner`）也是按类型硬编码的，新增类型需同步扩列。

**D08-10 证据**：`sync_client.uts` 共 **29 个 `export`**（`Select-String '^export '` 实测，任务书「22 导出」为旧值）。全仓引用搜索（排除自身）结果：
- **零外部消费者且零内部引用 = 真死**：`stopPeriodicSync`（`:584`，全文件仅 1 处命中=定义本身）。
- **零外部消费者，仅内部使用 = 导出面冗余**：`BACKOFF_MS`（`:64`，外部命中的 `retry.uts` 是其自身的定义）、`isoNow`（`:82`，外部 `event_ops/sentry/time` 各自本地定义）、`NetRestoredListener`（`:617`）、`PullOutcome`（`:665`）、`PushOutcome`（`:651`）、`SyncChainResult`（`:677`）、`ReconcileReport`（`:691`）、`pullIncremental`（`:442`）、`startPeriodicSync`（`:570`）、`registerBackgroundSync`（`:602`）。
- 真实外部消费者仅：`pendingSyncCount`、`isSyncPaused`、`runSyncChain`、`getLastSyncAt`、`subscribeSyncStatus`、`getPauseReason`、`resumeSync`、`pauseSync`、`enqueueFieldOp`、`enqueueDeleteOp`、`onNetworkRestored`、`initSync`、`drainBackgroundTasks`、`reconcileNow`。

**D08-11 证据**：`retry.uts:26-32`——`fn()` 返回**非 null 即 `resolve` 并 return**，`isFatal`/`onFail` 只在 `result == null` 时被调用。而 `uploader.uts:430` 传的 `isFatal: (r) => r != null && !r.ok` 与 `sync_client.uts:373` 传的 `isFatal: (r) => r != null && r.is4xx` 都依赖 `r != null` ⇒ 收到恒为 `null` 的实参，**恒返回 false，分支不可达**（4xx 停条实际由 `fn` 返回非 null 提前 resolve 实现）。`onFail` 在 `sync_client.uts:374-377` 传的是 `registerConsecutiveFailure()` + `isSyncPaused()`，该分支**可达**（网络/5xx 时 `result == null`），是真实生效的。

**D08-12 证据**：`uploader.uts:119-135`（`readPending/writePending`，`path|uploadId` 行分隔）+ `:252-269`（`readLines/writeLines`，key 参数化）与 `queue_store.uts:61-79`（`readLines/writeLines`，单 key）是**同构的行分隔存储读写**（都是 `getStorageSync` → `split('\n')` → 过滤空行 → `join('\n')` → `setStorageSync`）。`uploader` 内部还并存两套（pending 一套、held/failed 一套）。同语义、不同 key，改格式需多处同步。

**D08-13 证据**（候审）：`cleanup_job.py:65-71` `_clear_tombstone` 的删除条件只有 `SyncFieldVersion.entity_id == content_id`，**不带 `entity_type`**。UUID 撞库概率极低，但语义上跨类型误删是可能的；需人工确认是否接受该风险。

**D08-14 证据**：`DeletedLog.content_id`（`db/models/sync.py:57`，`UUID` 列，注释「软删除 30 天物理清理对账」）在 `sync.py:184` 被写入的是**任意 entity 的 id**（`entity_type` 可为 event / profile）。`cleanup_job._delete_content_objects`（`:48-62`）按 `db.get(Content, content_id)` 取行，对 event/profile 实体返回 `None` 直接 return（无害），但 `_clear_tombstone` 仍会清掉该实体的 SFV 行并标 `done` ⇒ event 墓碑被清但 event 行未删，清理语义不完整。

**D08-15 证据**：`mirrorSet`/`readMirror`（`sync_client.uts:186-236`）只追加、**无裁剪、无上限**；`reconcileNow`（`:482-487`）把 `mirrorSnapshot()` 全量作为 `items` 发出。服务端 `SyncReconcileRequest.items` 上限 `max_length=5000`（`schemas/sync.py:84`）⇒ 镜像超 5000 行时请求 422，`reconcileNow` 落到 `hr.status !== 200` 分支返回 `null`，对账**静默失效**。

**D08-16 证据**：`event_ops.uts:29` `import { getNetKind, NetKind } from './uploader'` —— 事件操作域为了一个通用网络类型探测函数，反向依赖照片上传编排模块。`getNetKind` 定义在 `uploader.uts:178-194`，与上传业务无耦合。依赖链 `event_ops → uploader → sync_client → {api, pause_controller, queue_store, retry}` **无环**（`sync_client` 刻意不引 `uploader`，见 `:598-599` 注释），但层级方向别扭。

**D08-17 证据**：`push_ops` 返回 `server_version`（`sync.py:243-244`，由 `_max_version` 计算），`SyncPushResult.server_version`（`schemas/sync.py:66`）随响应下发；客户端 `postBatch`（`sync_client.uts:319-336`）只读 `applied/conflicts/rejected`，全仓搜 `server_version` 在 `client/` 下 0 命中 ⇒ 服务端为该字段付出了一次额外查询（`_max_version` 每批一次）而无人消费。

---

## 依赖方向与抽象覆盖度结论

**FF1：本域是否存在反向依赖？**

存在一处**域间反向依赖**（非环）：
```
event_ops.uts:29  →  uploader.uts（仅为 getNetKind / NetKind）
```
其余依赖方向单向且无环：
```
uploader.uts:51 ──→ sync_client.uts ──→ { api, auth, device_id, retry, time, contract, pause_controller, queue_store }
event_ops.uts:32,33 ──→ queue_store, retry
event_sync.uts:13,17 ──→ api, auth, device_id, retry, contract, log
sync_client.uts:70 ──→ queue_store ；  :50-61 ──→ pause_controller
pause_controller / queue_store / retry ──→ 无内部依赖（纯叶子）
```
- **无循环依赖**。`sync_client` 与 `uploader` 之间是**刻意单向**（`sync_client.uts:598-599` 注释明确说明「此处静态引用 uploader 会成环，故不在此接 continuePendingUploads」），设计意图有据。
- 四者（`sync_client` / `event_sync` / `queue_store` / `retry`）方向为：`sync_client → queue_store`、`sync_client → retry`、`event_sync → retry`、`sync_client ↛ event_sync`、`event_sync ↛ sync_client`（两者互不依赖，各自持有 HTTP 封装与退避调用）。

**FF2：新增一种可同步实体类型（如「标签 tag」）需改几处？**

后端（6 处，缺任一即静默出错）：
1. `backend/app/schemas/sync.py:17` — `entity_type` 正则 `^(content|event|profile)$`（不放行则 422）。
2. `backend/app/services/sync.py:121` — `op.get("entity_type", "content")` 默认值语义。
3. `backend/app/services/sync.py:135-153` — 归属校验 `if/elif` 互斥链（**漏改 = 完全无归属校验**，见 D08-9）。
4. `backend/app/services/sync.py:71-95` — owner 批量预取映射（`content_owner` / `event_owner` 按类型硬编码，需为该类型新增一次 IN 查询）。
5. `backend/app/services/sync.py:184` — `DeletedLog(content_id=entity_id)` 复用（该类型删除时的审计语义，见 D08-14）。
6. `backend/app/workers/cleanup_job.py:48-71` — `_delete_content_objects`（按 `Content` 表删）+ `_clear_tombstone`（按 entity_id 删 SFV）；新类型的物理清理语义需在此定义。

另有一处**并行的实体上云通道**需同步考虑：
7. `backend/app/services/events/sync.py:20-152` — event 类型**不走 `push_ops`**，走独立端点 `/api/v1/events/sync`，自带幂等键 `client_event_id`（`schemas/event.py:121-123`）与独立变更日志写入（`events/sync.py:115-139`）。⇒ 新增可同步实体存在**两套并行注册面**（SFV 通用路径 vs 事件专用路径），扩展时容易只改一套。

客户端（3 处软点，无编译期强制）：
8. `client/utils/sync_client.uts:111,130` — `enqueueFieldOp/enqueueDeleteOp` 的 `entityType: string` 是**自由字符串**，无枚举约束。
9. `client/utils/sync_client.uts:288` — `payload.getString('entity_type') ?? 'content'` 默认值。
10. `client/utils/sync_client.uts:73` `SYNC_TYPES` 是 **op_type**（`upsert_field`/`delete`）过滤，与 entity_type 无关，**无需改**（易被误改）。

无需改（已泛化，可作正面参照）：`reconcile.py:19-47`（按 `user_id` 全量投影，与 entity_type 无关）、`db/models/sync.py`（`entity_type` 为 `String` 主键列，无 DB 枚举，无需迁移）。

---

## 本域已查无问题项（避免遗漏被误读）

- **退避重试已真正收口**：`retry.uts:16` 是唯一 `BACKOFF_MS` 定义（`[2000,4000,8000,8000,8000]`），`sync_client/event_sync/uploader/event_ops` 全部改为 `import { retryAsync }`；grep 证实无第二份退避表。重试次数语义正确（attempt 0..5 = 首试 + 5 次重试）。
- **队列单 key 迁移幂等**：`queue_store.uts:23-46` 有 `_migrated` 进程内守卫，旧 key 仅读 + 删（不写回），空新 key 才迁移，升级不丢操作。
- **`push_ops` 幂等与并发兜底**：幂等按 `(user_id, op_id)` 双条件（`sync.py:62-69`），模型层有 `uq_offline_queue_user_op` 复合唯一约束（`db/models/sync.py:35`）；`push_ops_safe` 两级降级（整批重试 → 逐条隔离，`sync.py:247-293`）语义正确。
- **游标用户级隔离**：`_max_version` 按 `user_id` 取最大 id（`sync.py:340-351`），已修复「全库最大 id 跨用户跳变」的旧缺陷。
- **事件上云幂等**：`client_event_id` 走**部分唯一索引**（`db/models/event.py:19-25`，`WHERE client_event_id IS NOT NULL`）+ 批内预取去重（`events/sync.py:41-52`）+ `IntegrityError` 重试兜底（`:155-163`），设计自洽。
- **断点续传链路完整**：`uploader` 侧 `savePending/clearPending/findPending`（`:119-170`）持久化 `upload_id`，`upload_pipeline` 侧 `spec.resumeUploadId != ''` 时跳过 init 直接 `status` 补传（`:178-182`），`complete` 后回调清理（`:154-161`）；`UploadPipeError.permanent` 区分 4xx 停条与网络可重试（`:53-62`），语义正确。
- **流量约束有明确的「不造假」降级**：`uploader.resolveMode:202-229` 在压缩步骤未接线时对 `thumbnail` 与蜂窝 `auto` 一律返回 `hold`（宁可暂缓也不上传原件冒充缩略图），并附 2026-09-23 取证更正注释（`:18-28,207-214`）——这是有意识的技术债，非缺陷。
- **暂停控制器单源**：`pause_controller.uts` 为唯一实现（存储 key `yishu_sync_paused` 不变），`sync_client:147-167` 与 `uploader:43-50` 均从该模块消费，`MAX_BATCH_FAILURES=10` 无二义。
