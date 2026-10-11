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

1. **等云打包完成**（2026-10-12 01:17 已提交云端，类型＝Android 自有证书）→ 下载 APK →
   `pack.ps1 -SkipPack -Apk <apk> -Deploy`（尸检 + 投放 `deploy/download/`）
2. **真机验收（无数据线）**：安装 APK，重点验证 **图片是否正常**（见阻塞 1）、
   空态页 / 时间轴 / AI 对话 / 画像四页往返
3. 百炼凭据续期后关掉 `MOCK_EXTERNAL_AI`（当前审核链走 mock 直通，仅过渡）

**已完成**：静态域名 `coleslaw-absence-harvest.ngrok-free.dev`（永久固定）已领取并配置
（`ngrok config add-authtoken` 完成）；隧道已起；三件套已切出包态；前置门 **8 节全绿**（含新增的
「固定地址隧道 · 已与 .tunnel-url 核对」判定与客户端编译门 43s）。

## 阻塞

1. 🟡 **图片通路待真机验证**（ngrok 免费版拦截页）：实测本机带浏览器 UA 的请求会拿到警告页 HTML；
   `ngrok-skip-browser-warning` 头可过（API 已统一带），但 **`<image>` 组件无法加自定义头** ⇒
   只能靠 Android 原生 UA（通常不含 Mozilla）放行。**若真机图片全裂**，二选一：
   ① 媒体改走 `uni.downloadFile({header})` + 本地路径显示（改动面较大）；
   ② 换 cloudflared named tunnel / ngrok 付费（去掉拦截页）。
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
