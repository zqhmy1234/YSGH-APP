# 域10 · 护栏与内容安全 深度审计

> 波次：易扩展/易维护重构波 · Phase A 只读筛查。**只登记，不修**。
> 本域无 `backend/app/llm_ops/guard*` 之外的目录；`agent/` 未审（排除）。

## 覆盖范围与实读清单

**实读文件（行数）**

| 文件 | 行数 | 说明 |
|---|---|---|
| `backend/app/services/external/dashscope.py` | 196 | 护栏 chat 主实现 `moderate()` L142-196 |
| `backend/app/services/external/sensitive_words.py` | 344 | 硬规则 `check_sensitive` / 软事件级 `check_event_sensitive` / 回流热加入 |
| `backend/app/services/external/content_safety.py` | 313 | 四实现适配器 + 阿里云 Green + 注册表 |
| `backend/app/services/external/tencent_ci.py` | （仅读引用点） | `image_audit` / `image_detect_label` |
| `backend/app/services/llm_ops/guard.py` | 207 | 检测器抽象 / 事件级 LLM 补漏 / 违规词回流 |
| `backend/app/services/llm_ops/guard_managed.py` | 159 | 百炼托管护栏 `qwen_response_check` / `moderate_managed` |
| `backend/app/services/llm_ops/moderate.py` | 43 | 策略选择器（托管优先、chat 兜底） |
| `backend/app/services/llm_ops/base.py` | 42 | `moderate` 转发入口 |
| `backend/app/services/llm_ops/parsing.py` | 63 | LLM JSON 容错解析统一入口 |
| `backend/app/services/llm_ops/annotate.py` | 138 | 画像标注（用户可见输出，未过护栏） |
| `backend/app/services/llm_ops/rerank.py` L100-155 | — | 解析调用方（字段级清洗保留，非重复实现） |
| `backend/app/api/asr.py` | 133 | `/transcribe` 护栏 + `/api/v1/guard/check` |
| `backend/app/api/contents.py` | 902 | `create_content` 护栏 L292-301 / `upload_photo` 委托 |
| `backend/app/services/photo_content.py` | 287 | 照片注册唯一编排（含 moderate pre-check L174-184） |
| `backend/app/services/pipeline.py` L110-190 | — | 入库钩子 `mark_sensitive_on_ingest` / `annotate_on_ingest` |
| `backend/app/services/pipeline_photo.py` L45-115 | — | 照片管线（caption / CI 打标 / AI 描述） |
| `backend/app/services/pipeline_ext/sensitive.py` | 176 | 事件级敏感标记 + DB 回流词读取 |
| `backend/app/services/pipeline_ext/__init__.py` | — | 钩子转发（异常吞掉不阻断） |
| `backend/app/services/wechat/service.py` L180-380 | — | 微信收消息审核（`_check_text_safe` / `_audit_image`） |
| `backend/app/services/echo.py` L130-270 | — | 回响双查敏感（唯一走托管选择器的调用点） |
| `backend/app/services/rag/__init__.py` L234-243 | — | 规则级敏感过滤 |
| `backend/app/services/rag/image.py` L130-140 | — | 规则级敏感过滤（同款） |
| `backend/app/core/errors.py` | 253 | 错误码唯一登记表 |
| `backend/app/core/config.py` L120-160 / L213-254 | — | `content_safety_provider` / `app_env` / 生产安全兜底 |
| `backend/app/core/ratelimit.py` L40-50 | — | guard 前缀归属 ASR 域配额 |
| `client/utils/voice.uts` L288-325 | — | 客户端 `guardrail` 消费侧（无护栏逻辑） |
| `client/utils/sentry.uts` | 168 | 客户端可观测侧（无护栏逻辑，无密钥外泄） |
| `backend/tests/test_guard_managed.py` / `test_moderate_selector.py` / `test_content_safety.py` / `test_event_sensitive.py` | — | 现有测试覆盖盘点 |

**未实读（说明）**：`backend/data/sensitive/*.txt` 词表文件内容（只读代码加载逻辑）；`scripts/check_dashscope_matrix.py` 仅看 grep 命中行（护栏探针脚本，非业务码）。

**`git status --short`**
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
→ 无在途业务文件改动；工作树干净（本域审计未触碰业务代码）。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D10-1 | `backend/app/services/external/dashscope.py:187` ↔ `backend/app/api/contents.py:297`、`backend/app/services/photo_content.py:180` | 门禁缺口（fail-open） | P0 | 恢复 fail-safe 铁律；消除生产漏审 | 低（补 `action` 键即闭合；调用点改判 `pass` 亦可） | 行为等价替换 | 单测：`APP_ENV=production`+空 key → POST /contents 带敏感文本，断言 422 CONTENT_003（当前必失败） | 批次1 | 确认 |
| D10-2 | `backend/app/services/llm_ops/guard_managed.py:132-139` | 门禁缺口（fail-open） | P0 | 托管护栏空响应不再放行 | 低（空响应判为不可用 → 抛 RuntimeError 走 chat 兜底） | 行为等价替换 | 单测：mock `httpx.post` 返 200 + `content:""` → 断言 `pass=False`（当前 `pass=True`） | 批次1 | 确认 |
| D10-3 | `backend/app/api/asr.py:26`、`backend/app/api/contents.py:292`、`backend/app/services/photo_content.py:175`、`backend/app/services/external/content_safety.py:125` | 注册表/端口覆盖度 | P1 | 托管护栏真正覆盖入库主链；换厂商只需改 1 处 | 中（改动 4 个调用点的 import；需回归 4 条链路的 mock 契约） | 行为等价替换 | 断言 4 处调用点均触达 `llm_ops.moderate.moderate`（monkeypatch 选择器打桩计数） | 批次2 | 确认 |
| D10-4 | `backend/app/services/llm_ops/guard_managed.py:143-159` | 死码 / 重复逻辑 | P1 | 消除同语义双实现 | 低（仅测试引用；删前需同步 2 个测试） | 删码(需证死) | `Grep "moderate_managed"`：生产代码 0 命中，仅 `tests/test_guard_managed.py`、`tests/test_moderate_selector.py` | 批次2 | 确认 |
| D10-5 | `backend/app/services/external/sensitive_words.py:140-152` vs `:113-125` | 声明漂移 | P1 | 回流词真正进入硬规则引擎，兑现"防重复调 LLM" | 中（需区分硬/软回流语义，避免把软事件词误升为 reject） | 行为等价替换 | 单测：`add_violation_word("某违规词")` 后断言 `check_sensitive("某违规词")["action"]=="reject"`（当前为 pass） | 批次2 | 确认 |
| D10-6 | `backend/app/core/config.py:23`、`dashscope.py:161`、`dashscope.py:185` | 门禁缺口（配置依赖 fail-open） | P1 | fail-closed 不依赖单一环境变量正确性 | 低（新增启动自检告警） | 新增工具 | 单测：`app_env="staging"` + 空 key → 断言 `moderate()` 拒发（当前放行 `reason="mock"`） | 批次2 | 确认 |
| D10-7 | `backend/app/services/pipeline_photo.py:63-94`（对比 `wechat/service.py:256`、`content_safety.py:134`） | 门禁缺口（覆盖盲区） | P1 | App 内上传图片纳入审核，堵上架合规缺口 | 中（新增 CI/阿里云图片审核调用；需处理 STORAGE_BACKEND=fs 无 cos_key 情形） | 新增工具 | `Grep "image_audit"`：仅 `content_safety.py:138`+`wechat/service.py`；`pipeline_photo.py` 只有 `image_detect_label`（打标非审核） | 批次3 | 确认 |
| D10-8 | `backend/app/services/ai_tagging.py:119`、`llm_ops/event_merge.py:51`、`llm_ops/annotate.py:73`、`backend/app/api/chat.py` | 门禁缺口（覆盖盲区） | P1 | LLM 生成态用户可见输出纳入护栏 | 中（每条路径加一次护栏调用 = 成本/延迟；需产品拍板是否只走规则层） | 新增工具 | 逐条列出调用点与护栏缺失（见下证据）；`Grep "chat_text"` 得全部生成点 | 批次3 | 候审 |
| D10-9 | `backend/app/services/llm_ops/guard.py:122-124`、`:138-145` | 门禁缺口（软敏感 fail-open） | P1 | 敏感话题不漏标，回响/追问不主动提及 | 中（改 fail-closed 会把 LLM 故障放大为"全量软敏感"，需与产品对齐） | 行为等价替换 | 单测：monkeypatch `chat_text` 抛异常 → 断言 `detect_event_sensitive` 返回非空（当前 `[]`） | 批次3 | 候审 |
| D10-10 | `client/utils/voice.uts:299` | 门禁缺口（客户端默认放行） | P2 | 客户端不再把缺失/空 guardrail 当放行 | 低 | 行为等价替换 | 断言 `guardrail` 缺失时 `passed=false`（当前 `?? true`） | 批次3 | 确认 |
| D10-11 | `backend/app/api/asr.py:125-133`、`backend/app/services/external/content_safety.py:313` | 死码 / 口径一致性 | P2 | 清理零调用端点与零引用导出；护栏拒绝码/文案统一 | 低（`guard/check` 若为对外契约需先确认） | 删码(需证死) | `Grep "guard/check"` 生产 0 命中（仅 `tests/test_asr.py`）；`Grep "ProviderLiteral"` 全仓 0 命中（仅定义行） | 批次4 | 候审 |
| D10-12 | `backend/app/api/asr.py:87-118` | 口径一致性 | P2 | 转写结果号码/身份证打码真正生效 | 低（改用 `masked_text` 回传） | 行为等价替换 | 断言 `moderate` 返回 `action=mask` 时响应 `text` 为打码后文本（当前为原文 `result.text`） | 批次4 | 确认 |

---

### D10-1 证据（P0 · fail-closed 判定被 `action` 分派吞掉）

读了 `dashscope.py` L142-196：三条 `pass=False` 分支中，L163（mask 分支未配 key）、L172（mask 分支 LLM 拦截）、L176（mask 分支异常）都带 `"action": "reject"`；**唯独 L182-189 的"无规则命中 + 生产未配 key" fail-closed 分支返回 `{"pass": False, "reason": "guard-unavailable"}`——不含 `action` 键**。

而两个入库调用点按 `action` 分派：
- `api/contents.py:297` `if verdict.get("action") == "reject":` → 拒；`:300` `if verdict.get("action") == "mask" ...` → 打码。
- `services/photo_content.py:180` `if verdict.get("action") == "reject":` → 拒。

该 fail-closed verdict 既非 `"reject"` 也非 `"mask"` → **两条 `if` 均不成立 → 直接落库**（`contents` 路径连 `masked_text` 都不套用，原文入库；`photo_content` 路径不抛 `ModerateRejectError`）。即：生产环境漏配 `DASHSCOPE_API_KEY` 时，护栏明确判定"拒发"，调用方却放行入库——**fail-safe 铁律在该分支失效**。按 `pass` 分派的调用点（`echo.py:175/254`、`asr.py:117`、`content_safety.py:128`）不受影响。

对比佐证：`scripts/check_dashscope_matrix.py:200` 的 SAF-005 探针只断言 `moderate()` 返回值，**未覆盖调用方分派**，故该缺口未被门禁拦住。

### D10-2 证据（P0 · 托管护栏空响应放行）

读了 `guard_managed.py` L124-139：`content` 取不到时为 `""`，L132 `answer = (content or "").strip().upper()` → `""`，L133 `blocked = any(m in answer for m in _BLOCK_MARKERS) or answer == "BLOCK"` → `False`，L135 `"pass": not blocked` → **`True`（放行）**，`detail="（空响应）"`。

即托管返回 200 但 content 为空/结构异常（choices 缺 message.content）时判为放行。`tests/test_guard_managed.py` 只覆盖 PASS / BLOCK / 400-inspection / 网络异常四种（L49-124），**空响应与畸形 200 响应均无测试**。同一函数对非 200 与非审查错误是 `raise RuntimeError`（走 chat 兜底），故"空响应"是唯一漏网的 fail-open 分支。

### D10-3 证据（P1 · 托管护栏接线覆盖度）

`Grep "moderate"`（递归 `backend/app`）得全部护栏调用点，按 import 来源分两组：

| 调用点 | import 来源 | 是否走托管 `qwen_response_check` |
|---|---|---|
| `echo.py:172` / `:251`（回响双查） | `llm_ops.base.moderate` | ✅ 是（经 `llm_ops/moderate.py:40`） |
| `api/asr.py:90`（转写护栏） | `external.dashscope.moderate`（L26） | ❌ 否 |
| `api/asr.py:132`（`/guard/check`） | 同上 | ❌ 否 |
| `api/contents.py:296`（内容入库） | `external.dashscope.moderate`（L292） | ❌ 否 |
| `photo_content.py:179`（照片 caption） | `external.dashscope.moderate`（L175） | ❌ 否 |
| `content_safety.py:127`（微信文本） | `external.moderate`（L125，经 `external/__init__.py:9` 转发 dashscope） | ❌ 否 |

→ 4 条入库/审核主链（内容入库、照片 caption、ASR 转写、微信文本）**绕过策略选择器**，B5b 定稿的"百炼托管护栏优先"实际只在回响一条路径生效；`guard_managed.py` 文档 L9-10 声称"策略入口由 llm_ops/moderate.py 选择器同构接线"，与调用现状不符。换护栏厂商/接入 `X-DashScope-DataInspection` 时需同时改这 4 处 import。

### D10-4 证据（P1 · 同语义双实现 + 零生产调用）

`Grep "moderate_managed"`（backend+client+scripts）：定义 1 处（`guard_managed.py:143`），调用仅出现在 `tests/test_guard_managed.py:144-146`、`tests/test_moderate_selector.py:78-80`。生产代码 0 命中 → **零调用导出**。

同时它与 `llm_ops/moderate.py:32-43` 是**同语义双实现**（两者都是"try `qwen_response_check` → `except RuntimeError` → `dashscope.moderate`"），差异仅在 `moderate_managed` 多一行 `logger.info`。`tests/test_moderate_selector.py:80` 自己就把两者当"行为等价"一起断言（`entries = (moderate_mod.moderate, base_moderate, moderate_managed)`），证实同语义。

### D10-5 证据（P1 · 回流词未入硬规则表）

`guard.py:15` 与 `pipeline_ext/sensitive.py:17` 均声明回流"写 `SensitiveWord(level=3)` 自动入规则表 + 进程内热加入"。但实读 `sensitive_words.py:140-152`：

```python
def add_violation_word(word, category=None):
    if category and category in _load_event_words():
        _load_event_words()[category].add(w)   # ← 只进"事件级软表"
    else:
        _EVENT_REFLUX_WORDS.add(w)             # ← 只进事件级回流集合
```

硬规则引擎 `check_sensitive`（L281-344）只读 `_load_words()`（L113-125，`@lru_cache(maxsize=1)`，**运行时不可变**）+ 号码正则 + 网址黑名单，**从不读 `_EVENT_REFLUX_WORDS` / `_load_event_words()`**。故"自动入规则表"未落实：被 `moderate` 拦下的违规词，下一轮 `check_sensitive` 依然不拦（仍要再调 LLM 才能拦，声明中的"防重复调 LLM"失效）。

`SensitiveWord` 表的全部消费方（`Grep "SensitiveWord"`）：写 = `guard.py:187`；读 = `pipeline_ext/sensitive.py:94`（`_db_event_words`，产出**软** `sensitive_tags`/"回流词"类别，非拦截）；其余为 models 与测试。**硬规则侧无任何消费者**。

### D10-6 证据（P1 · fail-closed 依赖 `APP_ENV=production`）

`config.py:23` `app_env: str = "development"`（默认非 production）。`dashscope.moderate` 的两处 fail-closed 判定为 `if settings.app_env == "production"`（L161、L185），否则走 `{"pass": True, "reason": "mock"}`（L188-189）。`config.py:228` 的 `_apply_production_safety` 亦以 `app_env != "production"` 提前返回，只校验 JWT/HMAC/mock 三项，**无"护栏 fail-closed 是否生效"的自检或告警**。→ 生产部署漏设 `APP_ENV=production`（或设为 `staging`/`prod`）时，全链路护栏静默退化为"仅规则层"，无任何启动期信号。

### D10-7 证据（P1 · App 内图片审核覆盖盲区）

`Grep "image_audit"`：仅 `content_safety.py:138`（适配器内）与 `wechat/service.py:202`（微信图片，L255-260 命中即 `status="sensitive"` 不入镜像）。
`Grep "tencent_ci"`：`pipeline_photo.py:70` 只用 `image_detect_label`（**打标**，写 `extra.ci_tags`），**无 `image_audit` 调用**。

`photo_content.py:12-13` 的注释称"分片路径 False，**护栏由管线 CI 审核覆盖**"，但 `pipeline_photo.py:50-100` 实际只有 caption / 打标 / AI 描述 / 逆地理，无图片审核。→ **App 内上传（multipart `/contents/upload` 与分片 complete、`POST /contents`）的图片内容全程无审核**，仅微信域图片被审。上架合规（用户生成图片内容审核）缺口。

### D10-8 证据（P1 · 用户可见 LLM 输出未过护栏）

`Grep "chat_text"` 得全部 LLM 文本生成点，逐条判定是否过护栏：

| 生成点 | 输出是否用户可见 | 过护栏？ |
|---|---|---|
| `ai_tagging.py:119` `generate_photo_description` → `content.ai_description` | 是（详情页 AI 描述） | ❌ |
| `llm_ops/event_merge.py:51`（事件归并 → 事件名/描述） | 是（时间轴事件卡） | ❌ |
| `llm_ops/annotate.py:73`（画像枚举值，含 ≤8 字开放新值） | 是（画像页） | ❌ |
| `api/chat.py`（Agent 反代回复） | 是（对话页） | ❌ |
| `dashscope.py:98/105` 改写/路由、`ner.py:107` 实体抽取、`rerank.py:195` 精排 | 否（内部） | 不适用 |
| `dashscope.py:169/191` 护栏自身 | — | 不适用 |

即"生成态"输出（区别于用户原文）全链路无护栏；`echo.py` 的二次护栏只检查**用户原文** `content.text`，不检查生成文案。判定：至少 `ai_description` / 事件名 / 画像开放枚举值应覆盖（标注 `候审`：是否只走规则层需产品拍板成本与延迟）。

### D10-9 证据（P1 · 事件级软敏感 fail-open）

`guard.py:106-124`：`ManagedDetector.available()` 为假 → `pass_=True`（L111）；`except Exception` → `logger.warning(...降级放行...)` + `pass_=True`（L122-124）。`guard.py:138-145` `detect_event_sensitive` 失败返回 `[]`。
消费方 `pipeline_ext/sensitive.py:130-137`：规则未命中且 LLM 返回空 → 不打 `sensitive_tags`，`sensitive_status` 保持"正常"；随后 `echo._is_sensitive`（L152-177）的便宜检查依赖该标记，其 LLM 二次查用的是**硬规则 `moderate`**，不会识别"分手/离世"等**软**话题。→ LLM 故障时，敏感话题内容可被回响/关怀追问**主动提及**（SAF-006/007 的保护目标）。

注：该 fail-open **不是"拒发"路径**（软话题本就不阻断入库，`sensitive_words.py:158-162` 明确设计），故不违反"护栏 fail-safe 拒发"铁律本身；但属本域 fail-open 点，且影响用户可见的主动提及行为，故登记为 P1（`候审`，修复方向需产品对齐）。

### D10-10 证据（P2 · 客户端默认放行）

`client/utils/voice.uts:298-299`：
```uts
const g = d.getJSON('guardrail')
const passed = g != null ? (g.getBoolean('passed') ?? true) : true
```
`guardrail` 字段缺失（老服务端/字段漂移）或 `passed` 取不到时**默认 `true`（放行）**。客户端无护栏判定逻辑（仅消费），故这是唯一的客户端侧 fail-open 默认值。

### D10-11 证据（P2 · 零调用端点/导出 + 口径不统一）

- `Grep "guard/check"`（backend+client+scripts）：仅 `api/asr.py:30/125`（定义）与 `tests/test_asr.py:473-481`（测试）；客户端全仓无调用 → **零生产调用端点**（`client/utils/voice.uts` 只用 `/transcribe` 的 `guardrail`）。判定 `候审`：若为对外契约（客户端发布前预检）则保留，否则可删。
- `Grep "ProviderLiteral"`：仅 `content_safety.py:313` 定义行，全仓 0 引用 → **零引用导出**。
- 口径不统一：护栏拒绝在三处形态各异——`CONTENT_003`（`contents.py:299`，登记表 `errors.py:57` 有）；`reason="guard-unavailable"` 纯字符串（`dashscope.py:163/176/187`，**无对应错误码**，`errors.py` 无 GUARD 族）；`/guard/check` 只回 `passed/reason` 布尔（`asr.py:133`），不产生任何 `ApiError`。同一"护栏不可用"语义在 `content_safety.py:20` 文档中叫"显式失败"、在 `dashscope.py:162` 叫"默认拒发"、在 `wechat/service.py:217` 叫"fail-safe 放行"（语义相反）——**同名术语三种相反处置**，是理解成本与误改风险源。

### D10-12 证据（P2 · ASR 丢弃 `masked_text`）

`api/asr.py:87-118`：`verdict = moderate(result.text)`（L90），响应 `text=result.text`（L93，**原文**），仅取 `verdict["pass"]` / `verdict["reason"]` 组装 `GuardrailVerdict`（L117）。`dashscope.moderate` 在 `action=mask` 时返回 `masked_text`（L165-167/178-180），但 ASR 路径完全忽略 → 转写出的手机号/身份证/银行卡以**未打码原文**返回客户端（打码只在后续 `POST /contents` 的 `create_content` 生效，`contents.py:300-301`）。同域内两处对 `mask` 的处置不一致。

---

## 依赖方向与抽象覆盖度结论

### FF1：本域是否存在反向依赖？

**未发现跨层反向依赖**（无 `core → services`、无 `services → api`）。实际 import 边：

- `api/contents.py:292`、`api/asr.py:26` → `services.external.dashscope`（api→services，正向）
- `services/photo_content.py:175`、`services/external/content_safety.py:125` → `services.external.dashscope`（services 同层，允许）
- `services/llm_ops/base.py:13-14` → `external.dashscope` + `llm_ops.moderate`（正向；`base.py:5-7` 已声明 `llm_ops/moderate.py` 直连 dashscope 为 P0-1 解环的例外）
- `services/llm_ops/moderate.py:26-27` → `external.dashscope` + `llm_ops.guard_managed`（正向，DAG 单向）
- `services/llm_ops/guard.py:26-27` → `db.models` + `llm_ops.base`（正向）
- `services/pipeline_ext/sensitive.py:27-35` → `db.models` + `external.sensitive_words` + `llm_ops.guard`（正向）
- `services/wechat/service.py:31` → `external.content_safety`（正向）

**但存在契约分叉（比反向依赖更隐蔽）**：同名 `moderate` 有两套返回契约——`dashscope.moderate` 带 `action`/`matched`/`categories`/`masked_text`，`llm_ops.moderate`（选择器/托管）只带 `pass`/`reason`/`detector`/`detail`（无 `action`）。D10-1 正是该分叉导致的 fail-open，且分叉无任何类型/测试约束（两者均为裸 `dict`）。

### 新增一类需改几处？

**换护栏厂商（如加"阿里云内容安全"为文本护栏 / 私有化自部署）——需改 5 处**
1. `external/content_safety.py:289-294` `_ADAPTERS` 注册表
2. `external/content_safety.py:313` `ProviderLiteral`（当前已零引用，形同虚设）
3. `core/config.py:140` `content_safety_provider` 的 `Literal[...]` 白名单
4. 4 个绕过选择器的调用点（D10-3）——若希望新厂商覆盖入库主链，须同时改 `api/asr.py:26`、`api/contents.py:292`、`services/photo_content.py:175`、`services/external/content_safety.py:125`
5. `core/errors.py:36-121` 登记表（若需新错误码）+ `core/ratelimit.py:40-46` `_DOMAIN_SCOPES`（若新增路由前缀）

**新增一类违规类型（如"涉毒"）——需改 4 处（硬规则）+ 3 处（软事件级）**
- 硬规则：`sensitive_words.py:40-46` `_CATEGORY_FILES`、`:49-55` `_CATEGORY_ACTION`、`:32-37` `_FALLBACK_WORDS`、`backend/data/sensitive/<新词表>.txt`
- 软事件级：`sensitive_words.py:62-70` `_EVENT_CATEGORY_FILES`、`:73-81` `_EVENT_FALLBACK_WORDS`、`llm_ops/guard.py:32` `EVENT_CATEGORIES`（**与上一项靠注释"对齐"，无代码/测试校验**）+ `:43-51` `_CATEGORY_SEED_WORDS`
- 若新类型需用户可见文案/错误码：`core/errors.py`

### 重复逻辑清单（同语义多实现）

| 语义 | 位置 A | 位置 B | 备注 |
|---|---|---|---|
| 拦截判定词表 | `dashscope.py:29` `_BLOCK_MARKERS` | `guard_managed.py:40` `_BLOCK_MARKERS` | 注释自认"同语义"，逐字相同 |
| 护栏模型名 | `dashscope.py:24` `QWEN_GUARD="qwen-flash"` | `guard_managed.py:36` `_MANAGED_MODEL="qwen-flash"` | 硬编码双份 |
| 护栏 system prompt | `dashscope.py:46-49` `_GUARD_SYSTEM` | `guard_managed.py:96-98` 内联 prompt | 文案几乎逐字相同 |
| 托管优先+chat 兜底策略 | `llm_ops/moderate.py:32-43` | `guard_managed.py:143-159` | D10-4，同语义双实现 |
| 规则层文本预检 | `dashscope.py:150-155`（内联 `check_sensitive`→reject 即拦） | `content_safety.py:66-76` `_rule_check_text` | 同语义（reject 拦截、mask 放行/打码） |
| 事件类别常量 | `sensitive_words.py:62-70`（文件键） | `llm_ops/guard.py:32` `EVENT_CATEGORIES` | 靠注释对齐，无校验 |

### 死码 / 零调用导出（Grep 已证）

| 符号 | 搜索式 | 结果 |
|---|---|---|
| `moderate_managed` | `Grep "moderate_managed"` backend+client+scripts | 生产 0 命中；仅 2 个测试文件 |
| `/api/v1/guard/check` | `Grep "guard/check"` backend+client+scripts | 生产 0 调用方；仅 `tests/test_asr.py` |
| `ProviderLiteral` | `Grep "ProviderLiteral"` 全仓 | 0 命中（仅定义行 `content_safety.py:313`） |
| `RuleContentSafety` | `Grep "RuleContentSafety"` | 生产仅注册表 `_ADAPTERS["rule"]`（L291），而 `config.content_safety_provider` 的 Literal **不含** `"rule"` → 生产不可达，仅测试经显式 `provider="rule"` 触达 |
| `SensitiveWord` 硬规则消费方 | `Grep "SensitiveWord"` | 0（仅软标签读取 + 写入 + models + 测试）→ 见 D10-5 |

### 异常 / 事务 / 软删口径一致性

- **事务**：`guard.reflow_violation_words`（L166-198）显式用独立 `SessionLocal` + 每词 `begin_nested()` SAVEPOINT，注释说明"回流词必须中途落库、不受外层回滚影响"——口径清晰且与调用方（`photo_content.reflow_violation` 吞异常 L99-104、`contents.py:298` 直调）一致；`db` 参数仅占位（已注释），属可接受的签名兼容。**唯一不一致**：`contents.py:298` 的 `reflow_violation` 调用**无异常保护**，而 `photo_content.reflow_violation`（L93-104）内层已 try/except —— 同一函数的两个调用点保护级别不同（`contents` 路径若回流抛错会 500 掉整条入库）。
- **软删**：本域不涉及软删过滤；`pipeline_ext/sensitive.py:58/73` 的 `Content.deleted_at.is_(None)` 过滤正确（`_mention_count` / `_downgrade_existing` 均排除已删内容）。
- **幂等**：`reflow_violation_words` 靠 `(word, user_id)` 存在性查询 + SAVEPOINT 实现幂等（测试 `test_event_sensitive.py:159-167` 覆盖）；`content_safety` 适配器每次 `get_content_safety()` 新建轻量实例（无状态，可测试切换）——均一致。
- **错误码**：见 D10-11（护栏族无登记码 + 同名术语三种相反处置）。

---

## 本域已查无问题项（避免遗漏被误读）

1. **`llm_ops/parsing.py` 统一解析已真正收口**：`Grep "extract_json_object|extract_json_array"` 得 5 个消费方（`ai_tagging.py:31/153`、`ner.py:105/108`、`annotate.py:17/74`、`event_merge.py:17/52`、`rerank.py:27/147`），全部走统一入口；`rerank.py:119` 的 `json.loads` 属**额外打捞路径**（`_salvage_blocks`，处理截断/全角/散文混入），非重复实现，且 docstring L140-145 明确保留"字段级清洗在调用方"。
2. **规则层无外部调用、零费用、所有模式生效**：`check_sensitive`（L281）纯本地（词表 + 正则 + 域名集合），`dashscope.moderate` 第一级预检在 mock/真实模式均先执行（L149-155）→ 无"mock 模式完全裸奔"问题。
3. **`content_safety` 适配器抽象完整**：4 实现（off/rule/tencent_ci/aliyun）+ `_ADAPTERS` 注册表 + 未知 provider 回退告警（L307-309），新增实现确为"加一个类 + 一行注册"；阿里云签名/解析实现完整（L154-282）且 docstring 明示"未实网验证"。
4. **`_mask_normalized_in_original`（L235-278）已修复全角/空格变体绕过**：归一化命中词按字符映射回原文打码，非 `str.replace`；`check_sensitive` L290-293 注释说明"网址分支不提前 return、reject 恒优先于 mask"（防旁路）——两处历史缺陷均已闭合。
5. **微信域降级为拍板结果而非遗漏**：`content_safety.py:16-20` 记录 2026-08-26 拍板"微信链路可靠性优先（M3 不丢消息 99.9%）→ 审核故障告警+放行"，`wechat/service.py:14/217` 与之对齐；`check_image` 的 CI 异常放行（L144-146）同款并有注释。**属有意设计**，但请见 D10-11 的术语冲突（"fail-safe 放行"与铁律"fail-safe 拒发"同名反义）。
6. **客户端无护栏逻辑**：`client/utils/sentry.uts`（信封协议上报，DSN 公开非密钥，注释 L8-9 明示"绝不携带任何密钥"）、`voice.uts`（仅消费 `guardrail`）——符合本域"客户端仅观测侧"的预期；未发现客户端硬编码敏感词表或重复护栏实现。
7. **`echo.py` 双查顺序合理**：画像级 L1（`_profile_hit`）→ 入库标记（`sensitive_status`）→ 出包前 LLM 二次查（L170-176），且 `get_today_echo` 用 `llm_checked` 把 LLM 调用限制为 ≤1 次（L246-255）——成本与保护平衡，无 N+1。
8. **无密钥外泄**：本域所有外部 key 均经 `settings`（`config.py`）读取，无硬编码；`_ensure_api_key`（`dashscope.py:57-68`）显式同步而非打印 key。
