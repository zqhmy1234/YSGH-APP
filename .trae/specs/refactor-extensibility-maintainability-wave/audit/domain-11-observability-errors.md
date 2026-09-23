# 域11 · 观测与错误 深度审计

> Phase A 只读筛查。范围：错误码登记表 / `core/errors.py` / `core/middleware.py` / `core/ratelimit.py` / Sentry 双通道 / `client/utils/sentry.uts` + `log.uts` + `api.uts`。**只登记不修**。
> 审计日期：2026-09-24。基线提交：`cec3172`。

## 覆盖范围与实读清单

实读文件（路径 + 行数）：

| 文件 | 行数 | 读法 |
|---|---|---|
| `backend/app/core/errors.py` | 253 | 全读 |
| `backend/app/core/middleware.py` | 14 | 全读 |
| `backend/app/core/ratelimit.py` | 237 | 全读（中间件/存储/域路由/信封） |
| `backend/app/main.py` | 153 | 全读（中间件顺序、Sentry 初始化、错误处理安装） |
| `backend/app/api/capsules.py` | 187 | 全读 |
| `backend/app/api/export.py` | 101 | 读 L60-101 |
| `backend/app/services/errors.py` | 42 | 全读 |
| `backend/app/api/__init__.py` | 57 | 全读（RequestIdRoute） |
| `backend/app/api/chat.py` | 165+ | 读 L70-170（动态码 raise 点） |
| `backend/app/api/classify.py` | 140 | 读 L55-140 |
| `backend/app/api/corrections.py` | 78 | 全读 |
| `backend/app/api/search.py` | 70 | 读 L30-81 |
| `backend/app/api/asr.py` | 106 | 读 L1-60（`_asr_error` 工厂） |
| `backend/app/core/queue.py` | — | 读 L78-90 |
| `backend/app/services/external/storage.py` | — | 读 L160-210（异常包装） |
| `backend/app/services/ai_tagging.py` | — | 读 L100-143（异常包装） |
| `backend/app/services/photo_content.py` | — | 读 L208-237 |
| `backend/app/services/auth/auth.py` | — | 读 L148-167 |
| `backend/app/services/pipeline_ext/payload.py` | — | 读 L35-63 |
| `backend/app/services/external/asr/audio.py` | — | 读 L118-132 |
| `backend/app/services/pipeline.py` | — | 读 L455-474（内部错误码） |
| `backend/tests/test_error_registry.py` | 86 | 全读（门禁逻辑） |
| `client/utils/sentry.uts` | 168 | 全读 |
| `client/utils/log.uts` | 161 | 全读 |
| `client/utils/api.uts` | 546 | 读 L1-45、L250-349 |
| `client/utils/config.uts` | 95 | 读 L1-95 |
| `client/App.uvue` | 112 | 全读（Sentry 初始化接点） |
| `deploy/.env.production.template` / `deploy/docker-compose.yml` | — | 抽查 SENTRY/APP_ENV |

`git status --short`（审计时）：

```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```

即：**本域无在途改动业务文件**（唯一未跟踪目录为本波审计产物）。AGENTS 提到的"第四窗媒体票据 8 文件在途"在本机当前工作区**并不存在**（`media.py`/`media_url.py` 等均无 modified）——审计基于当前落盘状态。

穷举方法（可复现，只读）：内联 `python -c` AST 扫描 `backend/app/**/*.py`，收集 ① `core/errors.py` 中 `ErrorSpec(...)` 首参 → 登记集；② 全模块 `ERR_X = "..."` 赋值表 → 常量→码值映射；③ 所有 `ApiError(...)` 调用点首参（`ast.Constant` 字面量 + `ast.Name` 常量）→ 使用集；再求差集。避免只 grep。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D11-1 | `backend/app/api/capsules.py:39-42,51,97,100,162,184` | 声明漂移（漏登记） | P0 | 高 | 低 | 新增工具 | AST 对拍 + 单测 | 批次1 | 确认 |
| D11-2 | `backend/tests/test_error_registry.py:20-47` | 门禁缺口 | P1 | 高 | 低 | 行为等价替换 | 造样例码复现 | 批次1 | 确认 |
| D11-3 | `backend/app/api/export.py:80-93` vs `backend/app/core/ratelimit.py:203-214` | 重复逻辑+魔法字符串 | P1 | 中 | 低 | 行为等价替换 | 读两处 | 批次2 | 确认 |
| D11-4 | `backend/app/main.py:82-93,107-116` + `core/errors.py:234-247` | 边界（中间件覆盖） | P1 | 中 | 中 | 新增工具 | 触发 500 看响应头 | 批次2 | 候审 |
| D11-5 | `backend/app/core/errors.py:50,58,65,136,142,149` | 死码（死登记） | P2 | 中 | 低 | 删码(需证死) | Grep 零命中 | 批次3 | 确认 |
| D11-6 | `classify.py:127,130` / `corrections.py:53` / `search.py:55` | 边界（跨域复用码） | P2 | 中 | 低 | 行为等价替换 | 读三处 | 批次3 | 确认 |
| D11-7 | `services/pipeline.py:372,469`、`pipeline_audio.py:170,182`、`external/asr/models.py:42` | 注册表覆盖度 | P2 | 中 | 低 | 新增工具 | 读四处 | 批次3 | 确认 |
| D11-8 | `backend/app/core/errors.py:10,123` + `tests/test_error_registry.py:70-76` | 门禁缺口 | P2 | 中 | 低 | 新增工具 | 构造 http 漂移 | 批次3 | 候审 |
| D11-9 | `backend/app/main.py`（无 logging 配置） | 可观测盲区 | P2 | 中 | 低 | 新增工具 | Grep dictConfig | 批次3 | 确认 |
| D11-10 | `backend/app/core/middleware.py:1-14` | 可观测盲区 | P2 | 中 | 低 | 新增工具 | Grep ContextVar 零命中 | 批次3 | 确认 |
| D11-11 | `payload.py:41,60`、`asr/audio.py:124`、`contents.py:118`、`queue.py:88` 等 | 可观测盲区（静默 except） | P2 | 中 | 低 | 新增工具 | AST 扫描无日志 except | 批次3 | 确认 |
| D11-12 | `backend/app/main.py:50-53` | 可观测盲区（Sentry） | P2 | 中 | 中 | 新增工具 | 运行时验证 | 批次3 | 候审 |
| D11-13 | `client/utils/api.uts:281,310-311` | 注册表覆盖度（客户端合成码） | P2 | 低 | 低 | 新增工具 | 读两处 | 批次3 | 确认 |
| D11-14 | `backend/app/main.py:104-106` | 声明漂移 | P2 | 低 | 低 | 纯移动+兼容再导出 | 读注释 vs 注册序 | 批次3 | 确认 |

---

### D11-1 证据（P0 · **4 枚漏登记**）
AST 对拍（只读，命令见上）：`registered count: 63`、`used count: 60`、`MISSING (used but not registered): ['CAPSULE_001','CAPSULE_002','CAPSULE_003','CAPSULE_004']`。
`capsules.py:39-42` **就地定义** `ERR_CAPSULE_001..004`，并在 L51 / L97 / L100 / L162 / L184 raise。这些码在 `core/errors.py` 的 `_ERROR_SPECS` 与 `ERR_*` 常量区**均不存在**（`Grep "CAPSULE_0" backend` 仅命中 capsules.py 与 tests，errors.py 零命中）。
违反 `errors.py:8-10` 明示的使用规则（"新增错误码必须先在本表登记，再在 raise 处引用常量，禁止裸字符串新码"）。后果：`CAPSULE_*` 无登记 http/retryable 语义、无法被客户端契约/运维对拍，且与"全仓错误码唯一登记表"（errors.py:3）声明矛盾。模块注释 `capsules.py:11` 自辩为"避免越批文件域"，但同批的 MEDIA_* 已登记进 errors.py，口径不一致。
数据面：客户端已在注释里引用该码（`client/components/CapsuleSheet/CapsuleSheet.uvue:55,139`）。**漏登记数 = 4**。

### D11-2 证据（P1 · 门禁 AST 盲区）
`test_error_registry.py:43-46`：仅对 `ast.Name` 且 `a0.id.startswith("ERR_")` 的常量，用 `getattr(app.core.errors, a0.id)` 解析；`capsules.py` 里定义的 `ERR_CAPSULE_*` 不在 `app.core.errors` 命名空间 → `getattr` 返回 `None` → **静默丢弃，不进入 used 集**，故 `test_error_registry_covers_all_raise_sites`（L70-76）对 D11-1 全绿。
这是与 `errors.py:79-81` 记载的 MEDIA_003 同类盲区（"AST 扫描盲区：ApiError 第一参为常量名非字面量"），只是换成了"常量定义在别的模块"。**打字机式修复（把名单硬编码/加白名单）会再留盲区**——真正的门禁应解析所有模块的 `ERR_*` 赋值（本审计内联脚本即此法，一次抓出 4 枚）。

### D11-3 证据（P1 · 429 信封双实现）
`ratelimit.py:203-214` 的 `_rate_limited()` 与 `export.py:80-93` 内联 `Response(status_code=429, ...)` 是**同语义两份 429 信封**：`{"code","message","request_id"[,"details"]}` + `Retry-After` 头。
`export.py:85` 用**字面量** `"RATE_LIMITED"`，而 `ratelimit.py:36` 定义了 `RATE_LIMIT_CODE = "RATE_LIMITED"` → 魔法字符串重复，改码需改两处。
附带：export 的限流走**独立实现** `_export_allowed`，**未登记进** `ratelimit.py:41-46` 的 `_DOMAIN_SCOPES`——即"限流域注册表"对 export 域失效，新增限流域要么改 `_DOMAIN_SCOPES`+settings，要么照 export 再造一套（复制粘贴式扩展）。

### D11-4 证据（P1 · 500 绕过全部用户中间件）
`errors.py:250-253` 用 `app.add_exception_handler(Exception, unhandled_error_handler)` 注册 500 兜底。Starlette 的 `Exception` handler 由**最外层** `ServerErrorMiddleware` 承接，而用户中间件注册序（`main.py`）为：`add_security_headers`（L82，最先注册→**最内层**）→ `RateLimitMiddleware`（L107）→ `RequestIDMiddleware`（L108）→ `CORSMiddleware`（L109，最外层）。
因此未处理异常产生的 500 响应在 **CORS/RequestID/RateLimit/安全头之外**生成：**缺失** `X-Content-Type-Options` / `X-Frame-Options` / `Referrer-Policy` /（生产）HSTS，也缺失 `X-Request-ID` 响应头与 CORS 头（body 内 request_id 仍在，因 `request.state` 是同 scope）。
同类：`RateLimitMiddleware` 的 429 位于 `add_security_headers` **外侧**（安全头在最内层），故 429 也**缺安全响应头**（但有 X-Request-ID）。
判据为注册序 + Starlette 语义，无运行期取头验证 → 标 **候审**（建议真机/测试触发一次 500 与 429 取 `curl -I` 核实）。可回滚性=新增工具（把安全头/RequestID 提到最外层，或改用纯 ASGI 外层中间件）。

### D11-5 证据（P2 · 死登记 3 枚）
`DEAD (registered but never raised): ['ASR_001','ASR_002','AUTH_099','CHAT_001','CHAT_002','CONTENT_004','CONTENT_011']`。
逐条证伪后确认 **3 枚真死**（其余 4 枚为助手/动态构造的假阳性）：
- `ASR_001/ASR_002`：经工厂 `_asr_error()`（`asr.py:42-50`）构造后 `raise _asr_error(exc)`（`asr.py:74,85`）→ **在用**。
- `CHAT_001/CHAT_002`：`chat.py:117` `code = ERR_CHAT_002 if ... else ERR_CHAT_001` 后 `raise ApiError(code,...)`（L119）→ **在用**。
- `AUTH_099`（`errors.py:50,136`）：`Grep "AUTH_099"` 仅命中 errors.py 自身 + 测试/注释中的"拆分"说明，**全仓零 raise**——已被 AUTH_010~014 拆分取代，遗留登记。
- `CONTENT_004`（`errors.py:58,142`）：全仓仅 errors.py 命中，**零 raise**（"STS 直传未接入（生产待实现）"占位）。
- `CONTENT_011`（`errors.py:65,149`）：全仓仅 errors.py 命中；收藏端点已改为**幂等返回既有态**（`contents.py:647` 注释），该"409 冲突"语义实际不再发生 → 死登记。
**死登记数 = 3**。删除需带"证死"（含动态构造路径复核，见 D11-2 教训）。

### D11-6 证据（P2 · 跨域复用错误码）
同码单义（无"同码异义"冲突），但 **码的域名前缀与 raise 所在模块不匹配**：
- `classify.py:127` `raise ApiError(ERR_CORR_003, "任务不存在或已过期", 404)`、`classify.py:130` `ERR_CORR_004`（**分类/裁决端点用"纠错域"码**）。
- `corrections.py:53` `raise ApiError(ERR_EVENT_005, "内容不存在或不属于当前用户", 404)`（**纠错端点用"事件域"码**，L44 注释自认"沿用既有口径"）。
- `search.py:55` `raise ApiError(ERR_CONTENT_006, ...)`（**搜索端点用"内容域"码**）。
影响：按域检索错误码（如"事件域哪几个码"）会漏/错；新增域错误时需跨域翻找。可回滚性=行为等价替换（拆出各自域码，但属对外契约变更，需评估客户端影响）。

### D11-7 证据（P2 · 未登记的"内部错误码"第三命名空间）
除 `ERROR_REGISTRY` 外，还存在两套**未登记**的码空间，均会落到持久化/响应中：
- 管线失败码持久化进 `content.error`：`pipeline.py:372` `{"code": "EMOTION_ENQUEUE_FAILED", ...}`、`pipeline.py:469` `"ASR_PIPELINE_ERROR" if is_voice else "PIPELINE_ERROR"`、`pipeline_audio.py:182` `{"code": "LOCAL_EMOTION_PIPELINE_ERROR", ...}`、`pipeline_audio.py:170` 透传 `exc.code`。
- 服务层私有码：`services/external/asr/models.py:42` `AsrError.code`（`AUDIO_NOT_FOUND`/`EMPTY_AUDIO`/…，见 `asr.py:33-39` 白名单），仅在边界映射为 ASR_001/002。
这些码**不在错误码唯一真源**，客户端/运维无从对拍，故障分类口径分裂。可回滚性=新增工具（登记为内部码子表或明确命名空间）。

### D11-8 证据（P2 · 登记 http 与 raise http 无一致性校验）
`errors.py:10` 示例 `raise ApiError(ERR_CONTENT_003, "...", http=422)` 表明 raise 处可**自传 http** 覆盖登记值；现有门禁 `test_error_registry.py:50-86` 只校验"码唯一 / retryable 与 5xx 关系 / 码在表内 / 已知拆分值"，**不校验 raise 处传入的 http 是否等于登记 http**。故登记 http 可被 raise 处静默漂移（登记表 http 失去约束力）。杀点：AST 取 `ApiError(...)` 第 3 个关键字 `http` 与登记比对即可。标候审（当前未发现实际漂移实例）。

### D11-9 证据（P2 · 后端无 logging 配置）
`Grep "dictConfig|basicConfig|logging.config" backend/app` 仅命中 `workers/cleanup_job.py:231`、`workers/requeue_job.py:177`（**worker 进程**）。`main.py`（API 进程）**无任何 logging 初始化** → `errors.py:238` 的 `logger.exception(...)`、`ratelimit.py:233` 的 `logger.exception(...)` 等输出依赖 uvicorn/root 的隐式 lastResort handler，级别与格式不可控、无法集中采集。与"可观测双通道"目标不匹配。

### D11-10 证据（P2 · request_id 未进日志上下文）
`Grep "contextvar|ContextVar" backend` **零命中**。`middleware.py:8-14` 只写入 `request.state.request_id` 与响应头；`api/__init__.py:15-50` 的 `RequestIdRoute` 仅把 request_id 回填进**响应体**。服务层/worker 的 logger **无 request_id 上下文注入**，故 `middleware.py:1` 声称的"全链路日志可按 request_id 串联"仅在 HTTP 信封/头部层面成立，日志行层面不成立。

### D11-11 证据（P2 · 静默 except 清单）
内联 AST 扫描：`except Exception/BaseException/bare` 且 handler body 内**无任何 logger/capture/print 调用**者 35 处；抽读剔除"包装后 re-raise"的假阳性（`storage.py:169,177,190,315,325,334,343` 均 `raise StorageError`；`ai_tagging.py:110,136` 均 `raise AiTaggingError`；`photo_content.py:215,240,281` 均 `db.rollback()+best_effort_delete+raise`；`auth.py:155,201` 均 `raise ApiError`）后，**真静默（吞掉且无留痕）** 的代表：
- `services/pipeline_ext/payload.py:41,60` → `pass`（字段访问异常不落任何日志，注释标 `S110`）。
- `services/external/asr/audio.py:124` → `return 0`（WAV 时长解析失败静默）。
- `api/contents.py:118` → `return None`（EXIF 解析失败静默）。
- `core/queue.py:88` → `return None`（job 缺失/过期静默）。
- `core/ratelimit.py:169` → `return None`（坏 token 静默降级）。
- 其余同族：`thumbnails.py:72,81`、`rerank.py:23`、`pipeline_audio.py:42`、`event_aggregation/run_validation.py:213`。
多为**有意降级**（已 `noqa: BLE001`），但**无 debug/telemetry 留痕** → 此类隐蔽降级在线上不可观测。可回滚性=新增工具（补 debug 级 logger 或计数指标）。

### D11-12 证据（P2 · 后端 Sentry 依赖自动集成）
`main.py:50-53`：`if settings.sentry_dsn and settings.app_env == "production": sentry_sdk.init(dsn=..., traces_sample_rate=0.1)`。
- `Grep "sentry_sdk\.|capture_exception|capture_message|before_send"` 全后端仅命中 `main.py:53` 这一处 init → 后端**没有任何显式上报**，500 完全依赖 SDK 的 FastAPI/ASGI 自动集成；`errors.py:234-247` 的兜底 handler 只 `logger.exception`，**未显式 capture to Sentry**。
- `sentry_sdk.init` 位于 `lifespan`（应用对象构建**之后**），自动集成的接线时机是否覆盖请求中间件需**运行时验证**（若未生效，后端 Sentry 通道可能整条是暗的）。
- 缺 `environment`/`release` 标注与 `before_send`（PII 脱敏）；`traces_sample_rate=0.1` 硬编码。
标候审。可回滚性=新增工具。

### D11-13 证据（P2 · 客户端合成码 / 4xx 不上报）
- 客户端**无硬编码业务码分支**：全仓（含 `.uvue`）`Grep` 业务码仅命中**注释**（`CapsuleSheet.uvue:55,139` 的 CAPSULE_001、`contract.uts:20` 的 AUTH_014、`play.uts:532` 的 MEDIA_003）；实际分支只看 `statusCode`（`api.uts:262,290,377,468`）。→ 该项为"已查无问题"（记录于此以免误读）。
- 但客户端**自造码**：`api.uts:281` `let code = 'UNKNOWN'`、`api.uts:311` `new ApiError('NETWORK', ...)`——`UNKNOWN`/`NETWORK` **不在任一登记表**，与后端码无共享契约。
- `api.uts:273` 仅 `statusCode >= 500` 上报 Sentry（4xx 有意做噪音闸门），故 **429/409 等运营可感错误在 Sentry 无痕**（部分为有意，登记为盲区口径即可）。

### D11-14 证据（P2 · 中间件顺序注释漂移）
`main.py:104-106` 注释："CORS(最外) → RequestID → RateLimit(**最内**)"。但 `add_security_headers` 经 `@app.middleware("http")`（L82）**最先注册**→ 实际为**最内层**；RateLimit 并非最内。注释漏列 `add_security_headers` 且层级标注错误，易误导 D11-4 类判断。

---

## 依赖方向与抽象覆盖度结论

**FF1 · 是否存在反向依赖？—— 否（本域干净）。** 实际 import 边：
- `core/errors.py` → 仅 `fastapi` / `logging` / `dataclasses`（无 app 内反向依赖）。
- `core/middleware.py` → 仅 `starlette` + `uuid`。
- `core/ratelimit.py` → `app.core.config`、`app.core.security`（**core→core**，同层，不构成反向）。
- `main.py` 聚合面 → `app.api.*`、`app.core.*`。
未发现 `services → api`、`core → services`、`core → api` 等反向边。

**新增一类实体/渠道/类型需改几处（注册点全列）：**
1. **新增一个错误码**：`core/errors.py` `_ERROR_SPECS`（L36-121）**+** `ERR_*` 常量区（L127-189）——**2 处**（缺一即 D11-1/D11-2）。若照 `capsules.py` 就地定义则**绕过**登记表（现网反例）。
2. **新增一个限流域**：`core/ratelimit.py` `_DOMAIN_SCOPES`（L41-46）**+** `settings.rate_limit_{scope}_{ip,user}`（L175-180 反射读取）——**2 处**；若走签名/异步专用限流则另起一套（`export.py:80-93` 先例，未登记）。
3. **新增一个可观测通道**：客户端 `config.uts` DSN（L18）**+** `App.uvue` onLaunch `initSentry()`（L43，已接线）；服务端 `config.sentry_dsn` **+** `main.py:50-53` init。
4. **新增响应信封豁免**：`api/__init__.py` `RequestIdRoute`（L33-49，按 content-type/body 形态判定）——非注册式，靠形态判别。

---

## 本域已查无问题项（避免遗漏被误读）

- **裸 `except:`（无类型）**：全后端 `Grep "except\s*:"` 命中**全为 `except Exception:`**（含 `noqa: BLE001` 标注），**无真裸 except、无 `except BaseException`**。
- **`raise HTTPException`**：全后端**零命中**——错误出口统一走 `ApiError`（未发现绕过登记表的框架异常）。
- **内联 `{"code": ...}` 响应**：全后端仅 `export.py:85`（`RATE_LIMITED`）与 `errors.py:207,226,242`（`VALIDATION_ERROR`/`INTERNAL_ERROR`，信封级码）、`ratelimit.py:208`（`RATE_LIMIT_CODE`）——即除 D11-1/D11-3 外，**无其他绕过登记表直出业务码**。信封级码 `VALIDATION_ERROR`/`INTERNAL_ERROR`/`RATE_LIMITED` 未入 `_ERROR_SPECS`，属**跨全部请求的固定协议码**，与"业务域码登记表"定位不同（`ratelimit.py:35` 注释已声明此口径）。
- **错误码唯一性 / 同码异义冲突**：`_ERROR_SPECS` 63 码**唯一**（`test_error_registry_codes_unique`），未发现"一码两义"的历史病（CONTENT_003/007/008、EVENT_005 拆分已落地并有回归测试 L79-86）。跨域问题仅 D11-6 的**同义越界复用**，非冲突。
- **客户端硬编码错误码分支**：无（见 D11-13 上半）。
- **客户端 Sentry 是否接线**：**是**。`initSentry()` 在 `App.uvue:43` onLaunch 调用（此前用不含 `.uvue` 的 glob 会误判为"零调用"，已纠正）；`api.uts`/`chat_api.uts`/`search_api.uts`/`voice.uts` 均调用 `captureException/captureMessage/addBreadcrumb`；`config.uts:18` 有真实 DSN。
- **客户端日志脱敏**：`log.uts` `redactLog()`（Bearer/手机号/敏感键/长文截断）已在 `event_sync.uts:70`、`text_recorder.uts:138` 实际接线。
- **RequestID 中间件对 4xx 生效**：`ApiError`/`RequestValidationError` 由内层 `ExceptionMiddleware` 承接，在用户中间件之内 → 4xx 带全量安全头与 X-Request-ID（未被 D11-4 波及；D11-4 仅涉 500 与 429）。