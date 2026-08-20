---
name: infisical-secrets
description: >-
  忆述光华项目 Infisical 密钥链路：命令行安装/登录/关联项目/查看与注入 Secrets 的完整流程。
  所有外部 API Key 与凭证（百炼/腾讯云/百度/企微等）均存于 Infisical（组织 Yishuguanghua，
  项目 yishu-backend，环境 dev/staging/prod）。含安全铁律与故障排查。
official: false
---

# Infisical Secrets 命令行链路（忆述光华）

> 目标：任何人/任何 Agent 在本仓库拿到全部外部 API Key 与凭证，且 Key 值永不落盘、不提交、不回显。
> 配套文档：《忆述光华_Infisical密钥管理操作手册.md》（交付文档，只读）、《忆述光华_外部API账号管理方案.md》。

## 适用场景

1. 本地/CI 需要外部 API Key（百炼 DASHSCOPE、腾讯云 COS/CI/STS、百度 OCR、企微、高德、Sentry）
2. 新机器入职：装 CLI → 登录 → 关联项目 → 查看有哪些 Key
3. 拿 Key 跑真实功能（RAG 图片塔 / ASR 真转写 / COS 分片 / 护栏真验）
4. 新增/轮换 Key（`infisical secrets set/delete`，仅管理员职责范围内）

## 前置条件

- Windows 本机：`winget install Infisical.infisical`（若不在 PATH，用 `winget list` 查安装路径后全路径调用）
- 已有 Infisical 账号（本机已登录：组织账号；token 存系统 Keyring，无需重复登录）

## 标准流程

### 1. 安装 CLI（一次性）

```powershell
winget install --id infisical.infisical -e --accept-source-agreements --accept-package-agreements --silent
# 装完需要新开 shell 或手动加 PATH：
$env:PATH = "$env:LOCALAPPDATA\Microsoft\WinGet\Packages\infisical.infisical_Microsoft.Winget.Source_8wekyb3d8bbwe;" + $env:PATH
```

### 2. 登录（浏览器授权，一次性）

```powershell
infisical login
```

- 交互向导先选托管区域：**Infisical Cloud (US Region)**（默认第一项，直接回车）
- 浏览器自动弹出授权页；登录成功后 CLI 自动完成（token 存系统 Keyring）
- 非交互/CI 场景：机器身份 `infisical login --method=universal-auth --client-id=... --client-secret=...`（需管理员先建 Machine Identity）
- 无人值守提示：`infisical user get` 可查当前登录身份

### 3. 关联项目（一次性）

```powershell
cd D:\GuangH-App
infisical init
# 向导：选组织 → 选项目
# 组织：Yishuguanghua（忆述光华主组织）；另有 GuangH App
# 项目：yishu-backend（dev/staging/prod 三环境）
```

生成 `.infisical.json`（只含 workspaceId，无密钥，可入库共享）。当前值：
`workspaceId=<workspace-id-local-only>`，`defaultEnvironment=dev`。

### 4. 查看有哪些 Secrets（每天要用）

```powershell
# 列出某环境全部密钥（⚠️ v0.43 表格输出含明文值，注意屏幕安全）
infisical secrets --env=dev --silent

# 只取单条值（脚本用，不回显）
infisical secrets get DASHSCOPE_API_KEY --env=dev --plain --silent
```

### 5. 注入密钥运行命令（推荐工作方式）

```powershell
# 后端启动：密钥自动注入进程环境变量，代码 os.environ 直读
infisical run --env=dev -- uvicorn app.main:app --reload
# 跑测试
infisical run --env=dev -- pytest
# 跑 RAG 全分布测评（真实 LLM 改写/路由/精排）
infisical run --env=dev -- python -m research.rag_benchmark.run_eval
```

### 6. 新增/更新/删除密钥（管理员）

```powershell
infisical secrets set  DASHSCOPE_API_KEY=xxx --env=dev
infisical secrets delete AMAP_WEB_API_KEY --env=dev
```

## 本项目密钥清单（只列名字，值一律经 CLI 取）

**dev 与 prod 当前各 10 条**（staging 为空，待填充）：

| 密钥名 | 用途 |
|---|---|
| DASHSCOPE_API_KEY | 百炼 qwen-flash 改写/路由/精排 + Qwen3-VL 图片塔 + FunASR/SenseVoice ASR + 护栏（格式 sk-ws-，工作空间级） |
| DASHSCOPE_WORKSPACE_ID | 百炼业务空间 ID（华北2 等地域调用必须带） |
| BAIDU_OCR_API_KEY / BAIDU_OCR_SECRET_KEY | 百度 OCR（后端尚无 service，待立项） |
| TENCENT_APPID / TENCENT_COS_BUCKET / TENCENT_COS_REGION | COS 业务标识（公开参数） |
| TENCENT_CI_SECRET_ID / TENCENT_GUANHAIFENG_CI_SECRET_KEY | 腾讯云子账号 AK/SK（COS + CI 图片标签 + STS 共用）⚠️ 命名与 config.py 期望的 TENCENT_SECRET_ID/TENCENT_SECRET_KEY 不一致，使用前需对齐 |
| TENCENT_STS_ROLE_ARN | STS 角色 ARN ⚠️ 当前值是主账号 root ARN，安全建议改为子账号角色 |

**缺失（申请后补存）**：WECHAT_APPID/WECHAT_SECRET（code2session）、WECOM_CORP_ID/TOKEN/ENCODING_AES_KEY（企微回调）、AMAP_WEB_API_KEY（高德逆地理）、SENTRY_DSN_DEV/PROD、uni-push 厂商通道（XIAOMI_*/HUAWEI_*/OPPO_*，M2 接入时再落）。

## 安全铁律（违反 = 事故）

1. **Key 值永不写进代码/文档/聊天/截图/Git**；回复中只报密钥名
2. 展示类命令输出含明文，用完即清屏；`.env` 导出后确认在 .gitignore 内
3. 本机环境变量有旧版 `DASHSCOPE_API_KEY`（sk-4980... 旧格式），与 Infisical 的 sk-ws- 不同——统一用 `infisical run` 注入，避免旧值覆盖新值
4. prod 环境只有 Admin 可改；普通成员 dev/staging 读写
5. 泄露应急：先平台吊销 → 再 Infisical 改值 → 通知使用者 → 评估同平台轮换（见操作手册第 8 节）

## 故障排查

| 现象 | 处理 |
|---|---|
| 登录向导 PTY 方向键无效 | 用十六进制发送：ESC `1B` + `[` `5B` + B(下)/A(上)，再回车确认 |
| 表格中文乱码 | `chcp 65001` 或 `$OutputEncoding=[Console]::OutputEncoding=[Text.Encoding]::UTF8` |
| `infisical run` 注入的 pwsh 命令引号被吞 | 用 `[Environment]::GetEnvironmentVariable('NAME')` 代替 `$env:NAME` 内插 |
| 登录态失效（401） | 重跑 `infisical login`（浏览器授权） |
| 找不到项目 | `infisical init` 重选；确认组织是 Yishuguanghua |
| 国内访问慢 | CLI 走 API 通常可用；长期不通切 Vaultwarden 自托管（见账号管理方案 4.2） |
| 需要 CI 用 Key | 机器身份 + `infisical/cli-action@v1`（见操作手册第 5 节），免费档 5 身份上限 |

## 命令速查

| 命令 | 作用 |
|---|---|
| `infisical login` | 登录（浏览器授权，token 存 Keyring） |
| `infisical init` | 关联组织/项目（生成 .infisical.json） |
| `infisical secrets --env=dev` | 列出 dev 全部密钥（含明文，慎屏） |
| `infisical secrets get NAME --env=dev --plain --silent` | 取单条值（脚本用） |
| `infisical run --env=dev -- <命令>` | 注入密钥后运行 |
| `infisical secrets set K=V --env=dev` | 新增/更新 |
| `infisical secrets delete K --env=dev` | 删除 |
| `infisical export --env=dev --format=dotenv > .env` | 导出 .env（仅本地调试，勿提交） |
