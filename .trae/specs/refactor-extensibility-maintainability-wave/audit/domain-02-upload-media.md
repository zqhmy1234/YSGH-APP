# 域② · 上传与媒体 深度审计

> 审计范围：`backend/app/` + `client/` + `scripts/` + `deploy/`（`agent/` 排除）。严格只读，未改任何业务文件。
> 审计时点：2026-09-24，基线分支 `develop`，HEAD `cec3172`。
> 工具说明：本机 `Grep` 工具不可用（`rg execution error: program not found`），全部内容检索改用 PowerShell `Select-String`（等价 `rg` 语义，含行号），下文每条均给出可复现命令。

## 覆盖范围与实读清单

### 后端（逐行实读）
| 文件 | 行数 | 备注 |
|---|---|---|
| `backend/app/api/upload.py` | 243 | 全读 |
| `backend/app/api/media.py` | 132 | 全读 |
| `backend/app/api/thumbnails.py` | 45 | 全读 |
| `backend/app/api/deps.py` | 80 | 全读 |
| `backend/app/api/__init__.py` | 59 | 全读（`make_router` / `RequestIdRoute`） |
| `backend/app/api/contents.py` | 130–290 | **部分读**（L100-290：`_validate_cos_key` / `upload_photo` / `_extract_exif_datetime` / `_resolve_size_bytes` / content_type 白名单）；其余端点未读 |
| `backend/app/services/upload/__init__.py` | 37 | 全读 |
| `backend/app/services/upload/protocol.py` | 235 | 全读 |
| `backend/app/services/upload/register.py` | 217 | 全读 |
| `backend/app/services/external/storage.py` | 392 | 全读 |
| `backend/app/services/external/media_url.py` | 143 | 全读 |
| `backend/app/services/external/tencent_ci.py` | 69 | 全读 |
| `backend/app/services/thumbnails.py` | 198 | 全读 |
| `backend/app/services/upload_meta.py` | 90 | 全读 |
| `backend/app/services/file_magic.py` | 43 | 全读 |
| `backend/app/services/photo_content.py` | 1–134 + 225–287 | **部分读**（键构造/事务/入队段）；中间段未逐行 |
| `backend/app/main.py` | L38-41 / L118-142 | **仅读路由注册段** |

### 客户端（逐行实读）
| 文件 | 行数 | 备注 |
|---|---|---|
| `client/utils/uploader.uts` | 628 | 全读 |
| `client/utils/upload_pipeline.uts` | 179 | 全读 |
| `client/utils/upload_protocol.uts` | 162 | 全读 |
| `client/utils/voice.uts` | 376–493 | **部分读**（`uploadVoicePersistent` 全段）；其余录音业务未读 |
| `client/components/UploadStatusBanner/UploadStatusBanner.uvue` | L19-20 | **仅读 import 段** |
| `client/components/TabIndex/TabIndex.uvue` | L214-251 | **仅读 import 段** |

### 契约 / 部署 / 脚本
- `backend/app/schemas/content.py`（`_STORAGE_KEY_PREFIX`，L9/L26/L30）— 仅读相关行
- `backend/app/core/config.py`（L88-104 存储/媒体票据配置）— 仅读相关行
- `deploy/.env.production.template`（L71-98 存储与媒体票据 env 键）— 仅读相关行
- `backend/scripts/seed_echo_today.py:96`、`backend/scripts/backfill_thumbnails.py:34`、`scripts/api_smoke_cases.py:214`、`scripts/smoke_cos.py:38` — 仅读命中行
- `deploy/` 其余文件（docker-compose / systemd / scripts）— **目录级浏览，未逐行读**；检索确认其中无上传/媒体专用注册点

### 未覆盖部分（明确声明）
- `backend/app/services/wechat/service.py` 仅读 L194-292 命中段（`wechat/` 键构造与入库），未全读 → 该文件归域⑧/微信域，本域只登记跨域键命名空间问题。
- `backend/app/services/pipeline.py` 的 content_type 分流表未逐行读（只做了引用检索）。
- 后端测试文件（`backend/tests/test_upload.py` / `test_thumbnails.py` / `test_storage_backends.py` / `test_wechat_media.py` / `test_cleanup_job.py`）**未逐行读**，仅作为"验证手段"引用。
- `client/utils/contract.uts` 未读（只通过 `upload_protocol.uts` 的 import 推断其承载路径/字段常量）。

### 在途未提交文件标注（重要）
**结论：本域当前无在途未提交文件。** AGENTS.md 所述「第四窗（媒体票据 Valet Key）主区 8 文件在途未提交＝勿碰名单」已**全部入库**，不再处于在途状态：

```
$ git status --porcelain
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/     # 本报告目录
```

逐文件核实最近一次触碰提交（`git log -1 --format='%h %ad %s' --date=short -- <file>`）：

| 文件 | 最近提交 | 日期 |
|---|---|---|
| `backend/app/api/media.py` | `6ca8b06` | 2026-09-10 |
| `backend/app/services/external/media_url.py` | `f14f311` | 2026-09-02 |
| `backend/app/api/events.py` | `6ca8b06` | 2026-09-10 |
| `backend/app/core/config.py` | `319ccb7` | 2026-09-23 |
| `backend/app/core/errors.py` | `319ccb7` | 2026-09-23 |
| `backend/app/main.py` | `319ccb7` | 2026-09-23 |
| `backend/app/schemas/content.py` | `9f34fe3` | 2026-09-09 |
| `backend/app/services/external/storage.py` | `65f256f` | 2026-09-10 |

⇒ **后续批次无需为该「勿碰名单」避让**；本域全部改动可在 `develop` 干净工作树上安全进行。本报告本身不写入任何业务文件。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D02-1 | `client/utils/voice.uts:391-493` vs `client/utils/upload_pipeline.uts:147-189` | 重复（状态机双实现）/声明漂移 | P1 | 高（消除第 2 套 init→chunk→complete） | 中（voice 无断点续传，接 pipeline 后行为变化需真机验证） | 行为等价替换 | 真机长录音上传 + 编译门；对比 4xx 语义 | 批次 2 | 确认 |
| D02-2 | `api/media.py:51` / `schemas/content.py:9` / `api/contents.py:130` / `services/external/storage.py:406`（＋未覆盖的 `services/wechat/service.py:251`） | 注册表覆盖度（4 套前缀白名单 + 1 个命名空间漏网） | P0 | 高（前缀白名单收口为单一声明源） | 低（纯枚举收敛，逐一比对可证等价） | 新增工具（常量表）+ 行为等价替换 | `pytest backend/tests/test_wechat_media.py test_upload.py test_contents.py`；新增 wechat 键下发断言 | 批次 1 | 确认 |
| D02-3 | `services/external/storage.py:428,440-441,448,466-483` + `core/config.py:89` + `deploy/.env.production.template:84` + `api/upload.py:264` | 注册表/端口抽象覆盖度（新增存储后端需改 7 处，含 API 层硬编码 COS） | P1 | 高（换 S3/OSS 只需改 1 处注册表） | 中（单例分支收敛涉及多后端，需逐后端回归） | 新增工具 + 行为等价替换 | `pytest backend/tests/test_storage_backends.py`（三后端 + STS policy） | 批次 2 | 确认 |
| D02-4 | `services/upload/protocol.py:265-277` | 异常/事务口径（commit 失败后 staging 已删、`upload_chunks` 行仍在 → 重试 complete 触发未捕获 `KeyError` → 500；注释"可重试"不成立） | P1 | 中（消除 500 与错误注释） | 低（只动异常分支与注释） | 行为等价替换 | `pytest backend/tests/test_upload.py`；构造 commit 失败后重试 complete | 批次 2 | 确认 |
| D02-5 | `services/upload/register.py:144-148` + `:207-212` | 异常/事务口径（先删旧键后 commit → commit 失败时两副本全删＝音频永久丢失，且任务已 completed 不再重试） | P1 | 高（防不可逆数据丢失） | 中（改删键时序，需覆盖 commit 失败路径） | 行为等价替换 | `pytest backend/tests/test_upload.py`（voice 分支）；注入 commit 异常断言旧键仍可读 | 批次 2 | 确认 |
| D02-6 | `client/utils/uploader.uts:202-229`（`resolveMode` thumbnail 分支恒 `hold`） | 门禁缺口/未接线（蜂窝流量策略实际未生效：蜂窝/离线一律不自动上传任何照片） | P1 | 高（B4 §5 流量约束真正生效） | 高（需 `uni.compressImage` 接线 + 真机验证，无验证不动） | 新增工具 | 真机蜂窝/WiFi 双场景 + 编译门；`docs/待办处理排期_20260923.md:36` 已登记 | 批次 3（需真机） | 确认（已知待办） |
| D02-7 | `services/upload/protocol.py:76-79` vs `services/photo_content.py:249` | 声明漂移/一致性（同一 `photos/` 命名空间两套键布局：带 `{yyyymm}/` vs 不带） | P2 | 中（键布局单一化，`media_url` 文档注释才成立） | 低（两形态对现有校验均兼容，收敛需兼容历史键） | 行为等价替换（保留历史键读取） | `pytest backend/tests/test_thumbnails.py test_upload.py`（`derive_thumbnail_key` 对两形态） | 批次 3 | 确认 |
| D02-8 | `services/upload/register.py:143` + `backend/scripts/seed_echo_today.py:96` | 重复（媒体键构造第 4/第 5 处独立实现） | P2 | 中（键构造收口为单一 helper） | 低 | 新增工具 | 脚本执行 + 键格式断言 | 批次 3 | 确认 |
| D02-9 | `services/external/media_url.py:53-64,144` vs `services/upload_meta.py:26` vs `services/file_magic.py:15-38` | 硬编码枚举漂移（扩展名表双向不一致：`.heif` 在 `ALLOWED_PHOTO_EXTS` 但不在 `_CONTENT_TYPES`；`.gif` 反之；`file_magic` 两者都不含 GIF） | P2 | 中（.heif 原图不再以 `image/jpeg` 下发） | 低 | 行为等价替换 | `pytest backend/tests/test_thumbnails.py`；新增 `.heif` 键的 `content_type_for` 断言 | 批次 3 | 确认 |
| D02-10 | `api/contents.py:257` / `services/upload/register.py:76` vs `services/vector_store.py:37-38` | 硬编码枚举（content_type 白名单 2 处内联元组，已有常量注册表未被复用） | P2 | 中 | 低 | 新增工具（复用已有常量）+ 行为等价替换 | `pytest backend/tests/test_content_upload.py test_upload.py` | 批次 3 | 确认 |
| D02-11 | `services/upload/protocol.py:53-55`（`staging_prefix`） | 死码/零调用导出 + 注释指向错误使用者 | P2 | 低（删 3 行） | 极低 | 删码(需证死) | `Select-String staging_prefix` 全仓 1 命中（仅定义处） | 批次 3 | 确认 |
| D02-12 | `services/file_magic.py:21-24`（`FORMAT_JPEG/PNG/WEBP/HEIC`）、`services/upload/__init__.py:27-28`（`MAX_CHUNK_COUNT`/`MAX_INLINE_MERGE_BYTES` 再导出）、`client/utils/upload_protocol.uts:41`（`urlEncode` 导出）、`client/utils/uploader.uts:59,327,331`（`MAX_CONCURRENCY`/`heldPhotos`/`failedPhotos` 导出） | 零调用导出（导出面大于消费面） | P2 | 低 | 极低 | 删码(需证死) / 收窄导出 | 逐符号 `Select-String` 计数（见各条证据） | 批次 3 | 确认 |
| D02-13 | `api/upload.py:264-273`（`_cos_sts_configured`） + `services/external/tencent_ci.py:20-30` | 边界/依赖方向（API 层直读 COS 私有配置；第 2 处独立 COS 客户端构造） | P2 | 中（STS 门控下沉到后端能力探测） | 低 | 行为等价替换 | `pytest backend/tests/test_upload.py`（STS 门控分支） | 批次 3 | 确认 |
| D02-14 | `services/external/media_url.py:130-135`（`key_belongs_to_user` 无法判定时放行） | 边界（归属二次校验对历史键失效） | P2 | 低 | 中（收紧可能误杀历史键下发） | 行为等价替换 | 需人工确认历史键形态 → 先加计数日志再收紧 | 批次 4（观察后） | 候审 |

**D02-1 证据**：实读 `voice.uts:391-493`，`uploadVoicePersistent` 自行串起 `initUpload`(L412) → `putChunk`(L425) → `completeUpload`(L451)，且用**字面量**字段名 `'upload_id'`(L418)、`'file_key'`(L457)、`'content_id'`(L460)，而非 `contract.uts` 的 `FIELD_*` 常量；而 `upload_pipeline.uts:147-189` 已提供同一状态机（且额外含 `status` 断点查询、`UploadPipeError.permanent` 4xx 语义、`FIELD_*` 常量）。`upload_pipeline.uts:2-4` 与 `:11` 的模块注释自称收口对象是"uploader/voice"，**与实况不符**（voice 只收口了 `upload_protocol` 层，未接 pipeline）。复现：`Select-String -Path client\utils\*.uts -Pattern 'runUploadPipeline'` → 仅 `uploader.uts:384` 一处调用。
**差异后果**：voice 链无断点续传（无 `status` 步）、无 4xx/5xx 分类（一律 `resolve(null)` + toast，无退避重试）。

**D02-2 证据**：四套同语义白名单各写一遍——
① `api/media.py:51` `_ALLOWED_PREFIXES = ("photos/", "voice/", "thumbnails/")`；
② `schemas/content.py:9` `_STORAGE_KEY_PREFIX = r"^(photos|voice|thumbnails)/"`；
③ `api/contents.py:130` `allowed = (f"photos/{user_id}/", f"voice/{user_id}/", f"thumbnails/{user_id}/")`；
④ `services/external/storage.py:406` `for prefix in ("photos", "voice", "thumbnails")`（STS policy resource）。
`api/media.py:50` 注释自称"与 schemas/content.py 的 `_STORAGE_KEY_PREFIX` 同源"——但两者是**两处独立字面量**，非同一常量。
**漏网命名空间**：`services/wechat/service.py:251` 写入 `cos_key = f"wechat/{user_id}/{msg_id}{ext}"`，而 `wechat/` **不在上述任一白名单内**。后果（代码可推）：`GET /api/v1/media/wechat/...` 走 `media.py:124` 前缀白名单 → 401 MEDIA_001（微信来源照片的**原件**无法经 Valet Key 下发）；`POST /api/v1/contents` 带 wechat cos_key 走 `contents.py:131` → 422 CONTENT_009。缩略图因 `thumbnails.derive_thumbnail_key` 首段替换为 `thumbnails/` 故仍可下发（`thumbnails.py:52-55`）。复现：`Select-String -Path backend\app\api\media.py -Pattern '_ALLOWED_PREFIXES'`；`Select-String -Path backend\app\services\wechat\service.py -Pattern 'wechat/'`。

**D02-3 证据**：新增一个存储后端（如 S3/OSS）当前需改 **7 处**：
1. `services/external/storage.py:428` `_BACKENDS` 字典注册；
2. `storage.py:436/440-441` 单例全局（`_FAKE_INSTANCE` / `_MINIO_INSTANCE` / `_COS_INSTANCE`）；
3. `storage.py:466-483` `get_storage_backend` 内 `if key == "fake"/"minio"/"cos"` 三分支 + 末尾 `return _BACKENDS[key]()`；
4. `storage.py:448-453` `reset_storage_backend` 需同步新增单例重置；
5. `core/config.py:89` `storage_backend: Literal["fake","fs","minio","cos"]` 类型收窄；
6. `deploy/.env.production.template:84-98` env 键与注释；
7. `api/upload.py:264-273` `_cos_sts_configured()` —— **API 层直接判 `settings.tencent_*`/`cos_bucket`/`cos_region`**，而非询问后端是否支持 STS；换 S3 后此门控语义即错。
（另：`services/external/tencent_ci.py:20-30` 自建第 2 个 `CosS3Client`，与 `storage.py:304-309` 并列——CI 能力天然绑 COS，属可接受，但换后端时需一并评估。）
`get_download_url` / `get_sts_credentials` / `local_root` 三个可覆盖钩子设计良好（基类默认 `None`/`NotImplementedError`），是本域**抽象做得对**的部分。

**D02-4 证据**：`protocol.py:265-267` 先 `put_object(task.file_key, merged)` 再循环 `delete_object(_staging_key(...))`，随后 `task.status="completed"` + `db.commit()`（L269-272）；`except`（L273-277）仅 `rollback()` + `best_effort_delete(task.file_key)` + `raise`，**注释写"staging 分片已删，任务留 pending 可重试"**。但 `UploadChunk` 行未删 → `get_status`（L224-230）仍报 `missing_chunks=[]` → 重试 `complete` 通过缺片检查（L251）→ `backend.get_object(_staging_key(...))`（L260）抛 `KeyError`，而 `api/upload.py:199-210` 的 `except` 只覆盖 `NotFoundError/ConflictError/TooLargeError/ValueError` → **`KeyError` 未捕获 → 500**。⇒ 注释的"可重试"不成立，且重试路径是 500 而非可恢复错误。

**D02-5 证据**：`register.py:144` `backend.put_object(voice_key, data)` → `:146` `backend.delete_object(cos_key)`（**先删原件**）→ `:206-212` `db.add(record)` + `commit()`，失败时 `rollback()` + `best_effort_delete(voice_key)`。此时 `cos_key` 与 `voice_key` 两份副本**均已删除** → 音频不可恢复；且上游 `protocol.complete_upload` 已把 `task.status="completed"` 提交入库，客户端不会重传。对比同域**正确**写法：`photo_content.py:240-243`（commit 失败删的正是刚写入的键，无双重删除）与 `protocol.py:273-277`（同样只删最终键）。⇒ 本处是域内唯一"移动语义 + 提交后删旧键"的逆序实现。

**D02-6 证据**：`uploader.uts:206-214` `mode == 'thumbnail'` 分支直接 `return 'hold'`（并带 2026-09-23 自我更正注释说明"真实拦阻＝本文件从未接入压缩步骤"）；`uploader.uts:222-225` 蜂窝分支同样 `return 'hold'`。⇒ `auto` 模式在蜂窝/离线/未知网络下**一律不自动上传任何照片**（仅入 `held` 队列等 WiFi），B4 §5 的"蜂窝传缩略图+元数据"路径未生效。已在 `docs/待办处理排期_20260923.md:36` 登记（检索命中）。本波只登记不修（需编译门 + 真机）。

**D02-7 证据**：`protocol.py:76-79` `_final_key` → `photos/{user_id}/{now:%Y%m}/{uuid12}_{safe}`（含 `{yyyymm}/` 段）；`photo_content.py:249` → `photos/{user_id}/{uuid4hex}{ext}`（**无** `{yyyymm}/` 段）。两者都写 `photos/` 命名空间。`media_url.py:121-123` 文档注释只描述带 `{yyyymm}` 的形态。对现有校验均兼容（`key_belongs_to_user` 只看 `parts[1]`；`_validate_cos_key` 只看前缀；`derive_thumbnail_key` 只替换首段），故非功能性缺陷，属键约定漂移。

**D02-8 证据**：媒体键构造共 5 处独立实现——`protocol.py:79`（photos + yyyymm）、`register.py:143`（voice + yyyymm）、`photo_content.py:249`（photos 无 yyyymm）、`backend/scripts/seed_echo_today.py:96`（`thumbnails/{uid}/{y}{m}/{h}_tb.jpg`）、`services/wechat/service.py:251`（wechat）。无共享 helper。

**D02-9 证据**：`upload_meta.py:26` `ALLOWED_PHOTO_EXTS = {".jpg",".jpeg",".png",".webp",".heic",".heif"}`；`media_url.py:53-64` `_CONTENT_TYPES` 含 `.heic` **不含 `.heif`**，且含 `.gif`（`ALLOWED_PHOTO_EXTS` 不含）；`file_magic.py:15-38` 魔数支持 jpeg/png/webp/heic(+avif brands)，**无 gif**。`media_url.py:144` 未知扩展名回落 `image/jpeg`。⇒ 上传允许的 `.heif` 原件经 `/media/{key}` 下发时 Content-Type 为 `image/jpeg`（错）；`api/media.py:103-105` 又对非 `audio/*` 二次覆盖为 `application/octet-stream`（第 3 层判定）。

**D02-10 证据**：`api/contents.py:257` `if req.content_type not in ("photo","text","voice","article")`；`services/upload/register.py:76` `if content_type not in ("photo","voice")`（同一枚举的子集，语义还不同）；而 `services/vector_store.py:37-38` 已定义 `CONTENT_TYPE_PHOTO = "photo"` / `CONTENT_TYPE_IMAGE_LEGACY = "image"` 并被 `rag/*` 复用。⇒ 常量表存在但上传/内容域未复用。

**D02-11 证据**：`protocol.py:53-55` 定义 `staging_prefix(upload_id)`，docstring 写"reaper 用，见 `workers/cleanup_job.reap_stale_uploads`"。全仓检索：
```
Get-ChildItem -Recurse -File -Path backend,client,scripts,docs | Select-String -Pattern '\bstaging_prefix\b'
→ 仅 1 命中：backend\app\services\upload\protocol.py:53（定义行）
```
而 reaper 实际调用的是 `discard_staging_for_task`（`workers/cleanup_job.py:133,149`）。⇒ 零调用 + 注释指向错误函数。

**D02-12 证据**：逐符号全仓计数（`Select-String '\b<sym>\b'`，排除 `__pycache__`）：
- `FORMAT_JPEG/PNG/WEBP/HEIC` → 各 **2 命中**，均为 `file_magic.py` 内"定义 + `detect_photo_format` 自用"，**无任何测试/日志断言消费**（与 `file_magic.py:20` 注释"测试与日志断言用"不符）；
- `detect_photo_format` → 2 命中（定义 + `is_photo_bytes` 内部调用），无外部调用方；
- `MAX_CHUNK_COUNT` / `MAX_INLINE_MERGE_BYTES` → 各 4 命中，全部在 `upload/__init__.py`（定义处 + `__all__` 再导出）与 `protocol.py`（定义 + 内部使用），**包外零消费**；
- `urlEncode`（`upload_protocol.uts:41`）→ 客户端全部命中均在 `upload_protocol.uts` 自身（L146/147/169 内部调用），`voice.uts` 只 import 了 `UploadResp/completeUpload/fieldOf/initUpload/putChunk`（`voice.uts:28`）；
- `MAX_CONCURRENCY`（`uploader.uts:59`）→ 2 命中（定义 + `uploader.uts:466` 内部使用），无外部消费；
- `heldPhotos` / `failedPhotos`（`uploader.uts:327,331`）→ 唯一消费方是 `uploader.uts:574-575`（`continuePendingUploads` 内部），`UploadStatusBanner.uvue:20` 只 import `heldCount/failedCount/continuePendingUploads/getNetKind`。
⇒ 以上均为"导出面大于消费面"（非功能缺陷，纯清理收益）。

**D02-13 证据**：`api/upload.py:41` `from app.services.external.storage import get_storage_backend`（API 层直接用端口，可接受）；但 `:244` + `:264-273` `_cos_sts_configured()` 直接读 `settings.tencent_secret_id/secret_key/cos_bucket/cos_region/tencent_appid/tencent_sts_role_arn` —— 端口实现细节上浮到 API 层。`tencent_ci.py:20-30` 独立构造 `CosS3Client`（与 `storage.py:304-309` 并列），是第 2 处 COS 客户端构造点。

**D02-14 证据**（候审）：`media_url.py:130-135`：`key` 切分后 `len(parts) < 3` 时**直接 `return True` 放行**（只记 debug 日志）。代码注释自述为"第二道防线，不该因历史数据形态不一而误杀"。属**有意的设计取舍**，但意味着无用户段的历史键不受归属校验保护。判为候审：收紧前需先统计真实历史键形态（建议先加计数日志），故不建议在本波动手。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？
**结论：无反向依赖（services → api 方向干净）。** 证据：
```
Get-ChildItem -Recurse -File -Path backend\app\services -Include *.py | Select-String -Pattern 'from app.api|import app.api'
→ 0 命中
```
**但存在 3 条"跨层直连"边**（api → services.external，绕过 service 编排层）：
1. `api/media.py:44` + `:93` / `:134` → `get_storage_backend()` 直取后端；
2. `api/contents.py:133-135` `_validate_cos_key` → `get_storage_backend().object_exists()`；`:163` → `get_object()` 实测体积；
3. `api/upload.py:41/250` → `get_storage_backend()`；`:264-273` → 直读 COS 私有 settings（见 D02-13）。
另有一条**刻意的循环规避**：`media_url.py:163-164` 延迟导入 `storage`（注释"storage.py 会 import 本模块"），而 `storage.py:124,275` 反向延迟导入 `media_url.build_media_path` —— 两模块互引，靠函数内延迟 import 打破环。**这是本域唯一的双向耦合**，虽可用但脆弱：新增后端若在模块顶层 import `media_url` 会立即成环。

### 客户端 uploader / upload_pipeline / upload_protocol 依赖方向
**结论：主链清晰，存在 1 条旁路。**
```
uploader.uts ──→ upload_pipeline.uts ──→ upload_protocol.uts ──→ api.uts(uploadFileHttp) / contract.uts
     │                                                                          ↑
     └──────────→ pause_controller.uts / retry.uts / time.uts / sync_client.uts  │
voice.uts ────────────────────────────────────────────────────────────────────┘  （旁路：不经 pipeline）
```
- 正向链（`uploader → pipeline → protocol`）层次正确，无环。
- **旁路**：`voice.uts:28` 只 import `upload_protocol`，自行编排状态机（D02-1）→ 第 2 套编排实现。
- `sync_client.uts:598-599` 注释说明"此处静态引用 uploader 会成环，故不在此接 continuePendingUploads"——**环规避是显式记录的设计**，方向可控。
- `uploader.uts:626-628` 在模块加载时向 `sync_client.onNetworkRestored` 注册钩子，形成 `uploader → sync_client`（反向回调）——属事件式解耦，可接受。

### 新增一类存储后端 / 媒体类型需改几处？

**新增存储后端（如 S3/OSS）：7 处** — 见 D02-3 逐条列点。

**新增一类媒体类型（如 `video`）：≥8 处**
1. `api/media.py:51` `_ALLOWED_PREFIXES`（前缀白名单）；
2. `schemas/content.py:9` `_STORAGE_KEY_PREFIX`（pydantic pattern）；
3. `api/contents.py:130` `allowed`（用户前缀校验）；
4. `api/contents.py:257` content_type 白名单；
5. `services/upload/register.py:76` content_type 白名单（且需新增 `_register_*_content` 分支，`:78-82`）；
6. `services/external/storage.py:406` STS policy `for prefix in (...)`；
7. `services/external/media_url.py:53-64` `_CONTENT_TYPES`（Content-Type 映射）；
8. `services/pipeline.py` content_type 分流表（检索命中 `:209` "text/voice/photo 处理器已注册"）；
   （另：`services/thumbnails.py:108,171` 硬编码 `content_type != "photo"` 判定，`:35` `THUMBNAIL_CONTENT_TYPE = "image/jpeg"` 硬编码。）
⇒ 本域**无媒体类型注册表**，每新增一类需在 8 处散点同步，是最大的可扩展性缺口（D02-2 是其在"前缀"维度的具体化）。

---

## 本域已查无问题项（避免遗漏被误读）

| 判据 | 检查方式 | 结论 |
|---|---|---|
| `services/external/*` 是否反向依赖 `api` | `Select-String 'from app.api'` over `backend/app/services` | **0 命中**，方向干净 |
| `isoString`（`uploader.uts:113`）是否零调用导出 | 全仓含 `.uvue` 检索 | **非死码**：`TabIndex.uvue:226` 实际 import 自 `uploader`（注释"保留导出名兼容 index.uvue 引用"成立） |
| `content_urls` / `content_type_for` / `build_media_path` / `verify_media` 是否死码 | 全仓检索（17/4/5/3 命中） | **均为活码**，跨 `capsules/contents/events/rag` 多域消费 |
| `discard_staging_for_task` / `local_root` / `reset_storage_backend` / `list_photos_without_thumbnail` 是否死码 | 全仓含 `backend/tests`、`backend/scripts` 检索 | **均为活码**：分别被 `workers/cleanup_job.py:133,149`、reaper 分片目录扫描、`backend/conftest.py:22,26`、`backend/scripts/backfill_thumbnails.py:34` 消费 |
| 上传幂等是否双轨冲突 | 读 `protocol.py:101-108`（`client_upload_id` 幂等）、`register.py:121-129`（`cos_key` 幂等）、`photo_content.py:276-280`（`perceptual_hash` 409） | 三条幂等键**语义分层清晰**（任务级 / 对象级 / 内容级），非重复实现 |
| `put_object` 覆盖语义与重试幂等 | 读 `storage.py:56-57`（抽象注释"覆盖语义，幂等"）+ 各后端实现 | 四后端均为覆盖写（fs 为 `write_bytes`、fake 为字典赋值、minio/cos 为 put_object），**幂等成立** |
| 孤儿对象兜底是否一致 | 逐点核对 `best_effort_delete` 调用 | `protocol.py:276`、`register.py:211`、`photo_content.py:217,242,275,283`、`thumbnails.py:141` 均已覆盖"写对象后 commit 失败"路径（**唯一逆序例外见 D02-5**） |
| 软删口径一致性 | 读 `media.py:80-82`（`deleted_at.is_(None)`）、`deps.py:84-101`（`load_alive_content` 同口径） | `/media/audio/{cid}` 已与 `contents` 域同口径；`/media/{key}` 为票据通路（无 content 记录可查，靠 key 归属），设计如此 |
| 上传失败是否留脏 DB 行 | 读 `protocol.py:271-277`（rollback）、`register.py:207-212`（rollback）、`photo_content.py:238-243/269-284`（rollback + IntegrityError 分支） | 均有 `rollback()`，无半提交残留（`IntegrityError` 分支还带重查返回 409） |
| 限流/DoS 防护 | 读 `protocol.py:34-42`（`MAX_UPLOAD_FILE_SIZE`/`MIN_CHUNK_SIZE`/`MAX_CHUNK_COUNT` 三重防御）、`upload.py:92-105`（API 边界先行拒绝）、`storage.py:443-473`（fake 容量上限） | 已覆盖大数构造 DoS、分片越界、单片超限（`protocol.py:154-155`）、内存无界增长；`thumbnails.py:36-38` 有解压炸弹防护 |
| `deploy/` 是否有上传/媒体专用注册点 | 目录遍历 + 检索 `cos/STORAGE/MEDIA` 命中 | 仅 `.env.production.template:71-98` 一处 env 声明（已计入 D02-3）；`docker-compose.yml`/`systemd`/`scripts` **无上传媒体专用配置** |
| 客户端 held/failed 队列行格式版本兼容 | 读 `uploader.uts:234-250` | `parseEntry` 对旧 7 段行（无 MediaStore id）兼容（`id=0`），**版本化解析正确** |
