# 系统性根因诊断报告 — 忆述光华 8 页面联调/还原遗留项

**诊断时间**：2026-08-30 01:00~02:05（GMT+8）
**分支/路径**：`wrap1-agentA2-ui-restore` / `D:\GuangH-App\.wt\wrap1-agentA2-ui-restore`
**诊断人**：主 Agent（多轮真机实验 + 官方文档/源码取证 + 3 个调研/审计 subagent）
**用途**：修复 Agent 的工作输入。所有结论附证据；修复方案按优先级给出，未实施。

---

## 〇、一页结论

| # | 问题 | 根因判定 | 状态 |
|---|---|---|---|
| P0-2 | index `fetchTimeline` 的 `uni.request` 无回调、时间轴不加载 | **Vapor 5.24 运行时对"特定参数组合"的 uni.request 静默丢弃（不派发、不回调、不超时）**。头号嫌疑：GET 传 `data: {}` 空对象（官方约束 app-android data 只能 UTSJSONObject/string）；次嫌疑：`header`/`timeout`。系统性背景：项目在 5.25 前 import 21 个 `.ts` 文件，违反官方蒸汽模式明文约束 | 根因已收敛到参数级，待二分定位后规避 |
| P1-1 | `loadFontFace` 报 `reading 'vm'` | `App.uvue onLaunch` 调用未传 `global: true`，非 global 路径需"当前页面"，onLaunch 无页面 → 空上下文解引用 | **已修复并真机验证**（3 个字重全部 loaded） |
| P0-1 | hens-svg 图标全空白（`HensSvgView` undefined） | 标准调试基座不含 UTS 原生组件插件（预期行为）；且 hens-svg 未声明支持蒸汽模式 | 方案已定：**弃用 hens-svg，改官方 `image`+SVG 文件**（4.81+ 支持，见 §2.3） |
| P0-3 | `UTSAndroid is not defined` | `yishu-photo-watch`/`yishu-background-tasks` 纯 UTS 插件在标准基座无 `UTSAndroid` 全局 | 非核心功能；index 侧已临时 guard，`initSync` 侧**尚未 guard（待修）** |
| 像素差距 | 8 页面与设计稿明显不符 | **提取管线 bug：8 份 design_data.json 解析全失败（`'Circle' object has no attribute 'r'`），几何/文字提取量为 0——还原 Agent 实际无数据可依**。已重新从原始 SVG 直接解析出真实差距清单（`design_gap_audit/`） | 差距清单已产出，待修 |
| Token | 缓存 token 为 2 天前签发（access 仅 2h）→ timeline 401 | token 过期；401→refresh 链路因 P0-2 未能验证 | 已 `pm clear` + 新鲜登录成功（01:45 验证） |

---

## 一、证据台账（P0-2 定位过程，全部可复现）

### 1.1 已排除假设（每条有实验依据）

| 假设 | 结论 | 依据 |
|---|---|---|
| adb reverse 隧道僵死 | **排除** | 请求挂起期间，设备浏览器/`adb shell curl` 走同一隧道 `127.0.0.1:8000` 秒回 200；`/proc/net/tcp` 无半开连接 |
| 后端未运行/代码版本不符 | **排除** | 曾发现监听 8000 的是旧进程（日志写到别处）——已杀掉并以 worktree 代码重启（现 PID 19168，日志 `backend/uvicorn_run.out` 可信） |
| cleartext http 拦截 | **排除** | 多个 http 明文请求成功过；若拦截会立刻走 fail（CLEARTEXT），不会静默 |
| 并发上限排队 | **排除** | uni-app x Android 无文档化并发上限（"10 并发"仅微信小程序）；启动期并发仅 2~4 |
| `utils/api.ts` 封装代码缺陷 | **排除** | 与 develop（联调通过的旧页面）**逐字节一致**（`git diff develop..HEAD -- client/utils/` 仅 event_ops/search_api/voice 有差异） |
| 旧日志误导 | **已澄清** | 后端日志中唯一的 `GET /api/v1/events/timeline 401` 是调研 subagent 的**设备侧 curl 取证**（无 token 故 401，11ms），不是 app 发的——**app 的 timeline 请求从未到达后端** |

### 1.2 关键对照实验（同一构建、同一隧道、相隔秒级）

| 调用 | 参数形态 | 结果 |
|---|---|---|
| `probe1`（App.uvue setTimeout） | `url + method:'GET' + success/fail` | ✅ 58ms 返回 200，回调正常，后端收到 |
| `wechatLogin`（auth.ts） | POST + **非空** `data:{code,device_id}` + success/fail | ✅ 回调正常（`status=200`），后端收到并签发新 token |
| `doRequest`（api.ts，timeline/echo） | GET + **`data:{}` 空对象** + `header:{Content-Type, Authorization}` + `timeout:15000` + complete + success/fail | ❌ `[dbg-req] fire` 打印后**石沉大海**：无 success/fail/complete，后端从未收到，15s 超时也不触发 |
| `probe2`（Promise 形态） | `uni.request(...).then` | ❌ `.then is not a function`——**uni-app x 的 uni.request 返回 RequestTask 非 Promise，属文档行为**，探针勿再写 Promise 形态 |

**推论**：同一运行时、同类（.ts）模块、同一 API，差异只在参数集 → **触发因子在 `data:{}` / `header` / `timeout` 三者（或其组合）**，不在模块、不在网络、不在封装逻辑。

### 1.3 官方文档约束（根因排序依据）

`uni.request` 参数表（https://doc.dcloud.net.cn/uni-app-x/api/request.html）：

- **`data any`——"在 app-android 端，参数类型只能为 UTSJSONObject 或者 string 类型"**；对应失败码 `600008 data参数类型不合法`。
- `header` 类型为 `UTSJSONObject`；`timeout number` 默认 60000；`complete` 官方支持。
- 官方 hello-uni-app-x 蒸汽模式示例含"不同 header、超时切换"演示 → header/timeout 本身在 Vapor 可用（降低其嫌疑，但不排除本项目编译形态下的组合问题）。

**头号嫌疑链**：`utils/*.ts` 在 Vapor（JS 引擎驱动）下编译后，`data == null ? {} : data` 的 `{}` 是**普通 JS 空对象**。GET 请求的 data 走"对象→查询串/桥接转换"路径，空对象在该路径的类型校验/序列化存在盲区 → 派发被静默吞掉（连 fail/complete 都不回调，即 5.24 桥接缺陷的表现形态）。`wechatLogin` 的**非空**对象能过桥，与此假说一致。

**系统性背景（加重因素）**：官方公告（置顶，2026-08-28 更新，https://ask.dcloud.net.cn/article/42377）明文：**"在 HBuilderX 5.25 之前……不要 import 外部后缀为 .ts 或 .js 的文件，把文件名改名为 .uts，里面还是 ts/js 的内容"**。项目 `utils/` 下 21 个 `.ts` 全部违规（同目录已有 `agg_runner.uts`，混用状态本身是风险面）。同一次启动的运行时异常群（`UTSAndroid is not defined`、`HensSvgView` undefined、loadFontFace vm 错）印证 5.24 蒸汽运行时在该写法下桥接不稳。

---

## 二、修复方案（供修复 Agent 执行，按优先级）

### 2.1 P0-2：uni.request 参数级规避 + 合规化（预计 1~2 小时）

**Step 1（二分定位，一次部署搞定）**：在 `App.uvue` 现有探针处改为 5 组单变量探针（每组独立 query 参数，后端日志对账 + 控制台对账）：

```
A: url+method+success/fail                      （基线，已知可通）
B: A + timeout:15000
C: A + header:{'Content-Type':'application/json'}
D: A + data:{}                                  ← 头号嫌疑
E: A + header(含 Authorization Bearer 真token) + data:{} + timeout:15000（完整复刻 doRequest）
```

**Step 2（按命中参数规避）**：
- `data:{}` 命中（最可能）→ `doRequest` 中 **GET 且 data==null 时不传 data 键**（构造 options 时条件性加入）；
- `timeout` 命中 → 移除 timeout 参数，改 JS 层竞速超时（`setTimeout` 兜底 resolve(null)）；
- `header` 命中 → 拆查是 `Content-Type`（GET 可去掉）还是 `Authorization`（dev 期可临时改 query 传 token，需后端配合）；
- 若 E 整体挂而单参数都通 → 组合问题，按"最小参数集"重构 doRequest。

**Step 3（官方合规，强烈建议无论结果）**：`utils/` 下 21 个 `.ts` 改名 `.uts`（内容不动；import 均不带后缀，无需改 import 语句）。改后冷启动复测——有可能顺带消灭一批桥接怪象。

**Step 4（防御，必做）**：业务层看门狗，框架再丢回调也不无限挂起：

```
withTimeout(promise, REQUEST_TIMEOUT_MS + 5000, path) → 超时 resolve(null) + 日志
```

**验收**：`[dbg-req] ok /api/v1/events/timeline status=200` + `[dbg-index] fetchTimeline resolved: N events` + 首页渲染卡片；连续冷启动 3 次稳定。

### 2.2 P1-1：字体（已修复，保留即可）

`App.uvue` 已改为 `global: true` + `weight` 移入 `desc` + url() 内路径加引号，真机验证 3 字重全部 `font loaded`。**修复 Agent 保留该改动，并按 §2.1 顺手给 onLaunch 每步加独立 try/catch**（本次取证两次观察到 onLaunch 被异常腰斩）。探针代码（`[dbg-probe]`）用完后删除。

### 2.3 P0-1：弃用 hens-svg → 官方 image+SVG（预计 0.5~1 人日）

依据：官方 `image` 组件 Android 4.81+ 原生支持 SVG（本项目 5.24），无需自定义基座；hens-svg 是付费加密小众插件（下载 15 次），未声明蒸汽模式兼容，云打包自定义基座路线风险多（账号授权/打包终止/蒸汽兼容未验证）。详见 `research_custom_base.md`。

**实施要点**：
1. 现状：7 个文件 41 处 `<hens-svg>`（TabBar.uvue 5、detail 6、index 1、interview 2、messages 4、profile 15、record 8）；TabBar 用动态 `:color` 着色（选中/未选中两态）。
2. 把内联 SVG path 数据落为 `static/icons/*.svg` 文件，**颜色直接烘进 fill**；需换色的图标（TabBar 5 个 + 其他激活态）出**双态两份文件**（`*-active.svg` / `*-inactive.svg`），模板按状态切 `src`。
3. 用 `<image src="..." style="width:Xpx;height:Ypx"/>` 替换（**必须显式宽高**，默认 320×240）。
4. 替换完成后移除 `uni_modules/hens-svg`（`HensSvgView` 报错刷屏消失即验收）。
5. 备选（仅当 image+SVG 渲染质量不达标）：iconfont（ttf + @font-face + unicode 直显，官方支持，可任意换色）；注意 app 平台无伪元素，必须 unicode 直显。

### 2.4 P0-3：UTSAndroid 守护（预计 0.5 小时）

- `index.uvue` 的 photoWatch 初始化**已临时 try/catch**（保留）。
- **未修**：`App.uvue onLaunch → initSync() → registerBackgroundSync`（`yishu-background-tasks`）同样炸 `UTSAndroid is not defined`，会腰斩 onLaunch 尾部。修：`sync_client.initSync` 内对 `registerBackgroundSync` 包 try/catch（标准基座降级为仅前台定时同步，插件自身已有"标准基座降级"分支但没兜住这条路径）。
- 长期：自定义基座落地后这些原生能力（相册监听/后台任务）自然恢复，不属于本轮阻塞。

### 2.5 像素级还原（差距清单已产出，按单施工）

**根因**：旧提取管线 bug——`design_data_for_agents/*/design_data.json` 解析时 `'Circle' object has no attribute 'r'`，**8 页几何与文字提取量全为 0**，还原 Agent 等于无数据目测。不要再信任 design_data_for_agents 的数值。

**新差距清单**：`design_gap_audit/`（00_summary.md + 01~08 每页报告，元素级 设计值/代码值/差距/严重度 表格 + 每页 Top10）。

**最重三页**：
1. **index 时间轴主页**：hero 标题应为"忆述光华"48rpx（现"回忆"73rpx）、缺"峰"头像、两处渐变方向反、播放钮配色反、TabBar 底色错；
2. **messages 消息中心**：结构级偏离（多分段器+TabBar、缺返回导航、时间胶囊渐变面板丢失、行距/卡距全错）；
3. **detail 记忆详情**：悬浮按钮配色反、收藏图标星→心。

**横切问题（先修组件再修页面）**：
- TabBar 组件：米色底/无阴影/图标大 47%/path 残缺 → **4 页受累，第一优先**；
- 横向渐变被做成纵向；半透明用裸 hex 不用 fill-opacity；图标混用 emoji；字号普遍偏大 2-6rpx；阴影偏弱；文案功能化漂移。

**验收**：每页修完用 `adb exec-out screencap -p` 截图，与设计稿 SVG 渲染图并排比对（主 Agent 可视觉复核）。

### 2.6 联调收尾（P1-2/P1-3）

- record 上传链路：P0-2 修好后真机选图上传，看后端 `POST /api/v1/contents/upload` + 聚合链路（复用 index 已验证范式）。
- 跳转链路：TabBar 5 标签 reLaunch、index 卡片→detail、profile→settings、FAB→record，逐一真机点击验证（pages.json 注册已在联调提交中修复过，回归即可）。
- messages 测试数据已造好（4 条，3 未读 1 已读）。

---

## 三、环境基线（修复 Agent 接手时的现场）

| 项 | 状态 |
|---|---|
| 后端 | worktree 代码，PID 19168，端口 8000，日志 `backend/uvicorn_run.out`（本次重启后全新可信）。启动命令（在 `backend/` 下）：`.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000` |
| 设备 | nova 11（DKS9K23526028855），USB 连接，USB 调试开 |
| adb reverse | `tcp:8000/8001/8002` 已建立（8001/8002 为 HBuilderX 自管，勿动）。僵死时：`adb reverse --remove-all && adb reverse tcp:8000 tcp:8000`，必要时 `adb kill-server && adb start-server` 后重加 |
| Token | 已 `pm clear io.dcloud.uniappx` 清掉 2 天前旧 token；01:45 新鲜 wechat 登录成功（access 2h 有效，过期后走 401→refresh 或重新登录） |
| HBuilderX | 5.24.2026081301，已拉起；部署：`D:\HBuilderX\cli.exe launch app-android --project D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client`（约 40s 编译+同步） |
| 设备侧连通性自检 | `adb shell curl -sS -m 10 -o /dev/null -w '%{http_code} %{time_total}' http://127.0.0.1:8000/healthz`（nova11 自带 curl） |
| 未提交改动（4 个文件） | `client/App.uvue`（字体修复【保留】+ dbg-probe 探针【完工后删】）、`client/pages/index/index.uvue`（dbg 日志 + photoWatch guard）、`client/utils/api.ts`（dbg 日志）、`client/utils/auth.ts`（dbg 日志）——dbg 日志建议保留到 P0-2 验收通过再删 |
| 已知报错刷屏（无害但干扰阅读） | `HensSvgView` undefined（待 §2.3 消除）、`UTSAndroid is not defined`（待 §2.4 收口） |
| 坑备忘 | ① 盯后端日志要盯**当前进程**的日志文件（本次曾被旧进程旧日志误导一整轮）；② `uni.request` 无 Promise 形态（返回 RequestTask）；③ 5.24 蒸汽模式下不要新写 .ts 文件（官方要求 .uts）；④ adb 用同一个二进制（SDK platform-tools），避免与 HBuilderX 内置 adb 版本冲突导致 server 重启击穿 reverse |

---

## 四、建议施工顺序（修复 Agent）

1. §2.1 P0-2 请求修复（二分→规避→.ts 改 .uts→看门狗）→ **时间轴出数据**
2. §2.3 弃 hens-svg → **图标可见，UI 可验收**
3. §2.4 initSync guard + §2.2 onLaunch try/catch 收尾
4. §2.5 像素修复（TabBar 组件先行，再 index/messages/detail 重灾页，再其余 5 页）
5. §2.6 上传链路 + 跳转链路回归
6. 清理探针/临时文件（worktree 根目录约 50 个诊断脚本、截图、`_wrap_*.html` 渲染辅助），提交干净状态

**完成定义**：8 页面真机可看（图标/字体/布局还原到差距清单的高严重度项清零）、前后端联调全通（时间轴/搜索/消息/访谈/详情/上传）、跳转链路无死链、冷启动 3 次稳定。

---

## 附：诊断过程产出文件

- `research_custom_base.md` — 自定义基座 + SVG 方案调研（含云打包流程/风险/CLI 参数）
- `research_vapor_issues.md` — Vapor 运行时问题调研（含 logcat 取证、官方源码级根因链）
- `design_gap_audit/` — 9 份像素级差距报告（基于原始设计稿 SVG 直接解析，非旧提取数据）
- `HANDOFF_20260830.md` — 上一任 Agent 交接文档（背景）
