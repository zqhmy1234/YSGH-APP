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

## 当前接口（14 路径）

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
| POST | /presign | COS STS 预签名（决策 #10）| ⚠️ mock |
| GET | / | 游标分页列表 | ✅ 真实 DB |

### 事件（/api/v1/events）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| GET | /timeline | 时间轴（F8）| ⚠️ mock（M2 实现）|
| POST | /merge /split /confirm | 用户手动操作（B3-5）| ⚠️ mock（M2 实现）|

### 检索（/api/v1/search）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | / | 描述性搜索（B2 RAG）| ⚠️ mock（M1 实现）|

### ASR 与护栏（/api/v1，需 Bearer token）
| 方法 | 路径 | 说明 | 状态 |
|---|---|---|---|
| POST | /asr/transcribe | 语音转写（双通道 FunASR/SenseVoice + 情绪 + 护栏，F3）| ✅ mock（拿 key 零切换）|
| POST | /guard/check | 内容安全护栏（B5b，fail-safe 默认拦截）| ✅ mock（真实模式拦拦截语义已测）|

> ASR 入参：multipart `file`（wav 16kHz 16bit 单声道，≤8MB）+ `preferred`（auto/funasr/sensevoice/mock）。
> 响应含 `channel`（funasr/sensevoice/mock）、`emotion`（开心/难过/生气/惊讶/恐惧/厌恶/平静）、`guardrail.passed`（false=拦截不可下发）。

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
