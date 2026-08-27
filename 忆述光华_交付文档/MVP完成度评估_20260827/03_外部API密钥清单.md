# 03 · 团队需提供的外部 API Key 清单

> 2026-08-27｜权威来源：backend/app/core/config.py + backend/.env.example + docs/项目API密钥清单与获取.md
> 存放：backend/.env（已 gitignore）；加载：pydantic-settings；别名两种名字都认。

## 1. 必需（当前阻塞 F6 微信 / 登录生产）

| 环境变量 | 别名 | 用途 | 状态 |
|---|---|---|---|
| `WECHAT_CORP_ID` | — | 企微回调验签 + 微信图媒体下载（corpid） | ⏳ **待团队提供**（现沙箱 mock） |
| `WECHAT_TOKEN` | — | 回调签名 Token + 应用 Secret（corpsecret） | ⏳ **待团队提供** |
| `WECHAT_ENCODING_AES_KEY` | — | 回调消息加解密 | ⏳ **待团队提供** |
| `WECHAT_APPID` | — | 微信开放平台 AppID（code2session 登录） | ⏳ **待团队提供**（未配 dev/test mock、生产 501） |
| `WECHAT_SECRET` | — | 微信 AppSecret（code2session） | ⏳ **待团队提供** |

获取途径：企业微信管理后台（我的企业→企业 ID；应用管理→自建应用→Secret；接收消息设置→Token/EncodingAESKey）+ 微信开放平台/小程序（开发管理→AppID/AppSecret）。详见《忆述光华_外部API申请操作手册.md》。

## 2. 上线必需（内测前）

| 环境变量 | 用途 | 状态 |
|---|---|---|
| 短信通道（阿里云短信 AccessKey，TODO T1） | 手机验证码登录真实通道；当前 mock 生产 501 冻结 | ⏳ 待接（0.045 元/条） |
| DCloud uni-push 厂商通道（小米/华为/OPPO AppID/AppKey/AppSecret） | 离线推送（B5d/消息中心）；审核 3-7 天 | ⏳ 待接（先配小米+华为+OPPO） |

## 3. 可选 / 上架前

| 环境变量 | 别名 | 用途 | 状态 |
|---|---|---|---|
| `ALIYUN_ACCESS_KEY_ID` | `ALIYUN_AK_ID` | 阿里云内容安全（Green）用户内容合规审核——上架前必接 | ⏳ 可选（当前 DASHSCOPE moderate + 腾讯 CI image_audit 双覆盖够用） |
| `ALIYUN_ACCESS_KEY_SECRET` | `ALIYUN_AK_SECRET` | 同上 | ⏳ 可选 |

## 4. 已就位（无需团队补交）

| 环境变量 | 用途 | 状态 |
|---|---|---|
| `DASHSCOPE_API_KEY` + `DASHSCOPE_WORKSPACE_ID`（sk-ws- key 必配） | 百炼大模型 qwen-flash/plus、图片塔 Qwen3-VL、托管护栏 | ✅ 已配置（真实 key 跑通） |
| `TENCENT_SECRET_ID`/`TENCENT_SECRET_KEY`（别名 TENCENT_CI_*）+ `COS_BUCKET`/`COS_REGION`/`TENCENT_APPID`/`TENCENT_STS_ROLE_ARN` | 腾讯云子账号：COS 对象存储 + CI 图片标签 + 图片敏感审核 + STS 直传 | ✅ 已配置（本地 dev 用 fs 后端，上生产切 cos） |
| `BAIDU_API_KEY`/`BAIDU_SECRET_KEY`（别名 BAIDU_OCR_*） | 百度 OCR 备用通道 | ✅ 已配置 |
| `AMAP_API_KEY`（别名 AMAP_WEB_API_KEY） | 高德逆地理编码（5000 次/日免费，geohash 缓存） | ✅ 已配置（E2E 验证过） |
| `SENTRY_DSN` | 错误监控上报 | ✅ 已配置 |

## 5. 运行时密钥（非外部，但生产必须处理）

| 环境变量 | 用途 | 状态 |
|---|---|---|
| `JWT_SECRET` | 登录令牌签名（≥32 字节） | ⚠️ dev 默认，生产必改 |
| `REFRESH_TOKEN_HMAC_KEY` | refresh token HMAC 哈希（G1 认证安全） | ⚠️ 生产部署需配独立强随机密钥 |

## 6. 常见问题

1. 腾讯云子账号务必用 CAM 最小权限（QcloudCOSFullAccess + QcloudCIFullAccess + QcloudSTSFullAccess），勿绑 AdministratorAccess。
2. DASHSCOPE 403 Workspace access denied → 检查 DASHSCOPE_WORKSPACE_ID。
3. 测试一律 MOCK_EXTERNAL_AI=true 不耗 key；真实链路用 api_smoke_cases.py 冒烟。
4. key 严禁入聊天/日志/代码；改完 .env 后跑 `python scripts/api_smoke_cases.py` 验证。
