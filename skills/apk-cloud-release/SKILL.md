---
name: apk-cloud-release
description: >-
  忆述光华 Android 云打包与出包流程：出包前置静态门 → 确认后端地址可达 → HBuilderX CLI 云打包
  （自有证书）→ 四项尸检（签名指纹 / 包内地址 / 权限 / apksigner）→ 部署到分发路径。
  任何要「重新打包 / 出包 / 出可安装 APK / 换服务器地址后重出包」的任务都用本 skill。
  含 2026-10-04 实测踩出的坑：签名参数走错会静默用 DCloud 共享证书、口令必须走配置文件、
  临时隧道地址重启即失效、HBuilderX 单队列并发互杀、本机 SSL 失败不等于隧道死。
official: false
disable-model-invocation: true
---

# Android 云打包与出包（忆述光华）

> 目标：产出一个**能连上后端、签名正确、可覆盖安装**的 APK，并留下可复现的取证。
> 配套：`deploy/ONE_SWAP.md`（换址清单）、`docs/图标与签名证书_20260921.md`（证书登记）、
> `deploy/scripts/preflight_pack.ps1`（前置门）、`agent/scripts/local_run.ps1`（agent 启动）。
> 参数速查与故障对照表见 [reference.md](reference.md)。

## 铁律（每条都是踩过的）

1. **前置门退出码必须 0**：`deploy/scripts/preflight_pack.ps1`。它检查
   `ENV`/`REAL_DEVICE_HOST`/`PROD_BASE_URL` 三项开关、图标齐备、真源可入库。
2. **签名必须用专用 CLI 参数**（见 §3）。**在 `--config` 的 json 里写 `keystore` 字段会被
   静默忽略**，日志显示「类型: Android云端证书」——你得到一个 DCloud 共享证书的包，
   **已安装的用户无法覆盖安装（只能卸载重装 → 数据全丢）**。每次出包都要看日志确认「自有证书」。
3. **口令绝不进命令行**：走 `--config` 的 json 文件，文件放**仓库外**（`~/.yishu-signing/`），
   用完立刻删。进命令行会留在 shell 历史与进程列表里。
4. **HBuilderX 全机单实例**：`cli pack` 与其它窗口的 `cli pack` / `cli launch` **并发会互杀**。
   开打前确认 HBuilderX 存在且 CPU 5 秒内不增长（空闲）。
5. **临时隧道地址会变**：`cloudflared tunnel --url ...` 每次重启换随机域名，**已分发的 APK
   立刻全空且不报错**（本项目最贵故障形态）。服务器到货前务必把「隧道在跑」写进交付说明。

## 固定地址模式（2026-10-11 起 · **内测期既定方案**）

> 目标：手机装 APK 即可测，**不需要数据线**。做法＝以**本机为后端载体**，用**固定地址隧道**
> 把 `127.0.0.1:8010` 暴露成公网 https 地址。**地址必须固定**，否则隧道一重启，
> 已分发的 APK 全部连不上（"所有页面全空且不报错"，本项目最贵故障形态）。

**选定实现**：**ngrok 免费静态域名**（`xxxx.ngrok-free.app`，永久固定、零成本、无需买域名）。
提供方可切换（cloudflared named tunnel 等），抽象在 `scripts/dev/tunnel.ps1`。

### 一条链（全部脚本化）

```powershell
# 1) 起本机服务栈（Docker + backend 8010 + worker + agent 8300），健康探针失败会如实报错
pwsh -File scripts\dev\start_all.ps1

# 2) 起隧道（拿到固定公网地址，落 deploy/.tunnel-url；上游不健康会直接拦下不白起）
pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain <你的静态域名>

# 3) 一键回写出包三件套（ENV='prod' / REAL_DEVICE_HOST='' / PROD_BASE_URL=<隧道地址>）
pwsh -File scripts\dev\write_base_url.ps1
#    切换回真机联调态： write_base_url.ps1 -Restore  （ENV='dev' + localhost + adb reverse）

# 4) 出包前置门（**注意 -AllowTunnel**：固定地址隧道合法，但显式声明"本包依赖外部隧道"）
pwsh -File deploy\scripts\preflight_pack.ps1 -AllowTunnel

# 5) 云打包 + 四项尸检 + 投放（见 §3/§4/§5；或走 scripts\dev\pack.ps1 一键链）
```

### 口径澄清（2026-10-11 修正，勿再踩）

- **`preflight` 的隧道判定按「地址是否固定」分类**：固定地址隧道（`*.ngrok-free.app`、
  named tunnel 自有域名）**合法放行**；**临时隧道**（`*.trycloudflare.com` / `loca.lt` /
  `localtunnel` / `serveo`）默认 **FAIL**，加 `-AllowTunnel` 才降为 WARN 并打印时效提醒。
  初版曾把 ngrok 域名一并判 FAIL ⇒ **把既定方案卡死**（出不了包）。
- `client/utils/config.uts` 的 `PLACEHOLDER_MARKERS` 同理**不再含 ngrok 域名**（只留临时隧道词）。
- `isDevCarrier()`（`config.uts`）：**打包产物一律不允许走 dev 地址**——即便 `ENV` 误留 `'dev'`，
  也强制走 `PROD_BASE_URL`。这条是「忘改 ENV ⇒ 新包全站不可用」事故的根治（见 `docs/决策台账.md` §4.18）。
- API 请求统一带 `ngrok-skip-browser-warning` 头（`api.uts` 的 `buildHeader`）：ngrok 免费版
  会对"类浏览器 UA"插入拦截页；uni-app 默认 UA 含 `Mozilla` ⇒ 不带此头时请求会拿到 HTML。
- **`<image>`/音频走 `uni.downloadFile({header})`**（同 `buildHeader`）⇒ 同样带得上该头，
  故图片通路不受拦截页影响。

## 流程

### 1. 前置门

```powershell
powershell -ExecutionPolicy Bypass -File deploy/scripts/preflight_pack.ps1
# 退出码 0 = 可打包；1 = 有 FAIL，禁止打包
```

### 2. 确认后端地址（**最容易被跳过、也最容易白打**）

包里的后端地址会被**焊死**。打包前必须确认它**现在真的可达**。

**⚠️ 本机到 Cloudflare 的网络可能被阻断**（本机实测 `huggingface.co` / `hf-mirror.com` /
`trycloudflare.com` 全部 `SSL UNEXPECTED_EOF`，而 amap / dashscope / modelscope 正常）。
这**不代表隧道坏了**，只代表这台机器连不上它——手机走自己的网络，不受影响。
所以隧道可达性**必须从外部视角验证**（用云端工具访问 `https://<隧道域名>/healthz`，
看到 `{"status":"ok"}` 才算通）。2026-10-04 我在这里误判过一次「隧道已死」。

```powershell
# 本机后端是否活着（只能证明后端本身，不证明隧道）
Invoke-WebRequest http://127.0.0.1:8010/healthz -UseBasicParsing
# 隧道域名当前配的是哪个
Select-String -Path client/utils/config.uts -Pattern "const PROD_BASE_URL"
```

隧道死了就重启 cloudflared 拿新地址，并把新地址写进 `PROD_BASE_URL`。

### 3. 云打包（自有证书）

口令文件在 `~/.yishu-signing/keystore-password-请转移后删除.txt`（键值形式：`path` / `alias` / `passwd`）。

生成配置（**仓库外**，用完删）——`androidpacktype=0` 才是自有证书：

```powershell
$cfg=[ordered]@{ android=[ordered]@{
  packagename="com.yishu.guanghua"
  androidpacktype=0
  certalias=$kv['alias']
  certfile="C:\Users\ghf\.yishu-signing\yishu-guanghua-beta.jks"
  certpassword=$kv['passwd']
  storepassword=$kv['passwd']
}}
$cfgPath="$env:USERPROFILE\.yishu-signing\pack-config.json"
[System.IO.File]::WriteAllText($cfgPath,($cfg|ConvertTo-Json -Depth 8),[System.Text.UTF8Encoding]::new($false))
```

> `$kv` 从口令文件的 `alias` / `passwd` 两行解析。**不给 `androidpacktype` 会落到 DCloud
> 云端证书**，而 json 里另写 `keystore` 嵌套字段**不生效**——第一次踩这个坑，打出来的包
> 签名是 DCloud 的，靠「下面四项尸检的签名比对」才抓出来。

启动（**后台**，云打包 3~10 分钟）：

```powershell
$p=Start-Process "D:\HBuilderX\cli.exe" `
  -ArgumentList "pack","--project","D:\GuangH-App\client","--platform","android","--config",$cfgPath `
  -RedirectStandardOutput "d:\GuangH-App\_tmp\pack.log" `
  -RedirectStandardError  "d:\GuangH-App\_tmp\pack.err.log" -PassThru -WindowStyle Hidden
```

轮询到「打包成功」，**记下这行**（含证书类型与下载地址）：

```powershell
Select-String -Path d:\GuangH-App\_tmp\pack.log -Pattern "安装包下载地址"
```

下载产物后**立刻删配置**：`Remove-Item $cfgPath -Force`

### 4. 四项尸检（缺一不可）

```powershell
$apk="<下载的 apk>"; $sdk="$env:LOCALAPPDATA\Android\Sdk\build-tools\34.0.0"

# ① 签名：须与 docs/图标与签名证书_20260921.md §五 登记值全对（DN/MD5/SHA1/SHA256）
& "$sdk\apksigner.bat" verify --print-certs $apk
& "$sdk\apksigner.bat" verify $apk            # 退出码 0

# ② 包内后端地址：须是当前活隧道域名，且旧地址已消失
Select-String -Path $apk -Pattern "<隧道域名>" -SimpleMatch -Quiet     # True
Select-String -Path $apk -Pattern "<旧域名>"   -SimpleMatch -Quiet     # False

# ③ 权限：本次新增的必须在（2026-10-04 加了定位权限）
& "$sdk\aapt2.exe" dump permissions $apk | Select-String "LOCATION|RECORD_AUDIO|INTERNET"

# ④ 大小/时间：确认是本次新打的
Get-Item $apk | Select-Object Name,Length,LastWriteTime
```

### 5. 部署到分发路径

```powershell
$new="<新包>"; $dist="d:\GuangH-App\deploy\download\apk\yishu-beta-latest.apk"
Copy-Item $dist "d:\GuangH-App\deploy\download\apk\yishu-beta-OLD-<原因>.apk" -Force
Copy-Item $new $dist -Force
(Get-FileHash $dist -Algorithm SHA256).Hash
```

### 6. 交付时必须写清的三件事

1. **APK 路径 + sha256**
2. **运行前提**：哪几个服务必须一直在跑（后端 8010、RQ worker、agent 8300、cloudflared 隧道、
   PostgreSQL、Redis），以及「关电脑前先通知到位」
3. **临时地址的时效**：隧道重启就换地址，APK 立刻失效；服务器到货后按 `deploy/ONE_SWAP.md`
   换址 + **重出包**（旧包不会自己恢复）

## 配套：出包后「装了却用不了」的必查项

出包前**先确认后端这边不缺东西**，否则包再漂亮也是白装。2026-10-04 一份包要同时补齐两样：

| 项 | 症状 | 查法 | 修法 |
|---|---|---|---|
| **内测登录通道** | 一打开就卡在登录 | `POST /api/v1/auth/device` 是否 501 `AUTH_014` | `backend/.env` 加 `ALLOW_DEVICE_LOGIN=true`（`config.py:159` 注明内测期开、**上架前必须置回**） |
| **异步管线 worker** | 记录能存但 AI 分类/地名/聚合不跑，内容卡 `processing` | 进程里有没有 `app.workers.worker` | `python -m app.workers.worker`（cwd=`backend`） |
| **agent 服务** | AI 页「没反应」或 502 | `http://127.0.0.1:8300/health` | `powershell -File agent/scripts/local_run.ps1`（会先清陈旧 `DASHSCOPE_*`） |
| **队列积压** | 新内容迟迟不处理 | `len(get_queue('low'))` | 开发期积压可 `q.empty()` 清空（内容在 PG，不丢数据） |

## 记录

- 2026-10-04：首次按此流程出包。签名坑（`keystore` 嵌套不生效）、隧道外部验证、
  登录开关与 worker 缺失，均为本次实际踩到并已写入上文。