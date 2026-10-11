# STATE — 忆述光华 · 当前状态（唯一来源）

> **规则**：本文件是「**现在我们到哪了**」的**唯一**回答处。任何「下一步 / 阻塞 / 全局数字」
> 只写在这里；其余文档（`AGENTS.md` / `progress.md` / `session-handoff.md` / `feature_list.json`）
> **只准引用，不准复述**（历史上正是"五处各写一份数字"导致漂移）。
> **每个会话结束前必须更新本文件**：流水写 `progress.md`（追加式），决策写 `docs/决策台账.md`。

**最后更新**：2026-10-11
**分支**：`feature/agent-closeout-20261008` ｜ **最近提交**：`bbb1c3e`（工作区另有在途批次，见下）

## 正在做什么

**harness / 门禁系统性更新 +「新包可跑」修复** —— 代码与文档侧**已全部交付**（见 `docs/决策台账.md`
§4.18 与 §4.19），现在**卡在一步外部依赖**：ngrok 静态域名与 authtoken 需用户领取（阻塞 1）。

**已交付要点**（细节见台账 §4.19）：
- 环境漂移根治（§4.18）：`isDevCarrier()` 强制打包产物走 `PROD_BASE_URL`；三件套一键回写脚本
- 解阻塞：隧道判定按「固定 / 临时」分类，固定地址（ngrok 静态域名）**合法放行**，`preflight -AllowTunnel` 显式声明
- 出包链脚本化：`scripts/dev/{tunnel,write_base_url,start_all,status,pack}.ps1`（**start_all/status 实测全绿**）
- 门禁补 4 项：客户端编译门（`--full` + 出包门）、`client/` 纳入密钥扫描（**含全量模式**）、
  `audit_security` 接入（备份域只告警）、`port_consistency`、`harness_state`
- state 改造：`STATE.md` 落地；外部审计 `validate-harness.mjs` **76 → 100/100**
- 客户端出网头统一到 `buildHeader()`/`commonHeader()`（含 **auth 登录链**与 **audio 下载**——
  此前不带通用头，ngrok 拦截页会导致「装了却进不去」）

## 下一步（按优先级）

1. **用户领取 ngrok 静态域名 + authtoken**（阻塞 1）→ `ngrok config add-authtoken <token>`
2. 打通出包链：`scripts/dev/tunnel.ps1 -Provider ngrok -StaticDomain <域名>` →
   `write_base_url.ps1` → `preflight_pack.ps1 -AllowTunnel` → `scripts/dev/pack.ps1 -Deploy`
3. **云打包出新 APK → 无数据线真机验收**：空态页 / 时间轴 / AI 对话 / 画像四页往返
4. 百炼凭据续期后关掉 `MOCK_EXTERNAL_AI`（当前审核链走 mock 直通，仅过渡）

## 阻塞

1. **ngrok 静态域名 + authtoken 需用户手工领取**（本机只有 Tabbit 非标准 Chromium，
   浏览器自动化 relay 不认它 ⇒ 无法代领）。拿到后：`ngrok config add-authtoken <token>`
2. agent 侧 LLM 暂用系统变量注入的 DeepSeek key（`deepseek-ugsimulator`）；
   百炼 Token Plan 到期未续 ⇒ 护栏/生成链走 mock（`MOCK_EXTERNAL_AI=true`）

## 权威指针（想知道 X 去哪看）

| 想知道 | 去哪 |
|---|---|
| 为什么这么决策 / 待拍板 / 未关闭缺陷 | `docs/决策台账.md`（§5 待拍板 · §7 未关闭） |
| 踩过什么坑（带 commit 时间戳） | `docs/lessons.md` |
| 最近发生了什么（流水，**以最后一条为准**） | `progress.md` |
| 能力清单（用户故事维度，只允许 1 条 in-progress） | `feature_list.json` |
| 现在能不能测 / 服务起没起 | `pwsh -File scripts\dev\status.ps1` |
| 怎么出包 / 换址 / 联调 | `skills/apk-cloud-release/`、`deploy/ONE_SWAP.md`、`scripts/dev/` |
| 客户端避坑（编译/真机/UTS） | `skills/hbuilderx-uniappx-runloop/` |

## 关键数字（唯一口径）

- **用户故事**：✅46 / 🟡7 / ❌0；A 级真机证据 **32** 条（口径基线 **2026-08-29**，尚未按 9–10 月变更重算）
- **测试**：agent 185 passed ｜ backend 1017 passed / 4 skipped（需 Docker redis/qdrant 在跑）
- **未关闭重大缺陷**：见 `docs/决策台账.md` §7（RAG 整体门禁 FAIL / 推送全 mock / 蜂窝上传恒 hold 等）
- **端口口径**：backend `8010` ｜ agent `8300`（历史 `8399` 仅一次性本机覆盖，`8000` 为旧值）
