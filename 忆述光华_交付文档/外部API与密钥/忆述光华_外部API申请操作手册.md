# 忆述光华 · 外部 API 申请操作手册（个人实名版）

> 版本：v1｜日期：2026-08-18｜调研：官方文档逐页核实（阿里云帮助中心 / 腾讯云文档 / 百度智能云文档 / DCloud uni-app 文档）
> 适用范围：你负责的 4 项申请——阿里云（百炼+FunASR）、腾讯云（图像识别+COS）、百度（OCR）、DCloud（uni-push+厂商通道）
> 配套：《忆述光华_外部API账号管理方案.md》（账号归属与密钥纪律）《忆述光华_外部API清单与成本.md》（17 项服务与成本）
> ⚠️ 所有平台统一用团队公共邮箱注册（如 yishu.team@163.com），实名用你本人身份证；账号归组织、密钥入台账，不进聊天/文档

---

## 0. 准备工作（10 分钟）

1. 团队公共邮箱 1 个（QQ/163/Outlook 免费，注册所有平台用）。
2. 你的身份证（实名认证用）。
3. 手机支付宝 App（阿里云实名必需，且必须已完成支付宝个人实名）。
4. 手机号（各平台短信验证）。
5. 拿到 App 的 **Android 包名**（如 `com.yishu.guanghua`）和 **签名 SHA1/SHA256 指纹**——DCloud 厂商通道申请必需，向客户端负责人要，没有就先占位后续改。

---

## 1. 阿里云（百炼 qwen-flash / FunASR / 图片塔 / 护栏）✅ 最简单，一次实名全搞定

### 1.1 注册账号（约 5 分钟）
1. 打开 <https://www.aliyun.com> → 右上角「注册」→ <https://register.aliyun.com>。
2. 用**手机号**注册（也可用支付宝/钉钉快捷登录，快捷登录会自动带出实名信息）。
3. 设置密码，登录控制台。

### 1.2 个人实名认证（即时完成）
官方文档：<https://help.aliyun.com/zh/account/verify-your-identity-individual-account>

1. 打开账号中心实名认证页：登录后访问 <https://account.console.aliyun.com/>，进入「账号认证/实名认证」。
2. 在「个人认证」区域，点 **「个人支付宝认证」**（推荐，无需手输姓名身份证）或「个人扫脸认证」。
3. 用**手机支付宝 App** 扫码（二维码 1 小时有效，过期重扫）。
4. 手机支付宝弹出服务授权页 → 阅读协议 → 点「同意」→ 完成，即时生效。
5. 到账号中心概览页确认状态为「个人实名认证-已通过」。

> 注意：实名认证 ≠ 绑定支付方式。后续若购买付费资源，还需在「费用中心」绑定支付方式/充值（百炼是后付费，账户欠费会停服务）。

### 1.3 开通百炼 + 创建 API Key（10 分钟）
官方文档：<https://help.aliyun.com/zh/model-studio/first-api-call-to-qwen>

1. 用主账号打开百炼控制台 <https://bailian.console.aliyun.com/>。
2. 阅读并同意服务协议 → **自动开通**（没弹协议说明已开通）。
3. 若提示「您尚未进行实名认证」→ 先完成 1.2。
4. 创建 API Key：控制台左侧/顶部找 **「API-KEY」** 页 → 「创建我的 API Key」→ 生成后**立即复制保存（只显示一次）**。格式 `sk-xxxx`。
5. 记下**业务空间 ID（WorkspaceId）**：控制台「业务空间管理」页查看（北京/新加坡/东京/法兰克福地域调用时 Base URL 要用）。
6. **FunASR 不用单独开通**：同一把 API Key 直接调用。模型广场搜「Fun-ASR / 语音识别」，录音文件识别用 `fun-asr`（HTTP，12 小时/2GB，支持说话人分离），实时用 `fun-asr-realtime`（WebSocket）。老 paraformer 系列也在同一平台。
7. 免费额度：百炼新用户一般送 100 万 token（90 天）+ 语音类新账号送 10 小时，以控制台实际显示为准。

> 成本速记：qwen-flash 输入 0.2 元/百万 token、输出 1.5 元/百万；FunASR 录音文件识别 ≈0.79 元/小时。100 用户月成本约 65 + 118 元。

---

## 2. 腾讯云（图像识别 + COS 云存储）

### 2.1 注册账号（约 5 分钟）
1. 打开 <https://cloud.tencent.com> → 右上角「注册」。
2. 微信 / QQ / 邮箱任选注册，手机号验证。
3. 登录控制台 <https://console.cloud.tencent.com/>。

### 2.2 个人实名认证（即时完成）
官方文档：<https://cloud.tencent.com/document/product/378/10495>

1. 控制台 → 右上角头像 →「账号信息」→ 实名认证，或直接访问 <https://console.cloud.tencent.com/developer/auth>。
2. 三种方式任选其一（系统会推荐）：
   - **微信扫码认证**（推荐，微信需已绑定本人银行卡）
   - QQ 扫码认证
   - 人脸识别认证
3. 即时完成。

> 限制：1 个身份证最多实名 3 个腾讯云账号；个人实名不能参加企业类活动、不能开增值税专票（不影响 MVP 使用）。

### 2.3 开通 COS 对象存储（照片镜像）
官方文档：<https://cloud.tencent.com/document/product/436/38484>

1. 控制台搜索「对象存储」→ 进入 COS 控制台 <https://console.cloud.tencent.com/cos5> → 按提示**开通服务**。
2. 创建存储桶：「存储桶列表」→「创建存储桶」：
   - 所属地域：选离用户近的，如**广州**
   - 名称：如 `yishu-photos`（设置后不可改）
   - 访问权限：**私有读写**（默认，配合 STS 临时密钥直传）
   - 其他默认 → 创建
3. 创建密钥（后端用）：访问管理 CAM <https://console.cloud.tencent.com/cam/capi> →「API 密钥管理」→「新建密钥」，得到 SecretId/SecretKey，**妥善保管**。
4. 建议：正式环境用 **CAM 子账号密钥**（只授本桶读写权限），客户端走 STS 临时密钥直传（30 秒有效）——按账号管理方案执行，App 里永不内置长期密钥。
5. 免费额度：新用户 50GB 标准存储 + 10GB/月流量，初期足够。

### 2.4 开通图像识别（图片标签）
官方文档：<https://cloud.tencent.com/document/product/865/17630>（TI-A）｜<https://cloud.tencent.com/document/product/460>（CI 数据万象）

1. 控制台搜索「图像识别」→ 进入 <https://console.cloud.tencent.com/tiia> → 按提示**开通服务**。
2. 忆述光华方案里的「图片标签」走**数据万象 CI**（1.5 元/千张，便宜 40%）：控制台搜索「数据万象」→ 开通服务 → 在 [CI 控制台](https://console.cloud.tencent.com/ci) 绑定你的 COS 桶 → **子用户绑 `QcloudCIFullAccess` 权限策略**。
3. ⚠️ **CI 没有「上传即自动打标」开关**——控制台绑桶只是开通服务，**自动打标要由后端实现**：
   - 照片上传 COS 成功 → 触发后端异步任务 → 调 CI 图像标签 API（`POST <BucketName-APPID>.ci.<Region>.myqcloud.com/image/Tagging`，Body: `{"Inputs":[{"Object":"photos/.../abc.jpg"}]}`） → 拿到 Tags 入库
   - CI 数据工作流（[doc/46488](https://cloud.tencent.com/document/product/460/46488)）支持"上传触发"，但**仅列了音视频格式，图片能否走工作流存疑，生产不建议依赖**
   - MVP 唯一稳妥路径 = 后端显式调 API；这一步记到 M2 后端任务
4. 手动测试（确认服务可用）：CI 控制台 → 你的桶 → [智能工具箱](https://console.cloud.tencent.com/ci/bucket) → 选图片试一次标签识别，能跑通就 OK
5. 免费额度：图像分析月 1000 次免费；CI 图像标签按调用计费
6. 调用凭证用 2.3 的同一把 SecretId/SecretKey，无需另建

> 成本速记：图片标签 1.5 元/千张（≈0.0015 元/张），100 用户月成本约 22.5 元（比 TI-A 通用图像标签 2.5 元/千次 省 40%）。

---

## 3. 百度智能云（OCR 高精度版）

### 3.1 注册账号（约 5 分钟）
1. 打开 <https://cloud.baidu.com> → 右上角「免费注册」（可用百度账号直接登录）。
2. 手机号验证，登录控制台 <https://console.bce.baidu.com/>。

### 3.2 个人实名认证
官方文档：<https://cloud.baidu.com/doc/UserGuide/s/8jwvy3c96>（个人刷脸步骤：<https://cloud.baidu.com/doc/UserGuide/s/ql1xbgodl>）

1. 控制台右上角「用户中心」→ 找到「实名认证」模块 → 点「个人认证」。
2. 二选一：
   - **个人刷脸认证**（推荐）：选证件类型（大陆身份证等）→ 填姓名+证件号 → 勾协议 → 提交 → 用**手机百度 / 百度智能云 App / 微信**扫码 → App 内点「开始身份验证」→ 刷脸 → 确认完成。
   - **个人银行卡认证**：填姓名+身份证+本人银行卡 → 银行打款/短信验证。
3. 刷脸失败：换手机或换 App 重试；多次失败改走银行卡认证。

> 注意：个人认证**不能开增值税专票、不能用百度短信服务（SMS）**——OCR 不受影响，MVP 无碍。

### 3.3 开通 OCR 高精度版 + 创建应用
官方文档：<https://cloud.baidu.com/doc/OCR/s/0k3h7y3tb>（计费）、调用方式 <https://cloud.baidu.com/doc/OCR/s/Ck3h7y2ia>

1. 控制台搜索「文字识别」→ 进入 OCR 控制台 <https://console.bce.baidu.com/ai/#/ai/ocr/overview/index>。
2. 在「通用文字识别（高精度版）」卡片点**领取免费额度 / 开通服务**（新用户 500-2000 次/月免费，以页面为准）。
3. 创建应用：OCR 控制台 →「创建应用」→ 填应用名称（如 `忆述光华-后端OCR`）、应用类型（文字识别）→ 创建。
4. 在「应用详情」页拿到 **API Key / Secret Key**（百度 AI 开放平台格式 AK/SK，与百度智能云主账号 AK/SK 是两套，用这个）。
5. 调用地址：`https://aip.baidubce.com/rest/2.0/ocr/v1/accurate_basic`（高精度版接口）。

> 成本速记：高精度版 0.005-0.01 元/次，100 用户月成本约 60 元。
> ⚠️ 隐私红线（团队已约定）：识别图上传百度即用即弃，不存原图；证件等敏感图**不进** OCR 管线。

---

## 4. DCloud（uni-push 2.0 + 厂商通道）⚠️ 最麻烦，建议提前 1-2 周启动

### 4.1 注册 DCloud 开发者账号（5 分钟）
1. 打开开发者中心 <https://dev.dcloud.net.cn> → 注册（手机号 + 验证码）。
2. 账号信息里**绑定手机号**（云服务合规要求，uni-push 首次开通也会要求向个推同步手机号）。
3. DCloud 个人开发者即可，**不需要身份证实名**（HBuilderX 云打包 / uniCloud 用同一账号）。

### 4.2 开通 uni-push 2.0
官方文档：<https://uniapp.dcloud.net.cn/uni-push/open.html>

1. 登录开发者中心 →「我的应用」→「创建应用」（名称如 `忆述光华`），生成 AppID（`__UNI__xxxx`）。
2. 左侧菜单「uni-push」→ 选 **2.0** → 进入配置页（左上角确认当前操作的是你的应用）。
3. **首次开通**：按页面提示验证手机号（向个推同步，DCloud 开发者无需自己注册个推账号）。
4. 填写应用信息：
   - Android 包名、应用签名（SHA1 指纹）——与客户端 manifest.json 证书信息**必须一致**
   - iOS Bundle ID（iOS 暂不做可后补）
5. **关联 uniCloud 服务空间**（uni-push 2.0 必需）：在 HBuilderX 项目里创建 uniCloud 环境，与开发者中心绑定**同一个服务空间**；即使业务服务器不用 uniCloud，推送也要走它。
6. HBuilderX 里：manifest.json → App 模块配置 → 勾选「Push(消息推送)」→ 选 uni-push 2.0。
7. iOS 需要推送时：上传 APNs 推送证书（消息推送 → 配置管理 → 应用配置）。
8. 费用：uni-push 本身免费，仅按 uniCloud 云函数/数据库调用计费，量级可忽略。

### 4.3 厂商通道申请（小米 / 华为 / OPPO，每家用 30-60 分钟 + 审核等待）
官方文档（含全部厂商步骤）：<https://uniapp.dcloud.net.cn/unipush_vendor_config.html>
总原则：**每家厂商注册开发者 → 创建应用（填包名+签名指纹）→ 开通推送 → 拿到 AppID/AppSecret 等 → 回填到 DCloud 开发者中心 uni-push 配置**。

#### 小米（推荐先做，审核最快）
1. 小米开放平台 <http://dev.xiaomi.com/console/> 注册开发者账号（个人开发者，需实名）。
2. 管理控制台 →「消息推送」→ 创建「手机/平板应用」→ 填应用名称 + 包名。
3. 勾选《小米推送接入合作协议》→「启用」。
4. 应用信息页获取：**小米 AppID、AppKey、AppSecret**。
5. 回填 DCloud：uni-push 配置页 → 厂商推送设置 → 小米。

#### 华为（审核最严，提前做）
1. 华为开发者联盟 <https://developer.huawei.com/consumer/cn/> 注册开发者（个人开发者，需实名认证，可能要求身份证+人脸）。
2. 登录 **AppGallery Connect** <https://developer.huawei.com/consumer/cn/service/josp/agc/index.html> →「我的应用」→ 创建应用（填包名）。
3. 项目设置 →「增长」→「推送服务」→「立即开通」。
4. 项目设置 →「常规」→ 填写 **SHA256 证书指纹**（不会填的按官方教程用 keytool 生成）→ 保存；**下载 agconnect-services.json** 备用。
5. 项目设置 →「推送服务」→「配置」→ 开通回执：回调地址填 `https://thirdrcp-hz.getui.com/hw`（杭州机房，可自定义回执名称）。
6. 回填 DCloud：华为 **AppID、AppSecret**、应用包名、agconnect-services.json 文件内容。
7. 应用信息里查看 AppID / SecretKey（AppSecret）。

#### OPPO
1. OPPO 推送平台 <https://push.oppo.com> 注册/登录开发者账号（个人开发者）。
2. 搜索「OPPO 推送服务」→ 创建应用 → 填应用名称 + 包名 + **上传应用图标**。
3. 应用信息获取：**OPPO AppID、AppKey、AppSecret、MasterSecret**。
4. 回填 DCloud。

#### 其余厂商（可后补，非 MVP 阻塞）
- vivo：<https://dev.vivo.com.cn>（推送平台开启指南 doc/281；回执地址 `https://receipt-hz.lmmindex.com/vv`）
- 荣耀：<https://developer.hihonor.com/cn/home>（推送服务申请，需包名+SHA256；回执 `https://thirdrcp-hz.getui.com/ho`）
- 魅族：<http://push.meizu.com>（回执 `https://thirdrcp-hz.getui.com/mz`）
- FCM（海外）：<https://firebase.google.com/>，海外上架才需要，暂不做。

> 行为说明：**没配厂商通道的手机，App 在线时走个推 socket 正常收；App 被杀后收不到离线推送**（自动降级）。MVP 阶段先配小米+华为+OPPO 覆盖绝大多数用户即可，vivo/荣耀/魅族在 M2 接入期补齐。

---

## 5. 申请顺序与排期建议

| 顺序 | 事项 | 耗时 | 说明 |
|---|---|---|---|
| 1 | 4 个平台注册 + 阿里/腾讯/百度实名 | 半天 | 腾讯/百度即时通过；阿里支付宝扫码即时 |
| 2 | 开通百炼 + 建 API Key、开通 COS + 建桶、开通 OCR + 建应用 | 1-2 小时 | 百度 OCR 免费额度领取后生效 |
| 3 | DCloud 注册 + 开通 uni-push 2.0 + 绑服务空间 | 1 小时 | 需 HBuilderX 配合 |
| 4 | 小米/华为/OPPO 开发者注册与审核 | **3-7 天不等** | 华为最慢，**今天就去注册**，审核通过再创建应用回填 |
| 5 | 全部 Key 登记台账 | 30 分钟 | 只记元数据不记值；值进 Infisical/密钥库 |

## 6. 完成标准（自检清单）

- [ ] 阿里云：控制台实名「已通过」；百炼已开通；API Key 已建并保存（sk- 开头）；业务空间 ID 已记录
- [ ] 腾讯云：实名「已通过」；COS 已开通、存储桶已建（私有读写）；CAM 密钥已建；图像识别/数据万象已开通
- [ ] 百度：实名「已通过」；OCR 高精度版已领取免费额度；应用已创建；AK/SK 已保存
- [ ] DCloud：开发者中心账号已注册绑手机；uni-push 2.0 已开通并绑定服务空间；小米/华为/OPPO 已申请并回填（或已提交申请等审核）
- [ ] 台账已登记：谁申请、哪个平台、用途、限额、下次轮换日期
