# 域① · 认证与设备 深度审计

> 波次：易扩展/易维护重构波 · Phase A（严格行为等价，**只登记不修**）
> 审计日期：2026-09-24 ｜ 审计范围：`backend/app/` + `client/`（`agent/` 排除）
> 本域对应路由前缀：`/api/v1/auth/*` + `/api/v1/users/me`（受保护资源样例）

## 覆盖范围与实读清单

**后端（逐文件实读，行数＝Read 全文）**

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/app/api/auth.py` | 102 | 协议层 6 端点 + `_client_ip` |
| `backend/app/services/auth/auth.py` | 424 | 服务层（登录编排/令牌/轮换/吊销） |
| `backend/app/services/auth/providers.py` | 395 | 渠道策略 + SmsSender 端口 + OTP 校验 |
| `backend/app/services/auth/otp_store.py` | 196 | 计数后端（Redis/Memory） |
| `backend/app/services/auth/__init__.py` | 58 | 包导出面（`__all__`） |
| `backend/app/core/security.py` | 55 | JWT 签发/解码 |
| `backend/app/core/ratelimit.py` | 237 | 通用限流中间件 + 存储抽象 |
| `backend/app/api/deps.py` | 102 | `get_current_user` + `uuid4_str` + `load_alive_content` + 分页 |
| `backend/app/db/models/auth.py` | 68 | User / Device / SmsCode |
| `backend/app/schemas/auth.py` | 76 | 认证契约 |
| `backend/app/core/errors.py` | 253 | 错误码登记表 + 常量 + handler |
| `backend/app/api/__init__.py` | 57 | `make_router` / `RequestIdRoute` |
| `backend/app/api/users.py` | 37 | `GET /users/me`（受保护资源样例） |
| `backend/app/main.py`（片段） | 20/38-45/105-142 | 路由与中间件接线 |

**客户端（逐文件实读）**

| 文件 | 行数 | 说明 |
|---|---|---|
| `client/utils/auth.uts` | 284 | 设备码登录/ensureLogin/refresh/logout/凭据存取 |
| `client/utils/device_id.uts` | 111 | 设备标识四级取值链（唯一入口） |
| `client/utils/api.uts` | 546 | 统一请求层（401 自愈 + header 注入 4 处） |
| `client/utils/contract.uts` | 40（实读 L1-L40） | 端点路径常量（含 4 个 `PATH_AUTH_*`） |
| `client/utils/play.uts` | 570-610（片段） | `fetchUsersMe` / `patchJson`（header 注入 2 处） |
| `client/pages/account/security.uvue` | 286 | 账号与安全页（退出/注销/绑定态） |

**辅助取证（Select-String 全仓检索，非全文实读）**：`get_current_user`（92 命中）、`decode_token`、`create_access_token/create_refresh_token`、`ERR_AUTH_099/002/006`、`services.auth` 导入边、`PATH_AUTH_*`/`is_new_user`/`ensureLogin`（客户端）、`'Bearer '` 注入点。
**测试侧（仅检索，未逐一实读）**：`tests/test_auth.py`、`test_auth_db.py`、`test_auth_device.py`、`test_auth_g1.py`、`test_ratelimit.py`、`test_otp_store_redis.py`、`conftest.py:276-280`。
**未覆盖**：`client/utils/audio_player.uts`、`upload_pipeline.uts`、`upload_protocol.uts`、`sync_client.uts`、`event_sync.uts`、`log.uts` 仅按行号取证（token 注入点），未通读；`backend/app/api/wechat.py` 的鉴权用法仅检索未实读；`deploy/` 与 `scripts/` 未发现认证域代码（未深挖）。

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D01-1 | `backend/app/services/auth/auth.py:300-309` / `:368-377` | 重复 | P1 | 中 | 低 | 纯移动+兼容再导出 | 单测 `test_auth_db.py` / `test_auth_device.py` 全绿 | 批2 | 确认 |
| D01-2 | `backend/app/services/auth/auth.py:54-58` / `:61-71` / `:74-81` | 重复（复制粘贴扩展范式） | P1 | 高 | 低 | 行为等价替换 | 三渠道登录端点冒烟（`/auth/wechat|device|phone`） | 批1 | 确认 |
| D01-3 | `backend/app/services/auth/providers.py:242-244` / `:256-258` | 重复 | P2 | 低 | 低 | 行为等价替换 | `test_auth.py` 手机渠道用例 | 批3 | 确认 |
| D01-4 | `backend/app/services/auth/otp_store.py:61-68` / `backend/app/core/ratelimit.py:97-104` | 重复（滑窗双实现） | P1 | 中 | 中 | 行为等价替换 | `test_ratelimit.py` + `test_otp_store_redis.py` | 批2 | 确认 |
| D01-5 | `backend/app/services/auth/auth.py:398-400` / `backend/app/services/auth/providers.py:159-167` | 重复 | P2 | 低 | 低 | 行为等价替换 | `test_auth_g1.py:95` | 批3 | 确认 |
| D01-6 | `backend/app/api/auth.py:99-101` vs `backend/app/core/ratelimit.py:153-159` | 边界/口径不一致 | P1 | 高 | 中 | 行为等价替换 | 反代场景手工验证 + `test_ratelimit.py` | 批1 | 确认 |
| D01-7 | `backend/app/services/auth/auth.py:155` / `:201` vs `backend/app/api/deps.py:66` | 异常口径不一致 | P1 | 中 | 低 | 行为等价替换 | `test_auth_db.py` 非法 token 用例 | 批2 | 确认 |
| D01-8 | `client/utils/contract.uts:18,22,23` vs `client/utils/auth.uts:221,269` | 声明漂移/死码 | P1 | 中 | 低 | 行为等价替换 | 客户端编译 + 401/登出链路真机复验 | 批1 | 确认 |
| D01-9 | `client/utils/api.uts:57,322,400,450` + `play.uts:542,605` + `audio_player.uts:317` + `upload_pipeline.uts:88` + `upload_protocol.uts:74` | 重复（header 注入 9 处） | P1 | 中 | 中 | 行为等价替换 | 客户端全量编译 + 上传/音频/列表链路复验 | 批2 | 确认 |
| D01-10 | `backend/app/schemas/auth.py:55-56` vs `backend/app/core/config.py:44-45` | 声明漂移（硬编码魔法数） | P1 | 中 | 低 | 行为等价替换 | `test_auth.py` 响应体断言 | 批1 | 确认 |
| D01-11 | `backend/app/services/auth/__init__.py:8,42` | 死码（零调用导出） | P2 | 低 | 低 | 删码(需证死) | `test_auth_db.py`（不导入该名） | 批3 | 确认 |
| D01-12 | `backend/app/core/errors.py:50,136` | 死码（零 raise） | P2 | 低 | 低 | 删码(需证死) | 全仓检索 `ERR_AUTH_099` 无 raise | 批3 | 确认 |
| D01-13 | `backend/app/api/auth.py:86,92` + `services/auth/auth.py:152,164,192` | 声明漂移（AUTH_006 幽灵码） | P2 | 低 | 低 | 行为等价替换 | `test_techdebt_p0` 码表校验 | 批3 | 确认 |
| D01-14 | `backend/app/schemas/auth.py:64` | 死字段 | P2 | 低 | 低 | 行为等价替换 | 客户端检索 `is_new_user` 零命中 | 批3 | 确认 |
| D01-15 | `backend/app/services/auth/providers.py:387-395` | 可扩展性（if 链分发） | P2 | 中 | 低 | 行为等价替换 | `test_auth.py` / `test_auth_device.py` | 批3 | 确认 |
| D01-16 | `backend/app/services/auth/providers.py:244,258` → `auth.py:282` | 语义漂移（platform 列混用） | P2 | 中 | 中 | 行为等价替换 | DB 抽查 `devices.platform` 取值 | 批3 | 候审 |
| D01-17 | `backend/app/services/auth/providers.py:244,258` | 魔法字符串 | P2 | 低 | 中 | 行为等价替换 | 多设备同号登录用例（当前不存在） | 批3 | 候审 |
| D01-18 | `backend/app/api/auth.py:36-38` | 边界（api 层 re-export 服务私有） | P2 | 低 | 低 | 纯移动+兼容再导出 | `test_auth_db.py:242` 导入路径不变 | 批3 | 确认 |
| D01-19 | `client/utils/play.uts:585` → `client/pages/account/security.uvue:74` | 边界（域归属错误） | P2 | 低 | 低 | 纯移动+兼容再导出 | 客户端编译 + 账号页手机号显示复验 | 批3 | 确认 |
| D01-20 | `backend/app/services/auth/otp_store.py:193` | 死码（仅测试消费） | P2 | 低 | 低 | 删码(需证死) | 检索仅 `test_otp_store_redis.py:108` | 批3 | 确认 |
| D01-21 | `backend/app/schemas/auth.py:21` + `providers.py:372` | 可扩展性（无枚举校验） | P2 | 低 | 低 | 行为等价替换 | `test_auth_device.py` 非法 platform 用例 | 批3 | 候审 |

### D01-1 证据：TokenPair 构造块在服务层重复两次
实读 `auth.py`：`_issue_tokens` L300-309 与 `_rotate_refresh_token` L368-377 是**逐字相同**的 10 行 `TokenPair(access_token=access, refresh_token=refresh, user=UserBrief(id=..., nickname=..., avatar=..., is_new_user=False))`，且 `is_new_user=False` 在两处都是硬编码。两条路径唯一的差别只是"写 devices 行的 SQL 形态"（`_store_refresh_token` vs 条件 UPDATE）。故"响应组装"与"会话写入"两个关注点被绑死在一起，任一处改响应形状必漏改另一处。

### D01-2 证据：三个渠道服务函数近乎同构（复制粘贴扩展范式）
`auth.py:54-58`（wechat）、`61-71`（device）、`74-81`（phone）三函数体均为 `provider = get_login_provider("<channel>"); identity = provider.resolve_identity(db, req, client_ip=...); return _finish_login(db, identity)`。`device_login` 的 docstring 自认"与 wechat_login 完全同构"（L66）。差别仅：渠道字面量 + 是否透传 `client_ip`。新增渠道 = 再抄一份，属"复制粘贴式新增范式"。

### D01-3 证据：phone 与 sms-mock 两 Provider 的 `resolve_identity` 同语义
`providers.py:242-244` 与 `256-258` 完全相同：`phone = _verify_sms_code(db, req, client_ip); return AuthIdentity(phone=phone, device_id="phone-login", platform="phone")`。L248-251 注释明示"共享 `_verify_sms_code`（同库校验，行为等价）；独立命名以表达渠道身份"——即**已知的、有意图的重复**，故降为 P2。

### D01-4 证据：滑动窗口限流算法双实现
`otp_store.py:61-68`（`MemoryOtpStore.rate_allow`）与 `ratelimit.py:97-104`（`MemoryRateLimitStore.allow`）是同一算法：`now = time.monotonic()` → 持锁 → `while bucket and now - bucket[0] >= window: popleft()` → `append(now)` → `return len(bucket) <= limit`。两文件各自维护 `defaultdict(deque)` + `threading.Lock`。Redis 侧反而不同（otp 用 ZSET 滑窗、ratelimit 用 INCR 固定窗口）——**同一"限流"语义在内存侧重复、在 Redis 侧分歧**，是"降级后口径与生产不一致"的结构温床。

### D01-5 证据：SHA-256 裸哈希双实现
`auth.py:398-400` `_sha256_legacy(token)` = `hashlib.sha256(token.encode("utf-8")).hexdigest()`；`providers.py:159-167` `_hash_code(code, salt=None)` 的 `salt` 为空分支 = `hashlib.sha256(code.encode("utf-8")).hexdigest()`。同语义（无盐 SHA-256 hex），分处两模块，`test_auth_g1.py:95` 直接断言后者等于 `hashlib.sha256(...)`。

### D01-6 证据：登录侧取 IP 与中间件取 IP 口径不一致
`api/auth.py:99-101`：`return request.client.host if request.client else ""`（**不读** `X-Forwarded-For`、**不读** 白名单）。
`core/ratelimit.py:153-159`：`if settings.rate_limit_trust_proxy: xff = request.headers.get("X-Forwarded-For"); if xff: return xff.split(",")[0].strip()`。
`config.py:206` `rate_limit_trust_proxy: bool = False`、`config.py:207` `rate_limit_whitelist`。后果：反代后开启 `RATE_LIMIT_TRUST_PROXY=true` 时，中间件按真实客户端 IP 计数，而 `/auth/sms/send`、`/auth/phone` 的**业务层**限流键（`sms:ip:*` / `login:ip:*`）全部塌缩到反代 IP 一个桶 → 全体用户共享配额，一个恶意客户端即可打满 `_SMS_SEND_IP_LIMIT=30`/`_LOGIN_IP_LIMIT=60` 把所有人 429。另：`api/auth.py` 的 `_client_ip` 也不认 `rate_limit_whitelist`（中间件会直通，业务层仍计数），白名单语义在两层不统一。

### D01-7 证据：同一"坏 token"异常在三处三种捕获宽度
- `services/auth/auth.py:154-156` `refresh()`：`except Exception:` → 401 AUTH_005
- `services/auth/auth.py:199-202` `logout()`：`except Exception:` → 静默 ok
- `api/deps.py:65-68` `get_current_user()`：`except jwt.PyJWTError:`（有注释"修复（审查 MINOR）：收窄为 PyJWTError，不吞其他异常"）
- `core/ratelimit.py:167-170` `_bearer_user_id()`：`except Exception:`
即 `deps` 已按审查意见收窄，`services/auth` 与 `ratelimit` 仍是宽 `Exception`，`decode_token` 之外的意外异常（如 settings 缺字段、算法名非法）会被伪装成"token 无效"或被静默吞掉，排障时无日志。

### D01-8 证据：客户端契约常量零消费 + 登录封装绕过常量硬编码
`contract.uts` 定义了 4 个认证路径常量（L18/21/22/23），其中 `PATH_AUTH_WECHAT`、`PATH_AUTH_REFRESH`、`PATH_AUTH_LOGOUT` **零消费**：全客户端检索 `PATH_AUTH_` 仅命中 `contract.uts` 自身定义 + `auth.uts:24`（import `PATH_AUTH_DEVICE`）+ `auth.uts:94`（使用 `PATH_AUTH_DEVICE`）。而 `auth.uts:221` 硬编码 `'/api/v1/auth/refresh'`、`auth.uts:269` 硬编码 `'/api/v1/auth/logout'` —— 存在常量却不用，直接违反 `contract.uts:13` 自述纪律"新增端点/字段改本文件一处即可"与 L31-32 明示的"零调用常量不登记"规则。

### D01-9 证据：客户端 Authorization 注入点共 9 处（另 2 处为日志脱敏）
`Select-String -Pattern "'Bearer '"` 命中：`api.uts:57,322,400,450`（`buildHeader` + `doRequest` GET 分支 + `doRawRequest` GET 分支 + `doUploadFileHttp`）、`play.uts:542,605`、`audio_player.uts:317`、`upload_pipeline.uts:88`、`upload_protocol.uts:74`；`log.uts:55,59` 为脱敏正则（非注入）。每处都是 `const token = getToken(); if (token != '') header.set('Authorization','Bearer '+token)` 的复制体，`getToken()` 调用点同为此 11 处（`Select-String -Pattern 'getToken\(\)'` 验证）。`api.uts` 内部已有 4 份同一逻辑，说明"统一 header 构造"这一抽象未真正收口。

### D01-10 证据：TokenPair 有效期硬编码，与 settings 无联动
`schemas/auth.py:55-56`：`access_expires_in: int = 7200  # 2h`、`refresh_expires_in: int = 2592000  # 30d`。
`config.py:44-45`：`jwt_access_ttl_minutes: int = 120`、`jwt_refresh_ttl_days: int = 30`。
签发实际使用 settings（`core/security.py:24,40`），而**响应体**里的秒数是字面量。改 `JWT_ACCESS_TTL_MINUTES` 后客户端拿到的 `access_expires_in` 立即失真（客户端若据此做本地过期预判会误判）。

### D01-11 证据：`_issue_tokens` 是零调用导出
`services/auth/__init__.py:8`（import）与 `:42`（`__all__`）导出 `_issue_tokens`。全仓检索 `_issue_tokens` 仅命中：`auth.py:230`（本模块内部调用）、`auth.py:186/321/330/365`（注释）、`__init__.py:8,42`。**无任何 `from app.services.auth import _issue_tokens`**。该名在 `__all__` 里对外承诺，实际无人消费（测试亦直取 `app.services.auth.auth` 私有路径）。

### D01-12 证据：`AUTH_099` 登记了但从未 raise
`errors.py:50` 登记 `ErrorSpec("AUTH_099", "认证服务未接入或上游不可用（微信/短信）", 501)`、`errors.py:136` 定义常量。全仓检索 `ERR_AUTH_099|AUTH_099` 仅命中：`errors.py:42`（注释说明已拆分）、`:50`、`:136`、`providers.py:281`（注释）、`tests/test_auth_db.py:355`、`tests/test_auth.py:154/167/184`（均为**注释/断言字符串**，非 raise）。R4#11 拆分后该码已成空壳。

### D01-13 证据：`AUTH_006` 被 5 处 docstring 当错误码引用，却未登记、未 raise
引用点：`api/auth.py:86`（"devices 表可吊销 AUTH-006"）、`api/auth.py:92`、`services/auth/auth.py:152`、`:164`、`:192`。`errors.py` 登记表（L36-121）**无 AUTH_006**，全仓无 `ERR_AUTH_006` 常量、无 raise。`errors.py:5` 自述"编号空洞（AUTH_002/006…）"——空洞已被承认，但散落 5 处 docstring 仍把 006 当有效码叙述，是文档/代码声明漂移。

### D01-14 证据：`is_new_user` 是恒 False 的死字段
`schemas/auth.py:64`：`is_new_user: bool = False  # 新用户 → 前端引导 AI 冷启动（F7）`；唯一两处赋值 `auth.py:307`、`:374` 均硬编码 `False`（见 D01-1 证据）。客户端检索 `is_new_user` **零命中**。即"新用户引导"（F7）契约字段从服务端到客户端全链未接线。

### D01-15 证据：渠道分发为 if 链，非注册表
`providers.py:376-395` `get_login_provider(channel)`：`if channel == "wechat" / "phone" / "sms-mock" / "device"` 四条独立 `if` + 末尾 `raise ValueError`。L378-380 注释自称"**全仓唯一的渠道分发点**（tests 中零引用）——新增渠道必须在此登记分支"。已用 `LoginProvider` ABC + 策略类做了一半抽象，分发仍是硬编码串比较（无 `dict[str, type[LoginProvider]]`、无 `register()`），且渠道名字符串在 `auth.py:56,69,79` 与 `contract.uts` 之间多处字面量重复。

### D01-16 证据（候审）：`devices.platform` 列被写入"渠道名"而非"平台名"
`db/models/auth.py:43`：`platform: Mapped[str]`（列语义＝平台，默认/微信/设备渠道均写 `"android"`，见 `auth.py:269` 默认值、`providers.py:331`、`:372`）。但 `providers.py:244` 与 `:258` 把手机渠道写成 `platform="phone"`，经 `auth.py:282` `Device(user_id=..., device_id=..., platform=platform)` 落库。同一列取值域混入 `android` 与 `phone` 两套语义，后续按 platform 做端侧统计/推送分端会误判。**候审**：需人工确认 `devices.platform` 是否有既定取值约定（交付文档/建表 SQL 未在本域实读）。

### D01-17 证据（候审）：手机渠道 `device_id` 恒为魔法串 `"phone-login"`
`providers.py:244` 与 `:258` 均返回 `AuthIdentity(phone=phone, device_id="phone-login", ...)`。结合 `db/models/auth.py:38` 的 `UniqueConstraint("user_id","device_id")`：同一用户的所有手机号登录共用**一行** devices → 只保留一个 refresh 会话、多端（两端都用手机号登录）会互相顶掉。**候审**：当前 MVP 主通道是设备码/微信，此路径未必被真实使用，需确认是否为已知取舍。

### D01-18 证据：api 层为测试 re-export 服务层私有函数
`api/auth.py:36-38`：`# 兼容既有引用（test_auth_db R2#7 直接从 app.api.auth 导入）` + `from app.services.auth.auth import _hash_refresh_token, _rotate_refresh_token  # noqa: F401`。消费方 `tests/test_auth_db.py:242`。即生产模块的公开面被测试导入路径绑架，`# noqa: F401` 是这一绑架的痕迹。

### D01-19 证据：账号页的"当前用户"能力放在音频模块
`client/pages/account/security.uvue:74` `import { fetchUsersMe } from '@/utils/play'`，而 `fetchUsersMe` 定义在 `client/utils/play.uts:585`（同文件 L579 注释"用户域 + 内容备注"）。`play.uts` 主体是播放/时间轴逻辑（L571 附近为另一 GET 封装）。账号页依赖"播放"模块取用户信息，域归属错误，`security.uvue` 与 `play.uts` 之间形成意外耦合。

### D01-20 证据：`is_degraded` 仅测试消费
`otp_store.py:193-196` `is_degraded()` 定义；全仓检索仅命中 `otp_store.py:193` 与 `tests/test_otp_store_redis.py:108`。生产代码零调用（运维观测承诺未接线）。对比 `ratelimit.py` 无对应函数——两套降级后端连"是否可观测"都不一致。

### D01-21 证据（候审）：`platform` 无取值域约束
`schemas/auth.py:21`：`platform: str = Field("android", max_length=16, description="平台标识")`（仅长度限制）；`providers.py:372`：`platform=req.platform or "android"` 原样透传落库。客户端固定发 `'android'`（`auth.uts:98`），但服务端接受任意 ≤16 字符串。**候审**：是否要收敛为枚举（`Literal["android","ios"]`）取决于二期平台规划，需拍板。

## 依赖方向与抽象覆盖度结论

### FF1：本域反向依赖检查
**结论：未发现 `services → api` 或 `core → services` 的反向 import。** 逐条 import 边：

| 源 | 目标 | 方向判定 |
|---|---|---|
| `api/auth.py:34,38` | `services.auth.auth` | 正向（协议层→服务层）✅ |
| `api/deps.py:14,17` | `core.security` / `services.errors` | 正向 ✅ |
| `services/auth/auth.py:28-47` | `core.config` / `core.errors` / `core.security` / `db.models` / `schemas.auth` / `services.auth.providers` | 正向（服务层→core/db/schema，同层 services）✅ |
| `services/auth/providers.py:29-42` | `core.config` / `core.errors` / `db.models` / `services.auth.otp_store` | 正向 ✅ |
| `services/auth/otp_store.py:110-118`（函数内延迟导入） | `redis` / `core.config` | 正向（且刻意延迟到调用期，避免导入期建连）✅ |
| `core/ratelimit.py:30-31` | `core.config` / `core.security` | 同层 ✅ |
| `api/__init__.py` | 无业务依赖 | ✅ |

**唯一越界（非 import 反向，但边界被穿透）**：D01-18（`api/auth.py` re-export 服务层私有名供测试）与 D01-11（服务层 `__all__` 暴露私有名）；测试侧 `tests/test_auth_g1.py:12-17`、`test_auth_device.py:100-101` 直接 import 服务层 `_hash_*`/`_sha256_legacy`/`_hash_code` 私有名，形成"私有名即事实公共 API"。

### 新增一类认证方式（新登录渠道）需改几处 —— 全部注册点

| # | 注册点 | 文件:行 | 是否必改 |
|---|---|---|---|
| 1 | 新 Provider 类（实现 `resolve_identity`） | `backend/app/services/auth/providers.py`（参照 `:237-258`、`:312-331`、`:344-373`） | 必改 |
| 2 | 分发分支登记 | `backend/app/services/auth/providers.py:387-395`（`get_login_provider` if 链） | 必改 |
| 3 | 服务层编排函数 | `backend/app/services/auth/auth.py:54-81`（现三函数同构，见 D01-2） | 必改 |
| 4 | 路由端点 | `backend/app/api/auth.py:43-96` | 必改 |
| 5 | 请求/响应 schema | `backend/app/schemas/auth.py:6-48` | 必改 |
| 6 | 包导出面 | `backend/app/services/auth/__init__.py:6-30`（import）+ `:32-58`（`__all__`） | 必改（否则分发点外的引用拿不到） |
| 7 | 错误码登记 + 常量 | `backend/app/core/errors.py:36-121`（`_ERROR_SPECS`）+ `:127-189`（常量） | 视需（新失败语义时必改） |
| 8 | 限流域登记 | `backend/app/core/ratelimit.py:41-46`（`_DOMAIN_SCOPES`） | 仅当新路径不在 `/api/v1/auth` 前缀下 |
| 9 | 客户端路径常量 | `client/utils/contract.uts:16-23` | 必改（纪律要求；但见 D01-8 现已被绕过） |
| 10 | 客户端登录封装 | `client/utils/auth.uts`（参照 `deviceLogin` `:69-128`；`ensureLogin` `:149-165` 已渠道无关） | 必改 |
| 11 | 客户端设备标识（若新渠道需新标识源） | `client/utils/device_id.uts:54-111` | 视需 |
| 12 | 测试 | `backend/tests/test_auth*.py` | 必改 |

即**最少 8 处生产代码同步修改**（1-6、9、10），其中 2、3、4 是典型的"三处同构抄写"。抽象覆盖度结论：`LoginProvider`/`SmsSender` 两个 ABC 已建立（端口抽象存在），但**分发点（2）是 if 链而非注册表**，**编排层（3）无公共包装**，导致新增渠道成本＝抄 3 处 × 3 行同构代码 + 手工登记 if 分支。

### 新增一个受保护资源需改几处
| # | 注册点 | 文件:行 |
|---|---|---|
| 1 | 端点挂 `Depends(get_current_user)` | `backend/app/api/deps.py:55`（唯一实现，全仓 92 处消费） |
| 2 | 归属校验 loader | `backend/app/api/deps.py:84`（`load_alive_content`，仅覆盖 Content） |
| 3 | 限流域登记（若需限流） | `backend/app/core/ratelimit.py:41-46` |
| 4 | 路由挂载 | `backend/app/main.py:118-142` |
| 5 | 客户端路径常量 + 请求封装 | `client/utils/contract.uts` + `client/utils/api.uts` |

鉴权侧抽象度**良好**：`get_current_user` 是单点依赖、无重复实现（全仓检索 92 处均为 `Depends(get_current_user)`，无第二份 token 解析）。缺口在于 `load_alive_content` 只服务 Content 一种实体，`event_items`/`capsules` 已各自复刻同语义查询（`deps.py:90-91` 注释自认"capsules.seal_capsule 同款语义"），本域只登记不展开。

## 本域已查无问题项（避免遗漏被误读）

1. **鉴权依赖无重复实现**：`get_current_user` 全仓仅 `api/deps.py:55` 一份；92 处消费点（`contents` 15 处、`upload` 6 处、`events` 7 处等）全部走同一依赖，无"各域自写 token 解析"。
2. **JWT 密钥读取已消除导入期快照**：`core/security.py:17-19` `_jwt_params()` 函数内读取 `settings`，`security.py:9-10` 有对应审查说明；`auth.py:380-382` `_hmac_key()` 同款处理（refresh 哈希密钥与 `jwt_secret` 隔离，`config.py:48`）。
3. **refresh 轮换与验证码消费均为原子 single-use**：`auth.py:348-363` 条件 UPDATE + `rowcount` 判定；`providers.py:225-233` 同款。两者都有对应竞态测试（`test_auth_db.py:242-277`）。
4. **并发首登唯一约束冲突已兜底**：`auth.py:254-265`（User）与 `:285-298`（Device）均捕获 `IntegrityError` → 回滚重查，不再 500。
5. **限流降级不 500**：`ratelimit.py:190-194`、`:230-234` 与 `otp_store.py:176-184` 三处均捕获异常并降级 Memory，有告警日志，无"限流后端故障拖垮业务"路径。
6. **`ApiError` 统一信封**：`errors.py:203-212` 单点映射 `{code,message,request_id,details}`；`api/auth.py` 六个端点均无自定义错误响应体（`ApiResponse` 包装 + 服务层抛 `ApiError`），错误码口径统一。
7. **认证域错误码在 raise 处均引用常量**：`api/auth.py`/`services/auth/*` 内 9 个 raise 点全部用 `ERR_AUTH_0xx` 常量，无裸字符串新码（符合 `errors.py:8-13` 使用规则）。
8. **渠道"未接入即拒绝"的 fail-closed 口径三处一致**：微信未配置生产 → 501 AUTH_011（`providers.py:323-326`）、短信未接入生产 → 501 AUTH_010（`auth.py:95-96`）、设备通道关闭 → 501 AUTH_014（`providers.py:360-361`），且都有注释指向同一设计原则，无静默降级 mock 的路径。
9. **客户端登录并发去重完备**：`auth.uts:147-165`（`_loginInflight`）与 `:174-188`（`_refreshInflight`）双 single-flight，无重复登录/双轮换窗口。
10. **客户端凭据安全存储降级已显式登记**：`auth.uts:4-14` 明确登记"临时降级为 uni storage 明文"、风险与恢复路径（`setSecure/getSecure/removeSecure` 三函数单点，`auth.uts:39-49`），未静默。
11. **设备标识取值链单一入口且不静默**：`device_id.uts:54-111` 四级链 + 每级打点 + 空串诚实返回；全仓检索无第二处 `androidId()`/`getDeviceInfo` 取标识实现。
12. **`contract.uts` 其余常量消费正常**：`PATH_CONTENTS`/`PATH_SEARCH`/`PATH_EVENTS_*`/`PATH_ASR_TRANSCRIBE` 等均有消费方（`play`/`upload_protocol`/`search_api`/`event_sync`/`voice`/`sync_client`，与 `contract.uts:11-13` 自述一致）——零调用问题仅限 3 个 `PATH_AUTH_*`（D01-8）。
