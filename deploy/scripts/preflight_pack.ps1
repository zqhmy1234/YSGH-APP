#requires -Version 5.1
<#
  忆述光华 · 出包前置门（把「卡 C3 实施要点 4」的三条手敲 grep 变成可执行门）

  用途：**每次云打包前必须跑，全绿才允许打包**。任一条 FAIL 就打包的后果是
        「包发出去后所有页面全空且不报错」——本项目最贵的一类故障（零报错、最难排障）。

  用法：
      powershell -ExecutionPolicy Bypass -File deploy\scripts\preflight_pack.ps1
      # 或在仓库任意位置： pwsh -File .\deploy\scripts\preflight_pack.ps1 -RepoRoot D:\GuangH-App

  退出码：0 = 全绿可打包；1 = 有 FAIL，禁止打包。

  说明：本文件含中文，**需以 UTF-8 BOM 保存**（Windows PowerShell 5.1 无 BOM 时会按 ANSI 解码
        导致中文乱码/匹配失效）。仓库内其余 deploy 脚本为 Linux bash（LF，无 BOM），二者约定不同。
#>
[CmdletBinding()]
param(
    [string]$RepoRoot = '',
    # 2026-10-11：**固定地址隧道模式**显式开关（见 §1 的隧道判定）。
    # 内测期既定方案是"以本机为后端载体"：ngrok 静态域名（永久固定）或 cloudflared 隧道
    # 把 127.0.0.1:8010 暴露成公网 https 地址，手机装 APK 即可测、不需要数据线。
    # 默认（不传）仍拦截所有隧道域名以防误用；显式传入即放行并打印时效提醒。
    [switch]$AllowTunnel
)

# 仓库根解析：不能写进 param 默认值 —— Windows PowerShell 5.1 在绑定参数时
# $PSScriptRoot 可能还是空串（实测踩到）。故在脚本体内按 可靠度 兜底解析。
if ([string]::IsNullOrWhiteSpace($RepoRoot)) {
    $here = $null
    if ($PSScriptRoot) { $here = $PSScriptRoot }
    elseif ($MyInvocation.MyCommand.Path) { $here = Split-Path -Parent $MyInvocation.MyCommand.Path }
    else { $here = (Get-Location).Path }
    $RepoRoot = (Resolve-Path (Join-Path $here '..\..')).Path
}

$ErrorActionPreference = 'Continue'
$script:failCount = 0
$script:warnCount = 0

function Section($title) { Write-Host ''; Write-Host "== $title ==" -ForegroundColor Cyan }
function Pass($msg) { Write-Host "  [PASS] $msg" -ForegroundColor Green }
function Note($msg) { Write-Host "  [INFO] $msg" -ForegroundColor Gray }
function Warn($msg) { Write-Host "  [WARN] $msg" -ForegroundColor Yellow; $script:warnCount++ }
function Fail($msg, $fix) {
    Write-Host "  [FAIL] $msg" -ForegroundColor Red
    if ($fix) { Write-Host "         -> $fix" -ForegroundColor Yellow }
    $script:failCount++
}

function Read-Utf8([string]$relPath) {
    $p = Join-Path $RepoRoot $relPath
    if (-not (Test-Path $p)) { return $null }
    return (Get-Content -Path $p -Raw -Encoding UTF8)
}

Push-Location $RepoRoot
try {
    Write-Host '忆述光华 · 出包前置门（C3）' -ForegroundColor White
    Write-Host "仓库根：$RepoRoot"

    # ─────────────────────────── 1. 客户端环境开关 ───────────────────────────
    Section '1. 客户端环境开关（client/utils/config.uts）'
    $cfg = Read-Utf8 'client/utils/config.uts'
    $prodBase = $null
    if ($null -eq $cfg) {
        Fail '读不到 client/utils/config.uts' '确认 -RepoRoot 指向仓库根'
    }
    else {
        $envMatch = [regex]::Match($cfg, "export const ENV:\s*string\s*=\s*'([^']*)'")
        if (-not $envMatch.Success) {
            Fail '未找到 `export const ENV`' '勿改该常量名；本门依赖它'
        }
        elseif ($envMatch.Groups[1].Value -ne 'prod') {
            Fail ("ENV = '" + $envMatch.Groups[1].Value + "'，必须为 'prod'") `
                '真机联调若临时改过 dev，出包前必须改回（否则包内走 10.0.2.2/127.0.0.1 隧道地址）'
        }
        else { Pass "ENV = 'prod'" }

        $realMatch = [regex]::Match($cfg, "export const REAL_DEVICE_HOST:\s*string\s*=\s*'([^']*)'")
        if (-not $realMatch.Success) {
            Fail '未找到 `export const REAL_DEVICE_HOST`' '不应删除该常量'
        }
        elseif ($realMatch.Groups[1].Value -ne '') {
            Fail ("REAL_DEVICE_HOST = '" + $realMatch.Groups[1].Value + "'，prod 构建下必须为空串") `
                '非空会让 getBaseUrl() 走 dev 分支（隧道/局域网地址），正式包连不上真实服务器'
        }
        else { Pass 'REAL_DEVICE_HOST = ''''（空串，prod 正确）' }

        $baseMatch = [regex]::Match($cfg, "const PROD_BASE_URL:\s*string\s*=\s*'([^']*)'")
        if (-not $baseMatch.Success) {
            Fail '未找到 `const PROD_BASE_URL`' '不应重命名该常量'
        }
        else {
            $prodBase = $baseMatch.Groups[1].Value
            $reasons = @()
            if ($prodBase -match '[<>]') { $reasons += '含 <> 占位符' }
            if ($prodBase -notmatch '^https?://') { $reasons += '未以 http:// 或 https:// 开头' }
            foreach ($marker in @('example.com', 'localhost', '127.0.0.1', '10.0.2.2')) {
                if ($prodBase -like "*$marker*") { $reasons += "命中占位/回退词 $marker" }
            }
            # ── 隧道域名判定（2026-10-11 **修正**：不再一刀切 FAIL）──────────────────
            # 背景：内测期既定方案是「**以本机为后端载体**」——手机装 APK 直连本机后端，
            # 不需要数据线；它的实现天然是隧道。初版把**所有**隧道域名判成 FAIL ⇒ 直接把
            # 既定方案卡死（既出不了包，也拿不到"地址会失效"的正确提醒）。
            # 现按「地址是否固定」分两类：
            #   · 固定地址隧道（ngrok 静态域名 `*.ngrok-free.app` / named tunnel 自有域名）
            #     ⇒ **合法**，等同普通公网地址，直接 Pass（已从占位词表移除）
            #   · 临时隧道（cloudflared quick tunnel 等，重启即换址）⇒ 默认 FAIL；
            #     显式 `-AllowTunnel` 时降为 WARN + 交付提醒（应急可用，但必须告知测试同学）
            # 更可靠的判据（2026-10-12）：**读 tunnel.ps1 记录的模式**与地址，而不是猜域名后缀。
            #   · 后缀写死会过期——ngrok 免费版静态域名从 `.io` → `.app` → **`.dev`** 换过好几轮
            #     （本机实测 `xxx.ngrok-free.dev`），按后缀列白名单必然要跟着改；
            #   · 更要命的是：`ngrok http 8010` **不带** `--url` 时拿到的是**随机地址**，
            #     与静态域名长得一模一样 ⇒ 黑名单根本判不出来。
            $modeFile = Join-Path $RepoRoot 'deploy\.tunnel-mode'
            $urlRecFile = Join-Path $RepoRoot 'deploy\.tunnel-url'
            $tunnelMode = if (Test-Path $modeFile) { (Get-Content $modeFile -Raw).Trim() } else { '' }
            $recordedUrl = if (Test-Path $urlRecFile) { (Get-Content $urlRecFile -Raw).Trim() } else { '' }
            $matchesRecord = ($recordedUrl -ne '') -and ($prodBase.TrimEnd('/') -eq $recordedUrl.TrimEnd('/'))
            $isRecordedStatic = $matchesRecord -and ($tunnelMode -eq 'static')
            $isRecordedVolatile = $matchesRecord -and ($tunnelMode -in @('quick', 'random'))

            $ephemeralTunnels = @('trycloudflare.com', 'loca.lt', 'localtunnel.me', 'serveo.net')
            $hitEphemeral = @($ephemeralTunnels | Where-Object { $prodBase -like "*$_*" })

            if ($reasons.Count -gt 0) {
                Fail ("PROD_BASE_URL = '$prodBase' —— " + ($reasons -join '；')) `
                    '改成真实公网地址（形如 http://43.x.x.x:8010）或固定地址隧道（https://xxx.ngrok-free.app）。换址清单见 deploy/ONE_SWAP.md'
            }
            elseif ($isRecordedStatic) {
                Pass "PROD_BASE_URL = '$prodBase'（**固定地址隧道** · 已与 deploy\.tunnel-url 核对，模式 static ⇒ 重启不换址）"
            }
            elseif ($isRecordedVolatile) {
                $why = if ($tunnelMode -eq 'random') { 'ngrok 未指定静态域名 ⇒ 免费版随机地址' } else { 'cloudflared 快速隧道' }
                if ($AllowTunnel) {
                    Warn "PROD_BASE_URL 是**会变的隧道地址**（$why）：重启即换址 ⇒ 已分发 APK 会立刻全部连不上。内测应急可接受，**交付时必须写明**"
                    Pass "PROD_BASE_URL = '$prodBase'（可变态隧道 · 已显式放行）"
                }
                else {
                    Fail "PROD_BASE_URL 是**会变的隧道地址**（$why）" '固定地址方案：pwsh -File scripts\dev\tunnel.ps1 -StaticDomain <你的静态域名>；确要应急请显式加 -AllowTunnel'
                }
            }
            elseif ($hitEphemeral.Count -gt 0) {
                if ($AllowTunnel) {
                    Warn ("PROD_BASE_URL 是**临时隧道**地址（命中 $($hitEphemeral -join ' / ')）：隧道重启即换址 ⇒ 已分发 APK 会立刻全部连不上。内测应急可接受，**交付时必须写明**")
                    Note '想要地址永久固定（隧道重启不用重出包）请改用：pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain <你的静态域名>'
                    Pass "PROD_BASE_URL = '$prodBase'（临时隧道 · 已显式放行）"
                }
                else {
                    Fail ("PROD_BASE_URL = '$prodBase' 命中**临时隧道**域名：" + ($hitEphemeral -join ' / ')) `
                        '固定地址方案：pwsh -File scripts\dev\tunnel.ps1 -Provider ngrok -StaticDomain <你的静态域名>；确要临时隧道应急请显式加 -AllowTunnel'
                }
            }
            else { Pass "PROD_BASE_URL = '$prodBase'" }
        }
    }

    # ─────────────────────────── 2. 权限（C1 成果） ───────────────────────────
    Section '2. 录音权限（C1 成果，manifest.json）'
    $mfRaw = Read-Utf8 'client/manifest.json'
    $mf = $null
    if ($null -eq $mfRaw) { Fail '读不到 client/manifest.json' '确认仓库完整性' }
    else {
        $recCount = ([regex]::Matches($mfRaw, 'RECORD_AUDIO')).Count
        if ($recCount -eq 2) { Pass 'RECORD_AUDIO 声明 2 处（android 与 app-android 权限块各一）' }
        else { Fail "RECORD_AUDIO 出现 $recCount 次，必须为 2" '两处权限块都要含 RECORD_AUDIO（否则运行时录音必挂）' }
        try { $mf = $mfRaw | ConvertFrom-Json }
        catch { Fail 'manifest.json 不是合法 JSON' "报错：$($_.Exception.Message)" }
    }

    # ─────────────────────────── 3. 图标与启动图（C3 成果） ───────────────────────────
    Section '3. 图标与启动图（C3 成果）'
    if ($null -ne $mf) {
        $dist = $mf.'app-android'.distribute
        $iconKeys = @('hdpi', 'xhdpi', 'xxhdpi', 'xxxhdpi')
        $missing = @()
        if ($null -eq $dist.icons) { $missing += '(icons 段缺失)' }
        else {
            foreach ($k in $iconKeys) {
                $v = $dist.icons.$k
                if ([string]::IsNullOrWhiteSpace($v)) { $missing += "icons.$k" }
                elseif (-not (Test-Path (Join-Path $RepoRoot ('client/' + $v)))) { $missing += "icons.$k -> $v 文件不存在" }
            }
        }
        if ($missing.Count -eq 0) { Pass 'icons 四档齐备且文件存在（72/96/144/192）' }
        else { Fail ('icons 不完整：' + ($missing -join '；')) '卡 C3 资产在 client/static/app-icon/，缺则按 docs/图标与签名证书_20260921.md 补齐' }

        $splashMissing = @()
        if ($null -eq $dist.splashScreens -or $null -eq $dist.splashScreens.default) { $splashMissing += '(splashScreens.default 缺失)' }
        else {
            foreach ($k in @('xhdpi', 'xxhdpi', 'xxxhdpi')) {
                $v = $dist.splashScreens.default.$k
                if ([string]::IsNullOrWhiteSpace($v)) { $splashMissing += "default.$k" }
                elseif (-not (Test-Path (Join-Path $RepoRoot ('client/' + $v)))) { $splashMissing += "default.$k -> $v 文件不存在" }
            }
        }
        if ($splashMissing.Count -eq 0) { Pass 'splashScreens.default 三档齐备且文件存在' }
        else { Fail ('启动图不完整：' + ($splashMissing -join '；')) '同上' }

        if ($dist.splashScreens.background) { Pass "Android12 启动背景色 = $($dist.splashScreens.background)" }
        else { Warn 'splashScreens.background 未配置（Android 12+ 用系统默认白色）' }
    }

    # ─────────────────────────── 4. 明文放行（内测期口径） ───────────────────────────
    Section '4. 明文流量放行（内测期口径 · 上架前须回收）'
    $am = Read-Utf8 'client/AndroidManifest.xml'
    if ($null -eq $am) { Fail '读不到 client/AndroidManifest.xml' '云打包的 manifest 合并点，不可缺' }
    else {
        $hasCleartext = $am -match 'usesCleartextTraffic\s*=\s*"true"'
        $isHttp = ($null -ne $prodBase) -and ($prodBase -like 'http://*')
        if ($isHttp -and -not $hasCleartext) {
            Fail 'PROD_BASE_URL 是 http:// 但 AndroidManifest.xml 未放行明文' `
                'release 包会拦掉全部 http 请求（调试基座自带放行，故只在出包后暴露）。加 android:usesCleartextTraffic="true"'
        }
        elseif ($isHttp -and $hasCleartext) { Pass '明文已放行，且 PROD_BASE_URL 为 http://（内测口径自洽）' }
        elseif (-not $isHttp -and $hasCleartext) {
            Warn 'PROD_BASE_URL 已是 https，但仍保留 usesCleartextTraffic=true —— 上架前必须删除（决策台账 §1.13）'
        }
        else { Pass 'HTTPS + 未放行明文（最干净的口径）' }
    }

    # ─────────────────────────── 5. 真源可入库（D-19 类暗物质防线） ───────────────────────────
    Section '5. 真源可入库（防「文件写了、git 里却不存在」）'
    $gitOk = $true
    $null = & git rev-parse --is-inside-work-tree 2>$null
    if ($LASTEXITCODE -ne 0) { $gitOk = $false; Warn '当前不是 git 工作树，跳过忽略规则检查' }
    if ($gitOk) {
        foreach ($rel in @('client/AndroidManifest.xml', 'client/manifest.json', 'client/static/app-icon/icon-192.png')) {
            $out = & git check-ignore -v -- $rel 2>$null
            # 命中 `!` 否定规则的路径**并未**被忽略，但 check-ignore 同样会打印 —— 必须区分
            if ($out -and ($out -notmatch '^\S+:\d+:!')) {
                Fail "$rel 被 .gitignore 忽略" '该文件是工程真源，必须可入库（避免 diff 审查盲区）'
            }
            else { Pass "$rel 可入库" }
        }
        $jksOut = & git check-ignore -v -- 'client/ci-test.jks' 2>$null
        if ($jksOut) { Pass 'keystore（*.jks）确实被忽略（签名私钥不入库）' }
        else { Warn '*.jks 未被忽略 —— 确认 .gitignore 的密钥忽略块未被人误删' }
    }

    # ─────────────────────────── 6. 服务端侧待办（不阻塞出包，阻塞能跑通） ───────────────────────────
    Section '6. 服务端侧提醒（不阻塞出包，但出包前应已就位）'
    $tpl = Read-Utf8 'deploy/.env.production.template'
    if ($null -eq $tpl) { Note '未找到 deploy/.env.production.template（S3 交付物），跳过' }
    else {
        $tplBase = [regex]::Match($tpl, '(?m)^BASE_URL=(.*)$')
        if ($tplBase.Success -and ($tplBase.Groups[1].Value -match '[<>]')) {
            # 模板**应当**保持占位——真值填在服务器上的 deploy/.env（本机看不到），故只作提示不作告警
            Note '模板 BASE_URL 保持占位 ✓（正确：真值填在服务器 deploy/.env，见 deploy/ONE_SWAP.md 处 2）'
        }
        elseif ($tplBase.Success) { Note "模板 BASE_URL = $($tplBase.Groups[1].Value.Trim())" }

        if ($tpl -match '(?m)^\s*#?\s*ALLOW_DEVICE_LOGIN\s*=\s*true') { Pass '模板中 ALLOW_DEVICE_LOGIN=true（内测建档通道）' }
        elseif ($tpl -match 'ALLOW_DEVICE_LOGIN') { Warn '模板中 ALLOW_DEVICE_LOGIN 未启用/被注释 —— 内测用户会收到 501 AUTH_014 进不去' }
        else { Warn '模板中未出现 ALLOW_DEVICE_LOGIN —— 确认已由 S2/S3 登记' }
    }

    # ─────────────────────────── 7. 出包后（非阻塞）提醒 ───────────────────────────
    Section '7. 出包后待回填（不阻塞出包）'
    $dl = Read-Utf8 'deploy/download/index.html'
    if ($null -eq $dl) { Note '未找到下载页（deploy/download/index.html），跳过' }
    else {
        if ($dl -match '约 — MB' -or $dl -match '更新于 —') {
            Warn '下载页仍有「文件大小/更新日期」占位 —— 出包后回填（并放 APK 到 deploy/download/apk/）'
        }
        else { Pass '下载页无残留占位' }
        if ($dl -match 'placeholder\.svg') { Note '下载页二维码仍是占位图 —— 出包后同名覆盖即可' }
    }

    # ─────────────────────────── 8. 客户端编译门（2026-10-11 新增）───────────────────────────
    # 为什么放进出包门（而不只在 review_agent --full）：出包是**最后一次能拦住低级错误的机会**。
    # 静态轴（导入 / 类成员 / 死路由 / 令牌）都是启发式；**编译不过的包绝不该打**——
    # 花 20~56s 换掉一次"装上去才发现全崩"的废包，是本项目性价比最高的一步。
    # 缺 HBuilderX ⇒ FAIL（出包机必须能编译）；脚本自身用 --strict 表达这一口径。
    Section '8. 客户端编译门（uni-app x）'
    $gateScript = Join-Path $RepoRoot 'scripts\client_compile_gate.py'
    if (-not (Test-Path $gateScript)) {
        Fail '缺少 scripts/client_compile_gate.py' '该文件是出包门的一部分，不应删除'
    }
    else {
        $raw = ''
        $code = 1
        try {
            $raw = (& python $gateScript --json --strict 2>&1 | Out-String).Trim()
            $code = $LASTEXITCODE
        }
        catch {
            $raw = "调用 python 失败：$($_.Exception.Message)"
        }
        $status = ''
        $detail = $raw
        try {
            $j = $raw | ConvertFrom-Json
            if ($j.status) { $status = $j.status }
            if ($j.detail) { $detail = $j.detail }
        }
        catch { }
        switch ($status) {
            'ok' { Pass $detail }
            'fail' {
                Fail '客户端编译未通过' '先修编译错误再打包；本地快速复现：python scripts/client_compile_gate.py'
                Write-Host ("         " + ($detail -split "`n" | Select-Object -First 6 | Out-String).Trim()) -ForegroundColor DarkGray
            }
            default {
                if ($code -eq 0) { Pass $detail }
                else { Fail "客户端编译门未通过（exit=$code）：$detail" '出包机必须能编译客户端（设置 HBUILDERX_CLI 指向 cli.exe）' }
            }
        }
    }

    # ─────────────────────────── 汇总 ───────────────────────────
    Write-Host ''
    Write-Host ('-' * 62)
    if ($script:failCount -eq 0) {
        Write-Host "全绿：可打包（WARN $($script:warnCount) 项，请逐条确认非阻断）" -ForegroundColor Green
        Write-Host '云打包命令见 deploy/ONE_SWAP.md §4' -ForegroundColor Gray
    }
    else {
        Write-Host "禁止打包：$($script:failCount) 项 FAIL（WARN $($script:warnCount)）" -ForegroundColor Red
    }
    Write-Host ('-' * 62)
}
finally {
    Pop-Location
}

exit $(if ($script:failCount -eq 0) { 0 } else { 1 })
