# 换址清单（ONE SWAP）· 内测下发只差这一步

> 本文件回答一个问题：**拿到服务器和域名之后，到底要改哪些地方？**
> 答案：**客户端 1 处 + 服务端 1 处 + 1 条自检命令**。其余全部无需人工介入。
>
> **还没拿到地址？** 先让买服务器的人填 `deploy/SERVER_INTAKE.md` 回报单（含一键采集命令）——信息齐了本清单能一次走完，不用来回问。

生成时间：2026-09-21 ｜ 依据：卡 C3 出包前置门（`deploy/scripts/preflight_pack.ps1`）+ 决策台账 §1.12/§1.13

---

## 0. 一句话

| # | 位置 | 环境 | 是否影响已打出的 APK |
|---|---|---|---|
| **处 1** | `client/utils/config.uts` → `const PROD_BASE_URL` | 开发机（入库） | ✅ **必须改，改完才能打包** |
| **处 2** | 服务器 `deploy/.env` → `BASE_URL` | 服务器 | ❌ 不影响 APK（服务端生成链接/回调自用） |

**其余一切都不需要改**：端口固定 8010、依赖服务连接串都是 `127.0.0.1`、`CORS_ORIGINS` 生产留空（原生 App 不走浏览器 CORS）、媒体下发 URL 由后端发**相对路径**、客户端拼自己正在用的 `getBaseUrl()`——所以后端**刻意不持有 host 配置**，换址不会引发 host 漂移。这是既有设计的功劳，不是巧合（见 `backend/app/services/external/media_url.py` 文件头）。

---

## 1. 处 1 · 客户端（唯一必须改、且决定 APK 能否联网的地方）

**文件**：`client/utils/config.uts`

```ts
// 第 40 行附近
const PROD_BASE_URL: string = 'http://<公网IP>:8010'
```

改成真实地址，二选一：

```ts
const PROD_BASE_URL: string = 'http://43.138.x.x:8010'      // ① 内测口径：明文 IP（需 cleartext，已放行）
const PROD_BASE_URL: string = 'https://api.你的域名'          // ② 域名口径：须先备案 + 配证书（见 §5）
```

**一键替换（PowerShell，无 BOM 写回——BOM 会污染工程源）**：

```powershell
$http = 'http://43.138.x.x:8010'      # ← 只改这一行（https 口径就写 https://api.你的域名）
$p = 'D:\GuangH-App\client\utils\config.uts'
$c = [IO.File]::ReadAllText($p)
$c = [regex]::Replace($c, "const PROD_BASE_URL: string = '[^']*'", "const PROD_BASE_URL: string = '$http'")
[IO.File]::WriteAllText($p, $c, [Text.UTF8Encoding]::new($false))   # $false = 不写 BOM，关键
Select-String -Path $p -Pattern 'const PROD_BASE_URL'
```

**不要改的**：
- `ENV` 已是 `'prod'` ✓
- `REAL_DEVICE_HOST` 已是 `''` ✓
- **端口固定 8010**：8000 会被 HBuilderX launcher 的 node 桩抢占（该桩对任何路径都返回 200 + 裸字符串 "404"，客户端守卫因此静默 resolve(null)，表现为"接口全空但不报错"）——8010 的由来见 `config.uts` 文件头。

---

## 2. 处 2 · 服务端（在服务器上改，不在仓库里改）

服务器首次部署时：`cp deploy/.env.production.template deploy/.env`，然后**在 `deploy/.env` 里填真值**：

```ini
BASE_URL=http://43.138.x.x:8010        # 与处 1 保持一致
```

**模板文件 `deploy/.env.production.template` 请保持占位不动**（它是模板；真值只应出现在服务器上、不入库的 `deploy/.env` 里）。

改完重载后端：

```bash
sudo systemctl restart yishu-api yishu-worker
curl -s http://127.0.0.1:8010/healthz    # 期望真 JSON（不是裸 "404" 字符串）
```

---

## 3. 网络侧（一次性，与地址无关的部分见括号）

| 项 | 值 |
|---|---|
| 安全组放行 | **22（SSH）+ 8010（后端）** |
| **严禁**公网放行 | 5432 / 6379 / 6333（依赖服务只绑 `127.0.0.1`） |
| 域名（若用 §5 口径） | 实名认证主体与备案主体一致；A 记录指向服务器 |

---

## 4. 换完自检 + 打包

### 4.1 一条命令自检（**全绿才允许打包**）

```powershell
powershell -ExecutionPolicy Bypass -File D:\GuangH-App\deploy\scripts\preflight_pack.ps1
```

退出码 `0` = 可打包；`1` = 有 FAIL，禁止打包（任一条不符就打包的后果是"包发出去所有页面全空且不报错"）。

门会逐项检查：`ENV`/`REAL_DEVICE_HOST`/`PROD_BASE_URL` 三项开关、`RECORD_AUDIO` 计数、图标与启动图齐备且文件存在、**明文放行与 http 口径自洽**、**真源可入库**（防 D-19 类"文件写了但 git 里没有"）、服务端侧提醒、出包后待回填项。

### 4.2 打包

```powershell
& D:\HBuilderX\cli.exe pack --project "D:\GuangH-App\client" --platform android `
  --iscustom true --android.packagename com.yishu.guanghua --ignoreWarnings true
```

- `--ignoreWarnings` **必须显式布尔值**（SKILL §6 军规）
- 全机只有一个 HBuilderX 实例，**禁止与其它窗口并发编译/打包**（会互杀）
- 签名证书：`%USERPROFILE%\.yishu-signing\yishu-guanghua-beta.jks`（alias `yishu`），密码在密码管理器里

---

## 5. 若改用 HTTPS 域名（替代 IP + 明文口径）

内测期默认走 `http://IP:8010` + 明文放行（省备案等待）。若已具备条件切 HTTPS：

1. 处 1 改成 `https://api.你的域名`；
2. **删除** `client/AndroidManifest.xml` 里的 `android:usesCleartextTraffic="true"`（连带 `tools:replace` 一行）；
   > 门脚本会在 http→https 之后**自动 WARN 提醒**你回收它，不用担心忘。
3. 证书与 nginx（如需）配置见 `deploy/RUNBOOK.md`。

---

## 6. 出包后待办（与地址无关，但别忘了）

| # | 事项 | 位置 |
|---|---|---|
| 1 | 把 APK 放进 `deploy/download/apk/`，**文件名固定** `yishu-beta-latest.apk`（页面无需改动） | `deploy/download/apk/README.md` |
| 2 | 回填下载页的「文件大小」「更新日期」 | `deploy/download/index.html`（`[替换点]` 注释处） |
| 3 | 覆盖两张二维码图（同名覆盖即生效） | `deploy/download/qr/` |
| 4 | **图标/签名核对（C3 验收）** | `aapt dump badging <apk> \| findstr application-icon`、`keytool -printcert -jarfile <apk>` |
| 5 | 真机冷启动目视启动图 + 录一段音（验收录音权限） | nova 11 |

下载页的部署与地址无关：用 Cloud Studio 部署 `deploy/download/` 即可拿到公网地址，**不占用你这台服务器**。

---

## 7. 上架前必须回收的临时口径（决策台账 §1.13）

| 项 | 回收动作 |
|---|---|
| `usesCleartextTraffic="true"` | 切 HTTPS 后删除（门脚本会 WARN） |
| `ALLOW_DEVICE_LOGIN=true` | 置回 `false`（设备码通道关闭，回归微信/手机号登录） |
| `http://IP:8010` 明文口径 | 见 §5 |
| `Settings.Secure.ANDROID_ID` 作为设备标识 | 属个人信息，隐私政策「收集的设备信息」条款须声明 |
| `CODE_...`/压测门禁等 6 项 | 见 `GAP_ANALYSIS_20260903_AppStore上架差距评估.md` |
