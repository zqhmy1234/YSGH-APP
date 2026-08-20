# 腾讯云 CAM 子账号 + COS + STS 角色 · 配置项获取指南

> 配套文档：[忆述光华_外部API申请操作手册.md](./忆述光华_外部API申请操作手册.md) + [_核心结论速查.md](./_核心结论速查.md)
> 适用阶段：M0（账号申请）→ M2（后端接 STS 直传）
> 本文 5 个配置项均非敏感，业务标识进 `.env`，密钥（SecretId/SecretKey）必须进 Infisical

---

## 你手里已有的
- ✅ 子账号 SecretId / SecretKey（已存密钥库）

## 还差 5 个配置项（按下面步骤一一拿到）

---

### 1. `TENCENT_APPID` —— 腾讯云账号唯一 ID

**是什么**：API 调用时签名所需的"账号标识"，会拼到资源路径里（如 `yishu-photos-{APPID}.cos.ap-guangzhou.myqcloud.com`）。

**怎么拿**（10 秒）：
1. 登录 [腾讯云控制台](https://console.cloud.tencent.com)
2. 右上角**账号头像** →「账号信息」→ 弹出面板里就能看到「APPID」
3. 复制纯数字（如 `1300000000`），不带任何前缀

> 注意：APPID 本身**不是密钥**（公开信息），但建议进 `.env`，不放聊天。

---

### 2. `TENCENT_COS_BUCKET` —— 存储桶完整名称

**是什么**：你之前创建的 `yishu-photos` 桶的完整名称（含 APPID 后缀），如 `yishu-photos-1300000000`。

**怎么拿**（20 秒）：
1. 打开 [COS 控制台](https://console.cloud.tencent.com/cos5)
2. 左侧「存储桶列表」
3. 在你的桶（如 `yishu-photos`）的**列表行**上直接看完整名称，格式 `{桶名}-{APPID}`
4. 也可以点桶名进入 →「概览」页顶部看

> ⚠️ 桶名带 APPID 后缀**是腾讯云 COS 强制的**，不是你起的名字就叫 `yishu-photos`，实际可能是 `yishu-photos-1300000000`。这就是为什么你需要先拿到 APPID 才能确定桶名。

---

### 3. `TENCENT_COS_REGION` —— 桶所在地域

**是什么**：桶所在的物理区域，如 `ap-guangzhou`（广州）。所有 COS API 请求和腾讯云 SDK 都要指定这个。

**怎么拿**（10 秒）：
1. 在 [COS 控制台 存储桶列表](https://console.cloud.tencent.com/cos5/bucket)
2. 看你的桶那行的「所属地域」列（如"广州"）
3. 映射成腾讯云 API 用的英文代号：

| 控制台显示 | API 用的 Region |
|---|---|
| 广州 | `ap-guangzhou` |
| 北京 | `ap-beijing` |
| 上海 | `ap-shanghai` |
| 成都 | `ap-chengdu` |
| 中国香港 | `ap-hongkong` |
| 新加坡 | `ap-singapore` |

> 记小技巧：地域前面统一是 `ap-`，后面取拼音/缩写。如果忘了，在 [COS 地域列表](https://cloud.tencent.com/document/product/436/6224) 查。

---

### 4. `TENCENT_STS_ROLE_ARN` —— 给客户端签发临时凭证的角色 ARN

**是什么**：后端用子账号密钥调 `AssumeRole` 时告诉腾讯云"以哪个角色的身份发临时凭证"。格式固定：
```
qcs::cam::uin/{主账号Uin}:roleName/{角色名}
```

**怎么拿**（首次需新建，约 5 分钟）：

#### 第 1 步：拿到主账号 Uin

1. CAM 控制台 → [用户列表](https://console.cloud.tencent.com/cam)
2. 左侧「**用户**」→「用户列表」
3. 在你的**子用户**行上，看「**主账号Uin**」一栏（**注意不是子用户自己的 ID**）
4. 或者：右上角账号头像 →「账号信息」→ 主账号 UIN

> ⚠️ 经常踩的坑：把**子用户的 UIN** 填进去了。角色 ARN 里 `uin/` 后面是**主账号** UIN，不是子账号。

#### 第 2 步：新建角色（CAM 控制台）

1. CAM 控制台 → [角色](https://console.cloud.tencent.com/cam/role)
2. 左上「**新建角色**」
3. 角色类型选「**腾讯云账户**」（注意：不是"用户"，是"账户"，这两个入口不一样）
4. 受信腾讯云账户选「**当前账户**」（子账号调 STS，绑的是当前主账号的 ARN；如要从其他账号调可填对应账号 UIN）
5. 勾选「**允许以下用户调用此角色**」→ 选你刚创建的子用户 → 完成
6. **角色名称**填 `yishu-cos-sts`（或你喜欢的英文名）

#### 第 3 步：给角色绑策略（关键，决定客户端能干啥）

新建角色默认没任何权限，需要手绑策略。两种方案选一：

**方案 A：简单起步（推荐 MVP）**
- 在「权限」标签 →「关联策略」→ 选 `QcloudCOSDataWriteOnlyAccess`（只写 COS 数据，不能删/读列表）

**方案 B：精确最小权限（推荐正式上线）**
- 「关联策略」→ 「**新建自定义策略**」→ 选"按策略语法创建"
- 策略语法（把下面的 `{APPID}` 和 `{BucketName}` 替换成你的真实值）：

```json
{
  "version": "2.0",
  "statement": [
    {
      "effect": "allow",
      "action": [
        "cos:PutObject",
        "cos:PostObject",
        "cos:InitiateMultipartUpload",
        "cos:ListMultipartUploads",
        "cos:ListParts",
        "cos:UploadPart",
        "cos:CompleteMultipartUpload",
        "cos:AbortMultipartUpload"
      ],
      "resource": [
        "qcs::cos:ap-guangzhou:uid/{APPID}:{BucketName}-{APPID}/*"
      ]
    }
  ]
}
```

> 这套策略只允许**上传**（含分片上传的全流程），不能读、不能删、不能列出桶，泄露顶多被乱传对象但拿不到现有数据。

#### 第 4 步：拿到角色 ARN

1. 回到 [角色列表](https://console.cloud.tencent.com/cam/role)
2. 点你刚创建的角色（如 `yishu-cos-sts`）进入详情
3. 顶部「**角色信息**」里就有 **RoleId**（格式 `qcs::cam::uin/100000xxxxxx:roleName/yishu-cos-sts`）
4. 完整复制到 `TENCENT_STS_ROLE_ARN`

---

### 5. `TENCENT_STS_EXTERNAL_ID` —— 可选，99% 不用填

**是什么**：跨账号 AssumeRole 时的额外密码。本项目**当前账户给当前账户签发**，**留空即可**。

> 只有一种情况要填：未来接入企业认证后，从另一个腾讯云账号（前端/移动 App 用的账号）调 STS 时才需要 ExternalId。MVP 阶段直接留空。

---

## 最终交付物

把以下 5 项填进后端 `backend/.env`：

```bash
# 已存密钥库的（不在 .env 里明文，从密钥库加载）
# TENCENT_SECRET_ID=...        # Infisical: tencent-cam-yishu / secret_id
# TENCENT_SECRET_KEY=...        # Infisical: tencent-cam-yishu / secret_key

# 业务标识（明文，进 .env）
TENCENT_APPID=1300000000
TENCENT_COS_BUCKET=yishu-photos-1300000000
TENCENT_COS_REGION=ap-guangzhou

# STS（如果走 STS 方案）
TENCENT_STS_ROLE_ARN=qcs::cam::uin/100000xxxxxx:roleName/yishu-cos-sts
TENCENT_STS_EXTERNAL_ID=
```

## 自检清单

- [ ] `TENCENT_APPID` —— 账号信息页拿到纯数字
- [ ] `TENCENT_COS_BUCKET` —— COS 桶名带 APPID 后缀
- [ ] `TENCENT_COS_REGION` —— `ap-` 前缀的英文代号
- [ ] `TENCENT_STS_ROLE_ARN` —— `qcs::cam::uin/{主账号Uin}:roleName/...` 格式（**注意是主账号Uin不是子账号Uin**）
- [ ] 角色策略已绑（至少 `QcloudCOSDataWriteOnlyAccess`）
- [ ] 后端 .env 模板已加这 5 项变量名（值从密钥库 / .env 加载）

---

## 引用文档

- [腾讯云账号信息](https://console.cloud.tencent.com/developer) → APPID
- [CAM 控制台](https://console.cloud.tencent.com/cam) → 用户列表 / 角色
- [COS 控制台](https://console.cloud.tencent.com/cos5/bucket) → 桶列表 / 地域
- [STS 文档](https://cloud.tencent.com/document/product/598/40956) → AssumeRole 参数
- [角色策略语法](https://cloud.tencent.com/document/product/598/10603) → resource 格式
- [COS 地域列表](https://cloud.tencent.com/document/product/436/6224) → Region 代号

---

最后修改：2026-08-18 17:20 GMT+8（基于腾讯云官方文档截图）