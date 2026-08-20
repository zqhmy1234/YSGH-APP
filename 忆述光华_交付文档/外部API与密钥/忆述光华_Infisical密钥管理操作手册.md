# 忆述光华 · Infisical 密钥管理操作手册（程序员视角）

> 版本：v1｜日期：2026-08-17｜主角：小海（后端开发）——你入职第一天到日常开发、联调、CI、离职交接，全部场景都在这
> 配套：《忆述光华_外部API账号管理方案.md》（原则与各平台落地）
> 免费档额度（已核实 2026-08）：5 个身份（人和机器都算）、无限项目、每项目 3 环境、密钥共享、2FA；**没有**审计日志/版本回滚/自动轮换（Pro 才有）

---

## 0. 一分钟搞懂 Infisical 是什么

就是一个**放在网上的加密保险箱**：团队所有 API Key 集中存进去，谁要谁从里面取，取的时候有记录（免费档记录有限，见第 9 节）。

关键概念（人话版）：

| 概念 | 人话解释 |
|---|---|
| 组织（Organization） | 公司。整个"忆述光华"一个组织 |
| 项目（Project） | 一个产品/一个代码仓库。如 `yishu-backend` |
| 环境（Environment） | 保险箱里的三个抽屉：dev（本地开发）/ staging（联调测试）/ prod（生产） |
| 密钥（Secret） | 一条条的 Key，比如 `DASHSCOPE_API_KEY` |
| 成员（Member） | 能进保险箱的人 |
| 机器身份（Machine Identity） | 给 CI/服务器用的"机器人账号"，不是人 |

工作方式：你本地跑代码时，Infisical 把密钥**注入成环境变量**——代码里 `os.environ["xxx"]` 直接能读到，但 Key 永远不写进代码和 Git。

---

## 1. 场景一：入职第一天，管理员把我拉进保险箱

**管理员（海峰）做一次（约 5 分钟）：**

1. 浏览器打开 <https://app.infisical.com> → Sign up（用邮箱或 GitHub 账号注册）。
2. 创建组织（Organization），名字填"忆述光华"。
3. 创建项目，名字 `yishu-backend`，套餐选 Free（免费档）。
4. 项目里默认就有 dev / staging / prod 三个环境——免费档正好 3 个，够用。
5. 邀请我：项目页 → **Access Control** → Add Member → 输我的邮箱 → 角色选 **Member**。

**我（小海）这边：**

1. 收邮件 → 点邀请链接 → 注册/登录 Infisical → 点"接受邀请"。
2. 进项目后第一件事：**开 2FA**（右上角头像 → Settings → Two-factor authentication → 用手机 Authenticator 扫码）。不开 2FA = 保险箱只有一道锁，等于没锁。

> 💡 角色含义：Admin=管理员（全权限）；Member=成员（能读写密钥）；Viewer=只能看。生产环境的 Key 默认只有 Admin 能改（第 6 节细说）。

---

## 2. 场景二：本地把后端跑起来，Key 自动注入（每天都要用的操作）

**装 CLI（命令行工具），一次即可：**

```bash
# Windows（任选其一）
scoop install infisical
# 或 choco install infisical

# macOS
brew install infisical/get-cli/infisical

# Linux
curl -1sLf 'https://packages.infisical.com/infisical/setup.deb.sh' | bash
```

**登录 + 进入项目：**

```bash
infisical login          # 浏览器弹出授权页，点允许
cd D:\GuangH-App\backend
infisical init           # 选项目 yishu-backend，选环境 dev
```

**跑服务（密钥自动注入）：**

```bash
infisical run -- uvicorn app.main:app --reload
```

代码里 `os.environ["DASHSCOPE_API_KEY"]` 自动就有值了——**你全程没碰过真实的 Key**。

**想把密钥拉成本地 .env 文件**（IDE 调试、非 CLI 启动时用）：

```bash
infisical export --env=dev --format=dotenv > .env
```

> ⚠️ `.env` 已被项目的 .gitignore 排除，不会进 Git。拉完看一眼文件权限，别给别人。

**查看当前环境有哪些 Key：**

```bash
infisical secrets --env=dev          # 值默认打码
infisical secrets --env=dev --plain  # 显示明文（需要时再用，别截图！）
```

---

## 3. 场景三：我申请了个高德 Key，要存进保险箱

高德后台申请到 Key 后，存进 dev 环境（Web 或 CLI 二选一）：

**Web 方式：** 项目 → Secrets 页 → 左上角选 `dev` 环境 → **Add Secret** → Key 填 `AMAP_API_KEY`，Value 填 Key 值 → 保存。

**CLI 方式：**

```bash
infisical secrets set AMAP_API_KEY=xxxxxxxx --env=dev
```

**团队命名规范（必须遵守，不然找不到）：** `平台-用途` 全大写下划线：

- `DASHSCOPE_API_KEY`（百炼）、`TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`（腾讯云子账号，COS/CI/STS 共用）、`AMAP_API_KEY`、`BAIDU_OCR_API_KEY`、`WECHAT_APP_SECRET`、`SENTRY_DSN`

**放对抽屉（环境）的原则：**

| 环境 | 放什么 | 谁在用 |
|---|---|---|
| dev | 本地调试用的 Key（可以用免费额度/测试 Key） | 全体开发 |
| staging | 联调测试环境专用 Key | CI、测试 |
| prod | 生产 Key（真实账号、真钱） | 只有后端主程/生产服务器 |

删一条：`infisical secrets delete AMAP_API_KEY --env=dev`

---

## 4. 场景四：同事要个 Key 联调，我不想发群里

**绝不在群里/邮件里发 Key 明文**（聊天记录永久留存，是最常见泄露渠道）。

正确做法——**Secret Sharing 临时链接**（免费档自带）：

1. Web 的 Secrets 页 → 找到那条 Key → 点 **Share**。
2. 设置过期时间（比如 24 小时）和可选访问密码。
3. 把**链接**发给同事（微信/飞书发链接没问题，因为链接会过期、有密码保护）。
4. 同事打开链接 → 登录 → 看到解密后的值 → 复制走。
5. 链接过期自动失效，不留永久记录。

> 适合"临时给一下"；如果这个 Key 以后经常要用，就正经存进对应环境（第 3 节），别老走分享链接。

---

## 5. 场景五：CI 要用 Key，我不想手填（机器身份）

**痛点**：GitHub Actions 跑测试/部署需要 Key，但 Key 不能写进 workflow 文件。

**方案：机器身份（Machine Identity）+ GitHub Actions 集成**

1. 管理员操作：项目 → **Access Control** → 创建 **Machine Identity**（名字如 `github-actions`）→ 生成 Client ID + Client Secret（这两样只显示一次，立即存进 GitHub Secrets 或管理员保险箱）。
2. 推荐走**官方集成**：项目 → **Integrations** → GitHub Actions → 授权仓库 → 选项目 + 环境 → Infisical 自动把密钥同步成 GitHub Secrets。
3. 之后 CI 的 workflow 里：

```yaml
- uses: infisical/cli-action@v1
  with:
    client_id: ${{ secrets.INFISICAL_CLIENT_ID }}
    client_secret: ${{ secrets.INFISICAL_CLIENT_SECRET }}
    project_slug: yishu-backend
    env_slug: dev
- run: infisical run -- pytest
```

效果：**CI 用机器人账号自动拿 Key，人全程不碰**，换 Key 也不用改 workflow。

> ⚠️ 机器身份算 1 个"身份"——免费档 5 个：4 个开发 + 1 个 CI = 正好。再来人要升级 Pro（$20/身份/月）。

---

## 6. 场景六：生产 Key 我能看吗？（角色与权限）

**默认约定**：

- **prod 环境的 Key：只有 Admin（海峰）能看能改。**
- 普通成员（我）：dev/staging 随便读写；prod 只读或看不到（按团队约定设）。
- Viewer：任何环境都只能看。

**怎么设**：项目 → **Access Control** → 选成员 → 按环境分配角色。

**自查**：我是 Member，dev 随便折腾，prod 想改 Key 改不了——这是对的，生产 Key 越少人碰越好。

---

## 7. 场景七：同事离职/离组了

管理员操作（当天完成，三连）：

1. **移除成员**：Access Control → Remove Member → 立即生效，他再也进不来。
2. **轮换他摸过的所有 Key**：去各平台后台生成新 Key → Infisical 里更新对应值 → 旧 Key 在平台后台吊销。
3. **更新台账**：把"谁有什么"表格里的持有人改掉。

> 为什么必须轮换：他可能已经记住了 Key 值（或者本地 .env 还留着）。移除账号 ≠ 他忘了 Key。

---

## 8. 场景八：Key 可能泄露了（紧急处理流程）

顺序很重要，先止血再追责：

1. **止血**：立刻去对应平台后台吊销/重置这条 Key（比如微信 AppSecret 一键重置、腾讯云 AK 禁用）。
2. **清理**：Infisical 里把这条删掉或改成新值。
3. **通知**：查台账谁在用 → 通知他们拉新值（`infisical run` 或重新 export .env）。
4. **判断**：如果 Key 是发到群里/公网/Git 历史了，把同平台其他 Key 也轮换一遍（风险扩散）。
5. **复盘**：这次是怎么泄的？如果是群里发的，重申纪律（第 10 节第 1 条）。

---

## 9. 免费档的边界（心里有数，别踩坑）

| 免费档没有的 | 影响 | 怎么补 |
|---|---|---|
| 审计日志（谁看了哪个 Key） | 泄露后查不到是谁看的 | 纪律兜底：Key 值只走 Infisical，聊天里永不出现 |
| 密钥版本回滚 | 改错值只能手动改回来 | 改之前先复制旧值存本地 |
| 自动轮换 | 没人提醒你换 Key | 台账记轮换日期，每季度第一个周一手动轮换 |
| 只有 3 环境 | dev/staging/prod 正好，多一个就要升级 | 别硬塞，4 个用途就合并 |
| 5 个身份上限 | 4 人 + 1 个 CI = 5，正好 | 加人前先想清楚，或升级 Pro |

**免费档上限 = 4 人团队刚好够用**，这是选它的原因。

---

## 10. 团队约定（打印出来贴墙上）

1. **Key 值只进 Infisical**，永远不进聊天/邮件/文档/代码/截图。
2. 命名规范：`平台-用途` 全大写下划线（见第 3 节）。
3. 新 Key 谁申请谁存，**当天**更新台账（"谁有什么"表格）。
4. 离职当天：删成员 + 轮换全部 Key + 更新台账。
5. 每季度第一个周一：轮换全部生产 Key + 吊销无活跃 Key。
6. 国内访问：app.infisical.com 如果慢或不通，CLI 照常能用（走 API）；长期不行就切 Vaultwarden 自托管（见方案文档 4.2）。

---

## 附：命令速查表

| 命令 | 作用 |
|---|---|
| `infisical login` | 登录 |
| `infisical init` | 选项目和环境 |
| `infisical run -- <命令>` | 注入密钥后运行命令 |
| `infisical secrets --env=dev` | 查看 dev 环境密钥（打码） |
| `infisical secrets --env=dev --plain` | 显示明文（慎用） |
| `infisical secrets set K=V --env=dev` | 新增/更新密钥 |
| `infisical secrets delete K --env=dev` | 删除密钥 |
| `infisical export --env=dev --format=dotenv > .env` | 导出为 .env 文件 |

---

## 附录：17 项外部服务凭证怎么存（对照表）

**通用三步**：①平台后台拿凭证 → ②选环境（dev=测试/免费 Key，prod=真实 Key）按规范命名 Add Secret → ③代码 `os.environ["名字"]` 读。

**关键认知**：一项服务 ≠ 一条 Secret。有的单条（高德），有的成对（百度 AK+SK），有的**多服务共用一组**（腾讯云 COS / 图像识别 / STS 全部共用 1 对 CAM 子账号密钥 + 4 个业务标识），有的成组（uni-push 厂商通道每家 3-4 条）。开源无 Key 的 8 项（#7/8/10/12/13/15 及护栏）不用存。

**2026-08-18 调研更新（重要）**：

1. **FunASR 不用阿里云 AccessKey**——fun-asr 就在百炼平台上（录音文件识别 `fun-asr` / 实时 `fun-asr-realtime`），**复用百炼 API Key 直接调**，跟 #1 是同一把 Key。
2. **百炼新增记录 `DASHSCOPE_WORKSPACE_ID`**——业务空间 ID（百炼控制台→业务空间管理），华北2北京等地域调用时 Base URL 必须带，不是密钥但缺了调不通。
3. **腾讯云图像识别走 CI 图片标签**（1.5 元/千张，比 TI-A 通用图像标签 2.5 元/千次便宜 40%），凭证与 COS **共用同一对 CAM 子账号密钥**（绑 `QcloudCIFullAccess` + `QcloudCOSFullAccess`），不再按服务拆成 `TENCENT_CI_*` / `TENCENT_COS_*` 两套。
4. **腾讯云命名统一**：子账号密钥统一叫 `TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`；业务标识（非敏感，进 .env 即可）`TENCENT_APPID` / `TENCENT_COS_BUCKET` / `TENCENT_COS_REGION` / `TENCENT_STS_ROLE_ARN`。STS 角色 ARN 在 CAM→角色→详情页整串复制（`qcs::cam::uin/{主账号UIN}:roleName/{角色名}`），不手拼。
5. **uni-push 厂商通道密钥成组**：DCloud 开发者账号只需手机号（无需身份证实名）；每家厂商独立申请后按 `XIAOMI_*` / `HUAWEI_*` / `OPPO_*` 命名存入（M2 接入时再落）。

| # | 服务 | 凭证（后台哪里拿） | Infisical 名字 | 环境 |
|---|---|---|---|---|
| 1 | 百炼 qwen-flash | 百炼 API Key（百炼控制台→API-KEY 页创建）+ 业务空间 ID | `DASHSCOPE_API_KEY` + `DASHSCOPE_WORKSPACE_ID` | dev+prod |
| 2 | FunASR | **复用百炼 API Key**（fun-asr 就在百炼平台，无需阿里云 AccessKey） | `DASHSCOPE_API_KEY`（同 #1） | dev+prod |
| 3 | 百度 OCR | API Key + Secret Key（百度智能云→文字识别→创建应用→应用列表） | `BAIDU_OCR_API_KEY` + `BAIDU_OCR_SECRET_KEY` | dev+prod |
| 4 | 腾讯云图像识别 | **CAM 子账号 SecretId/SecretKey**（绑 `QcloudCIFullAccess`，走 CI 图片标签） | `TENCENT_SECRET_ID` + `TENCENT_SECRET_KEY`（与 COS 共用同一对） | dev+prod |
| 5 | 高德逆地理 | Web 服务 Key（高德→应用管理） | `AMAP_WEB_API_KEY` | dev（免费额度）+prod |
| 6 | 企微认证 | corpid + 应用 secret | `WECOM_CORP_ID` + `WECOM_APP_SECRET` | prod |
| 7 | Qdrant 自部署 | 无（本地）；云版才有 | （云版）`QDRANT_URL` + `QDRANT_API_KEY` | — |
| 8 | Zvec | 无 | 不存 | — |
| 9 | Qwen3-VL 图片塔 | 百炼 API Key | `DASHSCOPE_API_KEY`（想分开用 `DASHSCOPE_EMBED_KEY`） | prod |
| 10 | SetFit | 无 | 不存 | — |
| 11 | 百炼护栏 | 同百炼 Key（header 传） | `DASHSCOPE_API_KEY` | prod |
| 12 | PP-OCRv5 | 无 | 不存 | — |
| 13 | Ollama 本地模型 | 无 | 不存 | — |
| 14 | COS | CAM 子账号 AK/SK + APPID + bucket + region + STS 角色 ARN | `TENCENT_SECRET_ID` + `TENCENT_SECRET_KEY` + `TENCENT_APPID` + `TENCENT_COS_BUCKET` + `TENCENT_COS_REGION` + `TENCENT_STS_ROLE_ARN` | prod（服务端）+CI |
| 15 | SetFit mini | 无 | 不存 | — |
| 16 | Sentry | 每项目一条 DSN（dev/prod 不同） | `SENTRY_DSN_DEV` / `SENTRY_DSN_PROD` | 对应环境 |
| 17 | uni-push | DCloud 开发者账号（手机号即可）+ uni-push 应用信息 + 厂商通道密钥（小米/华为/OPPO 先行） | `DCLOUD_APP_KEY` + `XIAOMI_APP_ID` / `XIAOMI_APP_KEY` / `XIAOMI_APP_SECRET` + `HUAWEI_APP_ID` / `HUAWEI_APP_SECRET` + `OPPO_APP_ID` / `OPPO_APP_KEY` / `OPPO_APP_SECRET` / `OPPO_MASTER_SECRET` | prod（M2 接入时） |

**场景示例**：

1. 腾讯云（一组密钥 + 四个业务标识，图像识别/COS/STS 共用）：海峰建 1 个 CAM 子账号拿 AK/SK → 密钥进 Infisical（`TENCENT_SECRET_ID` / `TENCENT_SECRET_KEY`，只存 prod+CI）；业务标识 `TENCENT_APPID` / `TENCENT_COS_BUCKET` / `TENCENT_COS_REGION` / `TENCENT_STS_ROLE_ARN` 是公开参数，放 .env 模板即可 → 小海 `infisical run` 启动后端自动读到；COS 上传、CI 图片标签打标、STS 签发临时凭证全部用这一对子账号密钥；客户端直传走 STS 临时凭证，永不碰长期 AK。
2. 高德（单条）：小海申请 Web 服务 Key → dev 环境 `AMAP_WEB_API_KEY` → 本地调免费额度；上线前海峰把企业 Key 存进 prod 同名变量——代码不改，环境切换自动换 Key。
3. 微信 AppSecret（最敏感）：只存 prod（Admin 可见）；小海联调要测试号时，海峰用 Share 临时链接（24h 过期）发。
4. Sentry：dev 项目 DSN 存 dev、prod 项目 DSN 存 prod，代码按环境读。
5. 开源无 Key 项（#7/8/10/12/13/15）：不存，本地/服务器直接跑。
