# DSH 插件选型与安装方案（40 项需求分层）

> 生成时间：2026-08（基于 GitHub topic dsh-plugin 生态实测 + 40+ 仓库本地源码研究）
> 当前环境：`@deepseek-ai/dsh@0.1.0-rc.6`，web profile（GUI 运行于 127.0.0.1:3080），Windows，主力模型 Deepseek-V4-Flash（纯文本）。

---

## 一、需求分层总览

### 第一层：用户明确指定插件 → 立即安装（按原样或最优替代）

| # | 需求 | 指定插件 | 决策 | 安装源 |
|---|---|---|---|---|
| 10 | 语义 UI 音效 | XanthanL/dsh-plugin-uisfx | ✅ 安装 | npm `dsh-plugin-uisfx` |
| 11 | 新建会话默认工作区 | wjy9902/dsh-web-default-session | ✅ 安装 | git |
| 12 | 对话式生成 UI | Nagi-ovo/dsh-visualize | ✅ 安装（134★，唯一完整实现） | git |
| 13 | 只读 Git 图形视图 | WhitePlusMS/dsh-git-graph | ⚠️ **替换为 1841220388zzzcccxxx-star/dsh-git-graph**（同 slot 冲突，alt 版 12★ 功能全：diff/未提交改动/暂存/AI 提交信息，WhitePlusMS 版 1★ pre-release 0.0.1 无测试） | git |
| 23 | Git worktree 隔离交付 | wloops/dsh-git-worktree | ✅ 安装（Domi 级状态机，与 FlashingChen/dsh-worktree 二选一取其） | npm `dsh-git-worktree` |
| 24 | 网络小说引擎 | x2802490130-prog/dsh-tool-writing | ✅ 安装（需配独立 DSH_WRITING_API_KEY） | npm `dsh-tool-writing` |
| 25 | Gemini 视觉/生图 | ConsoleSun/Gemini-Eyes | ✅ 安装（本机已有 Python 3.13+uv；⚠️ 需 Google 账号 Cookie，逆向接口有 ToS 风险，建议小号） | git |
| 26 | B 站视频分析 | CZX2244/dsh-bilibili | ✅ 安装（抓帧需本机 ffmpeg，无则自动降级纯文字） | git |
| 27 | 五种模式深读 | xiehuan123/dsh-deepread | ✅ 安装（npm 版，零 key，质量最高） | npm `dsh-deepread` |
| 28 | 免费生图渠道 | akqwpeter-prog/dsh-media-skills | ✅ 安装（需免费 GLM_API_KEY + SILICONFLOW_API_KEY） | git |
| 29 | 64 场考试倒计时 | zimai233/dsh-exam-countdown | ✅ 安装（零配置） | git |
| 31 | Hermes 风格工具搜索 | Letter2025/dsh-tool-search | ✅ 安装 | npm `dsh-tool-search` |
| 33 | 专业翻译 | ShiXiangYu2/dsh-translate-pro | ✅ 安装（需 SILICONFLOW_API_KEY） | git |
| 35 | 设计美学技能包 | zhaiyateng/dsh-design-skills | ✅ 安装 | git |
| 36 | 11 个可分享工作流 skill | jeremy9682/dsh-skill-pack | ✅ 安装（实际 12 个 skill） | npm `@jeremy9682/dsh-skill-pack` |
| 37 | 软件工程方法包 | GanyuanRan/Aegis | ✅ 安装（1013★，peer 依赖与官方 bundle 完全匹配；npm 上的 `aegis` 是无关旧库，必须 git 装） | git |
| 40 | 桌宠小鲸鱼 | nzl153/pet-whale | ✅ 安装 | npm `pet-whale` |

### 第二层：用户点名但需要二选一/评估 → 研究后决定

| # | 需求 | 候选 | 决策 |
|---|---|---|---|
| 3 | 逛商场式插件市场 UI | dsh-find-plugin / stakeswky-awesome-dsh / tuogusa-toggle / kimiya1010-market / 2BingLing-dsh-market / workshop | **@dsh-market/plugin**（npm，1928 收录+五维评分+推荐+收藏+AI 安装，真商场）+ **dsh-plugin-toggle**（唯一运行时开关/删除管理器）+ **dsh-find-plugin**（agent 内查找工具，零冲突）。不装 workshop（与市场双入口冗余）。stakeswky/awesome-dsh 依赖 Workers 付费计划，不装 |
| 5 | 读图/OCR/版面结构化 | modlens(2074★) vs dsh-vision-router(242★) | **dsh-vision-router**（npm，零 Key 开箱：OVH 免费链+11 像素工具+本地 tesseract OCR+JSON 结构化证据；modlens 无内置免费链需自配引擎，且两者同装会双份图片包装 → 二选一主力）。modlens 作为备选记录 |
| 6 | 操控本机 Tabbit 浏览器 | Tabbit-Browser/dsh-plugin / playwright / nuphus-mcp | **Tabbit-Browser/dsh-plugin**（唯一直接操控本机 Tabbit，含 canvas 视觉工作流，正合 web 游戏实时画面感知）+ **dsh-playwright-browser**（独立 Playwright 实例做自动化测试，勿与 nuphus 同装——工具名撞车） |
| 8 | 更好看的视觉页面 | Web UI 插件 vs 桌面应用(dshcode/DSH-Launcher) | **Web UI 插件路线**（与现有 GUI 无缝、可热更、风险低）：dsh-visualize（交互卡片）+ dsh-chat-timeline + uisfx + pet-whale 组合。桌面应用（dshcode 是 DSH fork 需独立维护、DSH-Launcher 需源码构建 Tauri）仅记录，不装 |
| 9 | 回退 + 自动继续 | auto-continue / recall-plugin / message-edit | **dsh-client-auto-continue**（npm，错误分类+自适应退避+模板化继续）+ **dsh-recall-plugin**（npm，文件影子 git 快照+会话回退，唯一同时回退文件与对话）+ **dsh-message-edit**（npm，分支版本时间线，兼需求 21） |
| 14 | 右侧聊天索引轨道 | dsh-chat-timeline / vlln-navbar / milestone | **dsh-chat-timeline**（npm，1:1 移植 DeepSeek 官网 ScrollNav：每用户消息一条/悬停预览/点击跳转/滚动高亮）。⚠️ 唯一缺口："两阶段 15→40 字符悬停预览"未实现（现为单阶段展开面板 80 字符），需二次开发 |
| 15 | md/docx/pptx/pdf 阅读 | dsh-office / mineru / dsh-doc | **@huiliyi37/dsh-office**（pptx 读写编辑+docx/pdf/xlsx，本地零依赖）+ **dsh-doc**（离线 OCR，Windows x64 开箱即用，扫描件/图片文档）。不装 mineru（需自部署服务器，与 dsh-doc 重叠） |
| 16 | 提示词优化前后对比/替换/回退 | prompt-enhancer / composer-polish / prompt-manager | **dsh-prompt-enhancer**（一键增强+撤回+记忆链+5 模式）+ **dsh-prompt-manager**（npm，系统提示词可视化流水线/幕布/20 轮历史回看，天然支持"改前改后对比"）。⚠️ 现成插件均无"左右 diff 对比视图"，需自行补 |
| 21 | 会话分叉独立演进 | message-edit / sidechain / btw / ramify | **dsh-message-edit**（parentSession 版本树+Timeline 切换，最接近"分叉后共享历史独立演进"）+ **dsh-btw**（git，rc.6 兼容的一次性旁路提问；sidechain 只适配 rc.5 不选） |
| 22 | 最先进 loop 工程 | spec-loop / deep-research / Aegis | **dsh-spec-loop**（git，唯一完整 spec 闭环：生成→批准→实现→逐条验收→归档，OpenSpec 兼容）+ Aegis（方法层） |
| 30 | 比 websearch 更强 | anysearch-dsh / search-boost | **@anysearch/anysearch-dsh**（npm，直驱内置 web_search：匿名额度免 key、批量 1-5 并发、垂直/标签/区域/语言高级搜索、可选正文清洗）。不装 search-boost（两者都 patch web_search，二选一） |
| 32 | nuphus-mcp 能否满足 web 游戏开发 | jiayan-xu/dsh-nuphus-mcp | ❌ **不装**。浏览器部分是 Chrome/Edge CDP，**无法操控 Tabbit**；需自行用 Rust 工具链编译 nuphus-mcp.exe（仓库 0★ 无 README 无安装自动化）；与 playwright 工具名撞车。桌面级 computer-use 场景才考虑 |
| 34 | skill 管理页（禁用/删除） | tuogusa-toggle / YTxue-manager | **dsh-skill-manager-ytxue**（git，设置侧边栏 Skill 管理面板：列表/启用/停用/批量导入/规范检查自动修复）+ dsh-plugin-toggle（插件层开关/删除） |
| 38 | 最强 git 插件 | 多个候选 | 组合：**alt dsh-git-graph**（图形视图）+ **dsh-git-worktree**（worktree）+ **dsh-github-login**（git，设备码登录同步 gh CLI）+ **@loserfox/git-identity**（git，提交身份固定），形成"登录→身份→提交"闭环 |

### 第三层：无指定插件 → 研究后选定

| # | 需求 | 决策 |
|---|---|---|
| 1 | 最先进 harness 工程 + 自主进化 | **dsh-continual-evolve**（npm，版本化/可审计/可回滚的 harness 状态，benchmark 验收，238 测试）+ **Aegis**（基线优先/验证优先方法）+ **dsh-plugin-scout**（侦察生态避免重复造轮子）。不装 dsh-evolve（与 continual-evolve 工具名冲突二选一） |
| 2 | 多 agent 协作 | **@huanlin/dsh-plugin-yet-another-subagent**（npm，可配置子代理 profile+Web UI 设置/实时进度/子代理树）+ 官方内置 subagent/workflow/ralph |
| 4 | 与 GitHub 协作 | **dsh-github-login** + **@loserfox/git-identity**（见 38） |
| 7 | 自主操控 PowerPoint | **@huiliyi37/dsh-office**（pptx_create/read/edit，pptxgenjs，唯一具备 pptx 读写编辑） |
| 17 | 专家/技能商店 + 对话内注入 | 由 **@dsh-market/plugin** 商店覆盖（含 AI 子代理安装、技能发现）；DSH 内置 agent-presets 机制；Aegis/skill-pack/design-skills 提供可注入技能包 |
| 18 | 连接器商店 + 对话内注入 | ⚠️ **生态空缺**：无成熟"连接器商店"插件。近似方案：@dsh-market/plugin 收录的 MCP 插件 + hyqhyq3/dsh-mcp-manager（设置页 MCP 管理，未装）。记录为待观察项 |
| 19 | 联网可更新可搜索的项目提示词库 | ⚠️ **部分满足**：dsh-prompt-manager（npm，本地提示词预设库+可视化编辑，非联网库）+ dsh-find-plugin/awesome-dsh（发现渠道）。真正"联网一键生成成熟项目提示词"插件未找到，记录为待观察项 |
| 20 | 安全彻底删除会话 | **dsh-plugin-session-delete**（git，风险确认+先停 agent+日志/投影/记账一致清理，web 与桌面通用） |
| 39 | 代码质量（安全/架构/效能/异味） | **Aegis**（验证/调试/审查/修复跟踪方法论，peer 全匹配）+ **dsh-spec-loop**（逐条验收门）。可选项记录：chaojixinren/dsh-reviewer-bot（评审机器人）、morluto/leantoken（上下文精简） |

---

## 二、冲突矩阵（已核查）

| 冲突对 | 情况 | 处理 |
|---|---|---|
| 两个 dsh-git-graph | 同 slot `conversation.view` 同 id 同 order | 只装 alt 版 |
| dsh-git-worktree vs dsh-worktree | 同名工具+同名命令+同目录 | 只装 dsh-git-worktree |
| dsh-evolve vs dsh-continual-evolve | 工具名冲突 | 只装 continual-evolve |
| dsh-nuphus-mcp vs dsh-playwright-browser | browser_* 工具名撞车 | 都不装 nuphus，装 playwright |
| dsh-sidechain vs dsh-btw | /btw 重叠，sidechain 仅 rc.5 | 只装 btw（rc.6） |
| dsh-market vs dsh-plugin-workshop | 双市场入口冗余 | 只装 @dsh-market/plugin |
| @anysearch vs dsh-search-boost | 都改 web_search | 只装 anysearch |
| modlens vs dsh-vision-router | 双份图片包装/路由重叠 | 只装 vision-router |
| uisfx vs notification-center | 事件层双响（任务成功/失败/等待批准） | 同装但分工：uisfx 管声音，通知中心各事件关音效只留浏览器通知 |
| tabbit-browser vs 其他浏览器插件 | SKILL 禁止切换后端 | 行为层约定，工具名不冲突 |
| pet-whale vs chat-timeline | 右下角位置轻微邻近 | 可共存（有光标避让/隐藏模式） |

---

## 三、安装清单（合计 ~40 个）

- **npm 安装（24）**：dsh-vision-router、dsh-plugin-uisfx、dsh-find-plugin、@dsh-market/plugin、dsh-plugin-toggle、dsh-git-worktree、dsh-tool-writing、dsh-deepread、dsh-tool-search、pet-whale、@jeremy9682/dsh-skill-pack、dsh-chat-timeline、@lyhalal/dsh-notification-center、dsh-client-auto-continue、dsh-recall-plugin、dsh-message-edit、@huiliyi37/dsh-office、dsh-doc、dsh-playwright-browser、dsh-continual-evolve、@huanlin/dsh-plugin-yet-another-subagent、@anysearch/anysearch-dsh、dsh-prompt-manager、**@linxin666/dsh-skins（需求8 皮肤全家桶：qq98/ths/xp/blue-fantasy/dragon-heir/minecraft/miku/trading 等，GUI 皮肤中心即时预览，互斥切换）**
- **git 安装（18）**：icefall7/dsh-plugin-scout、wjy9902/dsh-web-default-session、Nagi-ovo/dsh-visualize、1841220388zzzcccxxx-star/dsh-git-graph、ConsoleSun/Gemini-Eyes、CZX2244/dsh-bilibili、akqwpeter-prog/dsh-media-skills、zimai233/dsh-exam-countdown、zhaiyateng/dsh-design-skills、GanyuanRan/Aegis、Tabbit-Browser/dsh-plugin、lsz-asd/dsh-plugin-session-delete、Fishsb/dsh-prompt-enhancer、tianji-qingtian/dsh-spec-loop、iyllyt/dsh-btw、LoserFox/dsh-git-identity、YTxue/dsh-skill-manager-ytxue、**dsh-github-login（file: 本地依赖，见修复记录）**

> 说明：dsh-translate-pro 因 API 不兼容已卸载；dsh-github-login 从 git 依赖改为本地 file: 依赖（补丁持久化）。合计 42 个第三方插件 + 2 官方 bundle。
> ⚠️ 未装 `@linxin666/dsh-web-ui-all` 全家桶：其内置 git-graph（与已装 alt 版同 slot 冲突）与 pet（与 pet-whale 重叠），只装独立的 dsh-skins 皮肤包。

> 安装后需配置的密钥：DSH_WRITING_API_KEY（tool-writing）、SILICONFLOW_API_KEY（translate-pro / media-skills）、GLM_API_KEY（media-skills）、Google Cookie（Gemini-Eyes）、ANYSEARCH_API_KEY（可选）。
> 生效方式：web GUI 需重启 `dsh web` 才能加载新插件（本会话运行期间不重启，避免中断）。

---

## 四、安装执行结果（2026-08 实测）

### 已安装并验证：41 个插件全部启动成功

**启动验证**：备用端口 3099 测试实例 `node .../dsh/bin.js --profile web --port 3099` 启动成功，HTTP 200，stderr 零错误（仅 SQLite 实验特性警告）。关键插件 API 实测活跃：
- `/plugin-toggle/api/list` → 200 JSON（插件开关/删除管理）
- `/dsh-notification-center/poll` → 200 JSON（通知中心）
- `/prompt-manager/health` → 200 `{"ok":true,"version":"1.4.1"}`
- `/github-auth/status` → 200 `{"ok":true,"loggedIn":false,...}`
- gemini-web MCP（`uv run gemini-mcp`）→ 正常拉起，无 spawn 错误
- **`/market/api`（POST）→ `{"ok":true}` 返回 1928 个插件及评分**（数据源 dsh.market/plugins.json，4.75MB，重定向可达；评分示例：omdsh-dev/dsh-toolkit score=66）
- `/git-graph/index.html` → 200（140KB 图谱页面正常服务）

### 安装过程中发现并修复的问题

| 问题 | 插件 | 修复 |
|---|---|---|
| `Cannot find native binding`（@xberg-io/xberg 的 Windows 二进制缺失；npmmirror 镜像元数据陈旧只解析到 0.0.1） | dsh-doc | 用官方 registry tarball 精确安装 `@xberg-io/xberg-win32-x64-msvc@1.0.14`（URL 直装绕过镜像） |
| `Cannot find package 'schemastery'`（peer 未装，autoInstallPeers:false） | @huanlin/dsh-plugin-yet-another-subagent | 显式 `dsh plugin add schemastery` |
| `Cannot read properties of undefined (reading 'render')`（defineTool 缺 output 字段，与 dsh-tools 0.1.0-rc.6 API 不兼容） | dsh-translate-pro | **已卸载**（需 SiliconFlow key 且上游不维护，待上游修复后再装） |
| patch 引用 `@deepseek-ai/dsh-github-login` 但实际包名是 `dsh-github-login` | dsh-github-login | 修改其 `cordis.patch.yml` 的 name 字段 |
| `ctx.get('webServer')` 在 apply 时为 undefined 导致路由未注册 | dsh-github-login | 修改 `lib/index.js`：`inject: ['webServer']` + 改用 `ctx.webServer` |
| `gemini-mcp` 命令无法 spawn（cwd 空导致 uv 找不到项目） | Gemini-Eyes | 用户 patch 层（`cordis.patch.yml`）覆盖 gemini-web 的 `cwd` 指向包目录；已 `uv sync` 预装 venv |
| Windows 离线 OCR 运行时缺失（Node 引擎降级无 OCR） | dsh-doc | 运行 `fetch-runtime-win32-x64.mjs` 下载 146.8MB 运行时（SHA-256 锁定，60 文件校验通过）到 `~/.dsh/runtimes/dshdoc-runtime-win32-x64`，并在 patch 层配置 `engine: python` + `defaultOcr: true` |
| rc.6 设置面板只显示硬编码白名单命名空间，第三方插件设置卡渲染为空 | dsh-client-auto-continue | 运行随包 `patch-expose`（幂等，修改官方 dsh-host-apiproxy 的 `exposedNamespaces()` 为注册表驱动，无插件特定字符串）→ **所有**第三方插件（uisfx/notification-center/vision-router/deepread/auto-continue 等）的设置卡都能在 GUI 显示；已应用到 npx 缓存副本（profile junction 同一文件），语法校验+第 9 轮启动测试通过；dsh 重装后需重跑 |
| node_modules 内对 git 源包的补丁会被 pnpm 重新物化还原（EPERM 事故中发现） | dsh-github-login | **改用本地 file: 依赖**：克隆到 `~/.dsh/plugins/dsh-github-login`（tarball 经 codeload 下载，git 协议当时不可达），两处修复（patch name + inject webServer）打在持久源目录，`dsh plugin add file:...` 引用——此后任何 pnpm 操作都不会还原补丁 |
| Gemini-Eyes 的 uv venv 建在 node_modules 内导致 pnpm EPERM | dsh-gemini-eyes-bundle | venv 迁出至 `~/.dsh/runtimes/gemini-eyes-venv`，patch 层配置 `UV_PROJECT_ENVIRONMENT` 指向外部路径 |

> ⚠️ 上述 node_modules 内的两处修改（github-login 的 patch/lib）在插件重新安装后会丢失，需重打补丁。

### 未安装/暂缓（附原因）

| 插件 | 原因 |
|---|---|
| dsh-translate-pro（需求33） | 与当前 dsh-tools API 不兼容导致启动崩溃，已卸载；待上游修复 |
| jiayan-xu/dsh-nuphus-mcp（需求32） | 无法操控 Tabbit（仅 Chrome/Edge CDP）；需自行编译 Rust 二进制；与 playwright 工具名冲突 |
| liustack/modlens（需求5备选） | 与 dsh-vision-router 二选一（同装双份图片包装），vision-router 免 Key 开箱更优 |
| dsh-plugin-workshop / 2BingLing-dsh-market 之外的其它市场 | 与 @dsh-market/plugin 双市场入口冗余 |
| dsh-evolve / dsh-sidechain / FlashingChen-dsh-worktree / dsh-search-boost / composer-polish | 与已选插件功能重复或版本不兼容（详见冲突矩阵） |
| 桌面应用（dshcode/DSH-Launcher） | 非插件（fork/源码构建），Web UI 插件路线更稳 |
| 皮肤类插件 | 主观性强，可从插件市场自行挑选 |

### 重启后需要配置的密钥（写入 ~/.dsh/.credentials.yaml）

```yaml
DSH_WRITING_API_KEY: <独立 DeepSeek key>      # dsh-tool-writing（网络小说引擎）
SILICONFLOW_API_KEY: <SiliconFlow key>        # dsh-media-skills（免费生图）
GLM_API_KEY: <智谱免费 key>                    # dsh-media-skills（免费读图 GLM-4V-Flash）
ANYSEARCH_API_KEY: <可选>                      # anysearch 账号额度（匿名额度可直接用）
```
Gemini-Eyes 无需 key：Google Cookie 自动从 Chrome 提取（浏览器需登录 gemini.google.com，关闭浏览器后首次调用自动提取；新浏览器加密需手动导出 cookies.json 到 `~/.config/gemini-web-mcp/cookies.json`）。

### 环境注意事项（重要）

1. **Git HTTPS→SSH 已全局接管（2026-08 修复）**：原 `git config --global http.proxy/https.proxy = http://127.0.0.1:6696`（当前不可达）导致所有 github HTTPS git 操作失败。已执行：
   - `git config --global url."git@github.com:".insteadOf "https://github.com/"` —— 所有 github.com 的 clone/fetch/pull 自动走 SSH（id_rsa 已在 GitHub 授权，账号 Geetie；SSH 22 与 ssh.github.com:443 均可达），**彻底绕开死代理**，普通 `git clone`/`dsh plugin add github:...`/pnpm git 依赖均无需再带环境变量。
   - 若要还原：`git config --global --unset-all url."git@github.com:".insteadOf`。
   - 注意：非 github.com 的 HTTPS git 主机仍受死代理影响；若 6696 是某个代理工具（VPN 等）的端口，启动该工具后 HTTPS 才恢复，届时可保留 SSH 接管（互不冲突）。
2. **npm registry 是 npmmirror 镜像**：对新发布插件元数据可能陈旧（本次 xberg-win32 即因此解析错版本），可用官方 registry tarball URL 直装绕过。
3. **重启命令**：停止当前 dsh web 进程后，重新执行启动命令（`npx @deepseek-ai/dsh web` 或原启动方式）即可加载全部插件。已验证同一 profile 在 3099 端口冷启动无错误。

### Git 图谱（dsh-git-graph）使用说明
- **自动发现**：在某个 git 仓库目录内打开 DSH 会话，对话页旁的「Git 图谱」标签页即自动展示该仓库（无需配置）。workspace 自动发现优先级最高。
- **仓库切换器**：非当前会话工作区的其它仓库需在 `~/.dsh/profiles/web/cordis.patch.yml` 的 `git-graph` 条目下配置 `repos` 列表（正斜杠路径）。该文件里已留好注释示例。
- **已处理**：bundle 自带的占位路径 `C:/path/to/your/repo`（无效示例）已在用户 patch 层覆盖为 `repos: []`，避免出现在切换器里。
- 非 git 仓库目录的会话会显示空态提示（正常行为，不回落配置仓库）。
