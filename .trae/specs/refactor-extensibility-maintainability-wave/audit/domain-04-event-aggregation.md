# 域④ · 事件聚合与时间轴 深度审计

审计范围：`backend/app/`（事件域）+ `client/`（agg/timeline）+ `scripts/gen_agg_fixtures.py`。`agent/` 已排除。
本波性质：**严格行为等价**，缺陷**只登记不修**。

## 覆盖范围与实读清单

### 后端（实读全文）
| 文件 | 行数 |
|---|---|
| `backend/app/api/events.py` | 530 |
| `backend/app/api/event_items.py` | 123 |
| `backend/app/services/events/__init__.py` | 71 |
| `backend/app/services/events/aggregate.py` | 225 |
| `backend/app/services/events/aggregate_write.py` | 373 |
| `backend/app/services/events/edit.py` | 201 |
| `backend/app/services/events/sync.py` | 162 |
| `backend/app/services/events/timeline.py` | 61 |
| `backend/app/services/event_aggregation/pipeline.py` | 252 |
| `backend/app/services/event_aggregation/agg_types.py` | 100 |
| `backend/app/services/event_aggregation/agg_preprocess.py` | 116 |
| `backend/app/services/event_aggregation/agg_candidates.py` | 261 |
| `backend/app/services/event_aggregation/st_dbscan.py` | 138 |
| `backend/app/services/event_aggregation/run_validation.py` | 277 |
| `backend/app/schemas/event.py` | 146 |

### 客户端（实读全文）
| 文件 | 行数 |
|---|---|
| `client/utils/agg/agg_config.uts` | 28 |
| `client/utils/agg/agg_config.md` | 44 |
| `client/utils/agg/agg_types.uts` | 67 |
| `client/utils/agg/st_dbscan.uts` | 199 |
| `client/utils/agg/pipeline.uts` | 104 |
| `client/utils/agg/agg_check.uts` | 149 |
| `client/utils/agg/perf_timing.uts` | 57 |
| `client/utils/agg/fixtures.uts` | 仅读 L1-L30（生成物，13 用例头） |
| `client/utils/agg_runner.uts` | 123 |
| `client/utils/timeline.uts` | 526 |
| `client/utils/time.uts` | 仅 `dayKey`/`friendlyDay` 段（Select-String 取证） |

### 脚本
| 文件 | 行数 |
|---|---|
| `scripts/gen_agg_fixtures.py` | 317 |

### 未读 / 未覆盖
- `backend/app/services/event_aggregation/load_real_photos.py`（14 行，真实截图加载器）、`generate_test_photos.py`（14 行，合成数据生成器）：判定为**验证辅助件、非生产路径**，仅从 `run_validation.py` 的 import 与调用点确认其角色，未逐行实读。
- `client/utils/agg/fixtures.uts` 全文（生成物，13 用例；仅读头部 30 行确认结构与用例 1/2 期望值）。
- `client/components/TabIndex/TabIndex.uvue`、`client/pages/debug/agg-check/agg-check.uvue`：仅通过 Select-String 确认 `agg_runner`/`perf_timing`/`runAllCases` 的消费点，未实读页面逻辑。
- `client/utils/uploader.uts`（`UploadedPhoto` 定义）：仅从 `agg_runner.uts` 的字段用法推断契约，未实读。
- 后端测试 `backend/tests/test_event_sync.py`、`test_aggregation.py`、`test_event_ops.py`、`test_agg_reference.py`：仅确认存在与部分 import 行，未实读断言内容。

### `git status --short`
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
**在途业务文件 = 0**。注意：`AGENTS.md` 记录的「第四窗（媒体票据 Valet Key）主区 8 文件在途未提交＝勿碰名单」（media.py / media_url.py + events/config/errors/main/content-schema/storage）**当前已不在在途列表**，说明该批已入库。本域因此无「勿碰」冲突。

---

## 端云同源对照表（本域重点，逐参数核对）

对照源：`client/utils/agg/agg_config.uts`（端）× `backend/app/services/event_aggregation/agg_types.py::AGG_CONFIG`（云）× 两侧算法实现。

### A. 共享参数（契约表 `agg_config.md` §共享参数对照表声明的 8 项）

| 参数/常量 | 服务端值 | 服务端位置 | 客户端值 | 客户端位置 | 是否一致 |
|---|---|---|---|---|---|
| L0 时间窗默认 | `3600.0` | `agg_types.py:45` (`L0_EPS_T_SEC`) | `3600.0` | `agg_config.uts:14` | ✅ |
| L0 时间窗保守 | `1800.0` | `agg_types.py:46` | `1800.0` | `agg_config.uts:15` | ✅ |
| L0 保守模式开关 | `False` | `agg_types.py:47` | `false` | `agg_config.uts:23` | ✅ |
| L0 空间窗 | `500.0` | `agg_types.py:48` | `500.0` | `agg_config.uts:16` | ✅ |
| L0 min_pts | `3` | `agg_types.py:49` | `3` | `agg_config.uts:17` | ✅ |
| 连拍折叠阈值 | `5.0` | `agg_types.py:42` | `5.0` | `agg_config.uts:18` | ✅ |
| 步行速度上限 | `6000/3600` | `agg_types.py:22` | `6000.0/3600.0` | `agg_config.uts:19` | ✅ 数值一致 |
| 驾车速度上限 | `120000/3600` | `agg_types.py:23` | `120000.0/3600.0` | `agg_config.uts:20` | ✅ 数值一致 |

**8 项共享数值零漂移。** 但「值一致」≠「行为一致」——见 B 段。

### B. 语义/行为漂移（数值相同、实现不同）

| # | 语义 | 服务端实现 | 客户端实现 | 漂移性质 | 是否一致 |
|---|---|---|---|---|---|
| B1 | **L1 日界时区偏移** | `l1_daily_aggregate(..., tz_offset_minutes=0)` 默认 0（UTC） | `l1DailyAggregate(clusters, noise, tzOffsetMin)` 传设备偏移 | 生产路径参数未对齐 | ❌ |
| B2 | **预处理是否去重** | `preprocess()` **无去重步骤** | `preprocess()` **① 去重**（phash/id 兜底） | 步骤集不同 | ❌ |
| B3 | 步行速度分支 | `speed <= WALK_SPEED_MS` → 可信；`<= DRIVE` → `gps_state="approx"` 且**保留坐标** | 无此分支（客户端 `Photo` 无 `gps_state` 字段） | 分支缺失 | ❌ |
| B4 | 单点漂移众数拉回 | `mode_count >= GPS_MODE_MIN_SUPPORT(2)` → 坐标拉回众数格 `gps_state="corrected"` | 无（直接置 `null`） | 分支缺失 | ❌ |
| B5 | 漂移点是否污染后续判断 | 与**未修正**的 `folded[i-1]` 比较（注释明示"漂移点不污染后续判断"） | 与**已修正**的 `corrected[last]` 比较（坐标已被置 null → 后续 `prev.lat != null` 判假 → **跳过速度检查**） | 参考点不同 → 漂移检测级联差异 | ❌ |
| B6 | L2 伪簇日分桶 | `ts.date().isoformat()`（无深夜规则、无时区） | 客户端不做 L2 | 云侧第三套日界 | ❌（云侧内部不自洽） |
| B7 | 时间轴展示日分组 | — | `dayKey(startTime)`（本地自然日，**无深夜规则**） | 与 L1 聚合日界（23:30-1:00 归前一天）不一致 | ❌ |

### C. 云侧独有参数（契约表 `agg_config.md` §云侧独有参数声明 7 项）

| 参数/常量 | 服务端值 | 服务端位置 | 客户端 | 契约表登记 | 是否一致 |
|---|---|---|---|---|---|
| `night.hour` / `night.minute` | `23` / `30` | `agg_types.py:52`, `:67` | 无（端侧硬编码同规则 `st_dbscan.uts:147`） | ✅ 已登记 | ⚠️ **声明漂移**：值从未被读取（见 D04-3） |
| `l2_min_days` | `2` | `agg_types.py:68` | — | ✅ | ✅ |
| `l2_min_photos` | `10` | `agg_types.py:69` | — | ✅ | ✅ |
| `l2_place.max_gap_km` | `5.0` | `agg_types.py:70` (`L2_MAX_GAP_KM:30`) | — | ✅ | ✅ |
| `l2_place.max_gap_hours` | `12.0` | `agg_types.py:70` (`L2_MAX_GAP_HOURS:31`) | — | ✅ | ✅ |
| `l3_tag_threshold` | `3` | `agg_types.py:71` (`L3_MIN_COUNT:35`) | — | ✅ | ✅ |
| `l3_window_days` | `7` | `agg_types.py:72` (`L3_WINDOW_DAYS:34`) | — | ✅ | ✅ |
| `l3_lifecycle.active_days` | `30` | `agg_types.py:73` (`L3_ACTIVE_DAYS:38`) | — | ✅ | ✅ |
| `l3_lifecycle.archive_days` | `90` | `agg_types.py:73` (`L3_SILENT_DAYS:39`) | — | ✅ | ✅ |

### D. 未登记进契约表的参数（对照表遗漏）

| 参数/常量 | 服务端值 | 位置 | 客户端 | 契约表 | 是否一致 |
|---|---|---|---|---|---|
| `GPS_MODE_GRID_DECIMALS` | `3` | `agg_types.py:26` | 无对应实现 | ❌ **未登记** | ❌（端侧无此步） |
| `GPS_MODE_MIN_SUPPORT` | `2` | `agg_types.py:27` | 无对应实现 | ❌ **未登记** | ❌（端侧无此步） |
| L2/L3 硬编码 `span_days < 2` | `2` | `agg_candidates.py:127`, `:88` | — | ❌ **未登记**（未收敛进 AGG_CONFIG） | ❌ |

**端云对照结论**：8 项共享**数值**零漂移；但存在 **7 处语义漂移（B1–B7）+ 3 项未登记参数（D 段）**。其中 B1（L1 时区）与 B2（去重）是**生产路径真实分叉**，且被 AGG-016 双跑夹具的固定参数掩盖（见 D04-1、D04-2）。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D04-1 | `backend/app/services/event_aggregation/pipeline.py:95`, `:161`；`client/utils/agg_runner.uts:91`；`scripts/gen_agg_fixtures.py:38,84` | 声明漂移（端云契约） | P0 | 消除端云 L1 日卡片日期错位 | 改云侧默认值会影响既有落库事件的日期口径 | 行为等价替换 | 同批照片分别在 `tz_offset_minutes=0` 与 `480` 下跑 `l1_daily_aggregate`，比较 `date` 字段；对照 `gen_agg_fixtures.py:38` 的 480 | 批次1 | 确认 |
| D04-2 | `backend/app/services/event_aggregation/agg_preprocess.py:23-93` × `client/utils/agg/pipeline.uts:36-49`；`scripts/gen_agg_fixtures.py:57-71` | 真实重复逻辑 + 端云漂移 | P0 | 去重口径统一，双跑门禁不再自我欺骗 | 云侧补去重会改变既有聚合结果 | 行为等价替换 | `Select-String '_dedup\|dedup'` 证明云侧生产无去重、夹具生成器自造 `_dedup` | 批次1 | 确认 |
| D04-3 | `backend/app/services/event_aggregation/agg_types.py:52,66,67`；`st_dbscan.py:118-122`；`client/utils/agg/st_dbscan.uts:147` | 死码 + 声明漂移 | P1 | `AGG_CONFIG` 恢复"统一参数源"可信度 | 低（当前无人读取） | 删码(需证死) | 见下 D04-3 证据（`Select-String` 全库仅声明点命中） | 批次2 | 确认 |
| D04-4 | `backend/app/api/events.py:312-330` | 软删口径不一致 | P1 | 计数与照片条不再自相矛盾 | 低 | 行为等价替换 | 对比 `_batch_event_photos:200`（有过滤）与 `_batch_counts`（无过滤） | 批次2 | 确认 |
| D04-5 | `backend/app/services/events/aggregate_write.py:63` × `edit.py:98`, `:151-154` | 「用户操作优先」铁律漏洞 | P1 | 铁律在代码层真正成立 | 需产品确认合并后是否允许算法追加 | 行为等价替换 | 见「用户操作优先铁律的代码证据」节 | 批次2 | 确认 |
| D04-6 | `client/utils/timeline.uts:456-460`, `:514-524` | 边界/功能缺口 | P1 | L3 主题流在时间轴可见 | 低 | 行为等价替换 | `buildDayGroups` 仅 `level===1` / `===2` 两分支；L3 事件被静默丢弃 | 批次2 | 确认 |
| D04-7 | `backend/app/services/events/aggregate.py:174-185`；`edit.py:200`；`client/utils/timeline.uts:470`；`client/utils/time.uts:dayKey` | 真实重复逻辑（日分桶 4 套语义） | P1 | 日界口径单点化 | 改动面广（4 处） | 行为等价替换 | 四处日分桶实现逐行对比：`agg_candidates` 无深夜规则/无 tz；`_refresh_event_window` 用服务端本地时区；`dayKey` 用设备自然日 | 批次3 | 确认 |
| D04-8 | `backend/app/api/events.py:140-165` / `:168-221` / `:284-309` / `:333-363` | 真实重复逻辑 | P1 | 图片 URL 组装三写归一 | 低 | 行为等价替换 | 三段均为「Content IN ids → `content_urls()` → thumbnail dict」，字段与过滤条件近同 | 批次3 | 确认 |
| D04-9 | `backend/app/api/events.py:168-221` × `:333-363` | 真实重复逻辑（同语义双实现） | P1 | 删掉兼容通路重复实现 | 需确认 `photo_ids` 兼容通路是否仍被客户端消费 | 删码(需证死) | `_batch_event_photos` 用 `ROW_NUMBER` 截 6；`_batch_photo_ids` 拉全量再 Python 截 4——同语义两实现 | 批次3 | 候审 |
| D04-10 | `backend/app/services/event_aggregation/agg_preprocess.py:101-103` | 死码/空抽象 | P2 | 清理 | 无 | 删码(需证死) | `_cell_center(cell)` 函数体 `return cell`，恒等映射 | 批次4 | 确认 |
| D04-11 | `backend/app/services/events/aggregate_write.py:368-373` | 零调用导出 | P2 | 清理兼容 shim | 需确认无外部（文档/测试）引用 | 删码(需证死) | `Select-String '_write_upper_events'` 仅命中 re-export 与定义处，无调用方 | 批次4 | 确认 |
| D04-12 | `client/utils/agg/agg_config.uts:19` | 零调用导出 | P2 | 契约表与实际实现对齐 | 无 | 删码(需证死) | `Select-String 'WALK_SPEED_MS'` 在 `client/` 仅命中定义行 + `agg_config.md:18` | 批次4 | 确认 |
| D04-13 | `backend/app/services/events/sync.py:68-72` | 边界/异常 | P1 | 端侧同步不再被历史脏数据整条拒绝 | 需确认是否存在 `client_event_id IS NULL` 的存量事件 | 行为等价替换 | 分支逻辑：只要该用户存在**任意** `client_event_id IS NULL` 的事件，所有 `cid=None` 提交均判 duplicate | 批次2 | 候审 |
| D04-14 | `backend/app/services/event_aggregation/run_validation.py:126-129` | 门禁缺口 | P1 | 双跑门禁恢复鉴别力 | 低 | 新增工具 | 该断言自比 `AGG_CONFIG["l0"]["eps_s_m"] == 500.0`（常量比常量，恒真），未与端侧常量比对 | 批次2 | 确认 |
| D04-15 | `scripts/gen_agg_fixtures.py:158-165`；`client/utils/agg/fixtures.uts` | 门禁缺口 | P0 | 双跑门禁覆盖 `approx`/`corrected` 分支 | 需重生成夹具 + 端侧复跑 | 新增工具 | 13 用例中仅用例 6 触达"超驾车上限→置空"；无 `WALK<speed<=DRIVE` 与 `mode_count>=2` 拉回用例 | 批次1 | 确认 |
| D04-16 | `backend/app/api/events.py:100` | 边界/依赖方向 | P1 | API 层不再直连算法包 | 低 | 纯移动+兼容再导出 | `api/events.py` 直接 `from app.services.event_aggregation.pipeline import l3_lifecycle`，绕过 `services/events` 门面 | 批次3 | 确认 |
| D04-17 | `backend/app/services/event_aggregation/st_dbscan.py:31` | 声明漂移 | P2 | 类型标注不再说谎 | 无 | 行为等价替换 | `tags: list[str] = None`（标注 list 默认 None）；对照 `agg_types.py:85` 用 `field(default_factory=list)` | 批次4 | 确认 |
| D04-18 | `backend/app/services/events/aggregate.py:44` | 声明漂移（跨域重复常量） | P2 | 窗口常量收敛 | 低 | 行为等价替换 | `_AGG_WINDOW_DAYS = 30` 与 `L3_ACTIVE_DAYS = 30` 同值不同源，语义不同却无交叉引用 | 批次4 | 候审 |

---

**D04-1 证据（P0 · L1 日界时区端云未对齐）**：
读了 `st_dbscan.py:98-138`，`l1_daily_aggregate` 签名 `tz_offset_minutes: int = 0`，docstring 明示「端侧传设备偏移；默认 0 = UTC（保持第一波口径，向后兼容）」。
读了 `pipeline.py:95`：`days = l1_daily_aggregate(clusters, noise)` —— **未传 tz**；`pipeline.py:161`（增量路径）：`new_days = l1_daily_aggregate(new_clusters, new_noise)` —— **同样未传 tz**。
读了 `client/utils/agg_runner.uts:91`：`const days = l1DailyAggregate(clusters, noise, tzOffsetMin)` —— **端侧传设备偏移**。
读了 `scripts/gen_agg_fixtures.py:38`：`TZ_OFFSET_MIN = 480  # 上海（双跑同参）`，`:84` `l1_daily_aggregate(clusters, noise, tz_offset_minutes=tz)`。
结论：**双跑门禁在 tz=480 下比对（两侧一致），但云侧生产路径跑在 tz=0**。门禁参数 ≠ 生产参数，属典型「门禁自我欺骗」。上海用户 00:00-07:59 本地拍摄的照片，云侧按 UTC 分桶会落到前一日。

**D04-2 证据（P0 · 预处理去重端云分叉）**：
读了 `agg_preprocess.py:23-93`，`preprocess` 步骤为「排序 → 连拍折叠 → GPS 漂移修正」，**无去重**，且 `RawPhoto`（`agg_types.py:77-89`）**无 phash 字段**。
读了 `client/utils/agg/pipeline.uts:36-49`，`dedup()` 按 `phash != '' ? phash : id` 去重，且在 `preprocess():54` 第一步执行；客户端 `RawPhoto`（`pipeline.uts:17-33`）**有 phash 字段**。
读了 `scripts/gen_agg_fixtures.py:57-71`，`_dedup()` 在夹具生成器内**另写一份 Python 去重**，注释自称「与 UTS pipeline.dedup 同语义」。
结论：云侧生产 `aggregate_user → aggregate/incremental_aggregate → preprocess` **不做去重**；端侧做。夹具生成器用本地 `_dedup` 垫在 Python `preprocess` 前面，使双跑比对的是「客户端 vs 客户端行为的 Python 复制品」，而非「客户端 vs 云侧生产」。这是本域最隐蔽的契约分叉。

**D04-3 证据（P1 · AGG_CONFIG 部分键为死配置）**：
读了 `agg_types.py:57-74`，`AGG_CONFIG` 含 `l0.eps_t_sec` / `l0.eps_s_m` / `l0.min_pts` / `gps_speed` / `night` 五组键。
`Select-String 'WALK_SPEED_MS|NIGHT_HOUR|NIGHT_MIN|gps_speed'`（排除 unpackage/__pycache__）全库命中：
- `WALK_SPEED_MS`：`agg_types.py:22`（定义）、`agg_types.py:66`（写入 AGG_CONFIG）、`agg_preprocess.py:17,68`（**直接 import 常量，不走 AGG_CONFIG**）、`pipeline.py:9,67`（注释/re-export）、`client/agg_config.uts:19`、`agg_config.md:18`
- `NIGHT_HOUR`/`NIGHT_MIN`：`agg_types.py:52`（定义）、`:67`（写入 AGG_CONFIG）、`pipeline.py:65-66`（re-export）——**零读取点**
- `gps_speed`：仅 `agg_types.py:66`（定义处）

实际夜界规则在 `st_dbscan.py:118-122` 用字面量 `23` / `30` 硬编码，端侧 `st_dbscan.uts:147` 同样硬编码。故 `AGG_CONFIG["night"]` 是**装饰性配置**：改它不会改变任何行为，而契约表 `agg_config.md:25` 却把它登记为有效参数。
`AGG_CONFIG["l0"]["eps_s_m"]` 的唯一读取点是 `run_validation.py:127`（自比较，恒真）。`eps_t_sec` / `min_pts` 同理零读取——`aggregate()` 直接使用模块常量 `L0_EPS_S_M` / `L0_MIN_PTS`（`pipeline.py:90`, `:152`）。
故 D04-14 的「AGG-016 参数来自统一配置」断言为**恒真断言**。

**D04-4 证据（P1 · `_batch_counts` 缺软删过滤）**：
读了 `events.py:312-330`：`select(EventItem.event_id, count(), count().filter(Content.content_type=="photo")).join(Content, ...).where(EventItem.event_id.in_(event_ids)).group_by(...)` —— **无 `Content.deleted_at.is_(None)`，也无 `Content.user_id == user_id`**。
对照 `:200`（`_batch_event_photos`）与 `:354`（`_batch_photo_ids`）**均有** `Content.deleted_at.is_(None)` + `Content.user_id == user_id`；`:349-350` 注释更明确写了 P2-5 修复理由「不依赖上游不变量，DB 回查层自带归属/软删过滤（纵深）」。
后果：`EventOut.photo_count` / `content_count` 会统计已软删内容，而同一响应的 `photos[]` 已过滤 → **卡片显示"5 张"但照片条只有 3 张**。`event_items.py:82` 的计数同型缺过滤。
`Select-String 'deleted_at'` 全库取证：`events.py` 命中 L72 / L156 / L200 / L265 / L299 / L354 —— **恰好 6 处手抄软删**（与任务书一致），第 7 个 Content 聚合查询（`_batch_counts`）**漏了**。即"手抄 6 处"本身就是一致性风险的实证。

**D04-6 证据（P1 · L3 事件在客户端被静默丢弃）**：
读了 `client/utils/timeline.uts:442-525`，`buildDayGroups` 循环：`:451` `isPending` 分支 → `pending`；`:456` `ev.level === 1` → `l1`；`:458` `ev.level === 2` → `l2`；**无 `level === 3` 分支，无 else**。L3 事件既不入 `pending` 也不入任何 group → 从渲染列表消失。
同时后端确实产出并下发 L3：`api/events.py:112-116` 为 `level >= 3` 事件派生 `lifecycle` 并放入 `EventOut.lifecycle`（`schemas/event.py:68`），客户端 `TimelineEvent` **未解析 `lifecycle` 字段**（`timeline.uts:273-287` 构造参数中无 lifecycle）。
结论：L3 主题流的下发数据 + 生命周期计算在客户端**零消费**。

**D04-11 证据**：`Select-String '_write_upper_events'` 命中 `aggregate_write.py:29,108(注释),368(定义)`、`aggregate.py:34`、`services/events/__init__.py:21,50` —— 全部为 re-export / `__all__` / 注释，**无任何调用点**（含测试）。故为零调用导出。

**D04-12 证据**：`Select-String 'WALK_SPEED_MS'` 在 `client/` 下仅命中 `agg_config.uts:19`（定义）与 `agg_config.md:18`（文档表）。`client/utils/agg/pipeline.uts:13` 的 import 列表为 `{ BURST_GAP_SEC, DRIVE_SPEED_MS }`，**不含 WALK_SPEED_MS**。故该导出在客户端零调用——它对应的正是 D04-2/B3 中客户端缺失的"步行速度分支"。

**D04-14 证据**：`run_validation.py:126-129` `_check(failures, "AGG-016: 参数来自统一配置", AGG_CONFIG["l0"]["eps_s_m"] == 500.0, ...)`。左侧取云侧字典、右侧写字面量，二者同源同文件，**恒真**，不构成端云比对。真正的端云比对只有 `gen_agg_fixtures.py` 生成的夹具 + 端侧 agg-check 页（`agg_check.uts:139-149`），而该页是**手动调试页**（`client/pages/debug/agg-check/`，且 `client/utils/config.uts:72` `AGG_CHECK_ON_DEVICE: boolean = false` 默认关）——**不在任何自动门禁内**。

**D04-15 证据**：读了 `gen_agg_fixtures.py:94-255` 全部 13 个用例：
- 用例 6 `gps-drift-corrected`（`:158-165`）是唯一涉 GPS 漂移的用例，10s 内 100km → `speed ≈ 10000 m/s > DRIVE_SPEED_MS(33.3)` → 走"超物理上限"分支；且三点分属三个网格、`mode_count=1 < GPS_MODE_MIN_SUPPORT(2)` → 走 `degraded`（坐标置空）。端侧同样置 null，**两侧期望值巧合相同**。
- **无任何用例进入 `WALK_SPEED_MS < speed <= DRIVE_SPEED_MS`（`approx`）分支**，也**无任何用例构造 `mode_count >= 2` 的众数拉回（`corrected`）场景**。
故 D04-2 的 B3/B4/B5 三处漂移**完全落在夹具覆盖之外**，双跑全绿也不代表端云同源。

---

## "用户操作优先"铁律的代码证据

铁律原文（`AGENTS.md` 关键约束 / `edit.py:4` / `api/events.py:458`）：**手动合并/拆分/确认后，自动算法永不覆盖用户决定（AGG-013）**。

### 用户侧写入（确实置了保护标记）
| 操作 | 位置 | 置位 |
|---|---|---|
| merge | `edit.py:98` | `target.status = "confirmed"`（**未置 `title_source`**） |
| split | `edit.py:125-126` | `new_ev.status = "confirmed"` + `title_source = "user"` |
| confirm（带 title） | `edit.py:151-154` | `status = "confirmed"` + `title_source = "user"` |
| confirm（不带 title） | `edit.py:151` | 仅 `status = "confirmed"`（**未置 `title_source`**） |
| set_cover | `edit.py:172` | 无保护标记（仅封面字段） |

### 算法侧守卫（三处，判据不一致）
1. **L1 路径** `aggregate_write.py:63`：`if existing.status == "confirmed" and existing.title_source == "user": continue` → 需**同时**满足两条件。
2. **L2 路径** `aggregate_write.py:156`：`if any(mset <= covered for covered in members_by_event.values()): continue` → `members_by_event` 来自 `:119-125` 查询（`level >= 2` 且未软删，**不限 confirmed**）→ 靠"成员组合已覆盖"去重，不依赖 `title_source`。
3. **L3 路径** `aggregate_write.py:217`（`confirmed_titles` 需 `status=="confirmed" AND title_source=="user"`）、`:221-224`（`confirmed_member_events`，仅需 `status=="confirmed"`）、`:234-238`（同标签已有流时，仅当 `status=="confirmed" AND title_source=="user"` 才不动成员）。

### 结论（D04-5）
- **拆分/带标题确认**：铁律成立（`title_source="user"` 双条件满足）。
- **合并不成立**：`merge_events` 只置 `status`，不置 `title_source`。合并后的 target 若原始 `title_source` 为 `"template"`（云端 L1，`aggregate_write.py:77`）或 `"device"`（端侧 L1，`sync.py:101`），则 L1 守卫 `:63` 的 `title_source == "user"` 判假 → **算法可继续向该事件追加成员并外延时间窗**（`:65-71`）。这直接违反 `edit.py:65-66` 的注释承诺「合并后 target 置为 confirmed，自动聚合不再拆分/改动它」。
- **不带标题确认同理**：`confirm_event(title=None)` 是合法调用（`schemas/event.py:95` `title: str | None = None`），此时 L1 守卫同样失效，与 `:149` 注释「用户背书后算法不再改动」不符。
- 守卫判据**三套不一致**（L1 双条件 / L2 成员集 / L3 双条件+成员集），新增路径极易漏置其一。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？
**存在 1 处跨层反向/越级依赖（D04-16），包间无环。**

- `services/events/aggregate.py:24` → `services/event_aggregation.pipeline`（正向 ✅）
- `services/events/aggregate_write.py:309-310` → `event_aggregation.pipeline` / `st_dbscan`（正向 ✅）
- `services/events/sync.py:15` → `services/events.aggregate`（同包 ✅）
- `event_aggregation/*` → `services/events`：**零命中**（`agg_types.py:19` 只 import 同包 `st_dbscan`；`agg_candidates.py:17-26` 只 import 同包）→ **无环 ✅**
- ❌ **越级**：`api/events.py:100` `from app.services.event_aggregation.pipeline import l3_lifecycle` —— API 层**绕过 `services/events` 门面**直连算法包。`services/events/__init__.py` 未再导出 `l3_lifecycle`，故 API 无法走门面（门面覆盖度缺口）。
- 包内单向链：`agg_types` ← `agg_preprocess` / `agg_candidates` / `st_dbscan` ← `pipeline`（`pipeline.py:34-71` 纯 re-export，`agg_candidates.py:10` 明示「不 import pipeline，避免循环」）✅ 边界清晰。

**`services/events` vs `services/event_aggregation` 职责边界**：
- 语义划分清晰：`event_aggregation` = 纯算法（无 DB / 无 Session / 无 ORM import，已逐文件核实），`services/events` = 读写库编排。这是本域最干净的一处设计。
- 唯一侵蚀点：`event_aggregation/run_validation.py` 与 `load_real_photos.py` / `generate_test_photos.py` 属**验证件**却住在生产算法包内（`run_validation.py:206` 甚至 `from ...load_real_photos import` 读 `C:\Users\ghf\Pictures\Screenshots`）——算法包被掺入环境相关代码，`pipeline.py:26` 的 docstring 也承认 `run_validation` 是消费方。

### FF2：新增一层聚合 / 一种算法需改几处？（注册点全列）

**无任何注册表/插件机制**，全部为硬编码列举。新增 L4 或替换算法需改动 **19 处**：

后端（11）
1. `backend/app/services/event_aggregation/agg_types.py:22-52` —— 新增阈值常量
2. `backend/app/services/event_aggregation/agg_types.py:57-74` —— `AGG_CONFIG` 新键（并同步 docstring `:11-12`）
3. `backend/app/services/event_aggregation/agg_types.py:92-100` —— `AggregateResult` 增字段（`l0_clusters`/`l1_days`/`l2_candidates`/`l3_candidates` 硬编码 4 个）
4. `backend/app/services/event_aggregation/st_dbscan.py`（或新增算法模块）
5. `backend/app/services/event_aggregation/agg_preprocess.py:23-93`（若预处理需变）
6. `backend/app/services/event_aggregation/agg_candidates.py:59-142`（新候选构造）
7. `backend/app/services/event_aggregation/pipeline.py:34-71`（re-export 块）、`:79-116`（`aggregate`）、`:119-197`（`incremental_aggregate`）、`:200-207`（公共入口）
8. `backend/app/services/events/aggregate_write.py:24-30`（`__all__`）、`:93-299`（`_write_upper_candidates`）、`:302-359`（`_previous_aggregate_result`）
9. `backend/app/services/events/aggregate.py:29-35`（re-export）、`:67-162`（`aggregate_user` 模式分支）、`:165-185`（`_l2l3_candidates_from_photos`）
10. `backend/app/services/events/__init__.py:13-70`（re-export + `__all__`）
11. `backend/app/schemas/event.py:38`（`level: int = Field(..., ge=0, le=3)` —— **硬编码 4 层上界**）、`:36-68`（`EventOut`）、`backend/app/api/event_items.py:38`（同款 `ge=0, le=3`）

客户端（6）
12. `client/utils/agg/agg_config.uts:14-28` + `client/utils/agg/agg_config.md:8-31`（契约表）
13. `client/utils/agg/st_dbscan.uts` / `client/utils/agg/pipeline.uts`
14. `client/utils/agg/agg_runner.uts:67-123`（`aggregateToEvents`）
15. `client/utils/agg/agg_types.uts:28-66`（夹具类型）
16. `client/utils/agg/agg_check.uts:59-124`（`runCase` 比对维度）
17. `client/utils/timeline.uts:442-525`（`buildDayGroups` 硬编码 `level===1`/`===2`）

脚本（1）
18. `scripts/gen_agg_fixtures.py:94-255`（`build_cases`）+ 重生成 `client/utils/agg/fixtures.uts`

门禁（1）
19. `backend/app/services/event_aggregation/run_validation.py`（新增场景断言）

**关键结论**：`EventOut.level` 的 `ge=0, le=3` 与 `AggregateResult` 的四个硬编码字段是**结构性 4 层假设**，任何"新增一层"都必须穿透后端 11 处 + 客户端 6 处 + 脚本 1 处，无单点扩展位。

---

## 本域已查无问题项（避免遗漏被误读）

1. **`services/event_aggregation` 零 DB/ORM 依赖** —— 逐文件核实：该包 7 个生产模块（`agg_types`/`agg_preprocess`/`agg_candidates`/`st_dbscan`/`pipeline` + `__init__`）**无 `sqlalchemy` / `Session` / `app.db` import**，纯函数式算法层。这是本域最健康的一处分层。
2. **包间无循环依赖** —— `event_aggregation` 不 import `services.events`（全库 `Select-String` 零命中），`pipeline.py:34-71` re-export 与 `agg_candidates.py:10` 的单向约束均成立。
3. **软删 30 天口径在 `services/events/*` 内一致** —— `timeline.py:32`、`aggregate.py:89,96,196`、`aggregate_write.py:55,123,316`、`edit.py:23`、`sync.py:61` 均为 `Event.deleted_at.is_(None)` / `Content.deleted_at.is_(None)`，无遗漏（缺过滤的只有 `api/events.py:_batch_counts`，见 D04-4）。
4. **`haversine_m` 端云两份实现数值等价** —— `st_dbscan.py:38-45` 与 `st_dbscan.uts:45-54` 逐行同构（同 `r=6371000.0`、同 `asin(sqrt(a))` 公式），UTS 用 `Math.PI/180` 替代 `math.radians` 属语言等价改写，非漂移。
5. **深夜归属规则端云字面量一致** —— `st_dbscan.py:118-122` 与 `st_dbscan.uts:147` 三条件完全相同（`hour==23 && min>=30` / `hour==0` / `hour==1 && min==0`），含 `01:01 → 当天` 边界。问题在于"配置未接线"（D04-3），不在规则本身。
6. **`is_sparse` 阈值端云一致** —— `st_dbscan.py:137` `len(ps) <= 2` 与 `st_dbscan.uts:197` `ps.length <= 2` 一致。
7. **`l1_daily_aggregate` 的 `tz_offset_minutes=0` 短路逻辑无歧义** —— `st_dbscan.py:117` `shifted = ts + timedelta(...) if tz_offset_minutes else ts`，`tz=0` 时等价于客户端 `ts + 0*60000`，无 off-by-one。
8. **`merge_events` / `split_event` 的 autoflush 顺序已修复** —— `edit.py:96`、`:140` 均先 `db.flush()` 再 `_refresh_event_window`，注释记录了「真机拆分子验证暴露同型 bug」的历史，当前代码正确。
9. **`sync_client_events` 幂等主路径正确** —— `sync.py:41-52` 批量预取 `client_event_id` + `:155-162` `IntegrityError` 重试包装，并发同键只落一次（唯一问题在 `cid is None` 分支，见 D04-13）。
10. **`_pick_cover` L2 时间居中 / L3 最新优先的分支正确** —— `agg_candidates.py:244-259`：`level>=3` → `mid_ts=None` → `ts_key = p.ts.timestamp()`（最新优先）；`level<3` → `-abs(ts - mid_ts)`（居中）。与 `:236-238` docstring 一致。
11. **`_gps_reliable` 的 `getattr` 防御合理** —— `agg_candidates.py:194` `getattr(p, "gps_state", "ok")`，使直接喂 `RawPhoto`（无 `gps_state`）时不崩，注释已说明用途。
12. **`client/utils/agg/perf_timing.uts` 无缺陷** —— 幂等写 storage、`rawGrant == ''` 时不计时，30s 门禁文案与 `AGENTS.md`「30s 计时 ✅ 实测 6.0s」口径一致。

---

## 报告元信息
- 审计者：域④ 代码审计子 agent（易扩展/易维护重构波 · Phase A）
- 条目总数：**18**（P0 ×3、P1 ×9、P2 ×6）
- 端云对照漂移：共享数值 **0 处**；语义漂移 **7 处**（B1–B7）；未登记参数 **3 项**
- 状态：确认 **14** / 候审 **4**（D04-9、D04-13、D04-18 及 D04-1 的"批次1"落地口径待人工确认）
