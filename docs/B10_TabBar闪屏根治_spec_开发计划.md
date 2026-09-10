# B10 TabBar 切页闪屏根治 — SPEC + 开发计划（2026-09-09 北嘎霖）

> 状态：**待峰宝审批**。批准后按 §6 阶段表执行，一阶段一闭环一提交。
> 前置阅读：`_diff_ledger.md` §K（B10 首登记）、L651 行（09-03 switchTab 证伪回退记录）。

## 1. 问题定义

| 项 | 内容 |
|---|---|
| 现象 | 点底部 Tab 切页瞬间，屏幕闪露一帧非页面底色（白/灰），高频操作下明显 |
| 机理 | 自绘 TabBar 用 `uni.redirectTo` 切页 = **先销毁当前页 → 再创建新页**，destroy/create 间隙露出原生窗口底（uni-app x 每页一个原生 UI 实例，WebSearch 调研证实为通病） |
| 已做缓解 | `globalStyle.backgroundColor=#F8F7F4` + 四 Tab 页显式 `backgroundColor`（同色）→ 闪的是"无内容空窗"的底色一致性，闪感减弱未消失 |
| 已死方案 | **原生 tabBar + hideTabBar**：09-03 峰宝真机证伪（app-uvue Android 原生条残留渲染在最底、层级打乱、交互失效）→ 不再尝试 |
| 现状约束 | 页面栈恒为 1 页（redirectTo 语义）；铁律 1：中央 `+` 只拉 RecordSheet 浮层不许跳页；子页（detail/manage/messages…）经 navigateTo 压栈 |

## 2. 方案空间（完整枚举 + 成本）

| 方案 | 原理 | 根治度 | 成本 | 判定 |
|---|---|---|---|---|
| **D 转场动画禁用**（新增·探针） | redirectTo 传 `animationType:'none'`（uni-app x 路由动画参数）——若闪感主要来自系统默认转场过程，一行级消除 | 中（若间隙本身可见则无效） | **30 分钟**（4 处 TabBar + 2 处外部触点） | ✅ **先行执行**：最便宜的实验，赢了省一场架构手术，输了也拿到「间隙本身可见」的定性证据 |
| **A 单页容器化（保活）** | 唯一宿主页 `pages/shell/shell.uvue` 持四个 Tab 内容组件，`v-show` 切换零销毁零创建——闪屏物理性消失 | **根治** | 高（见 §4） | ✅ **主方案**（D 不足时） |
| B glassEffect 转场模糊 | 转场期用模糊底遮蔽间隙 | 低（治感知不治根，且与暖白底风格存疑） | 低 | 备胎：A 完成后若仍有微闪再评估 |
| C 原生 tabBar | — | — | — | ❌ 09-03 证伪，禁再试 |

**推荐执行序：D（半小时探针）→ 依据结果决定是否进 A → A 内 V1 前置验证 → 逐页金丝雀迁移。**

## 3. 方案 D 详设（探针）

- 改动面：`TabBar.uvue` goIndex/goAI/goSearch/goProfile 四处 `redirectTo({url, animationType:'none'})`；`goBack`/登录回跳等 2 处外部 tab 触点同步
- 输入输出边界：仅增参数不改语义；fail 回调维持现状（redirectTo 静默失败先例——补 `console.warn('[b10-d] redirect fail: ' + err)` 不静默）
- **判定口径（真机）**：连点 Tab 20 次录屏逐帧看。
  - 闪消失 → 关单 B10（记录拍板），A 留远期不再做
  - 仍有"空窗一帧"（白底无内容闪现）→ 定性为 create 间隙本身，**D 参数保留**（无副作用）转 A

## 4. 方案 A 详设（单页容器化）

### 4.1 目标架构

```
pages/shell/shell.uvue          ← 唯一 Tab 宿主（19 页 → 16 页，index/ai/search/profile 降级为组件）
 ├─ TabIndex   (components/tabs/TabIndex.uvue)    ← 原 index 模板+逻辑整体搬迁
 ├─ TabAI / TabSearch / TabProfile  同构
 ├─ <TabBar @plus> （现有组件零改动复用）
 └─ RecordSheet 唯一挂载（原四页各挂一份 → 收编归一）
```

- 切换 = `activeTab.value = 'search'`（纯 ref，零原生实例操作）
- 首次进入某 tab 才挂载（`v-if="everMounted[tab]"`）+ 挂载后 v-show 保活 → 首屏成本不涨、后续零闪
- 子页跳转（detail/messages/manage）维持 navigateTo 压栈不动；从子页返回 shell 时 activeTab 天然保持原值（**优于现状**：现在 redirectTo 回 tab 必丢滚动位置/必重新拉数据）

### 4.2 耦合点改造清单（调研实数）

| 触点 | 数量 | 处置 |
|---|---|---|
| `onLoad/onShow` 页内生命周期 | index 9 / ai 3 / search 4 / profile 1 处 | onLoad → 组件 `onMounted`（每 tab 首次激活）；onShow → shell 转发 `onTabShow(tab)`（激活+从子页回前台两个时机，封装成 props 回调，逐页映射并在对拍台账登记每处去向） |
| `redirectTo → tab 页` 外部入口 | 6 处（含 TabBar 4 + 登录回跳 + 消息深链） | 统一 `uni.reLaunch({url:'/pages/shell/shell?tab=x'})`；pages.json 旧四路径留 redirect 兼容一版（防通知栏等外链旧 URL） |
| RecordSheet 多页挂载 | 7 页引用 | shell 唯一挂载；`@plus` 事件从 TabBar → shell 一处接线；四组件删除自挂载（净删重复代码） |
| `onUnload` 停播（audio 引擎跨页复位） | 4 页 | shell 的 onBackPress/切子页时机转发；**注意**：切 tab 不再触发"离页停播"——产品语义变为跨 tab 连续播放（对齐微信/网易云行为），列为**待峰宝确认的产品决策 P1** |
| pages.json 页面注册 | 4 条 | index 路径改壳（启动页必须是它，`pages[0]`），其余 3 条删除 |
| enablePullDownRefresh | index/search/profile 有 | 下拉刷新逻辑上收 shell 按 activeTab 分发（uvue scroll-view 自绘刷新，组件暴露 `doRefresh()`） |

### 4.3 关键风险与前置验证（先验证后动工）

| # | 风险 | 验证法（V 阶段） | 失败处置 |
|---|---|---|---|
| **V1** | **uvue 组件样式隔离性**（四页存在 `page`/`n`/`card` 等泛型类名跨页撞车；若 uvue 样式不隔离=合并即串色） | 金丝雀页选 **profile(455 行) + search(733 行)**（两者都有 `page`/`n` 类），并入同壳对拍两页全部视觉 | 若串色：全量类名加页前缀波（估 +0.5 轮/页，先做再合） |
| V2 | 5.24 Vapor 模式下组件 v-show 保活 + 深层响应式性能 | 金丝雀期 30fps 录屏切页帧分析 + 列表滚动帧率 | 若掉帧：评估 v-if 双缓存（只保活当前+相邻 tab）降级态 |
| V3 | 内存四页共存（index 1722 行含时间轴大列表） | 全量迁完后 `dumpsys meminfo` 对比基线，阈值 +80MB（真机中端机预算） | 超阈：滚动列表虚拟化 / 未激活 tab 折叠重数据（登记台账择一） |
| V4 | 生命周期语义遗漏（onShow 里的重拉逻辑散在 17 处） | 迁移逐页做「生命周期触点清单」勾选表（上表数字即底稿），对拍含「从子页返回」路径 | 漏网=差异台账开单修 |

### 4.4 输入输出/错误边界（壳层核心逻辑）

- `shell?tab=<index|ai|search|profile>`：非法/缺省 tab 值 → 回落 index + console.warn（不静默）
- 子页 navigateBack 回 shell：activeTab 不变（保活语义）；`onBackPress`：任何 tab 上按返回 = 退出 App（现状一致，profile/manage 栈回退特例不动）
- TabBar `active` prop 从路由推导改为 shell 状态传入（现每页写死自己的字符串 → 收口单点）
- 编译门/推包/对拍全走 `uvue-deploy-device-ops` 既有 SOP，不新增工具面

## 5. 明确不做（防蔓延）

- 不做 19 页全量容器化（只收四个 Tab 页；detail/messages 等子页架构不动）
- 不做 glassEffect（B 留备胎）
- 不动 pages.json 其它页面配置
- 中央 `+` 铁律零触碰；audio_player v5 池引擎零触碰

## 6. 阶段计划（一阶段一提交，随时可停）

| 阶段 | 内容 | 产出/验收 | 预估 |
|---|---|---|---|
| P0 | 方案 D 探针（§3） | 真机连切 20 次录屏定性：**消除** → B10 关单出结论；**残留** → 保留参数进 P1 | 半小时 |
| P1 | V1 样式隔离金丝雀（profile+search 双页入壳） | 两页对拍全绿 + 串色结论落档；壳/组件/路由骨架定型 | 1 轮 |
| P2 | 逐页迁移：search → ai → profile → **index（最后，最重）** | 每页一闭环：八维对拍（restore-sop SOP）+ 真机连切零闪帧检 | 4 轮 |
| P3 | RecordSheet 收编 + 6 处 redirectTo 触点切换 + pages.json 清账 | 铁律 1 回归（四 tab 中央 `+` 均不跳页）；消息深链/登录回跳直达指定 tab | 1 轮 |
| P4 | 全量回归 + V2/V3 终检 + 台账/总账关单 | 连切 50 次录屏零异常帧；meminfo 达标；`远期待办总账.md` B10 打 ✅ | 1 轮 |

**回滚点**：每阶段独立 commit；P1 失败（V1 串色且前缀波不划算）→ 整体回滚，B 案或维持现状重新拍板。

## 7. 待峰宝拍板项（动工前）

1. **P1 决策**：跨 tab 连续播放（切页音乐不停，方案 A 天然结果）要不要？（现状=离页停播）
2. 方案 D 探针先跑？（推荐——半小时定生死，不亏）
3. 若进 A：接受「返回 tab 保留滚动位置/数据常驻」为正式产品行为？（微信同款，属于闪屏手术的伴生收益）
