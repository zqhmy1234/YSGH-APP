# 忆述光华 · OpenAPI 契约（S1-02 交付物）

> 生成：2026-08-16｜来源：FastAPI 应用实时导出（docs/openapi.json）
> 消费方：T2（客户端）/T3（UI）/T4（Windows）——对着契约 + mock server 联调，后端晚到不阻塞（开发规划依赖 #2）

## 使用方式

- **契约文件**：[docs/openapi.json](docs/openapi.json)（每次后端路由变更后重新导出）
- **交互式文档**：启动后端后访问 `http://localhost:8000/docs`（Swagger UI，实时生成）
- **重新导出**：

```bash
cd backend
python -c "import sys; sys.path.insert(0,'.'); from app.main import app; import json; json.dump(app.openapi(), open('../docs/openapi.json','w',encoding='utf-8'), ensure_ascii=False, indent=2)"
```

## 当前接口（39 路径）

### 认证（/api/v1/auth）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /wechat | 微信登录（code→unionid→token 对）| ✅ 真实 DB（code2session 仍 mock）|
| POST | /phone | 手机号验证码登录 | ✅ 真实 DB |
| POST | /sms/send | 发验证码（6 位/5min/60s 防刷）| ✅ 真实 DB（发送走 mock）|
| POST | /refresh | refresh 轮换（吊销校验）| ✅ 真实 DB |

### 内容（/api/v1/contents，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 内容入库（四类素材）+ RQ 入队 + 感知哈希去重 | ✅ 真实 DB |
| POST | /upload | 照片 multipart 中转上传（客户端第一波 B-BE-1：file + meta JSON → storage 存原件 → contents 落库 → 管线入队；复用 409 去重/护栏/类型白名单）| ✅ 真实 DB（2026-08-24）|
| POST | /presign | COS STS 预签名（决策 #10）| ⚠️ mock |
| GET | / | 游标分页列表 | ✅ 真实 DB |

### 事件（/api/v1/events）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /timeline | 时间轴（F8）| ⚠️ mock（M2 实现）|
| POST | /merge /split /confirm | 用户手动操作（B3-5）| ⚠️ mock（M2 实现）|

### 回响（/api/v1/echo，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /today | 去年今日回响（每天 ≤1 条，敏感排除）| 真实 DB |
| POST | /{content_id}/dismiss | 划掉不再出现 | 真实 DB |

### 冷启动访谈（/api/v1/interview，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /questions | 产品部三问（最重要的人/人生转折/最骄傲的事）| 固定 |
| POST | /answers | 提交答案 → 画像维度激活 + 复述确认 | 真实 DB |
| GET | /profile | 画像（冷启动状态）| 真实 DB |

### 同步（/api/v1/sync，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /push | 客户端提交操作批次（字段级 LWW，op_id 幂等，返回权威版本 + 冲突提示）| 真实 DB |
| GET | /pull | 增量拉取（since 游标，变更日志重放）| 真实 DB |
| POST | /reconcile | 端云对账（S5-04：本地快照 vs 云端权威 → 差异报告）| 真实 DB |

### 上传（/api/v1/upload，需 Bearer token，S5-03）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /init | 建分片任务（client_upload_id 幂等，返回 upload_id/chunk_count）| 真实 DB |
| PUT | /chunk | 传单片（幂等 + SHA256 校验，断点续传依据）| 真实 DB |
| POST | /complete | 合并落最终对象（分片未齐拒绝，幂等）| 真实 DB |
| GET | /status | 断点续传状态（已传/缺失分片）| 真实 DB |
| GET | /sts | 客户端直传临时凭证（cos 后端；STS 未就绪降级提示）| 待真验 |

### 检索（/api/v1/search）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 描述性搜索（B2 RAG：dense+sparse RRF + 路由/改写 + 溯源）| ✅ 真实（M1 Part 2 RAG 管线）|
| POST | /image | 以图搜图（B2-4：上传图片 → caption → image_vec 检索）| ✅ 真实（P2-07 生产接线：photo 入库写 image_vec）|

### 分类与裁决（/api/v1/classify，需 Bearer token，F2/B5-c）

> ⚠️ **异步化变更（2026-08-20，P2-01 推理移 worker）**
> 变更前：POST 同步执行 SetFit 推理（单条 ~27s CPU 实测）后直接返回分类结果，API 线程池被占满。
> 变更原因：决策 #9「API 只 enqueue 立即返回」落地——SetFit/BGE-M3 重推理移入 RQ worker 独立进程。
> 变更后：POST 只入队 high 队列并立即返回 `{job_id}`；客户端经 `GET /jobs/{job_id}` 轮询（queued/running/finished/failed）。

| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 文字碎片分类（入队异步，返回 job_id）| ✅ 真实（异步）|
| GET | /jobs/{job_id} | 分类任务状态 + 结果（finished 时带 result）| ✅ 真实 |
| POST | /classify/arbitrate | 三层裁决（个人规则 → 全局 SetFit；入队异步，返回 job_id）| ✅ 真实（异步）|
| GET | /classify/arbitrate/jobs/{job_id} | 裁决任务状态 + 结果 | ✅ 真实 |

> 轮询示例：POST 后每 2-3s 调 GET /jobs/{id}，status=failed 时 error 字段携带失败原因。

### ASR 与护栏（/api/v1/asr 与 /api/v1/guard，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /api/v1/asr/transcribe | 语音转写（双通道 FunASR/SenseVoice + 情绪 + 护栏，F3）| ✅ mock（拿 key 零切换）|
| POST | /api/v1/guard/check | 内容安全护栏（B5b，fail-safe 默认拦截）| ✅ mock（真实模式拦截语义已测）|

> ⚠️ **前缀统一变更（2026-08-20，P2-06）**：变更前 ASR 与护栏共用 `/api/v1` 前缀（`/asr/transcribe`、`/guard/check`）；变更原因：路由前缀风格统一为 `/api/v1/<domain>`（其余 11 个模块同风格），且 guard 独立成域（不属于 ASR 领域）；变更后路径不变（`/api/v1/asr/transcribe`、`/api/v1/guard/check`），仅路由定义拆分。

> ASR 入参：multipart `file`（wav 16kHz 16bit 单声道，≤8MB）+ `preferred`（auto/funasr/sensevoice/mock）。
> 响应含 `channel`（funasr/sensevoice/mock）、`emotion`（开心/难过/生气/惊讶/恐惧/厌恶/平静）、`guardrail.passed`（false=拦截不可下发）。

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
| GET | /healthz | 健康检查 |
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

## 契约演进规则

- 契约变更必须同步：①改 Pydantic schema → ②重导出 openapi.json → ③通知消费方（T2/T3/T4）
- 向后兼容：新增字段允许；删除/重命名字段需版本协商
- 未实现端点保持 mock 响应（`EVENT_099` 等明确错误码），消费方联调不受阻
- **接口语义变更（2026-08-20）**：classify/arbitrate 由同步改异步（见「分类与裁决」节）——消费方需适配 job_id 轮询模式；搜索保持同步（P95<3s 门禁）但后端有并发上限（信号量 4），超限时请求排队
