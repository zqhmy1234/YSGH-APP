# Wave 4 · Agent L（M3 微信域）任务卡——docs/parallel-dev/12

## Mission
完成 M3 微信 2 项：code2session 真实接入（替换 mock）、用户内容合规审核接入（上架前阿里云内容安全，接口预留+当前腾讯 CI 顶替）。

## Context（先读）
1. `docs/parallel-dev/00_总纲_harness.md`（必读）+ `13_集成规则`。
2. audit 证据：`audit_B4_sync.md` §9——code2session 恒 mock（auth.py:153-161 `_wechat_configured()` 恒 False + TODO(T1)、_wechat_code2session 501、登录走 mock unionid）；微信回调验签/幂等/只收找/软删/文本敏感排除已完成（S4-01，勿重做）；`audit_B5b_B5c_B5e.md` B5b #8——阿里云内容安全未接（现腾讯 CI image_audit + moderate 顶替）。
3. 现状：`backend/app/api/auth.py`、`backend/app/services/wechat/`（crypto/signature/gateway/service）、`backend/app/services/external/tencent_ci.py`（image_audit 已接）、`backend/tests/test_wechat.py`（11 项）、`test_auth.py`。

## Scope（可改）
1. `backend/app/api/auth.py`（code2session 接入，仅此文件）
2. `backend/app/core/config.py`（WECHAT_APPID/SECRET/企微三件套配置读取——只加配置字段）
3. **新文件** `backend/app/services/external/content_safety.py`（阿里云内容安全适配器：文本/图片审核，可插拔接口——规则/腾讯 CI/阿里云三实现）
4. `backend/app/services/wechat/service.py`（入库时内容安全调用点，仅加调用）
5. `backend/tests/test_auth.py`、新建 `test_content_safety.py`

## 绝不碰（只读）
dashscope.py、pipeline.py、models.py/migrations/（wechat_messages 表已够）、client/、feature_list.json、progress.md、docs/parallel-dev/。

## TODO 清单
1. **code2session 真实接入**：auth.py `_wechat_code2session` 实现（微信开放平台 code2session 接口，jscode2session），配置就绪后替换 mock unionid；未配置时保持 503/501 语义（不静默降级 mock 登录）。
2. **阿里云内容安全适配器**：content_safety.py——接口抽象（check_text/check_image）+ 阿里云实现（上架前启用）；当前保持腾讯 CI image_audit + moderate 顶替；接入开关 config（content_safety_provider: tencent_ci|aliyun|off）。
3. **入库调用点**：wechat/service.py 入库时调 content_safety（文本/图片），命中 → sensitive_status 标记 + 不进云端镜像（与现有敏感排除合并）。
4. **配置登记**：WECHAT_APPID/SECRET、WECOM_CORP_ID/TOKEN/AES_KEY、阿里云 access key 的 Infisical 登记（skills/infisical-secrets 流程），缺 key 时代码先行。

## Dependencies
- 微信开放平台 appid/secret（M3 里程碑，未到位代码先行 + 现有 mock 沙箱测试保持）
- 阿里云账号（内容安全，上架前；先用腾讯 CI 顶替）

## DoD
1. code2session 逻辑单测通过（mock 微信响应）；配置缺失时行为明确（503 不静默）。
2. content_safety 适配器测试通过（三实现可切换）。
3. 更新 .cowork-temp/audit_B4_sync.md 与 audit_B5b_B5c_B5e.md 状态列。
4. 完成消息：文件清单 + 测试 + 待 key 项（微信四件套/阿里云）。

## Integration
分支 `wave4-agentL`；与 J/K 并行（文件域零重叠）；merge 后全量测试 + 契约更新（auth 端点语义说明）；上架前需真实密钥验证。
