# 域09 · 微信域（企微「微信客服」+ 回调幂等 + 收/找）深度审计

> Phase A 只读筛查。本波性质＝严格行为等价，**缺陷只登记、不修**。业务代码零改动。
> 证据命令说明：本环境 Grep 工具不可用（`rg execution error: program not found`），
> 全部检索证据以 PowerShell `Get-ChildItem -Recurse -Include | Select-String` 复现，下文简写为 `Select-String "<pat>"`。

## 覆盖范围与实读清单

**实际读过（全量或指定区间）**

| 文件 | 行数 | 读法 |
|---|---|---|
| `backend/app/api/wechat.py` | 113 | 全读 |
| `backend/app/services/wechat/service.py` | 410 | 全读 |
| `backend/app/services/wechat/gateway.py` | 117 | 全读 |
| `backend/app/services/wechat/ports.py` | 70 | 全读 |
| `backend/app/services/wechat/signature.py` | 53 | 全读 |
| `backend/app/services/wechat/crypto.py` | 51 | 全读 |
| `backend/app/services/wechat/__init__.py` | 1 | 全读 |
| `backend/app/schemas/wechat.py` | 18 | 全读 |
| `backend/app/db/models/wechat.py` | 23 | 全读 |
| `backend/app/db/models/__init__.py` | 65 | 全读 |
| `backend/app/api/media.py` | 160 | 全读 |
| `backend/app/api/contents.py` | ~730 | L100-180 / L245-305（`_validate_cos_key`、`create_content`） |
| `backend/app/schemas/content.py` | 168 | 全读 |
| `backend/app/services/external/media_url.py` | 180 | 全读 |
| `backend/app/services/external/storage.py` | ~430 | L180-300 / L392-426 |
| `backend/app/services/thumbnails.py` | 198 | 全读 |
| `backend/app/services/photo_content.py` | — | L222-267 |
| `backend/app/api/upload.py` | — | L225-273 |
| `backend/app/core/config.py` | — | L1-30（model_config）/ L105-150 |
| `backend/app/core/errors.py` | — | L105-120 / L178-186 |
| `backend/app/core/ratelimit.py` | 170 | L1-170 |
| `backend/app/main.py` | — | L30-40 / L120-153 |
| `backend/app/workers/cleanup_job.py` | 240 | 全读 |
| `backend/tests/test_wechat.py` | 353 | 全读 |
| `backend/tests/test_wechat_media.py` | 147 | 全读 |
| `backend/tests/test_wechat_find.py` | 58 | 全读 |
| `backend/scripts/wecom_sandbox.py` | 158 | 全读 |
| `backend/sql/schema.sql` | — | L30-45（user_wechat_bindings）/ L390-410（wechat_messages） |
| `backend/migrations/versions/431bcaa8bd54_baseline_from_orm.py` | — | L20-30 / L800-820 |
| `scripts/check_schema_drift.py` | 372 | 全读 |
| `deploy/RUNBOOK.md` | — | L95-130 |
| `deploy/.env.production.template` | — | L150-175 |
| `docs/拿key后推进计划.md` | — | L40-45 / L140-145 |
| `docs/决策台账.md` | — | L125-135 |
| `feature_list.json` | — | L85-105（F6） |
| `忆述光华_交付文档/忆述光华_MVP方案_v3.md` | — | F6 相关段 |
| `client/**` | — | 检索确认无本域代码 |

**未读到的**：`agent/`（简报明确排除）；`client/` 无本域业务代码故无逐文件实读；`backend/app/services/rag/*` 仅经 `find_memories` 调用点间接涉及，未深读（属域⑤）。

**`git status --short`（审计时点）**
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
本域业务文件全部干净在途（无未提交改动），审计为只读，未产生业务侧 diff。

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D09-1 | `backend/app/api/wechat.py:99` + `backend/app/services/wechat/service.py:340,357` | 边界/声明漂移 | P0 | 恢复 F6 主链（微信收→内容→记忆） | 需产品决策绑定策略（绑定表未实现） | 行为等价替换 | 沙箱 send text 后查 contents 是否新增 | 3 | 确认 |
| D09-2 | `backend/app/services/wechat/service.py:251` + 白名单 `api/media.py:51`/`schemas/content.py:9`/`api/contents.py:130`/`services/external/storage.py:406` | 声明漂移（域② D02-2 复核） | P0 | 微信图片原件可下发（现恒 401） | 白名单扩容需同步 5 处，漏一处即再漂移 | 行为等价替换 | 单测：`build_media_path("wechat/...")` 走 `media.py` 端点应 200 | 1 | 确认 |
| D09-3 | `backend/app/services/wechat/service.py:365-379` + `backend/app/workers/cleanup_job.py`（全文） | 边界/软删口径 | P1 | 微信「删掉」与全局软删 30 天口径对齐 | 需迁移补列或改清理策略 | 行为等价替换 | 软删后查关联 Content.deleted_at | 3 | 确认 |
| D09-4 | `backend/app/api/wechat.py:35,72,94` vs `backend/app/services/wechat/service.py:100` | 声明漂移/魔法配置 | P1 | 生产媒体链可用（现必然 gettoken 失败） | 拆配置项需部署侧同步（与 D09-8 同批） | 行为等价替换 | 配真实企微凭证后调 `_corp_access_token()` | 3 | 确认 |
| D09-5 | `backend/app/api/wechat.py:80-100` + `backend/app/services/wechat/service.py:382-410` | 门禁缺口/未接线 | P1 | F6「找」在真实微信侧可用 | 需定义被动回复 XML 与超时预算 | 新增工具 | 回调发文本问句→断言返回 XML 而非 "success" | 3 | 确认 |
| D09-6 | `gateway.py:97,109-116` · `service.py:123-125,255,268,311,340,357` · `db/models/wechat.py:19` | 可扩展性缺口（无注册表） | P1 | 新增消息类型只改 1 处 | 8 处同步属跨文件改动，漏改静默失效 | 行为等价替换 | 新增类型后跑全链路冒烟 | 2 | 确认 |
| D09-7 | `backend/app/api/wechat.py:81` → `service.py:166-170` | 边界（async 内同步阻塞） | P1 | 消除回调互相阻塞/重推放大 | 改线程池需核对 db session 线程安全 | 行为等价替换 | 并发压测回调耗时与超时率 | 2 | 确认 |
| D09-8 | `backend/app/core/config.py:121-123` + `core/config.py:16-20` vs `docs/拿key后推进计划.md:42` | 声明漂移（命名不一致+静默丢弃） | P1 | 按文档注入 WECOM_* 也能生效 | 需确认生产实际注入名 | 行为等价替换 | 设 `WECOM_CORP_ID` 后 `_wechat_configured()` 应为 True | 2 | 确认 |
| D09-9 | `backend/app/services/wechat/service.py:63,67` + `ports.py:64` | 死码/零调用导出 | P2 | 消除误导性抽象（端口反转只落一半） | 删前需确认无外部注入计划 | 删码(需证死) | `Select-String "set_media_gateway\|VALID_SOURCES\|get_access_token"` | 2 | 确认 |
| D09-10 | `backend/sql/schema.sql:402` vs `db/models/wechat.py:22` vs `service.py:247,258,275,327,377` | 声明漂移 | P2 | 枚举/默认值单一来源 | 改 DDL 默认需迁移 | 行为等价替换 | 对比 schema.sql 与 ORM 注释枚举 | 2 | 确认 |
| D09-11 | `backend/app/services/wechat/service.py:311-312` | 死码（HTTP 路径不可达） | P2 | 消除重复防线 | 直调 service 的测试路径会变 | 行为等价替换 | 覆盖 gateway.py:97 后该分支不可达 | 2 | 确认 |
| D09-12 | `backend/app/services/wechat/service.py:302-304` vs `api/wechat.py:91-97` | 异常口径 | P2 | 缺 MsgId 不再 500/触发重推 | 改 403 会让企微放弃该条 | 行为等价替换 | 构造无 MsgId 的合法验签回调 | 2 | 确认 |
| D09-13 | `backend/app/core/ratelimit.py:41-46` | 门禁缺口 | P2 | 匿名回调端点有配额保护 | 需定 wechat 域配额值 | 新增工具 | 超频调 /callback 应 429 | 2 | 确认 |
| D09-14 | `backend/app/services/external/media_url.py:53-64,138-144`（交叉引用域② D02-9） | 声明漂移 | P2 | 微信语音 Content-Type 正确 | 与域②同源，勿重复改 | 行为等价替换 | `content_type_for("x.amr")` 应为 audio/* | 2 | 确认 |
| D09-15 | `deploy/RUNBOOK.md:108` + `deploy/.env.production.template:158-161` | 门禁缺口（部署文档） | P2 | 拿 key 后零排查完成接线 | 文档改动无代码风险 | 新增工具 | 全 `deploy/` 检索 wechat/企微 命中数 | 2 | 确认 |

**D09-1 证据**：`api/wechat.py:99` 为 `process_incoming(db, msg)`——**未传 `user_id`**；`service.py:340` `if user_id and msg["msg_type"] == "text" ...`、`service.py:357` `elif user_id and msg["msg_type"] in ("image","voice")`——两个建内容分支都以 `user_id` 真值为前提。`Select-String "process_incoming"`（backend 全量）命中调用点仅 `api/wechat.py:99`（无 user_id）与 `tests/test_wechat_media.py:55,81,93,112,127,140,141` + `tests/test_wechat.py:221,223,249`（全部显式传 user_id）→ 真实回调只走 `service.py:319-336` 的 `pg_insert(... status="processed")`，**不下载媒体、不建 Content、不入管线、不产记忆**。另：`Select-String "from_user|unionid|FromUserName|UnionId"` 于 `backend/app` 命中本域仅 `gateway.py:104`（`"from_user": _txt("FromUserName")`，解析后**无任何消费方**）；`Select-String "user_wechat_bindings|UserWechatBinding"` 全仓命中仅 `sql/schema.sql:37`、`migrations/.../431bcaa8bd54:27,806,812-814`、`tests/conftest.py:171`（清理）、`tests/test_auth_db.py:9`（注释）→ **无 ORM 模型、零业务引用**，F6 承诺的「UnionID 绑定」完全未实现。故 US-31/32/33 归因「仅卡凭证」不准确。

**D09-2 证据**（域② D02-2 独立复核）：`service.py:251` `cos_key = f"wechat/{user_id}/{msg.get('msg_id')}{_media_extension(msg['msg_type'])}"`（`tests/test_wechat_media.py:68,84` 断言该形态）。活链路：`api/events.py:210`（亦 `:306`）`content_urls(r.cos_key, r.thumbnail_key, user_id)` → `media_url.py:170-174` `backend.get_download_url` → `storage.py:268-277` `build_media_path` → `/api/v1/media/wechat/...` → `api/media.py:124` `if not key or not key.startswith(("photos/","voice/","thumbnails/"))` → **401 MEDIA_001**。缩略图不受影响（`thumbnails.py:52-55` 首段替换为 `thumbnails/`，在白名单内）。422 半为**潜伏**：`schemas/content.py:26` 的 pydantic `pattern=_STORAGE_KEY_PREFIX` 会先于 `contents.py:131` 拒绝 `wechat/` 键，但当前**无调用方把 wechat 键送进 `create_content`**（微信链路 `service.py:265-274` 直建 ORM Content），故非活缺陷。白名单共 **5 处独立字面量**：`api/media.py:51`、`schemas/content.py:9`、`api/contents.py:130`、`services/external/storage.py:406`、`services/wechat/service.py:251`。**复核结论＝确认**。

**D09-3 证据**：`soft_delete_by_msg`（`service.py:365-379`）仅 `record.status = "deleted"`，**不触碰关联 Content**（反向关联靠 `Content.extra.wechat_msg_id`，见 `service.py:208-213`）；`wechat_messages` 无 `deleted_at` 列（`models/wechat.py:16-23` / `schema.sql:395-404`）；`workers/cleanup_job.py` 240 行全文只处理 `deleted_logs` + `contents`，`Select-String "wechat"` 于该文件 0 命中 → 微信消息与内容均无 30 天清理路径。

**D09-4 证据**：`api/wechat.py:35` 判定 `settings.wechat_token`、`:72` 作 `verify_url` 的 token、`:94` 作 `handle_message` 的 token；而 `service.py:100` `params={"corpid": ..., "corpsecret": settings.wechat_token}` 把**同一值**当企微应用 Secret。企微协议中「回调 Token」是自定随机串、「应用 Secret」由企微生成，二者不同源。`deploy/.env.production.template:160` 注释显式承认 `# WECHAT_TOKEN=            # 兼作应用 Secret（corpsecret）` → 生产必然 `gettoken` 返回 errcode 40001 → `_corp_access_token()` 抛 RuntimeError → 媒体链永久降级为 mock（`service.py:159-161`）。

**D09-5 证据**：`Select-String "find_memories"` 命中 `service.py:382`（定义）、`api/wechat.py:29`（import）、`api/wechat.py:57`（唯一调用，在需 Bearer 的 `POST /find` 内）。`POST /callback`（`api/wechat.py:80-100`）只做 `process_incoming` 后 `return Response("success")`——不把文本当查询、不组装被动回复 XML。`service.py:386` docstring 自认「真实回调接入后由回调处理器调用并组装被动回复 XML」＝未实现。

**D09-6 证据**：新增一类消息（如 link/video/file）需同步改：①`gateway.py:97` 类型白名单 ②`gateway.py:109-116` 字段抽取 if/elif 链 ③`service.py:311` 二次类型白名单 ④`service.py:340` text 分支 ⑤`service.py:357` 媒体分支 ⑥`service.py:123-125` `_media_extension` 扩展名映射 ⑦`service.py:255` 图片审核判定 ⑧`service.py:268` `content_type` 三元映射 ⑨`db/models/wechat.py:19` 注释枚举。`Select-String "MESSAGE_TYPES|MSG_TYPE_REGISTRY"` 0 命中 → 无注册表机制，任一处漏改＝静默失效。

**D09-7 证据**：`api/wechat.py:81` 为 `async def wechat_callback`，函数体 `:99` 同步调用 `process_incoming` → `_process_media` → `service.py:166-170` `httpx.get(f"{WECOM_API_BASE}/media/get", ..., timeout=30)`（同步阻塞最长 30s）。`deploy/RUNBOOK.md:127` 铁律 `uvicorn --workers 1` → 单事件循环被阻塞期间所有并发回调排队 → 企微 5s 超时重推（重推被 `msg_id` 幂等吸收为 duplicate，但阻塞被放大）。

**D09-8 证据**：`config.py:121-123` 三个字段为普通 `str = ""`，**无 `AliasChoices`**；对比同文件 `:105-115`（baidu/amap）均用 `AliasChoices` 兼容多名。`config.py:16-20` `SettingsConfigDict(..., extra="ignore")` → 未识别环境变量静默丢弃。`docs/拿key后推进计划.md:42` 推荐 `WECOM_CORP_ID / WECOM_TOKEN / WECOM_ENCODING_AES_KEY（或 WECHAT_CORP_ID 等）`。若按文档注入 `WECOM_*`，`_wechat_configured()`（`api/wechat.py:34-35`）为 False → 全部回调 503 WECHAT_099，且无任何告警。

**D09-9 证据**：`Select-String "set_media_gateway"`（backend+client+scripts+deploy）命中 3 处**全在 service.py**：`:38`/`:55`（注释）、`:63`（定义）→ 零调用；`Select-String "get_access_token"` 命中 `ports.py:64`（Protocol 声明）、`service.py:47`（实现）→ 业务零调用（`download_media` 内直接调 `_corp_access_token()`，`service.py:159`）。另 `service.py:67` `VALID_SOURCES = ("active","echo","org")` 全仓仅此一处定义、零引用（死常量；且其值与 `schemas/content.py:34` 的 `source` pattern `^(app|windows|wechat|import)$` 语义冲突）。⇒ 端口反转（R1#9）只落了一半：端口已建但注入点从未被使用。

**D09-10 证据**：`schema.sql:402` `status text NOT NULL DEFAULT 'processing'  -- processed / failed`；`models/wechat.py:22` `default="processed")  # processed / failed / deleted`；实际写入值 `"processed"`（`service.py:275,327`）、`"media_failed"`（`:247`）、`"sensitive"`（`:258`）、`"deleted"`（`:377`）。三方声明互不一致，且 DDL 默认值与 ORM 默认值不同（ORM 写入走 Python 侧默认，DDL 默认实际不生效）。

**D09-11 证据**：`service.py:311-312` `if msg.get("msg_type") not in ("text","image","voice"): return {"status":"ignored"}`。上游 `gateway.py:97` 已把非三类返回 `None`，`api/wechat.py:98` `if msg is not None:` 才调 `process_incoming` → 该分支在 HTTP 路径**不可达**（仅直调 service 的测试可达）。

**D09-12 证据**：`service.py:302-304` `if not msg_id: raise ValueError("消息缺少 msg_id")`；`api/wechat.py:91-97` 的 `try/except ValueError` **只包 `handle_message`**，`:99` 的 `process_incoming` 在 try 之外 → 验签通过但缺 MsgId 的回调抛 ValueError 逃逸为 FastAPI 500（非 `ERR_WECHAT_002` 403），企微将持续重推同一包。

**D09-13 证据**：`ratelimit.py:41-46` `_DOMAIN_SCOPES` 仅登记 `/api/v1/auth`、`/api/v1/asr`、`/api/v1/guard`、`/api/v1/search`；`Select-String "wechat"` 于 `ratelimit.py` 0 命中 → `/api/v1/wechat/callback`（匿名可达，含 XML 解析 + AES 解密 + 媒体下载）与 `/api/v1/wechat/find`（Bearer，触发 RAG 成本）均无独立配额。

**D09-14 证据**：`media_url.py:53-64` `_CONTENT_TYPES` 无 `.amr`；`:138-144` `content_type_for` 未知扩展名回落 `"image/jpeg"`；微信语音键后缀为 `.amr`（`service.py:125`，`tests/test_wechat_media.py:84` 断言）。与域② D02-9 同源，此处仅补微信侧触发面（`api/media.py:103-105` 的 audio 端点对非 `audio/*` 会回落 `application/octet-stream`，但票据端点 `/{key:path}` 走 `content_type_for` 直出）。

**D09-15 证据**：全 `deploy/` 检索 `wechat|WECHAT|企微|微信` 仅命中 `RUNBOOK.md:108`（表格一行「短信 / uni-push / 企微 / 微信开放平台 | 到货后 | 后端已有 501 门控，不阻塞内测」）、`.env.production.template:158-164`（4 行注释配置）、`ONE_SWAP.md:141`（登录相关，非本域）→ 无回调 URL 配置步骤、无 EncodingAESKey 生成/校验方法、无「回调 Token ≠ 应用 Secret」提示（与 D09-4 同源，接线时必踩）。

## 依赖方向与抽象覆盖度结论

**FF1：本域是否存在反向依赖？→ 否。** 逐条实际 import 边：

- `api/wechat.py`（L14-29）→ `app.api`、`app.api.deps`、`app.core.config`、`app.core.errors`、`app.db.models`、`app.db.session`、`app.schemas.common`、`app.schemas.wechat`、`app.services.wechat.gateway`、`app.services.wechat.service`
- `services/wechat/service.py`（L29-32）→ `app.db.models`、`app.services.thumbnails`、`app.services.external.content_safety`、`app.services.wechat.ports`；函数内延迟 import：`app.core.config`（L86）、`app.services.external.storage`（L235,280）、`app.services.photo_content`（L236）、`app.services.pipeline`（L237）、`app.schemas.search` + `app.services.rag`（L390-391）
- `services/wechat/gateway.py`（L21-23）→ `app.services.wechat.crypto`、`app.services.wechat.ports`、`app.services.wechat.signature`
- `main.py:36,136` → `app.api.wechat`（组合根单向引入，正常）

**无** `services/* → api/*` 反向边，**无** `core/* → services/*` 反向边。多处函数内延迟 import 系规避循环依赖（storage ↔ media_url 已知互引），非违规。

**端口抽象覆盖度（本域重点判据 1）：存在漏网点。**

- 已反转：验签（`ports.SignaturePort` → `signature.py`）、加解密（`ports.CryptoPort` → `crypto.py`）、媒体下载（`ports.MediaGatewayPort` → `_DefaultMediaGateway`）。
- **漏网 1（直连 httpx 于业务模块）**：`service.py:98-102`（gettoken）与 `service.py:166-170`（media/get）在 `services/wechat/service.py` 内直接 `import httpx` + 硬编码 `WECOM_API_BASE`（`:70`）。`ports.py:16-17` 纪律明写「wechat 域业务代码不得直接依赖 httpx/企微 URL/具体加解密算法实现」——**本文件自身违反该纪律**（`_DefaultMediaGateway` 只是把调用转回同模块函数，未做实现隔离）。
- **漏网 2（access_token 未走端口）**：`MediaGatewayPort.get_access_token`（`ports.py:64`）已定义但**零调用**（D09-9），token 获取被 `download_media` 内部私有调用，端口契约形同虚设。
- **漏网 3（注入点未使用）**：`set_media_gateway`（`service.py:63`）零调用 → 无任何测试或生产代码替换过媒体网关，端口可替换性未被验证。

**新增一类消息类型需改几处？→ 9 处**（详见 D09-6：`gateway.py:97`、`gateway.py:109-116`、`service.py:311`、`service.py:340`、`service.py:357`、`service.py:123-125`、`service.py:255`、`service.py:268`、`db/models/wechat.py:19`）。

**新增一个渠道（如公众号/小程序客服）需改几处？→ 至少 6 处**：`core/config.py:121-123`（凭证字段）、`services/wechat/ports.py`（若需新端口）、`services/wechat/service.py:67`（渠道/来源常量，现为死码）、`schemas/content.py:34`（`source` pattern 硬编码枚举）、`core/errors.py:109-113`（域码）、`core/ratelimit.py:41-46`（限流域登记）。另 `main.py:36,136` 为组合根注册点。

## 本域已查无问题项（避免遗漏被误读）

1. **加解密/签名无重复实现**：仅 `crypto.py`、`signature.py` 各一份；`scripts/wecom_sandbox.py:26-27` 与 `tests/test_wechat*.py` 均 **import 同一实现**（非拷贝），无第二套算法。
2. **回调幂等设计正确**：`msg_id` UNIQUE（`models/wechat.py:17` / `schema.sql:397`）+ `pg_insert(...).on_conflict_do_nothing(index_elements=[WechatMessage.msg_id])`（`service.py:319-330`）+ `rowcount == 0 → rollback + duplicate`（`:331-333`）；并发同 msg_id 双请求不再 500（`tests/test_wechat.py:234-249` 覆盖）。**重放同一回调不会产生重复记忆/重复回复**（前提：D09-1 修复后亦然，因建内容在幂等插入之后）。
3. **验签 fail-closed**：`signature.is_timestamp_fresh` 越窗即拒（`signature.py`），`gateway.py:54-55,79-80` 签名不匹配抛 ValueError → `api/wechat.py:76,97` 统一 403。
4. **receiveid 校验齐备**：`gateway.py:57-58`（URL 验证）与 `:82-83`（收包）均比对 `receive_id != corpid` → 拒。
5. **XML 解析 DoS 防护**：`defusedxml`（`gateway.py:19`）+ `_MAX_XML_BYTES = 100_000` 早拒（`:42-43`）+ `ET.ParseError → ValueError`（`:46-47`），畸形 body 不再 500。
6. **媒体下载失败不丢消息**：`service.py:245-249` 捕获后标 `media_failed` + 告警，不向上抛（`tests/test_wechat_media.py:102-117` 覆盖）。
7. **审核 fail-safe**：`_audit_image`（`:201-205`）、`_check_text_safe`（`:221-225`）异常一律 `{"pass": True}` + 告警，符合「微信收消息可靠性优先」。
8. **写对象后 commit 失败兜底**：`service.py:276-284` `db.rollback()` + `best_effort_delete(cos_key)` 防孤儿对象（P0-6 范式已落地）。
9. **鉴权与归属校验**：`/find`、`/delete` 均 `Depends(get_current_user)`；`soft_delete_by_msg` 校验 `record.user_id`（`service.py:375-376`），他人消息按不存在处理（`api/wechat.py:110-112` → 404）。
10. **未配置时全拒**：`_require_configured()`（`api/wechat.py:38-40`）→ 503 WECHAT_099，不静默降级。
11. **无反向依赖**：见 FF1。
12. **无客户端本域代码**：`client/` 检索 `wechat|微信` 仅命中 `static/icons/share-wechat.svg`、`pages/*/security.uvue` 的 `onWechat` 入口、`privacy.uvue` 政策文案、`utils/contract.uts:18 PATH_AUTH_WECHAT = '/api/v1/auth/wechat'`（属域①认证，非本域）。与任务描述「客户端：无」一致。
13. **内容安全适配器统一**：文本/图片审核均经 `external.content_safety.get_content_safety()`（`service.py:31,202,222`），无第二套审核实现。
14. **错误码登记齐全**：`errors.py:110-113` 四条 WECHAT_001/002/003/099（099 `retryable=True`），与 `api/wechat.py` 使用一致。
