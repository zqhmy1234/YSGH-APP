# 项目 API 密钥清单与获取方式

> 权威来源：`backend/app/core/config.py`（Settings 字段）+ `backend/.env.example`（模板）。
> 本文件回答"项目需要哪些外部 key、在哪申请、怎么配"。任何新 Agent 开工前先查这里。
> 登记教训：2026-08-26（外部凭证未文档化导致多次受阻，lessons.md）。

---

## 0. 总则

1. **存放位置**：`backend/.env`（已 gitignore，不入库）；模板见 `backend/.env.example`。
2. **加载方式**：pydantic-settings 从 `backend/.env` 读取，字段名大小写不敏感（`dashscope_api_key` ↔ `DASHSCOPE_API_KEY`）。
3. **别名**：部分字段支持 Infisical 存量名（见下表"别名"列），两种名字都认。
4. **测试不耗 key**：`MOCK_EXTERNAL_AI=true` 时所有外部 AI 走本地 mock（确定性、零费用）；测试套件统一用此模式。真实通道在生产运行时自动启用（mock 与真实输出同构，切 key 零代码改动）。
5. **获取后验证**：改完 `.env` 后跑 `python scripts/api_smoke_cases.py`（真实链路冒烟）。

## 1. 外部 API Key 清单

| 变量名 | 别名 | 用途 | 获取途径 | 状态 |
|---|---|---|---|---|
| `DASHSCOPE_API_KEY` | — | 阿里云百炼大模型（qwen-flash）：LLM 改写/事件归并裁决/画像标注/L2 精排/护栏 chat 兜底 | 阿里云百炼控制台 → API-KEY 管理（需开通百炼服务） | ✅ 已配置 |
| `DASHSCOPE_WORKSPACE_ID` | — | 百炼业务空间 ID。**`sk-ws-` 工作空间级 key 必须配**（SDK 无环境变量兜底，缺失会 403 Workspace access denied） | 同一控制台 → 工作空间管理 | ✅ 已配置 |
| `DASHSCOPE_REGION` | — | workspace 专属 Host 地域（默认 cn-beijing；完整地址可用 `DASHSCOPE_BASE_URL` 覆盖） | 公开参数 | ✅ 默认值 |
| `DASHSCOPE_BASE_URL` | — | 可选：直接覆盖完整 API Host（优先级最高） | — | 可选 |
| `TENCENT_SECRET_ID` | `TENCENT_CI_SECRET_ID` | 腾讯云子账号密钥：内容安全 CI 审核（图片敏感/人脸标签）+ COS 签名 | 腾讯云控制台 → 访问管理 CAM → 用户 → API 密钥 | ⏳ 未配置（api_smoke 报"腾讯云未配置"） |
| `TENCENT_SECRET_KEY` | `TENCENT_GUANHAIFENG_CI_SECRET_KEY` | 同上 | 同上 | ⏳ 未配置 |
| `COS_BUCKET` | `TENCENT_COS_BUCKET` | 对象存储桶名（照片原件/缩略图/微信媒体） | 腾讯云控制台 → 对象存储 COS → 创建存储桶 | ⏳ 未配置（生产 fake/fs，开通步骤见 docs/COS开通与验证.md） |
| `COS_REGION` | `TENCENT_COS_REGION` | 存储桶地域（如 ap-shanghai） | 同上 | ⏳ 未配置 |
| `TENCENT_APPID` | — | 腾讯云业务标识（非敏感公开参数） | 腾讯云控制台 → 账号信息 | ⏳ 未配置 |
| `TENCENT_STS_ROLE_ARN` | — | 可选：客户端直传 STS 角色（未配自动降级后端中转） | 腾讯云 CAM → 角色 | 可选 |
| `BAIDU_API_KEY` | — | 百度 OCR（图片塔/OCR 备用通道） | 百度智能云控制台 → OCR 服务 | ⏳ 未配置（当前走 Qwen3-VL caption + 本地 OCR） |
| `BAIDU_SECRET_KEY` | — | 同上 | 同上 | ⏳ 未配置 |
| `AMAP_API_KEY` | `AMAP_WEB_API_KEY` | 高德逆地理编码（B3-3，5000 次/日免费） | 高德开放平台 → 应用管理 → 创建应用 → Web 服务 key | ✅ 已配置（逆地理 E2E 验证过） |
| `SENTRY_DSN` | — | 错误监控上报 | Sentry 控制台 → 项目 → 客户端 DSN | ⏳ 未配置 |
| `WECHAT_CORP_ID` | — | 企微回调验签 + 微信图媒体下载（gettoken 的 corpid；`WECHAT_TOKEN` 兼作应用 Secret 传 corpsecret） | 企业微信管理后台 → 我的企业 → 企业 ID；应用 Secret 在 应用管理 → 自建应用 | ⏳ 未配置（沙箱测试凭证见 scripts/wecom_sandbox.py） |
| `WECHAT_TOKEN` | — | 回调签名 Token + 应用 Secret（corpsecret） | 同上 | ⏳ 未配置 |
| `WECHAT_ENCODING_AES_KEY` | — | 回调消息加解密 | 企微后台 → 接收消息设置 | ⏳ 未配置 |
| `JWT_SECRET` | — | 登录令牌签名（≥32 字节；**生产必须改默认值**，否则启动即抛错） | 自行生成（`openssl rand -hex 32`） | ⚠️ dev 默认，生产必改 |

## 2. 本地基础设施（非密钥但必备）

| 项 | 配置 | 说明 |
|---|---|---|
| PostgreSQL | `DATABASE_URL`（默认 localhost:5432/yishu） | 28+ 表；迁移到 head（alembic + schema.sql 双轨） |
| Redis / RQ | `REDIS_URL`（Docker 容器 `yishu-redis` 6379） | 任务队列；Docker Desktop 未启动时 test_queue 等被 deselect |
| Qdrant | `QDRANT_URL`（Docker 容器 `yishu-qdrant` 6333/6334） | 向量库（BGE-M3 混合检索）；未启动时 test_pipeline 部分 deselect |
| 模型资产 | `backend/models/`（setfit-classifier / bge-reranker-* / BGE-M3 缓存） | gitignore 不入库，worktree 需从主仓复制（教训：漏复制 → HFValidationError） |
| 测试照片 | `.cowork-temp/test_photos/`（100 张） | api_smoke 相册链路必需，同样需 worktree 复制 |
| 存储后端 | `STORAGE_BACKEND`（fake/fs/minio/cos） | 生产默认 fake 直到 COS key 到位；测试用 fake 覆盖 |

## 3. 常见问题

1. **"腾讯云未配置：TENCENT_SECRET_ID/TENCENT_SECRET_KEY/COS_BUCKET"**（api_smoke CI 打标警告，非阻断）→ 属正常降级；COS key 到位后自动启用真实审核/存储。
2. **DASHSCOPE 403 Workspace access denied** → 检查 `DASHSCOPE_WORKSPACE_ID`（sk-ws- key 必须带 workspace 头）。
3. **真实验证 vs 测试**：单测/门禁一律 `MOCK_EXTERNAL_AI=true`；真实链路验证用 `.env` 已配 key 直接启动服务（api_smoke 已在真实 key 下跑过：L2 归并 qwen 真实裁决、精排/托管护栏真实调用无异常）。
4. **key 泄露防护**：commit 门禁密钥扫描只查提交文件（.env 已 gitignore 天然豁免）；不要把 key 贴进聊天/日志/代码。
