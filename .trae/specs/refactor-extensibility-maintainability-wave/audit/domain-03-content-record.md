# 域③ · 内容与记录 深度审计

> 波次：易扩展/易维护重构波 · Phase A（严格行为等价，只登记不修）
> 范围：`backend/app/` + `client/` + `scripts/` + `deploy/`；`agent/` 排除
> 取证工具说明：本次 `Grep` 工具不可用（`rg execution error: program not found`），
> 全部检索改用 `Get-ChildItem -Recurse -File -Include ... | Select-String -Pattern <p>`
> 等价式执行；下文每条均给出可复现命令。

## 覆盖范围与实读清单

### `git status --short`（审计开始时，工作区 `d:\GuangH-App`）

```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```

结论：**仅两个未跟踪目录，无在途修改文件**。域② 已确认的「第四窗 8 文件勿碰名单」
（media.py / media_url.py / events.py / config.py / errors.py / main.py /
content-schema / storage）在本时点**均已入库、无未提交改动**，本域审计无需避让。
（`backend/app/api/media.py`、`backend/app/services/external/media_url.py` 已实读，
见下文 D03-2。）

### 逐条实读清单（行数为 `@(Get-Content).Count`，与 Read 工具行号一致）

后端 API：

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/app/api/contents.py` | 902 | **全文实读**（L1-902）。4 个 router 混居 |
| `backend/app/api/deps.py` | 102 | 全文实读（`uuid4_str` / `load_alive_content` / `pagination_params`） |
| `backend/app/api/__init__.py` | 57 | 全文实读（`make_router` / `RequestIdRoute`） |
| `backend/app/api/media.py` | 160 | 部分实读（L39/L43/L75-103/L149：`uuid4_str`、`content_type_for`、`deleted_at` 口径） |

后端 service：

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/app/services/pipeline.py` | 488 | 全文实读（含冷冻核心与注册表） |
| `backend/app/services/pipeline_common.py` | 59 | 全文实读 |
| `backend/app/services/pipeline_photo.py` | 118 | 全文实读 |
| `backend/app/services/pipeline_audio.py` | 193 | 全文实读 |
| `backend/app/services/pipeline_tagging.py` | 50 | 全文实读 |
| `backend/app/services/pipeline_ext/__init__.py` | 69 | 全文实读 |
| `backend/app/services/pipeline_ext/payload.py` | 79 | 全文实读 |
| `backend/app/services/pipeline_ext/sensitive.py` | 176 | 部分实读（L40-90：两条同谓词查询） |
| `backend/app/services/photo_content.py` | 287 | 全文实读 |
| `backend/app/services/classifier.py` | 137 | 全文实读 |
| `backend/app/services/ai_tagging.py` | 191 | 全文实读 |
| `backend/app/services/ner.py` | 109 | 全文实读 |
| `backend/app/services/external/media_url.py` | 180 | 全文实读（`content_urls` 唯一实现） |
| `backend/app/schemas/content.py` | 167 | 全文实读 |

后端其它（检索取证，未全文实读）：`services/upload/register.py`（217）、
`services/upload_meta.py`（90）、`services/file_magic.py`（43）、
`services/events.py` 域（`api/events.py` L126-165/L200-310/L440-530 相关段）、
`services/rag/recall.py` L104-129、`api/capsules.py` L32-63、`services/notify.py` L83、
`db/models/content.py` L13-91、`main.py` L38/L118-142。

客户端：

| 文件 | 行数 | 说明 |
|---|---|---|
| `client/components/RecordSheet/RecordSheet.uvue` | 2208 | **结构实读**：template 1-252 / script 254-1349 / style 1351-2208；细读 L940-1060、L1260-1349 |
| `client/utils/text_recorder.uts` | 216 | 全文实读 |
| `client/utils/capsule_api.uts` | 172 | 全文实读 |
| `client/utils/voice.uts` | 493 | 部分实读（L320-493） |
| `client/utils/event_ops.uts` | 492 | 部分实读（L100-155） |
| `client/utils/search_api.uts` | 279 | 部分实读（L80-120、L185-200） |
| `client/utils/play.uts` | 653 | 部分实读（L380-440、L490-540、L580-645） |
| `client/utils/contract.uts` | 107 | 检索取证（L24-25） |
| `client/pages/storage/trash.uvue` | — | 检索取证（L56/L133-168 真端点接线） |

`scripts/` / `deploy/`：`scripts/api_smoke_cases.py`、`scripts/realdevice/r2_4b_verify.ps1`、
`scripts/build_image_index.py` 等**检索取证**（未见内容类型注册点）；`deploy/` 无本域注册点。

**未覆盖**：`RecordSheet.uvue` 的 template(1-252) 与 style(1351-2208) 未逐行读；
`client/pages/detail/detail.uvue`（约 600+ 行）未实读，仅检索取证。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D03-1 | `backend/app/api/contents.py:85,89,570,571` | 巨文件（4 router 混居 902 行） | P1 | 高 | 低（有 monkeypatch 约束，见专章） | 纯移动+兼容再导出 | `python -c "import app.api.contents"`；`pytest backend/tests/test_contents.py test_content_upload.py test_ab_scenarios.py test_echo.py` | 批次1 | 确认 |
| D03-2 | `contents.py:539` / `contents.py:711` / `events.py:140` / `rag/recall.py:111` / `capsules.py:63` | 真实重复（缩略图签票 5 份） | P1 | 高 | 低 | 行为等价替换 | 5 处 diff 对照 + `pytest -k thumbnail` | 批次2 | 确认 |
| D03-3 | `text_recorder.uts:78` / `voice.uts:337` / `capsule_api.uts:52,85` | 真实重复（客户端 POST /contents 4 份） | P1 | 中高 | 低 | 行为等价替换 | 三处请求体字段逐字对照 | 批次2 | 确认 |
| D03-4 | `contents.py:574` vs `event_ops.uts:135` / `play.uts:500` | 双通路 + 详情端点零生产调用 | P2 | 中 | 低 | 删码(需证死) | 检索式见证据 | 批次3 | 确认 |
| D03-5 | `pipeline_ext/payload.py:66` / `pipeline_ext/__init__.py:28` / `schemas/content.py:102` / `classifier.py:103` | 死码 / 零调用导出（4 项） | P2 | 中 | 低 | 删码(需证死) | 检索式见证据（各 1-2 命中＝定义自身） | 批次3 | 确认 |
| D03-6 | `contents.py:627,648,667,683,768` vs `590,610,882` | 边界（畸形 ID 守卫不一致） | P1 | 中高 | 低 | 行为等价替换 | 新增/复用 `uuid4_str`；对 5 端点发 `not-a-uuid` 断言 404 | 批次1 | 确认（500 后果＝候审） |
| D03-7 | `schemas/content.py:13` / `contents.py:257` / `pipeline.py:226-228` | 声明漂移（`article` 无管线处理器） | P1 | 中高 | 低 | 新增工具（注册处理器） | `Select-String 'article'` + `_resolve_handler("article") is None` | 批次1 | 确认 |
| D03-8 | `contents.py:142,268,285,310,357,367,411,700`（全仓 49 处） | 真实重复（软删过滤手抄） | P1 | 中高 | 低 | 新增工具 | 检索式见证据；本域 8 处逐行对照 | 批次2 | 确认 |
| D03-9 | `pipeline_ext/sensitive.py:53-63` vs `:66-84` | 真实重复（同谓词两查） | P2 | 中 | 低 | 行为等价替换 | 两段 where 子句逐字对照 | 批次3 | 确认 |
| D03-10 | `pipeline_photo.py:36-48` vs `ai_tagging.py:171-190` | 真实重复（COS→临时文件 2 份） | P2 | 中 | 低 | 新增工具 | 两处 tempfile 命名前缀不同（`yishu_photo_` / `yishu_aidesc_`） | 批次3 | 确认 |
| D03-11 | `client/components/RecordSheet/RecordSheet.uvue:1-2208` | 巨文件（2208 行单体组件） | P1 | 高 | 中 | 纯移动+兼容再导出 | 编译门 + 录音/文字/照片三链真机回归 | 批次2 | 确认 |
| D03-12 | `schemas/content.py:9,13` / `contents.py:130,257` / `pipeline.py:226-228` / `notify.py:83` / `search_api.uts:90-101` | 可扩展性缺口（新增类型需改 6+ 处） | P1 | 高 | 中 | 新增工具 | 见「抽象覆盖度」章节逐点列举 | 批次1 | 确认 |
| D03-13 | `schemas/content.py:114` → `contents.py:561` | 边界（业务常量落在 schema 层） | P2 | 低 | 低 | 纯移动+兼容再导出 | 唯一来源＝`schemas/content.py:114`，被 api 层 import | 批次3 | 确认 |
| D03-14 | `contents.py:517` / `events.py:366` / `messages.py:22` | 命名冲突（`_to_out` 三实现，非真重复） | P2 | 低 | 低 | 纯移动+兼容再导出 | 三处签名与出参类型不同 | 批次3 | 确认 |
| D03-15 | `contents.py:300-301` vs `photo_content.py:180-184` | 口径不一致（护栏 `mask` 分支在照片路径被丢弃） | P2 | 中 | 低 | 行为等价替换 | 两处 `verdict.get("action")` 分支对照 | 批次2 | 确认 |

---

### D03-1 证据：巨文件 / 4 router 混居（已核实真实行号）

读了 `contents.py` 全文（L1-902），核实任务书给出的行号基本准确，**真实边界**：

- `router = make_router(prefix="/api/v1/contents", ...)` → **L85**
- `profile_sensitive_router = make_router(prefix="/api/v1/profile", ...)` → **L89**
- `trash_router = make_router(prefix="/api/v1/trash", ...)` → **L570**
- `favorites_router = make_router(prefix="/api/v1/favorites", ...)` → **L571**
- 另有**中段 import 块 L559-568**（`# noqa: E402`，为 trash/favorites 补 import），
  是「文件被两个时期拼接」的结构化石。
- 端点归属：`router` 11 个（upload L169 / create L250 / list L394 / detail L574 /
  PATCH L597 / DELETE L620 / favorite×3 L641,660,676 / waveform L863）；
  `profile_sensitive_router` 3 个（L466,484,501）；`trash_router` 3 个（L723,761,783）；
  `favorites_router` 1 个（L689）。合计 **18 个端点**。

### D03-2 证据：缩略图签票 5 份同语义实现

同语义＝「`content_urls(None, <thumbnail_key>, <uid>)` → 赋 `item.thumbnail_url`，异常降级不阻塞」：

1. `contents.py:539-548` `_attach_thumb`（守卫 `c.content_type == "photo"`）
2. `contents.py:711-719` `favorites_list` 内联（**无 content_type 守卫**，仅判 `c.thumbnail_key`）
3. `backend/app/api/events.py:140-165` `_attach_thumbnail_urls`
4. `backend/app/services/rag/recall.py:111-116`
5. `backend/app/api/capsules.py:63`

**为何同语义**：五处都调用同一个唯一实现 `media_url.content_urls`（`media_url.py:147`），
差异仅在「是否限 photo」与「异常日志级别」。其中 #1 与 #2 同在 `contents.py` 内、
**仅 172 行之隔**，同一渲染语义两种写法，且守卫条件不同（photo-only vs any-with-key）——
这是本域内最直接可复现的重复。检索式：
`Get-ChildItem -Recurse -File -Include *.py -Path backend | Select-String -Pattern 'content_urls'` → 17 命中。

### D03-3 证据：客户端 `POST /api/v1/contents` → `data.id` 四份

- `client/utils/text_recorder.uts:78-98` `createTextContent(text)`
- `client/utils/voice.uts:337-374` `saveVoiceContent(text, durationMs, emotion, cosKey, filePath)`
- `client/utils/capsule_api.uts:52-75` `createTextContentRemark(text, remark)`
- `client/utils/capsule_api.uts:85-121` `createVoiceContentRemark(...)`

四者外层结构逐字相同（`post(...)` → `res == null → resolve(null)` → `dataObj(res) == null →
resolve(null)` → `resolve(d.getString('id'))`）。`capsule_api.uts:13-15` 自述
「text_recorder.uts / voice.uts 的 save 系封装不收 remark 参数…请求体逐字段复刻原封装」——
即**为加一个 remark 字段复制了整条函数**。旁证：`text_recorder.uts:85` 用裸串
`'/api/v1/contents'`，而 `voice.uts:361` 用 `PATH_CONTENTS`（`contract.uts:24`）——同一路径
两种写法。

### D03-4 证据：单条内容双通路 + 详情端点零生产调用

- 后端 `GET /api/v1/contents/{content_id}` → `contents.py:574`（2026-09-09 新增，
  自述「客户端此前用 `GET /contents?content_id=` 列表过滤替代」）。
- 客户端**至今两处仍走旧法**：`event_ops.uts:135`、`play.uts:500`，均为
  `get('/api/v1/contents?content_id=' + contentId + '&limit=1')`。
- 检索式：`Select-String -Pattern 'content_id=' -Path client` → 仅上述 2 命中；
  对 `client` 检索 `'/api/v1/contents/'` 的 GET 仅命中 `waveform` 与 `favorite`，
  **无任何客户端调用 `GET /contents/{id}`** ⇒ 详情端点在客户端侧零调用。
- 旁证声明漂移：`backend/tests/test_ab_scenarios.py:39` 仍写
  「③ 无 GET /contents/{id} 详情路由，详情走 GET /contents?content_id=」——与新端点事实相反。

### D03-5 证据：零调用导出 4 项

检索式（对 `backend,client,scripts` 递归、`*.py,*.uts,*.uvue`）：

| 符号 | 定义 | 命中数 | 命中明细 |
|---|---|---|---|
| `build_payload` | `pipeline_ext/payload.py:66` | 1 | 仅定义行本身 |
| `HOOKS_VERSION` | `pipeline_ext/__init__.py:28` | 1 | 仅定义行本身 |
| `CosPresign` | `schemas/content.py:102` | 1 | 仅定义行本身（STS 直传契约，客户端已改走分片协议） |
| `classify_batch` | `classifier.py:103` | 2 | `classifier.py:44` 为 docstring 提及 + `:103` 定义；**无任何调用者** |

四者均无调用点，故判零调用。注意 `build_payload` 的 docstring 自称
「集成用：入库与重处理共用」，但 `pipeline.py:106-114` 实际内联构造 payload 后直接调
`extend_payload`，从未调 `build_payload`。

### D03-6 证据：畸形 ID 守卫不一致（同文件 8 个端点两种口径）

`deps.py:42-52` 的 `uuid4_str` 存在意义（原文）：「非法 → NotFoundError（api 层映射 404）…
不触发 psycopg2 DataError → 500」。但 `contents.py` 内使用不一致：

| 端点 | 行 | `uuid4_str` |
|---|---|---|
| `get_content_detail` | 590（`cid = uuid4_str(...)` → 593 传 `cid`） | ✅ |
| `update_content_remark` | 610（**返回值被丢弃**，613 传原始 `content_id`） | ⚠️ 半 |
| `get_content_waveform` | 882（`cid = ...` → 883 传 `cid`） | ✅ |
| `delete_content` | 627（直接传原始 `content_id`） | ❌ |
| `favorite_add` / `favorite_remove` / `favorite_get` | 648 / 667 / 683 | ❌ |
| `trash_restore` | 768-774（自写查询，无守卫） | ❌ |

- `Content.id` 列类型为 `UUID(as_uuid=False)`（`db/models/content.py:36`，PostgreSQL UUID），
  非 UUID 串入 SQL 比较会触发 PG 侧 cast 失败。故 5 个无守卫端点对
  `DELETE /api/v1/contents/not-a-uuid` 一类请求**预期 500 而非 404**（后果未实跑，
  标 `候审`；守卫缺失本身为直接代码证据，标 `确认`）。
- 现有测试只覆盖了 GET 详情（`test_contents.py:399`）与 PATCH（`test_bb1_users_update.py:165`）
  的畸形 ID → 404；**无 DELETE/favorite/trash-restore 的畸形 ID 用例**。
- 次生不一致：L610 与 L590 的写法差异导致**大写十六进制 UUID** 在 detail 命中（归一化后查询）
  而在 PATCH 404（用原始串查询）。

### D03-7 证据：`article` 契约声明 vs 管线无处理器

检索式 `Get-ChildItem -Recurse -File -Include *.py,*.uts -Path backend,client | Select-String 'article'`：

- **声明侧**：`schemas/content.py:13`（`pattern=r"^(photo|text|voice|article)$"`）、
  `contents.py:257`（白名单元组）、`schemas/search.py:11`（过滤注释）、
  `services/notify.py:83`（`"article": "文章"`）、`client/utils/search_api.uts:97`
  （`contentTypeCn` 的 article 分支）。
- **实现侧缺失**：`pipeline.py:226-228` 只注册
  `register_content_handler("text"/"voice"/"photo")`，**无 `article`**。
  故 `_resolve_handler("article")`（`pipeline.py:231-236`）返回 `None` →
  `process_content` 跳过处理器（`pipeline.py:315-320`）→ 直接 `status="done"`：
  **无分类、无 `annotate_on_ingest`、无 `mark_sensitive_on_ingest`**，静默「成功」。
  这与域①/② 关注的「不允许阉割或占位」口径直接冲突。

### D03-8 证据：软删过滤手抄（本域 8 处 / 全仓 49 处）

检索式：`Get-ChildItem -Recurse -File -Include *.py -Path backend | Select-String 'deleted_at.is_\(None\)'`
→ **49 命中**（任务书给的 44 为较早基线；`deleted_at.is_not(None)` 另 3 命中，全在
`contents.py:733,772,796`）。本域内 8 处：`contents.py:142, 268, 285, 310, 357, 367, 411, 700`。

**两个同语义、写法各异的具体位置**：

1. `contents.py:305-314`（`create_content` 的 voice 幂等分支）：
   `select(Content).where(user_id ==, cos_key ==, deleted_at.is_(None))`
2. `services/photo_content.py:61-69`（`_query_by_cos_key`）：
   `select(Content).where(user_id ==, cos_key ==, deleted_at.is_(None))`

**为何同语义**：谓词三元素完全一致（同用户 + 同 cos_key + 未软删），返回类型一致
（`Content | None`），调用目的同为「cos_key 幂等去重」。差别只在写法
（`db.scalar(...)` vs `db.execute(...).scalar_one_or_none()`）。
同类第二组：`contents.py:280-289`（perceptual_hash 去重）vs
`photo_content.py:50-58`（`_query_by_hash`）——同语义，同样是「同用户 + 同哈希 + 未软删」。
而 `deps.py:84-102` 的 `load_alive_content` 已是「按 id 取本人未删内容」的唯一入口，
但 `trash_restore`（`contents.py:768-774`）仍自写等价查询（只把 `is_(None)` 换成 `is_not(None)`）。

### D03-9 证据：`sensitive.py` 同谓词两查

`pipeline_ext/sensitive.py:53-63` `_mention_count` 与 `:66-84` `_downgrade_existing` 的
where 子句逐字相同：`user_id == / deleted_at.is_(None) / id != exclude_content_id /
sensitive_tags.isnot(None)`；差别仅投影（前者只取 `(id, sensitive_tags)` 计数，后者取全行改字段）。
同一候选集在同一次 ingest 内被扫两遍。

### D03-10 证据：COS→临时文件两份

- `pipeline_photo.py:36-48`：`storage.get_object(cos_key)` → 写
  `tempfile.gettempdir()/f"yishu_photo_{content.id}.jpg"`
- `ai_tagging.py:171-190` `_resolve_image`：`get_storage_backend().get_object(cos_key)` → 写
  `tempfile.gettempdir()/f"yishu_aidesc_{content.id}.jpg"`

两份实现同语义（COS 取字节 → 落临时文件 → 用后 unlink）。**正常路径不会双下载**：
`pipeline_photo.py:89-91` 把 `image_path=` 显式传入，`_resolve_image` 在
`ai_tagging.py:176-177` early-return，故第二份仅在无 `image_path` 的调用路径生效
（当前无此调用者）→ 属「防御性重复实现」，清理风险低。

### D03-11 证据：`RecordSheet.uvue` 2208 行单体

`Select-String -Path client/components/RecordSheet/RecordSheet.uvue -Pattern '^<template|^</template|^<script|^</script|^<style|^\t*// =====|^\t*function '`
给出结构：`<template> 1-252`、`<script setup lang="uts"> 254-1349`、`<style> 1351-2208`。
单文件内并存至少 6 个独立职责：

1. 圆点动画引擎（L298-481：`buildDotTrack`/`buildDotPts`/`dotRender`/`startDotAnims`…）
2. 轮盘动画（L1002-1036：`startWheelAnims`/`pause`/`resume`/`stop`）
3. 录音状态机（L1039-1200：`toggleRecord`/`pauseRecordNow`/`endFromTap`/`stopRecordNow`…）
4. 三套保存链（L910 `savePhoto`、L945 `submitText`、L1266 `submitVoice` + L1325 `afterVoiceSaved`）
5. 自定义分类持久化（L707-804：`readCustomLabels`/`persistCustomLabels`/`confirmCustom`…）
6. 分类纠错登记（L975-990、L1326-1331，两处 `submitCorrection` 调用）

相邻组件 `RecordSheet/CategoryChips.uvue`（108 行）已存在 → 说明该目录已有子组件化先例，
拆分有既定范式可循。

### D03-12 证据：新增一类内容类型的注册点（全部列出）

见下文「抽象覆盖度」章节。核心事实：`pipeline.py:218-228` 的
`CONTENT_HANDLERS` + `register_content_handler` **已是正确范式**（新增处理器只需注册），
但**其余 6 个注册点仍是散落的硬编码枚举**，导致注册表的价值被抵消。

### D03-13 证据：`TRASH_RETENTION_DAYS` 落在 schema 层

定义：`schemas/content.py:114`（`TRASH_RETENTION_DAYS = 30`）；消费：
`contents.py:561`（import）、`:636`（`permanent_at = now + timedelta(days=...)`）、
`:746`（`days_left` 计算）。业务常量（保留期）挂在契约模块，且与客户端
`trash.uvue` 文案「30 天」双源（`storage.uvue:128` `ref('30 天后清除')`）——客户端未从后端取该值。

### D03-14 证据：`_to_out` 三实现（命名冲突，非真重复）

`contents.py:517`（`Content → ContentOut`）、`events.py:366`（`Event → EventOut`，
签名含 `counts`）、`messages.py:22`（`Message → MessageOut`）。三者出参类型不同，
**不是重复逻辑**，但同名私有函数跨模块共存，拆分/迁移时极易误引；建议拆分时改名
（如 `_content_out` / `_event_out`）以消歧。此处仅登记，不主张改动。

### D03-15 证据：护栏 `mask` 分支在照片路径被丢弃

- `contents.py:296-301`（`create_content`）：`reject` → 抛 422；
  **`mask` → `req.text = verdict["masked_text"]`（打码后入库）**。
- `photo_content.py:178-184`（`register_photo_content`，`moderate=True` 路径）：
  只判 `reject`，**无 `mask` 分支** → 带 `text` 的照片走 multipart 上传时，
  `mask` 判决被静默丢弃、原文入库。

两处都自称「B5b 护栏」，但同一 `moderate()` 契约（`services/external/dashscope.moderate`）
的三个 action 只在一个调用点被完整处理。影响面小（照片 `meta.text` 通常为空），
故 P2。

---

## contents.py 拆分约束专章（本域重点）

### 4 个 router 的真实行号边界

| router | 定义行 | prefix | 端点（行） | 段长 |
|---|---|---|---|---|
| `router` | **85** | `/api/v1/contents` | 169, 250, 394, 574, 597, 620, 641, 660, 676, 863 | 85-451 + 517-686 + 863-902 |
| `profile_sensitive_router` | **89** | `/api/v1/profile` | 466, 484, 501 | 89 + 454-514 |
| `trash_router` | **570** | `/api/v1/trash` | 723, 761, 783 | 570-571 + 723-807 |
| `favorites_router` | **571** | `/api/v1/favorites` | 689 | 570-571 + 689-720 |

注：`trash_router` / `favorites_router` 的定义（L570-571）位于 `router` 的端点流中间，
其后 `@router.*` 端点（L574-686）继续出现 —— **4 个 router 的代码块在物理上交错**，
这是必须整体重排的直接证据。

### 共享私有符号清单（拆分的关键约束）

模块级私有（`_` 前缀）——**被 >1 个 router 使用的**：

| 符号 | 定义行 | 被哪些 router 用（行） |
|---|---|---|
| `_to_out` | 517 | `router`（247, 272, 314, 361, 391, 447, 594, 617）、`favorites_router`（710） |
| `_attach_thumb` | 539 | `router`（447, 594） |
| `content_urls`（L568 迟导入） | 568 | `router` 经 `_attach_thumb`（544）、`favorites_router`（713） |
| `ERR_CONTENT_010`（L559 迟导入） | 559 | `router`（592, 612）、`trash_router`（776） |
| `TRASH_RETENTION_DAYS`（L561 迟导入） | 561 | `router`（636）、`trash_router`（746） |
| `ContentDeleteOut`（L562 迟导入） | 562 | `router`（632）、`trash_router`（761, 780） |
| `parse_ts`（L75 顶部导入） | 75 | `router`（425）、`favorites_router` 的 favorite 端点（656, 685） |
| `logger` | 83 | `router`（165, 889, 892, 895） |

模块级私有——**单 router 专用（可随其迁走）**：

| 符号 | 定义行 | 唯一使用者 |
|---|---|---|
| `_MAX_IMAGE_PIXELS` | 95 | `_extract_exif_datetime`（110） |
| `_extract_exif_datetime` | 98 | `router.upload_photo`（224） |
| `_validate_cos_key` | 123 | `router.create_content`（276） |
| `_resolve_size_bytes` | 150 | `router.create_content`（333） |
| `_profile_sensitive_out` | 454 | `profile_sensitive_router`（481, 514） |
| `_WAVEFORM_MAX_BYTES` | 815 | `router.get_content_waveform`（894） |
| `_wav_bucket_peaks` | 818 | `router.get_content_waveform`（898） |

其余跨 router 共享的**非私有**符号：`make_router`、`get_db`、`get_current_user`、
`ApiResponse`、`Page`、`Content`、`User`、`Session`、`select`、`ApiError`、
`datetime`/`timezone`/`timedelta`、`ContentOut`、`FavoriteOut`、`TrashItemOut`、
`TrashClearOut`、`ContentRemarkUpdate`、`ProfileSensitiveOut` 等（全部为外部导入，
拆包后由各子模块各自 import 即可，无迁移障碍）。

### ⚠️ 两条 monkeypatch 硬约束（决定哪些端点**不能**离开包命名空间）

检索式：`Get-ChildItem -Recurse -File -Include *.py -Path backend/tests | Select-String 'contents_api'`

1. `backend/tests/test_ab_scenarios.py:221` —
   `monkeypatch.setattr(contents_api, "enqueue_unique", lambda *a, **k: None)`
   （`contents_api` 由 `test_ab_scenarios.py:60` 的 `import app.api.contents as contents_api` 得来）
2. `backend/tests/test_content_upload.py:126` —
   `monkeypatch.setattr(contents_api, "MAX_PHOTO_BYTES", 100)`

`enqueue_unique` 只被 `create_content` 使用（`contents.py:381, 383`）；
`MAX_PHOTO_BYTES` 只被 `upload_photo` 使用（`contents.py:204, 207`）。
CPython 的函数全局查找走**函数定义模块的 `__dict__`** —— 与 `pipeline.py:26-39`
已登记的同类陷阱（「被 patch 的名字与其调用方必须同模块共存，否则 patch 静默失效」）
完全同源。故：

> **`upload_photo`（L169-247）与 `create_content`（L250-391）必须留在
> `app.api.contents` 这一模块命名空间内**；若包化，二者必须位于
> `api/contents/__init__.py`（且 `MAX_PHOTO_BYTES` / `enqueue_unique` 也须
> 在该命名空间可见）。否则上述两条 monkeypatch 会**静默失效**，测试症状为
> 真去连 Redis / 真读 20MB 上限。

若坚持把二者也移入子模块，则必须同步改这两条测试的 patch 目标 ——
**超出本波「严格行为等价、不改业务文件」的边界**，须先经用户拍板，故本条拆分方案
按「二者留守」设计。

### 若转 `api/contents/` 子包的最小移动方案

目标：**不新增逻辑、不删逻辑、不改任何调用点**，只做物理搬移 + 兼容再导出。

```
backend/app/api/contents/
├── __init__.py            # 留守核心（≈520 行，< 600 阈值）
│     · 顶部 import 块（原 L1-83）
│     · _MAX_IMAGE_PIXELS / _extract_exif_datetime / _validate_cos_key / _resolve_size_bytes
│     · upload_photo（L169-247）  ← monkeypatch 约束，必须留守
│     · create_content（L250-391）← monkeypatch 约束，必须留守
│     · list_contents（L394-451）
│     · get_content_detail / update_content_remark / delete_content（L574-638）
│     · 兼容再导出块（见下）
├── _routers.py            # 4 个 make_router 调用（原 L85/L89/L570/L571，≈12 行）
├── serializers.py         # _to_out（L517-536）+ _attach_thumb（L539-548），≈35 行
├── profile_sensitive.py   # _profile_sensitive_out + 3 端点（L454-514），≈62 行
├── favorites.py           # favorites_list（L689-720）+ 3 个 @router favorite 端点（L641-686），≈82 行
├── trash.py               # trash_list / restore / clear（L723-807），≈86 行
└── waveform.py            # _WAVEFORM_MAX_BYTES + _wav_bucket_peaks + 端点（L810-902），≈94 行
```

搬移后的行数核算：`__init__.py` ≈ 85（头）+ 73（4 个私有工具）+ 79（upload）+ 142（create）
+ 58（list）+ 65（detail/PATCH/DELETE）+ ≈18（再导出）≈ **520 行**（不含空行口径；
按 `@(Get-Content).Count` 实测口径亦在 520-560 区间）→ **低于 600 棘轮阈值**。
各子模块均 12-94 行，无一超限。

**兼容再导出清单（`api/contents/__init__.py` 必须 re-export，且顺序/名字不变）**：

| 再导出符号 | 外部引用点（零改动证据） |
|---|---|
| `router` | `backend/app/main.py:119` |
| `profile_sensitive_router` | `main.py:120`、`tests/test_echo.py:293` |
| `trash_router` | `main.py:121` |
| `favorites_router` | `main.py:122`、`main.py:38`（同一行 import 三者） |
| `MAX_PHOTO_BYTES` / `ALLOWED_PHOTO_EXTS` | `tests/test_content_upload.py:126`（monkeypatch，**须留在 `__init__` 命名空间**） |
| `enqueue_unique` | `tests/test_ab_scenarios.py:221`（monkeypatch，**同上**） |

（`test_ab_scenarios.py:60` 与 `test_content_upload.py:13` 的 `import app.api.contents as contents_api`
在包化后自动解析到 `__init__.py`，**测试零改动**。）

### 该文件拆到多少行才能解除棘轮

- 阈值 **600**（本波棘轮口径）。
- 现状 **902 行** → 需移出 **≥ 303 行**。
- 最小方案移出量 ≈ 382 行（`_routers` 12 + `serializers` 35 + `profile_sensitive` 62
  + `favorites` 82 + `trash` 86 + `waveform` 94）→ 留守 ≈ 520 行，**留有余量 80 行**。
- 若只移 `trash` + `favorites` + `waveform`（不动 `serializers`/`profile_sensitive`），
  留守 ≈ 640 行 → **仍超阈**，故必须连 `profile_sensitive` + `serializers` 一起移出。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？

**否，本域无反向依赖。** 证据（两条检索式，均 0 命中）：

```
Get-ChildItem -Recurse -File -Include *.py -Path backend/app/services | Select-String 'from app\.api|import app\.api'   → 0 命中
Get-ChildItem -Recurse -File -Include *.py -Path backend/app/db,backend/app/workers,backend/app/core | Select-String 'from app\.api|import app\.api'   → 0 命中
```

本域正向边实测：`api/contents.py → services/{photo_content, pipeline, upload_meta, file_magic,
external/storage, external/media_url, sync_common, errors}`；
`api/contents.py → schemas/content`、`→ db/models`、`→ core/{errors,queue}`；
`services/pipeline*.py → services/{classifier, ai_tagging, embedding, vector_store, pipeline_ext.*}`
—— **全部单向**。唯一值得记录的近似反向依赖是
`services/pipeline_ext/__init__.py` 对四个同包实现模块的**延迟 import**
（L34/45/54/65，函数内 import），这是为「pipeline.py 冻结 + 多域并行」刻意设计的，
且不跨越 api 层，判定为**合规**。另：`services/external/media_url.py` 与
`services/external/storage.py` 之间存在**互相延迟 import**（`media_url.py:164`、
`storage.py:124/275`），双方均已用函数内 import 打破环，非本域新增问题。

### 新增一类内容类型（如「文档 document」）需要改哪几处？——全部注册点

| # | 文件:行 | 内容 | 性质 |
|---|---|---|---|
| 1 | `backend/app/schemas/content.py:13` | `pattern=r"^(photo\|text\|voice\|article)$"` | 硬编码枚举 |
| 2 | `backend/app/api/contents.py:257` | `if req.content_type not in ("photo", "text", "voice", "article")` | **与 #1 重复的第二份白名单** |
| 3 | `backend/app/schemas/content.py:9` | `_STORAGE_KEY_PREFIX = r"^(photos\|voice\|thumbnails)/"` | cos_key/thumbnail_key 前缀白名单 |
| 4 | `backend/app/api/contents.py:130` | `allowed = (f"photos/{user_id}/", f"voice/{user_id}/", f"thumbnails/{user_id}/")` | **与 #3 语义重复的第三份前缀清单** |
| 5 | `backend/app/services/pipeline.py:226-228` | `register_content_handler("text"/"voice"/"photo")` | **唯一正确的注册表范式**（新增只需加一行） |
| 6 | `backend/app/services/pipeline.py:381` | `if content.content_type in ("text", "voice")`（chips 打标） | 硬编码枚举 |
| 7 | `backend/app/services/pipeline.py:378` | `if req.content_type in ("voice", "photo")`（高优队列，实为 L378） | 硬编码枚举 |
| 8 | `backend/app/services/pipeline.py:253` | `if content.content_type != "photo": return`（image_vec 分支） | 硬编码 |
| 9 | `backend/app/api/contents.py:542` | `if c.content_type == "photo" and c.thumbnail_key` | 硬编码 |
| 10 | `backend/app/api/contents.py:884` | `if row.content_type != "voice" or not row.cos_key` | 硬编码 |
| 11 | `backend/app/services/notify.py:83` | `{"article": "文章", ...}` 类型→中文标签映射 | 硬编码映射 |
| 12 | `client/utils/search_api.uts:90-101` | `contentTypeCn()` 的 if 链（photo/voice/article/默认文字） | 客户端 if 链 |
| 13 | `client/utils/search_api.uts:234` / `schemas/search.py:11` | 搜索过滤 contentTypes 契约注释 | 文档级 |
| 14 | `backend/app/services/external/media_url.py:53-64` | `_CONTENT_TYPES` 扩展名→MIME 映射 | 新增二进制类型时需扩 |

**结论**：新增一类内容类型至少需要改 **11 处代码 + 2 处契约注释**，且 #1/#2、
#3/#4、#5/#6 三对内部重复。**抽象覆盖度结论**：
`pipeline.py` 的 `CONTENT_HANDLERS` 注册表是**已建立的正确端口**（处理器维度已解耦），
但**契约校验维度、存储前缀维度、类型展示维度均无对应注册表**，
导致「注册表覆盖度 ≈ 1/4」。这解释了为什么 D03-7（`article` 有声明无处理器）能长期潜伏：
声明侧（#1/#2/#11/#12）与实现侧（#5）没有任何机制强制同步。

---

## 本域已查无问题项（避免遗漏被误读）

逐条列出「检查过但无问题」的判据：

1. **无反向依赖**：`services`/`db`/`workers`/`core` 对 `app.api` 的 import 均 0 命中（检索式见上）。
2. **软删 30 天口径本身一致**：`TRASH_RETENTION_DAYS = 30`（`schemas/content.py:114`）
   是唯一来源，`contents.py:636`（`permanent_at`）与 `:746`（`days_left`）同源引用，
   **未发现第二处硬编码 30**。（缺陷在「放置层次」与「客户端文案双源」，见 D03-13。）
3. **幂等键语义自洽**：`client_generated_id`（`contents.py:263-272, 352-361`）、
   `perceptual_hash`（`:280-289, 363-371`）、`cos_key`（`:305-314`）三条幂等路径
   均含「先查 → 冲突重查 → 幂等返回」的完整闭环，含并发 `IntegrityError` 回滚重查分支。
4. **`photo_content.register_photo_content` 的孤儿对象防护完整**：三条 commit 失败路径
   均 `best_effort_delete`（`photo_content.py:215-218, 240-243, 271-284`），
   与 `_require_photo_bytes`（`:87-89`）的魔数失败清理一致，未发现漏网路径。
5. **护栏 fail-safe 方向正确**：`create_content`（`contents.py:296-299`）与
   `register_photo_content`（`photo_content.py:178-184`）在 `reject` 时**均拒绝入库**
   （不因百炼不可用而放行）。（`mask` 分支缺失是另一问题，见 D03-15。）
6. **`load_alive_content` 已正确提升为公共入口**：`deps.py:84-102` 语义与迁移前逐字等价，
   被 7 个端点复用（detail/PATCH/DELETE/favorite×3/waveform），
   **不存在「已修但未入库」的暗物质**（本域已 `git status --short` 核实无在途文件）。
7. **游标分页口径统一**：`pagination_params`（`deps.py:30-39`）是 `limit`/`cursor` 唯一来源，
   `contents.py:397` 与 `messages.py` 同款；复合游标 `(created_at, id)` 的
   `parse_ts` 归一化（`:425`）与 `favorites`/`favorite_get` 的时间解析（`:656, 685`）同源。
8. **`pipeline.py` 的冻结边界有据可查**：`pipeline.py:26-39` 明文登记四条
   monkeypatch 同留约束与物理下限（≈440 行），本域**不建议**再动 `pipeline.py`——
   其「胖」是测试形态的硬约束，不属结构债。
9. **`pipeline_ext` 钩子协议无死签**：4 个钩子（`extend_payload` / `mark_sensitive_on_ingest` /
   `annotate_on_ingest` / `consume_emotion`）在 `pipeline.py:106/142/143/195/196` 与
   `pipeline_photo.py:97-100`、`pipeline_audio.py:158-160` **均有实际调用点**，
   `__init__.py` 的 4 个转发函数无零调用项。（零调用的是 `HOOKS_VERSION` 常量，见 D03-5。）
10. **`text_recorder.uts` 的仲裁残留已彻底清除**：L205-216 明确登记
    `submitArbitrate`/`pollArbitrate` 已删除且**不应接回**，并给出 O-2 恒混合的证据链；
    现状 `RecordSheet.uvue:978` 与 `:1328` 均为 `submitCorrection(cid, text, picked, '')`，
    与登记口径一致，未发现残留接线入口。
11. **`trash_clear` 的降级已有显式登记**（`contents.py:788-792`：MVP 只删 DB 行，
    Qdrant/COS 未挂接，挂远期账 A3）——属**已声明**的取舍，不计为隐藏缺陷。
12. **`favorites` 用 `extra["favorite_at"]` JSONB 零迁移方案**（`contents.py:649-655`）
    遵循了「dict 重建再赋值」防 JSONB in-place mutation 的既定写法（与
    `pipeline_common.patch_extra` 同源），未发现原地修改导致不落库的写法。
