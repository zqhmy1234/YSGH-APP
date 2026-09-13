# 忆述光华 UI 还原 + 前后端联调 修复计划（修订版 v2）

> 修订要点（回应用户反馈「漏了很多 / 数据提取脚本无效」）：
> 1. 新增**第一优先阶段：修复设计数据提取管线**——旧的 `parse_svg_v2/v3.py`（svgelements）产出的 `design_data_for_agents/*/design_data.json`、`design_data_v2/`、`design_data_v3/` **8 份全部解析失败（elements=0）**，必须弃用；改用已在 `.cowork-temp/svg_parse.py` 跑通的 etree 解析器为唯一正源。
> 2. 修正图标数据来源：TabBar 图标/颜色须取自**页面 SVG**（`定稿 · 精修高保真/我的页/搜索页/消息中心`），**不是**独立的 `TabBar胶囊.svg`（实测为 `#F6F1E7@0.75` 米色旧版，与页面版白色 α.55 不同）。
> 3. 明确临时产物清单（worktree 根 ~50 脚本 + `.cowork-temp` ~100 文件），清理阶段逐项点名。

---

## 摘要（一页结论）

本计划把 `wrap1-agentA2-ui-restore` 分支（worktree `D:\GuangH-App\.wt\wrap1-agentA2-ui-restore`）的 8 页面「能看 + 好看 + 联调通」一次推动到位。事实来源：`DIAGNOSIS_20260830.md`（根因判定+施工顺序）、`research_vapor_issues.md` / `research_custom_base.md`（修复路线取证）、`design_gap_audit/`（9 份像素差距报告）、提取管线脚本（`parse_svg_v2/v3.py` 失败 vs `.cowork-temp/svg_parse.py` 成功）。

**目标（完成定义）**
- 8 页面真机可看：图标可见（弃 hens-svg）、字体加载（global:true）、布局还原到设计稿高严重度项清零。
- 前后端联调全通：时间轴 / 搜索 / 消息 / 访谈 / 详情 / 上传 / 同步。
- 跳转链路无死链；冷启动连续 3 次稳定；探针/临时文件清理干净。
- 设计数据提取管线修复为**唯一可信正源**，不再有 0 元素/缺 fill-opacity/缺渐变方向的问题。

**范围确认（已与用户对齐）**
- 全量推进（P0 阻塞 + P1 字体/守护 + 像素还原 + 联调 + 清理）。
- TabBar 两个坏图标（时间轴/我的）与全部图标：**从页面设计 SVG 提取真实 path**。
  - 设计稿 SVG 目录：`C:\Users\ghf\Downloads\忆述光华 · W1 时间轴页方向小样\`
  - 页面版 TabBar（白色 α.55 + 罗盘/气泡/放大镜/人形）在 `定稿 · 精修高保真.svg`、`我的页.svg`、`搜索页.svg`、`消息中心.svg` 内嵌；`TabBar胶囊.svg` 为 `#F6F1E7@0.75` 旧版，**不采信**。

---

## 当前状态分析（接手现场）

- 分支/路径：`wrap1-agentA2-ui-restore` / `D:\GuangH-App\.wt\wrap1-agentA2-ui-restore`；远程已推送（`a586e80..1d62365`），未合 develop。
- 前端：uni-app x，`manifest.vapor=true`，HBuilderX 5.24.2026081301（< 5.25）。
- 后端：运行中 PID 19168，端口 8000，日志 `backend/uvicorn_run.out`。启动命令（在 `backend/` 下）：`.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 0.0.0.0 --port 8000`。
- 设备：nova 11（`DKS9K23526028855`）USB 连接；`adb reverse tcp:8000 tcp:8000` 已建（僵死处理见风险）。
- Token：已 `pm clear io.dcloud.uniappx` + 重新登录（access 2h 有效）。
- 部署：`D:\HBuilderX\cli.exe launch app-android --project D:\GuangH-App\.wt\wrap1-agentA2-ui-restore\client`。

**关键根因（结论表）**
| # | 现象 | 根因 |
|---|---|---|
| 提取管线 | `design_data_for_agents/*/design_data.json` **8 份全是 elements:0 / texts:0** | `parse_svg_v2/v3.py`(svgelements) 抛 `'Circle' object has no attribute 'r'`，`parse_with_svgelements` 被 try/except 吞掉 → 几何为 0；且 svgelements 丢失 `fill-opacity` 与渐变方向 |
| P0-2 | `fetchTimeline` 的 `uni.request` 不回调 | 5.24 蒸汽运行时桥接吞掉带完整参数的请求；系统性加重：`utils/` 21 个 `.ts` 违反官方 5.25 前「只可 .uts」 |
| P0-1 | hens-svg 图标全空白 | hens-svg 是含原生 Kotlin 的 UTS 组件，标准基座不含其原生代码，且未声明蒸汽模式兼容 → 弃用 |
| P0-3 | `UTSAndroid is not defined` | `initSync → registerBackgroundSync → setBackgroundTaskHandler` 引用 UTSAndroid，标准基座无此全局 → onLaunch 尾部腰斩 |
| P1-1 | `loadFontFace` 报 `reading 'vm'` | `App.uvue onLaunch` 未传 `global:true`，非 global 路径需「当前页面」，onLaunch 无页面 → 已修复保留 |

**范围外（此次不做）**：自定义基座/云打包（不采用）、iconfont（不用，改 image+SVG 双态）、HBuilderX 5.25 升级、DCloud 提 bug、相册监听/后台任务真原生能力（标准基座天然降级）。

---

## 拟改动（8 阶段，每阶段可独立验证 + 独立 commit）

### Phase 0：修复设计数据提取管线（最高优先 · 审计 §四 第 1 条）
**根因**：`parse_svg_v2.py`/`parse_svg_v3.py` 用 `svgelements` 的 `SVG.parse` 后，`recursive_extract_svgelements` 读 `Circle.r`/`cx`/`cy` 时抛 `'Circle' object has no attribute 'r'`，整体被外层 try/except 捕获 → `elements:[]`；且 svgelements 层面丢失 `fill-opacity`（半透明色层）与渐变方向（多处横向渐变）。
**修复**：
1. **确立正源解析器**：采用 `.cowork-temp/svg_parse.py`（原始 `xml.etree` 直接遍历 + transform 累积 + fill-opacity/pattern/渐变(含方向)/radial 保留 + text-run 聚类），它是当前唯一跑通并生成 `design_gap_audit` 的正确解析逻辑。
2. **重新生成可信设计数据**：用正源解析器扫描 `C:\Users\ghf\Downloads\忆述光华 · W1 时间轴页方向小样\`（8 页面 + TabBar/中央＋按钮/空状态等），输出到 **`design_data_v4/`**（新目录，或直接持续写入 `.cowork-temp/parsed/`），每份 JSON 校验 `rects/circles/paths/gradients/filters/text_runs` 非空、含 `fill-opacity` 与渐变方向。
3. **弃用/删除坏产物**：`parse_svg_v2.py`、`parse_svg_v3.py`、`check_parse.py`（及 svgelements 依赖脚本）标记 deprecated 或删除；**明令禁止**再使用 `design_data_for_agents/*/design_data.json`、`design_data_v2/`、`design_data_v3/`（全为 0 元素，属无效数据）。
4. **把测试/比对基线指向正源**：`design_gap_audit/0X_*.md` 的数值以 `design_data_v4/`（或 `.cowork-temp/parsed/`）复核通过后作为像素还原的唯一依据。

**数据源结论（写进交接，避免再踩）**：
- 像素数值权威 = `design_gap_audit/0X_*.md` + `design_data_v4/`/`.cowork-temp/parsed/*.json`。
- 图标 path 权威 = `.cowork-temp/final_icons.json`（detail 六枚）、`icon_paths.json`、`record_icons.json`、`parsed/tabbar.json` 及各页面 `parsed/0X_*.json` 的 `paths[].dhead`。
- ⚠️ TabBar 底色/图标两套数据冲突：`parsed/tabbar.json`（`TabBar胶囊.svg`）= `#F6F1E7@0.75` 米色；但 4 个页面内嵌 TabBar = 白色 `α.55` + 罗盘/人形线性图标。**以页面内嵌为准**，从 `定稿 · 精修高保真.svg`/`我的页.svg`/`搜索页.svg`/`消息中心.svg` 提取 TabBar 与图标 path。

**验收**：`design_data_v4/` 8 页面 JSON 由脚本自动检查：`rects+paths+circles>0`，`gradients` 含方向（x1/y1/x2/y2）、`filters` 含 shadow 参数、`text_runs` 非空；已无 `parse_error`。

---

### Phase 1：P0-2 请求修复（时间轴出数据）

**1a. `.ts` → `.uts` 合规改名（必做）**
- `client/utils/` 下 21 个 `.ts` 全部改名 `.uts`：`auth.ts, api.ts, event_ops.ts, search_api.ts, voice.ts, uploader.ts, upload_protocol.ts, upload_pipeline.ts, timeline.ts, time.ts, text_recorder.ts, sync_client.ts, sentry.ts, retry.ts, queue_store.ts, play.ts, pause_controller.ts, log.ts, event_sync.ts, contract.ts, config.ts`。
- `agg_runner.uts` 已是 `.uts`，保留。内容不动；import 不带后缀，无需改 import。`git add` 记录 rename；改后冷启动复测。

**1b. `client/utils/api.ts` 参数级规避（防御，确定性）**
- `doRequest`/`doRawRequest`：GET 且 `data == null` 时**不传 `data` 键**（条件构建 options，规避 `data:{}` 空对象被桥接转换吞掉）。
- `header`：GET 去掉 `Content-Type`，仅保留 `Authorization`（有 token 时）；POST/PUT/DELETE 维持 buildHeader。
- **移除 `uni.request` 的 `timeout` 参数**，改 JS 层看门狗：
  ```ts
  function withTimeout<T>(p: Promise<T>, ms: number, tag: string): Promise<T> {
    return new Promise<T>((resolve) => {
      let done = false
      const timer = setTimeout(() => {
        if (!done) { done = true; console.log('[watchdog] timeout ' + tag); resolve(null as T) }
      }, ms)
      p.then((v: T) => { if (!done) { done = true; clearTimeout(timer); resolve(v) } })
    })
  }
  ```
  `request()` 包 `withTimeout(doRequestInner(...), REQUEST_TIMEOUT_MS + 5000, path)`；`rawRequest()` 同理超时 resolve `new HttpResult(0, null)`。
- 保留 `[dbg-req]`/`[dbg-auth]` 日志（供验收），Phase 7 删除。

**1c. 保持回调式写法**：`uni.request` 返回 RequestTask 非 Promise（文档行为），现有 `new Promise` 回调解法正确；勿写 `.then` 形态。

**验收**：冷启动 3 次，日志出现 `[dbg-req] ok /api/v1/events/timeline status=200` + `[dbg-index] fetchTimeline resolved: N events`，首页渲染时间轴，无「正在翻找相纸」挂死。

---

### Phase 2：P1-1 字体收口 + onLaunch 防腰斩
- `client/App.uvue`：`loadCustomFonts()` 已改 `global:true` + `weight` 入 `desc` + `url()` 包引号（**保留**）。
- onLaunch 每步独立 `try{}catch{}`：`loadCustomFonts()`、`initSentry()`、`ensureLogin()`、`initSync()` 各自包裹，任何一步异常不腰斩后续。
- 删除 `[dbg-probe]` 定时探针块（probe1/probe2，promise 形态必报错）。

**验收**：日志出现 `[yishu] font loaded` + `sentry/ensureLogin/sync` 完整链路；无 `vm`/`typeerror`；`[dbg-probe]` 不再出现。

---

### Phase 3：P0-3 initSync `UTSAndroid` 守护
- `client/utils/sync_client.ts`：`initSync()` 内把 `registerBackgroundSync()` 调用包 `try{}catch{}`（打日志 `[yishu] sync 标准基座降级`），不阻断 onLaunch。
- 若包住后仍在**模块加载期**抛 `UTSAndroid`（说明插件 module 顶层引用），进一步把 `initBackgroundTasks`/`setBackgroundTaskHandler` 改为先探测存在性再调用；当前取证为函数调用期异常，try/catch 应可兜住。

**验收**：onLaunch 尾部日志完整；`UTSAndroid is not defined` 消失或已明确降级不阻断。

---

### Phase 4：P0-1 弃 hens-svg → 官方 image + SVG（0.5~1 人日）

**原则**：静态图标 `<image src="/static/icons/xx.svg">`（**必须显式 width/height**）；需换色出**双态两文件**（`*-active.svg`/`*-inactive.svg`），模板按状态切 `src`；颜色烘进 `fill`。

**4a. 图标资产提取**（数据源见 Phase 0 结论，落 `client/static/icons/`）：
- Detail 六枚：`iconBack/iconHeart(→改五角星描边)/iconSparkle/iconBubble/iconCapsule/iconShare` → 取自 `.cowork-temp/final_icons.json`（已提取，`iconHeart` 需按审计换成五角星描边，非心形）。
- TabBar（罗盘/气泡/放大镜/人形/＋）+ 各页图标 → 从**页面 SVG**（`定稿 · 精修高保真/我的页/搜索页/消息中心/记录面板/记忆详情页/冷启动访谈`）经正源解析器提取 path + 颜色；TabBar 图标按页面版（白色 α.55 外壳 + 线性图标）生成 `tab-*-active.svg`(#B05A3A)/`tab-*-inactive.svg`(#8A7A6A) 与 `tab-plus.svg`(#F6F1E7)。
- messages 头像三枚：`msg-bell.svg`(#B05A3A)/`msg-ai.svg`(#7A8C5A)/`msg-teal.svg`(#4E7D8C)。

**4b. 替换 41 处 `<hens-svg>`**：
- `:src="inlineSvg"` → `src="/static/icons/xx.svg"`，`style="width:<原width>px;height:<原height>px"`（hen-svg 的 width/height 按 px）。
- TabBar 用 `:src="active==='x' ? '/static/icons/xx-active.svg' : '/static/icons/xx-inactive.svg'"`；FAB `tab-plus.svg`。
- messages 动态头像：`MessageItem.iconSvg`（内联字符串）改为 `iconPath`（路径字符串），`msgIcon(t)` 返回 `/static/icons/msg-*.svg`；`msgBg(t)` 维持背景色。

**4c. 删除插件**：整目录删除 `client/uni_modules/hens-svg/`（`encrypt/index.uts/package.json` 等）。删除后 `HensSvgView`/`hens-svg is not found` 报错刷屏消失即验收。

**验收**：真机所有页面图标可见、颜色随状态切换；无 `HensSvgView is not defined`；`uni_modules/hens-svg` 不存在。

---

### Phase 5：像素还原（`design_gap_audit/` 已给精确值）

**5a. 共享 TabBar 组件（4 页受益，第一优先）** — `components/TabBar/TabBar.uvue`
- 底色：米色 `rgba(246,241,231,.85)` → **白色毛玻璃 `rgba(255,255,255,0.55)`** + 白描边 `α.65` + 大阴影 `0 10rpx 14rpx rgba(58,46,37,0.098)`；`rx=31px` 保持。
- 图标：22px → **15px**；用 Phase 4a 从页面 SVG 提取的罗盘/气泡/放大镜/人形 path；非激活 `#8A7A6A`、激活 `#B05A3A`。
- FAB `＋`：28px → **15px**，烘 `#F6F1E7`。
- 文字 10px、激活 `#B05A3A`（正确，保持）。

**5b. `pages/index/index.uvue`（重灾页）** — 按 `design_gap_audit/01_index.md`
- Hero 标题 `忆述光华`48rpx → **「回忆」73rpx** 白色粗体；右上角补琥珀「峰」头像（#C4913C 圆盘+白「峰」+白描边环）。
- 副标题两行：`八月 · 28 条新记忆`（白 α.85）+ `清晨的湖边，风很轻 · 8月28日 07:42`（白 α.92）。
- 渐变**横向**：顶部暗化 `#19140F α.498→0`（左→右）；底部溶解横向米色（右溶入 `#f8f7f4`）。
- 搜索胶囊：占位文案白字、阴影加大、放大镜缩到 ~9.4rpx。
- 日期分组头：34rpx bold → **22rpx #8A7A6A + 右侧发丝线**（`#3A2E25 α.08`）。
- 播放按钮：琥珀圆+白三角 → **白圆(α.8)+琥珀三角#B05A3A**。
- 文字卡：补 `#C4913C α.10` 米色面板；引言色 `#3a2e25`→`#5A4C40`。
- 空状态：CTA 高 ~108rpx、补「或者，录一段语音」次级入口。

**5c. `pages/messages/messages.uvue`（结构级）** — 按 `design_gap_audit/03_messages.md`
- **删除**分段选择器与**删除整条 `<TabBar>`**（设计稿消息中心非 TabBar 页）。
- 导航改：返回箭头 `‹` + 居中标题「消息」~35rpx；去掉「⋯」与左对齐大标题。
- 时间胶囊卡：恢复**横向渐变内面板**（`#D9E2CA α.6→α.12`）+「时间胶囊 · 8 个月前」元信息 +「去年今天：…」标题；移除绿色波形+「本周记忆摘要」。
- 消息列表卡：**去掉行间 1rpx 分隔线**、行距 64px=123rpx、去未读红点（或按设计降级）、卡片间距 24→~111rpx。

**5d. `pages/detail/detail.uvue`** — 按 `design_gap_audit/08_detail.md`
- 悬浮按钮×3：白底 → **深烟熏底 `rgba(59,46,38,0.3)`+白环 α.4+白图标**。
- 收藏图标：心形 → **五角星描边**（白），尺寸 ~14.6×14px；`more` 三点 8rpx→~4.6rpx。
- 顶部暗化/底部溶解渐变**横向**；相关卡缩略图渐变**横向**（α 值已对，只改方向）。
- `✦` ~20rpx、返回箭头 ~13×23（缩半）；标题 56→50rpx；操作条第三项归位。

**5e. 其余 5 页**（各页 `design_gap_audit/0X_*.md` Top10）
- `02_search`：加回大标题「搜索」(73rpx)；TabBar 共享修复；去掉清除×/麦克风；margin-top ~146rpx。
- `04_profile`：统计卡改**大数字**（128/42/23）而非图标；头像白「峰」字；用户名「峰宝」/签名对齐；菜单文案对齐；去品牌条+Beta；头像 128→~123rpx。
- `07_settings`：行图标 emoji（🔒🔔🌙🗑️）→ **琥珀线性图标 #C4913C**；行标题 28→24-25rpx；页标题 34→~38.5rpx；卡片阴影加强。
- `06_interview`：同心圆 α.2/α.4 → **α.06/α.12**；光斑纯色→**径向渐变**；问题卡纯白→**白 α.55 毛玻璃+白描边**；✦ 补三层琥珀光晕；下半区上移 40-60rpx。
- `05_record`：背景卡内米色面板位置/尺寸/圆角修正（左移+11、上移-27、宽+23、圆角 35→23rpx）；选项圆补阴影（dy3 blur4 α.067）；背景渐变改横向；背景搜索胶囊补白 α.32 毛玻璃+白字；背景副标题缩到 24rpx。

**验收**：每页 `adb exec-out screencap -p` 截图，与设计 SVG 渲染图（`.cowork-temp/renders/*.png` 或设计目录 `_wrap_*.html`）并排比对，高严重度项清零。

---

### Phase 6：联调收尾（P1-2/P1-3）
- record 页真机选图/拍照上传 → `POST /api/v1/contents/upload` + 聚合（`aggregateToEvents`+`syncClientEvents`），确认后端落库。
- 跳转链路：TabBar 5 标签 reLaunch、index 卡片→detail、profile→settings、FAB→record、detail/messages 返回，逐一真机点击。
- 消息测试数据：4 条（3 未读 1 已读）已造好，验证列表/已读/未读数。

**验收**：后端日志出现对应接口成功记录；跳转无死链、无空白页。

---

### Phase 7：清理 + 提交 + 审核门禁
- **删探针/临时产物**：
  - 源码内：`App.uvue` 删 `[dbg-probe]`；`index.uvue` 删 `[dbg-index]`；`api.ts`/`auth.ts` 删 `[dbg-req]`/`[dbg-auth]`（验收通过后删）。
  - worktree 根临时产物：诊断 `*.py`（`parse_svg_v2/v3.py`、`check_parse.py`、`rewrite_*.py`、`fix_*.py`、`check_*.py`、`probe_*.py`、`extract_*.py`、`subset_*.py`、`scan_*.py`、`analyze_*.py`、`locate_*.py`、`compute_bbox.py`、`audit_layout.py`、`copy_data.py` 等）与 `*_out.txt`/`*_err.txt`、`c2/c3 等日志`、`client/*.png` 截图、`_wrap_*.html`、`_dump_elements.json`。
  - `.cowork-temp/`：约 100 个脚本/几何 dump/渲染图/解析 JSON（`geo_*.txt`、`parsed/*`、`renders/*`、`rec_*.png`、`svg_parse.py`、`parse_svg_abs.py` 等）——gitignore 内可整体清理，但 **`design_gap_audit/`、`research_*`、`DIAGNOSIS_*`、`HANDOFF_*`、`design_data_v4/` 保留**为交接/审计记录。
  - 用 `git status` 甄别，仅删**未跟踪**临时文件与明确中间产物；不误删已提交文件。
- **git 提交（分支 `wrap1-agentA2-ui-restore`）**：按阶段分多个 commit，descriptive message（如 `fix(api): .ts→.uts + GET 空对象规避 + request 看门狗`、`chore(design): 弃 svgelements 提取管线，改用 etree 正源`、`fix(ui): 弃 hens-svg 换 image+svg`、`fix(ui): index 像素还原`、`feat(api): 联调回归`、`chore: 清理探针与临时产物`）。
- **审核门禁（AGENTS.md Pre-Commit Review Gate，强制）**：
  - 每次 commit：`python scripts/review_agent.py` 快速门禁通过，禁止 `--no-verify`。
  - 完成验收前：`python scripts/review_agent.py --full` 通过（全量语法/lint/密钥 + pytest/API 冒烟/原型验证，覆盖率阈值 50%），退出码 0。
  - 确认 `git config core.hooksPath .githooks` 已配置。
- 提交后 `git log --oneline -8` 记录基线，供下会话 `./init.sh` 重启。

---

## 假设与决策（已定）

1. **不建自定义基座/不走云打包**：hen-svg 未声明蒸汽模式兼容 + 付费加密 + 云打包风险多 → 弃用，用官方 image+SVG。
2. **不做 iconfont**：换色只需 tab 图标双态文件即可，避免额外 ttf。
3. **`.ts → .uts` 必做**：官方 5.25 前明文要求，预期消除一批桥接怪象。
4. **请求修复用「确定性规避」**而非先探针再二分：同时 改名 + 参数规避（GET 空 data 不传/去 timeout/看门狗）。
5. **提取管线正源** = `.cowork-temp/svg_parse.py` 的 etree 实现；**弃用** svgelements `parse_svg_v2/v3.py` 及全部 `design_data_for_agents/design_data.json`/`design_data_v2`/`design_data_v3`。
6. **TabBar 数据以页面内嵌为准**（白色 α.55 + 罗盘/人形），非独立 `TabBar胶囊.svg`（`#F6F1E7@0.75` 米色旧版）。
7. 标准基座下原生能力（相册监听/后台任务/安全存储）为天然降级，不属本轮阻塞。
8. 保留功能性扩展（待确认区/回响卡/文字弹窗/完整表单等），除非审计明确「设计无对应物且影响首屏」。
9. git 管理严格：生产代码改动须在分支上合理提交。

## 风险与注意事项

- **repo 内无原始设计 SVG**：全部从 `C:\Users\ghf\Downloads\忆述光华 · W1 时间轴页方向小样\` 提取；**绝不**信 `design_data_for_agents/*/design_data.json`、`design_data_v2/`、`design_data_v3/`。
- **adb reverse**：用 SDK platform-tools adb；僵死 `--remove-all` 再建；HBuilderX 自带 adb 版本冲突会重启 server 击穿隧道（本日已多次）。
- **盯当前进程日志**：旧进程旧日志曾误导一整天；重启后以 `uvicorn_run.out` 当前 PID 为准。
- **UTS Promise 限制**：`uni.request` 返回 RequestTask、`uni.uploadFile` 返回 UploadTask，均无 Promise；本库回调式 resolve-only 已正确，勿写 `.then`。
- **5.25 前不新增 `.ts`**：新工具文件一律 `.uts`。
- **TabBar 双数据源冲突**（米色 vs 白色 α.55）：像素还原必须落实到页面渲染出的版本，勿被 `TabBar胶囊.svg` 误导。

## 验证清单（阶段闭环逐项打勾）

- [ ] 提取管线：`design_data_v4/` 8 页 `rects+paths+circles>0`、`gradients` 含方向、`filters` 含 shadow、`text_runs` 非空、无 `parse_error`
- [ ] P0-2：`[dbg-req] ok /api/v1/events/timeline status=200` + `fetchTimeline resolved: N`，时间轴渲染，冷启动 3 次稳定
- [ ] `.ts→.uts` 全部 rename，HBuilderX 全量编译零错误
- [ ] P1-1：字体 `font loaded`，onLaunch 后续完整
- [ ] P0-3：`UTSAndroid is not defined` 消失或已降级不阻断
- [ ] P0-1：全部图标可见、TabBar 双态正常、`uni_modules/hens-svg` 已删、无 `HensSvgView` 报错
- [ ] 像素：8 页高严重度项清零（TabBar → index/messages/detail → 其余 5）
- [ ] 联调：record 上传/聚合、跳转无死链、消息 4 条正确
- [ ] 清理：探针日志/临时产物删净；`python scripts/review_agent.py --full` 通过
- [ ] 提交：分支干净、`git log --oneline -8` 记录基线
