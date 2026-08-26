# 忆述光华 · OpenAPI 契约（S1-02 交付物 · 核对式）

> 生成本文件：2026-08-26｜来源：FastAPI 应用实时导出（docs/openapi.json）
> 消费方：T2（客户端）/T3（UI）/T4（Windows）——对着契约 + mock server 联调，后端晚到不阻塞（开发规划依赖 #2）
>
> **核对式契约**：本文件是"索引 + 核对"而非独立事实源——路径/方法的唯一事实源是 `docs/openapi.json`（由 `app.main` 实时导出）。改动后端后按下方「重新导出」重导，再对照「路径数核对门禁」校验。

## 使用方式

- **契约文件**：[docs/openapi.json](docs/openapi.json)（每次后端路由变更后重新导出，与代码实时一致）
- **交互式文档**：启动后端后访问 `http://localhost:8000/docs`（Swagger UI，实时生成）
- **重新导出**：

```bash
cd backend
python -c "import sys; sys.path.insert(0,'.'); from app.main import app; import json; json.dump(app.openapi(), open('../docs/openapi.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

## 路径数核对门禁（P1-A 新增）

路由变更后必须过此门禁，防止本文件与契约漂移：

1. 重导 `docs/openapi.json`（上方命令）。
2. 统计路径数：`python -c "import json; j=json.load(open('docs/openapi.json',encoding='utf-8')); print(len(j['paths']))"`
3. 把本文「当前接口」的**路径数**改为该输出值，并核对各域表格路径与 `j['paths']` 一致。
4. 事件域/上传域等状态列变更时同步更新本文件。

> 当前门禁值：**45 路径**（2026-08-26 实时导出核对）。历史沿革：39（2026-08-16）→ 45（2026-08-26，新增 profile/sensitive、thumbnails、contents/{id}/events、events/{id}/items|cover、upload/sts 等；同时契约收敛，无独立 /presign 路径）。

## 当前接口（45 路径）

### 认证（/api/v1/auth）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /wechat | 微信登录（code→unionid→token 对）| ✅ 真实 DB + 真实 code2session（Wave4-L；WECHAT_APPID/SECRET 未配时 mock/501 语义）|
| POST | /phone | 手机号验证码登录 | ✅ 真实 DB |
| POST | /sms/send | 发验证码（6 位/5min/60s 防刷）| ✅ 真实 DB（发送走 mock）|
| POST | /refresh | refresh 轮换（吊销校验）| ✅ 真实 DB |

### 内容（/api/v1/contents，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 内容入库（四类素材）+ RQ 入队 + 感知哈希去重 | ✅ 真实 DB |
| POST | /upload | 照片 multipart 中转上传（file + meta JSON → storage 存原件 → contents 落库 → 管线入队）| ✅ 真实 DB |
| GET | / | 游标分页列表 | ✅ 真实 DB |

> ⚠️ 契约收敛（2026-08-26 核对）：原文档的 `POST /contents/presign` 已不在契约中——预签名/直传收敛到 `upload/sts`（见上传域）；上传统一走 `/contents/upload` 中转或 `/upload/*` 分片协议。

### 内容↔事件反向入口（/api/v1/contents/{content_id}/events，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /{content_id}/events | 照片→事件反向入口（B3-4，Wave2 AgentE）| ✅ 真实 DB |

### 画像级敏感（/api/v1/profile/sensitive，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 新增画像级敏感话题（B5b FIX-4）| ✅ 真实 DB |
| DELETE | / | 删除敏感话题 | ✅ 真实 DB |
| GET | / | 查询敏感话题列表 | ✅ 真实 DB |

### 缩略图（/api/v1/thumbnails/{content_id}，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /{content_id} | 缩略图（B4，Wave3 AgentG）| ✅ 真实 DB |

### 事件（/api/v1/events，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /timeline | 时间轴（F8；level/status/pending 过滤；L3 附 lifecycle）| ✅ 真实 DB |
| POST | /sync | 端侧 L1 事件批量提交（client_event_id 幂等 + 归属校验）| ✅ 真实 DB |
| GET | /{event_id}/items | 事件成员明细（split 选片前置）| ✅ 真实 DB |
| PUT | /{event_id}/cover | 手动换封面（B3-4；cover 必须是事件成员）| ✅ 真实 DB |
| POST | /merge | 用户手动合并（B3-5：source 并入 target，算法不覆盖）| ✅ 真实 DB |
| POST | /split | 用户手动拆分（拆出内容建独立事件）| ✅ 真实 DB |
| POST | /confirm | 用户确认（置信度<0.7 转正；用户背书后不再改动）| ✅ 真实 DB |

### 回响（/api/v1/echo，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /today | 去年今日回响（每天 ≤1 条，敏感排除）| ✅ 真实 DB |
| POST | /{content_id}/dismiss | 划掉不再出现 | ✅ 真实 DB |

### 冷启动访谈（/api/v1/interview，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /questions | 产品部三问（最重要的人/人生转折/最骄傲的事）| 固定 |
| POST | /answers | 提交答案 → 画像维度激活 + 复述确认（dimensions 为 {dim: [当前值]} dict）| ✅ 真实 DB |
| GET | /profile | 画像（冷启动状态；dimensions 同 dict 格式）| ✅ 真实 DB |

### 同步（/api/v1/sync，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /push | 客户端提交操作批次（字段级 LWW，op_id 幂等，返回权威版本 + 冲突提示）| ✅ 真实 DB |
| GET | /pull | 增量拉取（since 游标，变更日志重放）| ✅ 真实 DB |
| POST | /reconcile | 端云对账（S5-04：本地快照 vs 云端权威 → 差异报告）| ✅ 真实 DB |

### 上传（/api/v1/upload，需 Bearer token，S5-03）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /init | 建分片任务（client_upload_id 幂等，返回 upload_id/chunk_count）| ✅ 真实 DB |
| PUT/POST | /chunk | 传单片（幂等 + SHA256 校验，断点续传依据）| ✅ 真实 DB |
| POST | /complete | 合并落最终对象（分片未齐拒绝，幂等）；meta.content_type=voice 时直接建 voice 内容 | ✅ 真实 DB |
| GET | /status | 断点续传状态（已传/缺失分片）| ✅ 真实 DB |
| GET | /sts | 客户端直传临时凭证（cos 后端；STS 未就绪降级提示）| 待真验 |

### 检索（/api/v1/search）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 描述性搜索（B2 RAG：dense+sparse RRF + 路由/改写 + 溯源）| ✅ 真实（M1 Part 2 RAG 管线）|
| POST | /image | 以图搜图（B2-4：上传图片 → caption → image_vec 检索）| ✅ 真实（P2-07：photo 入库写 image_vec）|

### 分类与裁决（/api/v1/classify，需 Bearer token，F2/B5-c）

> ⚠️ **异步化契约（2026-08-20，P2-01 推理移 worker）**：POST 只入队立即返回 `{job_id}`；客户端经 `GET /jobs/{job_id}` 轮询（queued/running/finished/failed）。搜索保持同步（P95<3s 门禁，信号量 4）。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 文字碎片分类（入队异步，返回 job_id）| ✅ 真实（异步）|
| GET | /jobs/{job_id} | 分类任务状态 + 结果（finished 时带 result）| ✅ 真实 |
| POST | /arbitrate | 三层裁决（个人规则 → 全局 SetFit；入队异步）| ✅ 真实（异步）|
| GET | /arbitrate/jobs/{job_id} | 裁决任务状态 + 结果 | ✅ 真实 |

### ASR 与护栏（/api/v1/asr 与 /api/v1/guard，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /api/v1/asr/transcribe | 语音转写（双通道 FunASR/SenseVoice + 情绪 + 护栏，F3）| ✅ mock（拿 key 零切换，真实通道已接线）|
| POST | /api/v1/guard/check | 内容安全护栏（B5b，fail-safe 默认拦截）| ✅ mock（真实拦截语义已测）|

> ASR 入参：multipart `file`（wav 16kHz 16bit 单声道，≤8MB）+ `preferred`（auto/funasr/sensevoice/mock）。
> 响应含 `channel`（funasr/sensevoice/local_vad/mock）、`emotion`（开心/难过/生气/惊讶/恐惧/厌恶/平静）、
> `emotion_bonus`（笑声等正向音频事件情绪加分，P1-A 起客户端 AsrResult 已消费）、
> `guardrail.passed`（false=拦截不可下发）。

### 纠错（/api/v1/corrections，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 分类纠错（new_label 覆盖 SetFit 结果）| ✅ 真实 DB |

### 企微回调（/api/v1/wechat，F6）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /callback | URL 验证（echostr 验签解密回显）| ✅ 协议 1:1（沙箱测试凭证）|
| POST | /callback | 收包（text/image/voice 验签解密入库，msg_id 幂等）| ✅ 协议 1:1 |
| POST | /delete | 微信端软删除本条（msg_id）| ✅ 真实 DB |
| POST | /find | 微信"找"（S4-02：消息解析→RAG 搜索→回复，沙箱可测 10s/3s）| ✅ 真实 DB |

### 消息中心（/api/v1/messages，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | / | 消息列表（游标分页 + status 过滤）| ✅ 真实 DB |
| POST | /{msg_id}/read | 单条已读（幂等/越权 404）| ✅ 真实 DB |
| POST | /read-all | 全部已读 | ✅ 真实 DB |

### 元
| 方法 | 路径 | 说明 |
|---|---|---|
| GET | /healthz | 健康检查（含 env + mock_external_ai）|
| GET | /docs | Swagger UI |

## 认证示例

```bash
# 1. 登录拿 token
curl -X POST http://localhost:8000/api/v1/auth/wechat \
  -H "Content-Type: application/json" \
  -d '{"code":"test-code","device_id":"dev-001"}'
# → data.access_token

# 2. 带 token 建内容
curl -X POST http://localhost:8000/api/v1/contents \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <access_token>" \
  -d '{"content_type":"text","text":"明天买咖啡豆","source":"app"}'
```

## 错误信封（P1-A 统一）

所有错误响应统一信封 `{code, message, request_id, details}`：

- 业务错误：`ApiError` → `code`=业务码（AUTH_001 等），`http` 语义见 `backend/app/core/errors.py` 登记表
- 参数校验失败：422 → `code=VALIDATION_ERROR`，`message`=首个校验错误（detail[0].msg）
- 未处理异常：500 → `code=INTERNAL_ERROR`，对外只给脱敏 message（堆栈只进日志/Sentry，不泄漏）

## 契约演进规则

- 契约变更必须同步：①改 Pydantic schema → ②重导出 openapi.json → ③更新本文件（含路径数）→ ④通知消费方（T2/T3/T4）
- 向后兼容：新增字段允许；删除/重命名字段需版本协商
- 未实现端点保持 mock 响应（明确错误码），消费方联调不受阻
- **接口语义变更（2026-08-20）**：classify/arbitrate 由同步改异步（job_id 轮询模式）；搜索保持同步（P95<3s 门禁）但后端有并发上限（信号量 4）
- **事件域已全部真实 DB（2026-08-26）**：timeline/sync/merge/split/confirm/items/cover 均为真实读写，不再 mock
