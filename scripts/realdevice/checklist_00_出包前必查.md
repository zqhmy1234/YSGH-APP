# Checklist 00 · 出包前必查（客户端）

> 生成：2026-10-11｜归属：scripts/realdevice（开发 C）｜用途：**每次云打包/出内测包前的硬闸门**
> 由来：2026-09-21 内测包事故——`config.uts` 带 dev 口径出包 →「所有页面全空且不报错」
> 相关文件：`client/utils/config.uts`、`scripts/realdevice/r2_4b_verify.ps1`、`docs/交接提示词_R2打包与复验_20260901.md`

---

## 1. 目的

把「本地联调口径」和「出包口径」分开管住：**联调改动允许留在工作树，但不得进入出包提交**。

## 2. 硬闸门（逐条打勾，任一不过则不出包）

- □ **`config.uts` 两个开关已回到出包口径**
  - `ENV = 'prod'`（`'dev'` 是联调口径，出包必须 prod）
  - `REAL_DEVICE_HOST = ''`（非空会让 `getBaseUrl()` 走 dev 分支，正式包连不上真实服务器）
- □ **`PROD_BASE_URL` 已替换为真实公网地址**
  - 端口保持 **8010**（与 dev 分支一致，避免端口漂移）
  - 仍是占位/可疑值（`<公网IP>`、`example.com`、`localhost`、`127.0.0.1`、`10.0.2.2`、缺 scheme）会被 `getBaseUrl()` 的 `console.error` 喊出来
  - 内测期走明文，需配合 `client/AndroidManifest.xml` 的 `usesCleartextTraffic`；**上架前必须切 HTTPS 并移除该开关**（决策台账 §1.13）
- □ **`git status --short -- client` 里没有 ` D` 删除标记**（AV 批量删除工作树文件的老雷）；发现 ` D` 立即 `git checkout -- client/`
- □ **`git diff -- client` 逐条看过**，确认没有把联调临时改动混进出包提交
- □ 证据目录已确认落盘位置：`scripts/realdevice/evidence/`（该目录不入库，但关单要用）

## 3. 本地联调口径怎么管理（推荐做法）

1. 联调期间 `ENV='dev'` + `REAL_DEVICE_HOST='localhost'` 可以留着，但**每次 commit 前先跑 `git status --short -- client`**，确认它没进暂存区。
2. 真机联调链路：`adb reverse tcp:8010 tcp:8010`（**8010，不是 8000**）→ 设备侧 `curl -s http://127.0.0.1:8010/healthz` 应返真 JSON。
   - 8000 会被 HBuilderX launcher 的 httpServer 抢占，对任何路径返假 200 + 裸字符串，表现为「接口全空但不报错」——本项目最贵的一类故障，已由 2026-08-31 拍板换到 8010 根治。
3. 出包前把两个开关一次性改回 prod 口径，改完**立刻** `git diff -- client/utils/config.uts` 复核。
4. 复验基线用**内容判定**，不写提交哈希：4b 批次那批哈希（`8cb15b4`/`f1f8a3c`/`6f43e87`/`97eb795`/`381a210`/`2a0194b`）在本仓库不可解析。基线记录写「分支 + 内容证据」：`client/AndroidManifest.xml` 含 `DataSyncService` + `FOREGROUND_SERVICE*`；`yishu-background-tasks` 用 `Class.forName` 探测；`getBaseUrl()` 走 8010。

## 4. 关联清单

- `checklist_05_真实转写情绪.md`、`checklist_07_自定义基座.md`
- 自动化段：`pwsh -File scripts/realdevice/r2_4b_verify.ps1 -Stage autopsy|install|backend|logs|manual`（路径按脚本自身位置推导，端口默认 8010）
  - 用 Windows PowerShell 5.1 跑需加 `-ExecutionPolicy Bypass`（默认策略禁用 .ps1）；无 BOM 的 UTF-8 脚本在 5.1 下会中文乱码解析报错，本脚本已加 BOM
  - `autopsy` 依赖 `aapt`：不在 PATH 时脚本自动从 `%LOCALAPPDATA%\Android\Sdk\build-tools` 兜底
