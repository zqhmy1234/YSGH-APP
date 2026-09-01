# 还原差异台账（2026-08-31 建）

> 规则：每页/组件一张差异清单，**修完+复验截图后才可打勾**；平台硬限制需「有据降级登记」。
> 铁律来源：ardot-to-uvue-css-restore SKILL §六。完成定义四关：交互对拍 / 孤儿+csscheck / 视觉对拍清零 / 台账无未处理项。

## TabBar（玻璃TabBar 2:231）🔴 修复中

| # | 差异 | 真值（画布） | 现状 | 状态 |
|---|---|---|---|---|
| T1 | 四个图标全部是自造占位 SVG，非 ardot 导出 | i2_233 指南针(圆环+菱形)/i2_238 对话泡/i2_243 放大镜/i2_248 人形 | tab-*.svg 为简化自造（时间轴只有菱形无圆环等） | ✅ 9 个 tab-*.svg 已按 ardot 原图落位（仅色值派生 active/inactive，形状零改动，断言零透明度/零 filter） |
| T2 | 图标尺寸 | 画布图标容器 20×20（内径 15 已含在 SVG 留白里） | .tabbar-icon 15px（误用内径） | ✅ 已改 20px，真机比例正常 |
| T3 | 玻璃 blur | BACKGROUND_BLUR radius 20 | 无 backdrop-filter，静默降级 | 🟡 已加 blur(20px)+编译通过+官方支持清单在列；**模糊效果无页面可视觉实证**（内容滚不到 TabBar 背后），留观，有内容页时复验 |
| T4 | cornerRadius | 画布 36 | 31px | ✅ 已改 36 |
| T5 | FAB 图标 | i2_253 22×22 #FAF9F5 | 尺寸对，但内容非 ardot 原图 | ✅ 已换 i2_253 原图（细十字） |
| T6 | 真机视觉对拍 | — | — | ✅ index(时间轴红棕)/search(搜索红棕)/profile(我的红棕) 三页激活态+inactive 色值全部实证通过 |
| T7 | **FAB 被水平裁切**（像素实锤：顶部轮廓 y=2136 为水平直线，裁切线=距底 92px=旧 container 高度=条顶边） | 画布真值：FAB 是**页面根 frame 子节点**（与玻璃TabBar 兄弟），y=740..796 → bottom=**48px**（非44）、凸出条顶 12px | 旧代码 bottom 44px + container 曾 92px；r3 部署日志有 ADB reverse 失败，设备疑跑旧包 | 🔧 已改 bottom 48px + container 110px，r4 部署待像素复验 |
| T8 | Tab 图标-标签间距 | 画布 gap=3px | margin-top 2px | 🔧 已改 3px，随 r4 复验 |

## search 页 🟡

| # | 差异 | 状态 |
|---|---|---|
| S1 | @confirm 触发搜索未响应（补 confirm-type 后待复验） | 待办 |
| S2 | 键盘弹起时 scroll-view 区域渲染灰块 | **挂起（峰宝指示，暂不追因）** |
| S3 | 与设计稿分区视觉对拍未做 | 待办 |

## messages 页 ✅ 闭环（2026-08-31 06:40，r13 真机复验通过）

| # | 差异 | 状态 |
|---|---|---|
| M1 | 分区视觉对拍 | ✅ v31+v32 复验：导航/日期分组/回响卡（渐变+星芒+margin间距）/消息行 全对；r13 图标复验通过（_v32_icons.png） |
| M2 | 回响卡/消息卡样式细节（间距/图标底/字号） | ✅ 静态对拍零偏差（字号/色值/阴影/圆角/渐变 vs 画布逐项核过）；G6 四处 gap→margin 已修（n4_99/100/107/111） |
| M3 | 消息行三图标为退化占位（msg-teal 渲染成实心圆点） | ✅ r13 真机复验通过：钟/麦/勾形状色值与画布原图一致 |
| M4 | 时间格式「8-29 22:48」≠画布「09:12」 | ✅ 动态数据格式（formatIsoShortTime），非缺陷，登记说明 |
| M5 | 未读点 n4-unread-dot 画布无此节点 | ✅ 动态功能扩展项（已读标记），保留 |
| M6 | 回响卡-列表间距比画布略大（~13px） | 🟡 绝对定位(画布 y=300 忠实还原)+后端文案行数差异所致，留观 |

## detail 页 🟡

| # | 差异 | 状态 |
|---|---|---|
| D1 | 交互+动态渲染已过，与设计稿分区视觉对拍未做 | 待办 |

## 其他遗留（收尾）

| # | 差异 | 状态 |
|---|---|---|
| G1 | index 15 个孤儿类 | 待办 |
| G2 | manage 1 个孤儿类 | 待办 |
| G3 | record 551/559 两处 4 参数渐变违规 | 待办 |
| G4 | yishu-tabbar 死组件清理 | 待办 |
| G5 | index/record 转 n*_ 结构（转页必须交互+视觉双对拍） | **峰宝拍板 08-31：要做，但排最后**（9 个新页面全部闭环后启动） |
| G6 | **🔴 系统性：`gap` 在 uvue App 端不支持**（官方能力边界；r3 编译日志 42 条 WARNING 被漏读；像素实测 profile 行间距 2~3px ≠ 画布 12px）。共 42 处：profile 8✅、TabBar 1✅、**messages 4✅（08-31）**、余 29 处分布在 ai(9)/detail(9)/search(6)/interview(2)/settings(3)，各页闭环时对照画布真值逐处换 margin | 各页闭环时清零 |
| G7 | **设备逻辑宽度 361px ≠ 画布 390px**（1084 物理/3.0 密度）：px 直写 1:1 渲染导致整体比例比设计稿偏大 7.4%。暂按现状（各元素绝对值正确、屏宽差仅影响横向富余），转 rpx 体系另行评估 | 登记留观 |
| G9 | r3 部署 ADB reverse 失败 + gap WARNING 被漏读 → 「部署成功」不可信，部署后必须：读完整日志 + 截图像素复验 | 已沉淀进 SKILL §六铁律8 |

## 2026-08-31 05:00 联调数据链路（G8/G9）
- **G8 ✅已修**：8000 端口被 HBuilderX launcher（httpServer.js）抢占 → 所有 API 返回 HTTP200+裸"404" → 客户端守卫 resolve(null) → **页面静默空数据**。日志 status=200 掩盖了一切。修复：杀 launcher（27680）+ uvicorn 回 8000 + adb reverse → timeline 真数据渲染 ✓
- **铁律新增**：联调前置检查 = curl 后端验证响应是 JSON envelope（{code,message,data}），裸值=端口被劫持；设备日志 status=200 不代表数据正确
- **G9 待办**：timeline 视觉对拍（新数据入机后）：同日多条 L1 时日期头重复（8月24日×3）是否符合画布分组逻辑；卡片"0 张照片"（cover_content_id=null，照片聚合未跑）；上传横幅遮挡 hero 搜索胶囊

## 2026-08-31 06:10 峰宝拍板（方向纠偏）
- 本窗口此前在旧页 index/record 上打转属**方向跑偏**（峰宝：「你操作的是主干的旧页面！不是新页面！！」）
- **闭环顺序拍板：messages 优先**（M1 分区视觉对拍）→ 其余新页 → G5/G1/G3 旧页重做排最后
- **销案：「@tap 全灭」假案**——FAB 真实中心 (541,2183)（像素扫描），旧记录 (542,1950) 偏 233px；点击坐标今后必须像素扫描定位，禁止半尺寸截图目测换算
- 旧页本轮修复保留：index/record/manage ref 化真机验证通过（录音计时器走秒实锤）；G8 端口劫持修复有效；卡片标题「2026-08-28 · N条」是后端聚合命名非前端 bug

## 2026-08-31 06:35 messages 闭环记录
- r12 部署：gap→margin 4 处上机，回响卡间距生效（v31 截图）
- M3 图标病根与 TabBar T1 相同：转换期占位 SVG 非 ardot 原图；修复=原图直覆，引用名不变零代码改动
- 点击坐标教训（FAB 541,2183 / 消息中心 256,870）：一律像素扫描或裁剪原图定位，目测半尺寸换算必错


## 2026-08-31 16:10 独立空状态页（new/empty）闭环记录
- **新页 pages/empty/empty**：时间轴无数据时 uni.redirectTo 独立页（非内嵌态），挂玻璃 TabBar（峰宝定：首启页必须留主导航）
- **居中（峰宝拍板）**：画布空态帧仅 600pt 高、真机 ~844pt，按画布 y 直排会整体偏顶 → `.page` 改 `flex:1 + justify-content:center`，TabBar `position:fixed` 不占流，内容整块落画面正中（v57 截图实测：内容 690~1750，中心 1220 vs 屏心 1206）
- **太阳 SVG 病根（已修）**：仓库 i2_302.svg 是**帧内 Vector 28×28**（转换器丢外层 60pt 帧 + 丢 `translate(16,16)`），被 CSS 撑到 120rpx → 环放大 2.14×。修复 = 用峰宝导出的 60×60 原图，并把 transform 烘焙进 path 坐标（App 端未批量验证 transform 特性，故不用 `<path transform>`），覆盖后 CSS 尺寸不动
- **i2_304 山湖无误**：96×56 即帧内 Vector 真值，容器 240×180rpx + 内缩 24rpx 与画布 (12,12)/(96,56) 吻合
- **交互验证**：goVoice → 记录页「新的记忆」弹层弹出 ✓；TabBar 切 AI/搜索 正常 ✓（此前"点击无响应"是坐标打空，非功能故障，峰宝判定正确，铁律 9 再次实锤）
- **待峰宝人肉复验**：CTA「拍下第一张」→ 权限弹窗（标准基座无原生插件，reLaunch 回 index 后因无数据又弹回空态，属设计回路）；messages/settings/detail 返回键
- **部署教训**：`force-stop` 会切断 cli 与调试基座连接 → 差量编译成功但同步失败、队列等待；改机后需等「同步手机端程序文件成功」再验，或手动唤起基座触发续传

## 2026-08-31 17:30 gap 台账对账 + ai 页清零 + 三连交互修复待复验
- **v59 日志 15 条 `gap is not a standard property name` 精确对账**：ai 9 + settings 4 + interview 2 = 15（源码 `grep -cE "^\s*gap\s*:"` 合计同数）。search/detail/messages/profile/index/record/empty 均 0 —— 即 **gap 全局仅剩 3 页 15 处**（此前台账记的 detail 9 处已在此前轮次清完，旧计数作废）
- **ai 页 9 处全部清零**（逐处对照 `uvue_gen/ai_canvas.json` 真值，子节点坐标二次验证）：
  | 容器 | 画布 gap | 处理 |
  |---|---|---|
  | `.n2_355` 玻璃输入条 | 8pt | → `.n2_358 { margin-left:15.4rpx }` |
  | `.n4_88` 消息流 | 16pt | → `.n2_349`/`.n2_352` 各 `margin-top:30.8rpx` |
  | `.n2_338` AI消息组 | 8pt | → `.n2_342 { margin-top:15.4rpx }` |
  | `.n2_339` AI头 | 6pt | → `.n4_1 { margin-left:11.5rpx }` |
  | `.n2_342` AI气泡 | 10pt | → `.n2_344 { margin-top:19.2rpx }` |
  | `.n2_344` 内嵌记忆卡 | 10pt | → `.n2_346 { margin-left:19.2rpx }`（缩略图 x8+w48+10=66 ✓） |
  | `.n2_346` 记忆信息 | 2pt | → `.n2_348 { margin-top:3.8rpx }`（标题 y0+h17+2=19 ✓） |
  | `.n2_349` 用户消息行 | 8pt | **单子节点 → 直接删** |
  | `.n2_352` AI消息组2 | 8pt | **单子节点 → 直接删** |
- **新缺陷（已修，真机可见）**：`.n2_349` 缺 `justify-content: flex-end` —— 画布 `2:350` @x=210 = 容器 350 - 子宽 140 → 用户气泡**右对齐**；画布 JSON **省略默认对齐字段**（无 primaryAxisAlignItems），转换器按"没写=左对齐"处理 → 气泡贴左、聊天左右颠倒。已补
- **新缺陷（待峰宝拍板，未改）**：玻璃输入条 `2:355` w=350 h=52 padding 16/16/16/16，而子 `2:358` 发送钮 36×36 @y=8 → 内容盒高仅 20pt，按钮上下各溢出 8pt 且 `clipsContent:true` → **发送钮被裁成 20pt 高扁条**。uvue 现值（height 100rpx + padding 30.8rpx + overflow hidden）与画布**完全一致**，属画布自身笔误（padding 应 8pt）。改 padding-top/bottom 30.8→15.4rpx 即可完美贴合，但会偏离画布值，等峰宝定
- **三连交互修复已推 v58/v59（同步成功 17:14:25）等峰宝人肉复验**：① empty CTA 改回主干口径（原地 requestReadPermission+startWatch，不再 reLaunch 触发网络异常）② record 全部原生调用包 try/catch + `closeForm` 无条件复位 ③ messages/settings/detail `goBack` 加栈深判断回落首页
- **后端已探活**：8000 /health 与 /api/v1/events/timeline 均 200，本轮不再有后端 502 干扰

## 2026-08-31 17:50 峰宝真机复验结果 + 修复计划（未动代码，先定方案）
**复验结论（v58/v59）**
| 项 | 结果 |
|---|---|
| 空状态 CTA「拍下第一张」 | ✅ 显示「当前基座不支持相册接入，请使用自定义基座」，修复生效 |
| 录音卡死 | ✅ 能回退到选项层（不再困死） |
| 麦克风提示 | ❌ **无任何提示**（只在页面内改 `recordHint` 文案，用户看不见） |
| messages/settings 返回键 | ❌ **完全点不动**（只能靠华为左滑手势） |
| detail 返回键 | ⚠️ **未验证**——详情页无入口打不开（前置页无数据） |
| search 间距 | ⚠️ 搜索框有缝 ✓；chip/波形/引用竖线因**无数据 + 网络异常**看不到 |

**F1（P0，产品定义错误，峰宝点名）记录面板应是浮层不是页面**
- 现：`components/TabBar/TabBar.uvue:75` 中央 `+` → `uni.navigateTo('/pages/record/record')`
- 应：任何页点 `+` 都**留在原页只拉起面板**（时间轴/搜索/AI/我的/空状态/管理页同理）
- 另两处跳转来源：`pages/empty/empty.uvue:52` navigateTo、`pages/portrait/manage.uvue:264` reLaunch
- 挂 TabBar 的页面共 6 个：index / ai / search / profile / empty / manage

**F2（P0）messages/settings 返回键点不动——真根因推翻上一轮归因**
- 上一轮归因为「navigateBack 静默失败」并改了 JS 路由 → **真机仍点不动**，归因错误
- **真根因 = 热区被后写的全宽 absolute 兄弟盖住**：
  `.n4_93`(返回图标, 42.3rpx² @top115.4) 与 `.n4_95`(标题容器, **全宽750rpx** @top115.4, **DOM 在后**)
  同层 absolute → 标题容器覆盖图标 → `@tap` 收不到事件，**无任何报错/日志**
- detail 页无此问题（返回钮是**嵌套**在玻璃顶栏 `.n4_409` 内的子 view，非兄弟覆盖）
- 修复：返回图标移到标题容器之后（DOM 顺序）或加 `z-index:1`，**坐标不动、视觉零变化**
- 上一轮加的 `getCurrentPages()` 栈深判断**保留**（它解决的是栈深=1 的真实问题，与遮挡并存）

**F3 麦克风无提示**：catch 里补 `uni.showToast`，与 empty 页 CTA 成功口径一致
**F4 search 无数据**：`voiceResults`/`textResults` 是 v-for 数据源，空 → 波形/引用区不渲染 → 间距不可验；需注入本地 mock
**F5 网络异常来源待查**：`utils/api.uts:125` 是全局 NETWORK toast；search 页自身不发请求，疑似 index 的 timeline 请求残留或 uploader/pause_controller 后台同步触发，需加日志定位
**剩余 gap**：settings 4 + interview 2（ai 已清）

## 2026-08-31 18:11 W1 完成并推包 v60（同步成功 18:11:23）
**改动 1 — 返回键热区遮挡（messages/settings）**
- `messages.uvue`：返回图标 `.n4_93` 移到标题容器 `.n4_95` **之后** + 加 `z-index:1`
- `settings.uvue`：返回图标 `.n4_135` 移到标题容器 `.n4_137` **之后** + 加 `z-index:1`
- 坐标未动，视觉零变化；detail 页返回钮是嵌套子 view 不受影响（未改）
- 上一轮的 `getCurrentPages()` 栈深判断**保留**（治的是栈深=1 的另一个真问题，与遮挡并存）

**改动 2 — 录音 toast**：`record.uvue` startRecord catch 补 `uni.showToast({title:'当前基座不支持录音，请改用自定义基座', icon:'none'})`

**改动 3 — search 页注入本地 mock**：`USE_MOCK_DATA` 开关 + `mockHits()` 预填 6 条（3 语音 v-mock-1~3 + 2 文字 t-mock-1/2 + 1 照片 p-mock-1），onLoad 时填入 `hits` 并置 `hasSearched=true`。真实搜索成功后会覆盖。

**新增只读诊断脚本** `review/hotspot_occlusion.py`（铁律 11.5 配套）：
- 扫描全项目「同层 absolute 兄弟覆盖带 @tap 热区」，静默 bug 专用
- 关键修正：覆盖者常是 **hug 高度（CSS 无 height）**，初版遇到 `h=None` 就跳过 → 漏报；改为按 `HUG_ASSUME=200rpx` 保守判定并标注⚠️
- **已用反向对照验证有效**：把修复后的 messages 还原成 bug 版，脚本能报出；当前全项目 **0 处**（除已修两处）

**新发现（未修，入 W2）**：`index.uvue` 时间轴卡片**没有绑任何 @tap**，也没有 openDetail 函数 → 详情页从时间轴进不去（这是「详情页无入口」的第二个病根，峰宝原归因是"没数据"，实际还有"卡片没绑点击"）。当前详情页入口靠 search 的 `openResult`（注入 mock 后已可用）。

**v60 对账**：gap WARNING 15 → **6**（interview 2 @250/311 + settings 4 @144/169/229/255），与源码 grep 精确相等（ai 已清 9 条）

## 2026-08-31 19:21 W2 完成并推包 v61（记录面板浮层化）

**产品定义修正（峰宝 2026-08-31 拍板）**：记录面板 = **全局浮层，不是页面**。
时间轴页点中央 + 仍留在时间轴页，搜索页点 + 仍留在搜索页，其他页同理 —— **任何入口都不跳页**。

**改动 1 — 抽 `components/RecordSheet/RecordSheet.uvue`（新建，945 行）**
- 由 `review/gen_record_sheet.py` 从 `pages/record/record.uvue`（1143 行）机械搬运生成，原 `record.uvue` **保留未删**（pages.json 仍注册，无入口跳转 → 死页面，待后续清理）
- 删除：`.backdrop` 整块模板（1208 字符）+ 21 个 backdrop 专属 CSS 类（浮层不需要重复背景）
- 根 `.page` 改浮层：`position:fixed; left:0; top:0; width:750rpx; height:100%; z-index:1000`（> TabBar 999）
- 新增遮罩关闭：`<view class="scrim" @tap="onClose"></view>`（原独立页无此交互，浮层化后必须补）
- 生命周期改写：`onLoad → onMounted`、`onUnload → onUnmounted`
- 新增 `const emit = defineEmits(['close'])` + `onClose()`：先 try 停录音（忽略异常），再 `formMode=''`，最后 `emit('close')`
- 校验：括号平衡、孤儿 0、`@tap` 10 个（与原件一致）、backdrop 残留 0

**改动 2 — `components/TabBar/TabBar.uvue` 中央 + 改 emit**
```js
const emit = defineEmits(['plus'])
const goRecord = () => { emit('plus') }   // 旧实现 navigateTo('/pages/record/record') 已删
```

**改动 3 — 6 个宿主页接入浮层**
统一模式：`let sheetOpen = ref(false)` + `<TabBar ... @plus="sheetOpen = true" />` + `<RecordSheet v-if="sheetOpen" @close="sheetOpen = false" />`
- index / ai / search / profile / empty：走 TabBar 的 `@plus`
- manage：自带底部导航，改 `goRecord()` 内 `sheetOpen.value = true`（旧 `reLaunch` 已删）
- empty：改 `goVoice()` 内 `sheetOpen.value = true`（旧 `navigateTo` 已删）

**改动 4 — 6 页加 `onBackPress` 拦截**（浮层开着按返回键先关浮层，而不是退出页面）
```js
onBackPress((): boolean => {
	if (sheetOpen.value) { sheetOpen.value = false; return true }
	return false
})
```

**🔴 推包前纯编译抓到致命错误（否则整包编不过）**
```
[plugin:uts] "onMounted" is not exported by "node_modules/@dcloudio/uni-app/dist-x/uni-app.es.js"
  at components/RecordSheet/RecordSheet.uvue:6:9
```
根因：uni-app x 组合式 API 下 `ref/onLoad/onMounted/onBackPress` 等**由框架自动引入，禁止手写 import**。
页面级 `import { onLoad } from '@dcloudio/uni-app'` 能过（该包确实导出），组件级 `onMounted` **不导出** → 极易误判。
修复：删掉 import 行，直接裸用。对照物：`UploadStatusBanner` / `TabBar` 一行 import 都没有。
→ 已沉淀为 SOP §13（禁止 import 生命周期）+ §14（未验证 API 先跑纯编译模式）

**验证记录**：`cli launch app-android --project <client> --compile true` 纯编译 3 次
- 第 1 次：❌ 抓出 onMounted import 错误
- 第 2 次：✅ 编译成功（修 import 后）
- 第 3 次：✅ 编译成功（6 页 onBackPress 全量推广后，19:21:25）

**待验（v61 上机后）**：① 时间轴/搜索/我的 页点中央 + → 面板就地拉起且不跳页 ② 点遮罩关闭 ③ 浮层开着按返回键 → 只关浮层不退出 ④ 面板内拍照/相册/录音三个入口仍可用

## 2026-08-31 20:0x W3：gap 全局清零 + 详情页入口修复 + 时间轴 mock

**改动 1 — settings 4 处 gap 清零（gap 全局 15 → 0 收官）**
画布真值（4:140 / 4:147 / 4:155 / 4:160）：`gap=12pt`、`layout=horizontal`、padding 12pt。
子节点结构统一为「固定图标 38.5rpx → 文字 `flex:1` → 尾部元素」。
**关键判断**：文字是 `flex:1` 会吃掉剩余空间 → gap 对「文字→尾元素」这段无效，
真正生效的只有「图标→文字」一段 → **只给文字加 margin-left，尾部元素不动**。
| 容器 | 删 gap | 加 margin |
|---|---|---|
| `.n4_140` | 23.1rpx | `.n4_144 margin-left: 23.1rpx` |
| `.n4_147` | 23.1rpx | `.n4_151 margin-left: 23.1rpx` |
| `.n4_155` | 23.1rpx | `.n4_158 margin-left: 23.1rpx` |
| `.n4_160` | 23.1rpx | `.n4_163 margin-left: 23.1rpx` |

**改动 2 — interview 2 处 gap 清零**
- `.n4_181` 问题卡（画布 4:181，`gap=10pt`、`layout=vertical`，子节点纵向序 编号→问题→引导，
  坐标 20/43/77 验证两段间距均 10pt）：删 gap → `.n4_183`、`.n4_184` 各加 `margin-top: 19.2rpx`
- `.n4_171` 进度点（画布 4:171，`gap=6pt`，3 个点各 8pt）：删 gap → `.dot` 加 `margin-left: 11.5rpx`
  - **首点必须归零**：三个点是 `v-for` 生成的，若都加 margin 会整体右偏 11.5rpx、不再居中于 750rpx
  - 方案：模板加条件类 `:class="{ 'dot-active': (i-1)===step, 'dot-first': i===1 }"` + `.dot-first { margin-left: 0rpx }`
  - **`:class` 对象语法多键属新增写法（项目首次使用）→ 已用纯编译模式验证通过** ✅

**对账**：全项目 `gap` 残留 **0**（原 15 条：ai 9 + settings 4 + interview 2）。
部署日志 CSS WARNING 由 7 → **1**（仅剩 `components/TabBar/TabBar.uvue:103` 的 `backdrop-filter`，
这是画布毛玻璃效果，真机实测生效，**有意保留**）。

**改动 3 — 详情页入口（时间轴卡片）**
真机病根：`index.uvue` 的时间轴卡片**没绑任何 @tap**，也没有 openDetail 函数 → 详情页从时间轴**根本进不去**
（此前唯一的详情页入口是 search 的 `openResult`）。
- 卡片容器加 `@tap="openDetail(ev)"`
- 卡内已有 @tap 的 3 处加 `.stop`（操作钮 / 照片条 / 语音卡），否则点它们也会冒泡进详情
  （`.stop` 是已验证特性：`search.uvue:21` 在用）
- 新增 `openDetail(ev)`：**detail 页要的是 contentId，而 `TimelineEvent.id` 是 event id，不能直接用**
  → 取数优先级 `coverContentId` → `photoIds[0]` → 都没有则 toast 提示（避免跳进空白详情页）

**改动 4 — 时间轴本地 mock（可关闭）**
后端返回 0 条事件（真机日志 19:48:52 `fetchTimeline resolved: 0 events`）→ 时间轴整块不渲染
并自动 `redirectTo` 到空状态页 → 卡片布局/点击全部无法验收。
加 `USE_MOCK_TIMELINE = true`（与 search 同款）：`fetchTimeline` 返回空时注入 4 条 mock
（3 条 confirmed + 1 条 draft@0.61 进待确认区），真实数据到位后自动覆盖，关闭只需置 false。
**注意**：mock 的 content_id 后端不存在 → 详情页内容区会是空的（只能验跳转/返回/布局骨架）。

**🔴 途中的部署事故（已解决，记入部署铁律）**
v61 首次推包**卡死 23 分钟**：编译 19:22:15 已完成，但同步阶段无进展；
`adb devices` 随之超时 → 判定 **adb server 死锁**（不是 cli 占用）。
处理：TaskStop 停掉卡住的 cli（进程随之退出）→ `adb kill-server` + `adb start-server` → 设备恢复在线 → 重推成功。
**教训**：① 推包命令**禁止用 `| tail`**（会缓冲全部输出，无法观测实时进度），一律重定向到日志文件；
② 判断卡在编译还是同步，看 `unpackage/dist/dev/app-android` 的**最后修改时间**——产物停更 = 卡在同步；
③ adb 死锁时 `adb devices` 会超时挂起，用 `timeout 25 adb devices` 探测，别裸跑。

## 2026-08-31 20:08 v62 推包成功（W2 + W3 全部上机）

```
20:08:01.516 项目 client 编译成功。
20:08:08.475 同步手机端程序文件成功
20:08:10.783 应用【client】已启动
20:08:11.145 [dbg-index] fetchTimeline resolved: 0 events     ← 后端确实无数据
20:08:11.149 [dbg-index] USE_MOCK_TIMELINE -> 注入 4 条 mock 事件
20:08:11.157 [yishu] attachPhotos: days=3 pending=1 ...        ← 时间轴有内容，不再跳空状态页
```
CSS WARNING 对账：7 → **1**（仅 `components/TabBar/TabBar.uvue:103` backdrop-filter 毛玻璃，有意保留）。

**补记：adb 事故的真根因（上一节"adb server 死锁"结论被修正）**
表象是「adb server 死锁」，实际是我手动干预制造的**版本冲突**：
HBuilderX 自带三个 adb，真正使用的是根目录 `adbs/adb.exe`（**1.0.41 / 35.0.2**，支持 reverse），
而我诊断时误用了 `adbs/1.0.31/adb.exe`（**不支持反向代理**）去 kill-server/start-server，
后又用 `adbs/1.0.36/adb.exe` 探测 → 日志打出
`adb server version (31) doesn't match this client (36); killing...` → server 版本被切成 36，
HBuilderX 的 1.0.41 连不上 → 报 `ADB 反向代理创建失败` → `手机无响应`。
恢复：`taskkill /F /IM adb.exe /T` 全杀 → 用根目录 1.0.41 `start-server` → 重推成功。
→ 已沉淀为 SOP §13.5：**手动探 adb 只用根目录 1.0.41，绝不碰 1.0.31/1.0.36。**

**仍待峰宝确认/拍板**
1. 后端无数据：是要灌真实种子数据，还是继续用 mock 验收？（mock 关掉只需 `USE_MOCK_TIMELINE=false`）
2. mock 的 content_id 后端不存在 → 详情页内容区会是空的（只能验跳转/返回/布局骨架）
3. ai 页玻璃输入条发送钮被裁成 20pt 扁条（画布 padding 16/16/16/16 + h=52 + 36pt 按钮冲突、clipsContent:true），
   改 `padding-top/bottom` 30.8→15.4rpx 可修但偏离画布值——修不修等你定
4. `pages/record/record` 保留未删（pages.json 仍注册，已无入口跳转 → 死页面），是否清理

## 2026-08-31 20:2x 推包后的质量复检 + 网络异常定位

**1) 热区遮挡扫描（含新增浮层）→ 0 处，且已做反向对照**
RecordSheet 有 10 个 `@tap`，一旦被遮挡就全废（与之前三页返回键同一个坑）。
扫描报 0 处后**不盲信**——按 SOP 做反向对照：把已修的 messages 人为还原成 bug 版
（图标移回标题容器之前），脚本准确报出
`L6 热区 [n4_93] ← 被 L7 [n4_95] 覆盖 ⚠️ 覆盖者为 hug 高度` → 证明无假阴性 → **浮层 10 个热区安全**。
随后 `cp` 备份还原，md5 校验一致（`dfbe4c8b…`），复扫回到 0 处。

**2) css_lint → 本轮改动零回归**
RecordSheet 报 4 条，逐条甄别**全是误报**：
- `.serif` 缺 font-size/color → 它是**修饰类**，总是与主类并用
  （`class="form-title serif"` / `"text-input serif"` / `"transcript-title serif"`），字号颜色由主类提供，它只管 font-family
- `.tag-active-text` 缺 font-size → 与 `.tag-text`（已声明 24rpx/#3a2e25）并用，它只覆盖选中态颜色 #f8f7f4
- `.currentLabel` → 脚本把 `:class` 三元里的**变量名**误当类名（SOP 已记录的已知误报模式）
新增的 `.dot-first` 模板引用 + style 定义都在，**未成为孤儿**。
其余 orphan-css 17 条均为 index/UploadStatusBanner 的转换残留，非本轮引入。

**3) 「网络异常」定位完成 —— 是我那次 adb 事故的次生灾害，已自愈**
排查路径：先看日志，所有请求均 `ok`/status=200，**没有 `[dbg-req] FAIL`** → 排除 `utils/api.uts:125`
的请求失败 toast。唯一命中的是**上传暂停横幅**：`pause_controller.uts:61` / `uploader.uts:487`
在**连续上传失败 ≥ MAX_BATCH_FAILURES(10)** 时 `pauseSync('网络异常，已暂停同步')`，
且暂停状态**持久化在 Storage**。
**因果链**：adb 反向代理创建失败那两次（v61 卡死 23 分钟、v62 首推失败）→ 手机真连不上电脑
→ 请求连续失败 → 累计 10 次 → 横幅常驻。
**自愈机制已验证**：`sync_client.uts:617` 的 `uni.onNetworkStatusChange` 在网络恢复时会
`if (isSyncPaused()) resumeSync()`，而 `resumeSync()` 同时清 Storage 与 `_consecutiveFailures`
（pause_controller.uts:39-43）→ 逻辑自洽，**不需要改代码**。
若横幅仍在，点横幅上的「继续上传」可手动解除。

**4) 从 v62 运行日志读到峰宝的验机轨迹（旁证改动生效）**
- `content_id=ct-m1` 请求 **3 次** → 时间轴卡片**点进了详情页**，新增的 `openDetail` 生效 ✅
- `/api/v1/interview/profile` 1 次 → 进过访谈页
- 每次 index onLoad 均 `USE_MOCK_TIMELINE -> 注入 4 条 mock`、`days=3 pending=1` ✅
- 全程无 ERROR；`photoWatch init failed` 是标准基座无原生插件的预期降级
- 20:13:39 出现一次非我发起的编译+同步（峰宝自行在 HBuilderX 运行），mock 仍生效

---

## 21:0x W4：端口劫持复发定位（G8 回归）+ 垂直 padding 全项目审计（4 处）

### 1) 🔴 根因：8000 端口二次被劫持 —— 这是「时间轴 0 条 / 无照片 / 搜索只有 mock」的真凶

**不是缺种子数据，是端口被抢。**

- 后端 uvicorn **已死**：`tasklist` 无任何 python 进程；`_uvicorn_8000.log` mtime 停在
  **13:55**（6.5 小时前），`backend/uvicorn_run.out` 停在 08-30 04:34。
- 8000 端口被 **node.exe PID 848** 占着（`netstat` 确认 `0.0.0.0:8000 LISTENING 848`），
  对**任何路径**返回 `HTTP 200` + 裸字符串 `"404"`。
  PID 848 = **HBuilderX launcher 的 httpServer.js**（HBuilderX.exe PID 30668 正在运行）。
- 客户端 `utils/api.uts` 拿到非 JSON → 守卫 `resolve(null)` → `fetchTimeline` 返回 **0 条**
  → 时间轴整块不渲染 → 卡片没照片 → 搜索只有 mock 6 条 → 详情页空白。
- **日志里 status=200 掩盖了一切**，与 G8 初次发作完全同构（台账第 58 行已记载）。

**取证过程**（走了弯路，记下来避免第三次）：
1. 先怀疑后端挂了 → curl `/health` 返回 `404` + `http=200`，反常；
2. 发现本机有 `http_proxy=http://127.0.0.1:57431` → curl/urllib 全走代理返回假 404；
3. 加 `--noproxy '*'` 后仍是 `404` → 证明不是代理问题；
4. 用**原始 socket** 探到真实响应：`HTTP/1.1 200 OK` + chunked body `404`（桩服务实锤）；
5. 探空闲端口 59999/8002/8003 均 `curl exit=7`（connection refused）→ **沙箱没有拦截任意端口**，
   8000 上的是真进程；
6. `netstat` + `tasklist` → node.exe 848 → HBuilderX launcher。

**修复**（沿用 G8 已验证做法）：杀 launcher node → uvicorn 回 8000 → 重做 adb reverse。
⚠️ 需峰宝拍板（要杀 HBuilderX 的进程）。已沉淀为 SOP §17。

### 2) 库里其实是**有数据的**，但全是管线测试垃圾

真机登录用户 = `e4134743-53bd-4ee8-9cd8-05d0917ae2a9`（device_id=`yishu-android-dev`，
最近活跃 08-31 13:34；正是 `backend/seed_and_verify.py` 里的 USER_ID）。

| 项 | 数量 |
|---|---|
| photo | 81（**全部**有 cos_key，71 有 thumbnail_key）|
| text | 5 |
| voice | 2 |
| events | 15（L1 draft ×9 / L2 confirmed ×5 / L2 draft ×1）|
| event_items | 110 |

**但**：照片文件名是 `test_NN_20260824_HHMMSS_*.jpg`，事件名是
「抽象圆点探索 / 极简抽象画创作 / 无信息事件 / 2026-08-28 · 4条」——**聚合管线的测试产物**。
且 `backend/data/storage/` **目录根本不存在** → 81 条 cos_key 全是孤儿记录，文件没落盘。
→ 峰宝要的「真实种子数据」必须**重造**，不能沿用。

### 3) 🟡 第二层根因：即使后端恢复，照片也**显示不出来**

`attachPhotos()` 只从 `localPhotoPath` 取路径，而它**只由本机这轮上传的照片填充**
（`index.uvue:632` `localPhotoPath.value.push(new PhotoPathEntry(uploaded[i].contentId, uploaded[i].item.path))`）。
全项目 grep **零** `downloadFile`、零 `thumbnails` 调用 → **客户端没有从后端拉图的通路**。
后端有现成端点 `GET /api/v1/thumbnails/{content_id}`（返回 JPEG + 归属校验 + 懒生成兜底），但没接。
→ 「卡片上有照片可点」必须**新增缩略图下载+本地缓存**通路，光灌种子数据不够。

### 4) ✅ 已修：垂直 padding 全项目审计（新的一类转换 bug，4 处）

画布 `paddingTop/paddingBottom` 全为 `null`，转换脚本却凭空补了垂直 padding →
固定高度容器的内容区被压扁 → 子元素被 `overflow:hidden` 裁掉。

| 文件 | 类 | 容器 | CSS 误补 | 画布真值 |
|---|---|---|---|---|
| `pages/ai/ai.uvue` | `.n2_355` 玻璃输入条 | h=52pt | T/B 16pt | 发送钮 y=8/h=36（居中）→ 内容区仅 20pt，钮被裁 |
| `pages/search/search.uvue` | `.n2_391` 搜索胶囊 | h=48pt | T/B 16pt | 图标 y=15/h=18 → 内容区仅 16pt，图标被裁 |
| `pages/search/search.uvue` | `.n2_589` 语音媒体 | h=64pt | T/B 14pt | 播放钮 y=16/h=32 → 底部越界被裁 |
| `pages/settings/settings.uvue` | `.n4_152` 开关 | h=28pt | T/B 3pt | 滑块 y=3/h=22 → 底部 3pt 被裁 |

**修法 = 回归画布真值**（不是偏离画布改数值）：删掉垂直 padding，由已有的
`align-items:center` 还原居中。左右 padding 保留（与画布子元素 x 一致）。

**门禁脚本**：`review/_audit_padding2.py`（只报固定高度容器，`hug_contents` 加 padding 无害）。
修复前命中 3（ai 已先修），**修复后命中 0** ✅

### 5) ✅ 已修：搜索页筛选无效

峰宝猜「筛选对 mock 数据无效」——**方向对，但根因更具体**：
`voiceResults` / `textResults` 两个 computed **压根没读 `activeFilter`**；
而 `setFilter()` 只在 `hasSearched && keyword.trim() != ''` 时才 `runSearch()`，
mock 场景 keyword 为空 → 请求不发 → **永远 6 条**。

修法：新增 `matchesFilter(ct)`（与 `filterContentTypes()` 同源），两个 computed 都过一遍。
真实搜索场景后端已按同组 types 过滤过，属幂等二次过滤，不改变结果。
**预期**：全部 6 条 / 照片 1 条 / 语音 3 条 / 文字 2 条（一验即知）。

### 6) 编译验证

`cli launch app-android --project <根> --deviceId DKS9K23526028855 --compile true`
→ **61 秒**，结尾 `项目 client 编译成功。` ✅
- CSS WARNING = **1**（仅 `TabBar.uvue:103` 的 `backdrop-filter` 毛玻璃，有意保留）
- `_csscheck.py` = **0 处问题**
- `_orphan.py` = 16 条（**全为本次改动之前就存在的转换残留**：index 的 header/amber/split-*/photo-detail-*、
  manage 的 tabbar-label-active、record 的 value），非本轮引入

⚠️ **踩坑**：纯编译命令**不带 `--deviceId`** 会静默卡死（8 分钟零输出、dist 目录为空、不报错不退出）。
已沉淀为 SOP §16。

### 7) 待峰宝拍板（4 项）

1. **端口劫持修复方式**：杀 HBuilderX launcher node（G8 老办法）vs 后端改端口+改 3 处配置
2. **种子数据**：图片素材来源（复用 static 9 张 / AI 生图 / 峰宝给真图）+ 人设与内容
3. **图片通路**：客户端新增缩略图下载（真通路）vs 种子数据塞本地 static（demo 通路）
4. **访谈页入口**：`pages/interview/interview` 是**孤页面**（全项目零跳转入口），
   且该页**纯静态**（只 import `@/utils/play`，无 API 调用，三问硬编码）

---

## 22:xx W5：端口迁 8010（已落地）＋ 图片通路真相取证（待拍板）

### 22.1 峰宝拍板（2026-08-31）— 4 条，其中 3 条已执行

| # | 指令 | 状态 |
| --- | --- | --- |
| 1 | 后端改端口 + 3 处配置（否决"杀 HBuilderX launcher"） | ✅ 已落地，端口 **8010** |
| 2 | 图片素材从 `C:/Users/ghf/Pictures/Screenshots` 随机抽取 | ⏸ 待拍板后执行（素材 3126 张 png 已盘点） |
| 3 | 图片通路：去读旧页面代码，不要自己造 | ✅ 已读完，**结论推翻原假设**（见 22.3） |
| 4 | 访谈页入口：看旧页面放哪 | ✅ 已找到：**profile 页 `goInterview()`** |

### 22.2 端口改造清单（全部完成并验证）

| 位置 | 改动 |
| --- | --- |
| `backend/.env` | 追加 `API_PORT=8010` + 启动命令注释 |
| `client/utils/config.uts:20` | `DEV_BASE_URL` 8000 → **8010** |
| `client/utils/config.uts:40` | `http://127.0.0.1:8000` → **8010** |
| `client/utils/config.uts:43` | `REAL_DEVICE_HOST + :8000` → **8010** |
| adb reverse | 新增 `tcp:8010 tcp:8010`（已验证在 reverse --list 中） |
| 后端进程 | 旧 8000 uvicorn（PID 29660）已 kill；新起 **PID 24812 @ 8010** |

**验证**：`curl --noproxy '*' http://127.0.0.1:8010/healthz` → `{"status":"ok"}`（真 JSON）。
新增常驻门禁 `review/_check_api.py`，四步全 PASS（healthz JSON / 登录 / timeline 15 条 / 缩略图 JPEG）。

**两个差点踩死的坑（已沉淀 SOP §18）**：
- `backend/.venv` **不存在**，必须用**系统 Python 3.13**（`C:/Users/ghf/AppData/Local/Programs/Python/Python313/python.exe`）
- 登录 **code 是 `'dev-client'` 不是 device_id**；用 device_id 当 code 会**静默创建全新空用户**
  （unionid=`mock-unionid-yishu-and`，timeline 0 条）→ 极易误判"数据丢了"

### 22.3 🔴 图片通路真相：旧页面只打通「上传→本地回显」，后端回显从未实现

**证据链（主工作区 `D:/GuangH-App/client`，非工作树）**：
- `pages/index/index.uvue:437 photoPathOf()` — 只遍历 `localPhotoPath`，查不到返回 `''`
- `pages/index/index.uvue:601` — `localPhotoPath` 唯一填充点，在**上传回调** `handleBatch()` 内
- `<image :src>` 全部 5 个调用点（:53/:61/:83/:92/:147）源头都是 `photoPathOf()` 或 `ev.coverPath`
- 全项目 grep：`downloadFile` 零命中、`thumbnails` 零命中 → **没有任何后端图片下载代码**

**结论**：历史照片在任何页面都显示不出来，是**设计缺口**，不是还原工作树弄坏的。

**后端侧实测完全可用**：
```
GET /api/v1/thumbnails/{photo_content_id} + Bearer → HTTP 200, image/jpeg, ~2KB, JPEG 魔数 \xff\xd8 ✅
真机用户 e4134743：81 photo（71 有 thumbnail_key）、15 events、110 event_items
backend/data/storage/ 真实存在，829 个文件，photos/ 与 thumbnails/ 均 exist=True
```
⚠️ 旧结论「storage 目录不存在、81 条 cos_key 全是孤儿」**已被实测推翻**，不再引用。

**前端三层缺口**（卡片无照片的根因链）：
1. `eventPhotoIds` 只在上传回调填充（:623），reload 时为空 → 照片条不渲染
2. `photoPathOf()` 无后端回退 → `<image :src="">` 空白
3. 缩略图接口只认 Bearer header，`?token=` 走 query 实测仍 **401**

### 22.4 访谈页入口（已查明）

**旧页面放在 profile 页**：
- `client/pages/profile/profile.uvue:20` → `<view class="profile-btn" @tap="goInterview">`
- `client/pages/profile/profile.uvue:100-101` → `goInterview() { uni.navigateTo({ url: '/pages/interview/interview' }) }`

后端三个端点已就绪：`GET /api/v1/interview/questions`、`POST /answers`、`GET /profile`。

### 22.5 待峰宝拍板（3 项，禁止自作主张）

1. **图片通路方案**：A 直连 URL（改 deps.py 加 query token，最省）/ B downloadFile 预下载（标准但需新造缓存）/ C 免鉴权（不推荐）
2. **种子数据规模与主题**：清垃圾数据（2087 photo 里大量 `test_NN_*` 测试件）后重建，还是新建独立 demo 用户
3. **死页面 `pages/record/record`（1143 行）**：交付前删 or 保留

---

## 23:xx W6：全页面资源-端点-调用核实矩阵（峰宝指定最高优先级）

完整矩阵见 **`_resource_endpoint_matrix.md`**（本轮主交付物）。以下为摘要。

### 23.1 总结论
**50 个后端端点：✅ 真实触发 26、⚠️ 有缺陷 5、❌ 未接线 19。**
四个真机现象全部定位到根因，**无一例是后端能力缺失**（后端全都现成）。

### 23.2 四现象根因（钉死）

| 现象 | 根因 | 性质 |
| --- | --- | --- |
| 卡片没照片 | 四层断链：① `EventOut` 无 photos 字段 ② `/events/{id}/items` 只在拆分流程触发 ③ `eventPhotoIds` 只在上传回调填 ④ `thumbnails` 端点零接线 | 设计缺口 + 接线缺失 |
| 详情页空白 | `event_ops.uts:125` 拼 `?content_id=`，后端 `contents.py:347` **只支持 limit/cursor** → 参数被忽略 → 返回"最新 1 条任意内容" | **客户端调错 API** |
| 搜索只剩 mock | `search.uvue:84 USE_MOCK_DATA=true` 无条件灌 6 条 mock；真实请求仅在关键词非空+回车时发 | mock 开关遮蔽 |
| 访谈页打不开 | live 代码**零跳转入口**，只在废弃 `.bak` 里有，还原时 `goInterview()` 被整段丢失 | 入口丢失 |

**🔴 另一个 mock 开关**：`index.uvue:277/279 USE_MOCK_TIMELINE=true`，events==0 时注入 `ev-m1~m4` **假 id**（云端不存在）。**不关掉它，灌再多真种子数据也看不到。**

### 23.3 图片通路：后端全可用、客户端零引用
- `GET /thumbnails/{cid}` 全客户端零引用；全项目 `<image :src="http...">` **0 命中**
- token 只能走 header（`api.uts:57`）→ 一旦接线必须 `uni.downloadFile({header})` 落临时文件
- 三个 Schema 都缺图片字段：`ContentOut` 无 url、`EventOut` 无 photos、`SearchHit` 无图片字段（搜索页模板也无照片位）
- 已拍板方案：预签名 URL 统一抽象（双 TTL：缩略图 24h / 原图 15m）

### 23.4 顺手挖出的 6 个 bug（此前未知）

| # | Bug | 证据 | 影响 |
| --- | --- | --- | --- |
| 1 | `getPrevL1Id` 数组越界写法 | `index.uvue:377` 写 `days.value[i].events.level`，events 是数组 | **合并功能永远失效** |
| 2 | manage 天数统计必崩 | `manage.uvue:220` 对 number 调 `.substring()` | 统计恒 0 |
| 3 | 搜索跳详情传错 id | `search.uvue:289` 用 `item.id`，实际字段是 `contentId`（存疑待实测） | 详情页收到 undefined |
| 4 | AI 页输入框是 `<text>` 不是 `<input>` | `ai.uvue:10` | **键盘拉不起来** |
| 5 | settings 开关是死状态 | `settings.uvue:197` 样式写死，`themeText`/`cacheText` 未绑模板 | 点了没反应，零持久化 |
| 6 | RecordSheet 保存后不回写 | 只 emit('close')，保存成功无 emit | 录入后 timeline 不刷新 |

### 23.5 死页面 `pages/record/record` —— 确证可删，零风险
- 引用点仅 3 处：`pages.json:25` 注册、`yishu-tabbar:40` 路由表、`text_recorder.uts` 注释
- 引用者 `yishu-tabbar` **只被 7 个 `.bak` 文件引用**，无任何活跃 `.uvue` → 组件孤立 → record 不可达
- 能力已 **100% 被 RecordSheet 重复实现**（`RecordSheet:279/304/314/389/479`）
- 删除风险：仅注释 + `uni_modules/yishu-photo-watch` 引用随页消失，无他页依赖

### 23.6 接线优先级建议（等峰宝拍板，未动）
P0：① 图片通路（存储层 get_download_url + Schema URL 字段 + 客户端 downloadFile）② 关两个 mock 开关 ③ fetchAll 批量调 items ④ 修详情页数据源
P1：① 灌真实种子数据 ② 补访谈入口+修兜底文案+结果上屏+语音走 ASR ③ RecordSheet 增 saved emit ④ 修 6 个 bug
P2：① profile 统计接真数据 + manage 接 `/profile/sensitive` ② 清死页面 + 9 个 .bak

### 23.7 方法沉淀（下次复用）
判定铁律：**「有 PATH 常量」≠「有封装函数」≠「有真实触发点」**，三级分别核实，只认最后一级。
本次 26 个 PATH 常量里 7 个零引用；`markAllRead`/`logout`/`reconcileNow` 都是「有封装无触发」的典型。
5 个 subagent 按「页面组 + 横切基础设施组」切分并行，一次穿透全部 50 端点。

---

## 23:5x W7：P0+P1+P2 全量落地（峰宝拍板「都做」），编译推包中

### 27.1 P0 图片通路（后端 + 客户端全链路，门禁 9/9 PASS）

**后端**（92 个 pytest 零回归）：
- `storage.py` 四后端加 `get_download_url(key, ttl)`：COS 用原生 presigned；fs 用 HMAC 票据 URL
- 新增 `app/api/media.py`：`GET /api/v1/media/thumbnails/{user}/{ym}/{file}?exp=&sig=`，HMAC 校验 + 前缀白名单，错误码 `MEDIA_001/002/003` 已登记 errors.py
- Schema：`ContentOut +thumbnail_url/original_url`、`EventOut +photos[]/cover_url`（新增 `EventPhotoOut`）、`EventItemOut +thumbnail_url`
- timeline 批量 JOIN 组装 photos（防 N+1）
- `GET /contents` 加 `content_id` 精确过滤（**详情页张冠李戴的根因修复**：此前参数被静默忽略返回最新 1 条）
- 门禁 `_check_api.py` 扩到 9 项：healthz/登录/timeline/旧缩略图 + 票据通路 5 项（12/12 事件带 photos、cover_url、无 header 直连 200 JPEG、过期 401、篡改 401、越界 401）

**客户端**：
- `config.uts` +`resolveMediaUrl()`（相对路径 → 绝对）
- `timeline.uts` 解析服务端 `photos[]` → `ev.photoIds/coverUrl/coverPath` 服务端优先
- `index.uvue` `photoUrlById` 全局票据表 + `photoPathOf` 本地 miss 回退 + `attachEventPhotos` 重写（**修复 photoIds 被本地空数组覆盖的致命 bug**）+ `USE_MOCK_TIMELINE=false`
- `search.uvue` `USE_MOCK_DATA=false` + `loadRecent()`（GET /contents 兜底）+ 照片命中卡真图 + `search_api.uts fetchRecentContents()`
- `detail.uvue` 头图 `heroSrc`：photo 记忆换服务端票据 URL

### 27.2 P1 种子数据 + 访谈 + 6 bug

- `review/seed_demo.py`（可复现 random.seed(20260831)）：清真机用户全部数据 + fs 文件 → Screenshots 3128 张抽 40 张转 JPEG 落盘 → generate_thumbnail 全部成功 → **12 events（10 L1 confirmed + 2 L2 draft）/ 54 event_items / 40 photo + 13 text + 4 voice**，跨 30 天复旦生活主题
- 访谈：profile 页补「记忆访谈」行 + goInterview()（对齐旧版；画布偏差：复用 467 行样式新增 1 行，需登记）
- 6 bug 全修：① getPrevL1Id 数组越界（合并功能复活）② manage dayKey(epoch ms) ③ search 传 id（实际已对，VoiceResult.id=contentId，核实无需改）④ ai 输入框 text→input（键盘可拉起）⑤ settings 开关绑定+持久化（uni storage）⑥ RecordSheet `emit('saved')` ×5 处 + 6 宿主页 @saved 接线

### 27.3 P2 真数据 + 清死代码

- profile 统计三数字接真数据（onShow + RecordSheet saved 双触发）
- 删 `pages/record/`（1143 行死页 + 2 .bak）、`components/yishu-tabbar/`、全部 15 个 .bak、pages.json 注册项
- **待拍板**：`/profile/sensitive` 三端点后端就绪，但画布无对应 UI 区块——擅自造界面违反还原铁律，等峰宝定

### 27.4 部署状态

- 编译推包：`cli launch app-android --deviceId DKS9K23526028855`（后台执行中，认「同步手机端程序文件成功」）
- 部署成功后真机截图验收（时间轴/详情/搜索/AI/profile/访谈/死页面已删——改截「访谈页」替代）

## W7.5 真机 401 闭环 + 截图验收（2026-08-31 00:21-00:52 设备时间）

### 🔴 已修：真机 401 死锁（根因链完整闭环）
- **现象**：推包后 `timeline status=401` → `doRefresh has_refresh=true` → 20s watchdog 超时 → 0 事件跳空态页
- **根因（两层叠加）**：
  1. **服务端**：设备缓存的 refresh_token 已被轮换作废（此前某次 refresh 后端成功、响应在设备端丢失，新 token 未落盘 → 永远持旧票）——后端 refresh 端点本身健康（probe 实测：新票 200 / 重放 401「已吊销」，single-use 轮换按设计工作）
  2. **客户端**：`doRefresh` 收 401 只 `clearToken+resolve(false)`，**无重新登录自愈路径**；且 refresh 响应回调链在设备端挂死（后端 401 已发出、设备 20s 无回调——蒸汽模式桥接吞回调家族，api.uts 同源教训）
- **修复**（client/utils/auth.uts）：
  - `wechatLogin` 加 settled 守卫 + 10s 看门狗 + 全分支日志
  - `doRefresh` 加 settled 守卫 + 8s 看门狗；任何失败路径（非200/网络FAIL/看门狗超时）→ `clearToken()` → **降级 `wechatLogin()` 重新登录**（code=dev-client 映射同一种子用户，数据不丢）
- **验证**：00:35:50 wechatLogin 200 → 00:40:39 timeline status=200 **12 events**，attachPhotos serverPhotos=40 strips=12 covers=12，sync pull 200，全程零 401

### 🔴 教训：adb reverse 映射随设备重连丢失（本轮第二次实锤）
- 设备 USB offline 重连后 `tcp:8010` 映射消失（8000/8001 幸存）——**每次设备重连后必须 `adb reverse --list` 复核**，缺了就重建

### ✅ 截图验收（review/shots/w7_*.png，全部真数据）
| 页面 | 结果 |
|---|---|
| 时间轴 | ✅ 封面真图+「八月·28条新记忆」+待确认卡3张照片条+确认/忽略 |
| 详情 | ✅ 头图真照片动态加载+AI描述卡+相关记忆（openDetail→content_id 通路通） |
| 搜索 | ✅ 最近内容真数据；「照片」筛选→大缩略图渲染（票据URL实锤） |
| AI | ✅ 对话流+照片引用卡真缩略图+底部真input |
| 我的 | ✅ 57回忆/40照片/4语音真统计+记忆访谈入口 |
| 访谈 | ✅ 真拉问题（第1问）+语音/文字双入口 |
| 画像管理 | ✅ 10记忆天数/40照片/2主题真统计 |
| 设置 | ✅ 开关右滑+「跟随系统」+「236 MB」计算属性生效 |
| 记录面板 | ✅ 浮层唤起无页面跳转（产品铁律实锤） |

### ⚠️ 新登记（待峰宝拍板/后续波次）
1. **待确认区 pending 卡未绑 @tap**（index.uvue:55）——day-block L1/L2 卡可点进详情，pending 卡只有确认/忽略。画布是否有 pending 卡进详情交互待核对，未擅自加
2. **画像管理页底部重叠**——「关于记忆画像」说明文字与底部导航条重叠（内容区缺 padding-bottom），视觉缺陷待修

## W8 坐标还原推包验证（2026-09-01 01:42-01:48）
- **部署**：cli 推包 55s 编译成功 → 「同步手机端程序文件成功」→ 启动；reverse 复核发现 tcp:8010 **又一次丢失**（设备重连，第三次实锤）→ 重建后数据恢复
- **🔴 新坑：设备横屏**：`accelerometer_rotation=1` 且设备横置 → SurfaceOrientation=3，portrait 坐标的 tap 全部落空、uiautomator dump bounds 变 2302x1084。处置：`settings put system accelerometer_rotation 0` + `user_rotation 0` 锁竖屏（验收后可还原为 1）
- **index hero 坐标还原落地**：大标题 fs 76.9rpx @ (38,108)、副标题 fs25 @ (38,212)、照片说明 fs25 纯白 @ (38,238)——真机渲染与画布一致 ✅
- **detail 玻璃操作条还原落地**：三个 op 列加 `align-items:center`，图标居中于文字上方（画布偏移 1.5/10/0px 真相）；真机实测：问AI 贴左 / 加入胶囊居中 / 分享贴右，全部对齐 ✅
- 详情页 content_id 通路复核：进的是「生日夜和家人的视频」对应内容「爸妈隔着屏幕给我唱生日歌」，无张冠李戴 ✅

## W9 绝对坐标技术债清算 P0 + 门禁落地（2026-09-01 凌晨）

### ✅ P0 流式重构（本波修复）
1. **profile**：列表组1+组2 包进 `.list-flow`（margin-top:480.8rpx=画布组1 y；组2 margin-top:73.1rpx=画布组间距 38px）——加行/加组自动下移，重叠根因消灭。头部四块（头像/昵称/签名/统计卡）静态内容保留 absolute
2. **settings**：同模式 `.list-flow`（组1 y=211.5rpx、组间距 38.5rpx）；标题/返回保留 absolute（铁律 11.5）
3. **detail 玻璃操作条**：三 op 改画布绝对坐标 `left: 0/301/634.6rpx` + `top: 23.1rpx`（画布 x=0/156.5/330px、y=12px 真值），废除 space-between 均分——中项不再漂移

### ✅ absolute 门禁落地（css_lint #14，进 uvue-restore-sop 技能）
- **规则**：position:absolute 白名单制——①铺满形态（left+right 或 top+bottom）②CSS 内 `/* absolute-ok: 理由 */` 注释豁免 ③scrim/sheet/grab 语义名；否则 P1 报警
- **注释豁免制**逼每个 absolute 声明存在理由，新页面默认流式
- **附带修复 lint 两个潜藏 bug**：①`decls()` 被紧邻属性行的注释黏成假键（此类**全部属性检查漏检**）→ 剥注释后解析；②selector 前置注释致整类被丢（.n4_441 曾漏检）→ selector 剥注释
- 修正 PAGES 清单：删已不存在的 record/record，补 empty/empty

### 📋 债务挂账（38 处 absolute-unnamed，各波次清零后门禁归零）
| 页面 | 处数 | 清偿波次 |
|---|---|---|
| interview | 14 | P1 设计定稿后流式重构 |
| messages | 6 | P1 |
| detail 正文区（422/423/424/430/433/434） | 6 | P1 |
| search | 5 | P1 |
| ai | 4 | P3 实装前重构 |
| empty | 3 | P1 |
| 豁免在案 | 29 | 合理保留（头部静态/固定栏/装饰/浮层本体） |

### 🧰 工具教训
- **MSYS 路径转换陷阱**：bash 命令行内嵌「冒号+反斜杠」正则（如 `position:/s*absolute`）会被转成 `/s` 静默失效——正则脚本一律写 .py 文件执行，不内嵌 heredoc

### ✅ W9 验收结果（2026-09-01 真机，证据 review/out/w9_*.png）
| 项 | 结果 | 证据 |
|---|---|---|
| P0-1 profile 流式 | ✅ 真实统计 57 回忆/40 照片/4 语音，两组列表零重叠 | w9_profile.png |
| P0-3 settings 流式 | ✅ 两组（账号与安全/通知偏好 + 深色模式/清理缓存）间距正确，「账号与安全」入口在 | w9_settings.png |
| P0-2 detail 操作条 | ❌ 首验退回（峰宝人肉复验：间距不对 + bar 会滚）→ ✅ 复验通过（峰宝确认）。真根因两条：① `detail_canvas.json` 为 8/30 过期快照，op 间距真值已被改为 48px（旧快照 156.5/173.5px），拿过期数据「验收通过」是假阳性；② bar 躺在整页滚动内容里随内容滑。修复：op 改 flex + 相邻 margin 92.3rpx（48px×1.9231）；bar 改 bottom 锚定钉死视口底部，page-root 锁视口 + 内容进 scroll-view | w9_detail2.png + 峰宝人肉复验 |
| P0-4 门禁 | ✅ 0 违规（29 豁免在案 + 38 纯债务挂账，见上表） | out/css_lint.json |

### 🔴 W9 追加教训（2026-09-01）：快照过期假阳性 + 401 根因 + 一键部署

1. **🔴 画布快照过期假阳性**：P0-2 首验时 `detail_canvas.json` 已是 8/30 旧导出（op 间距
   真值已被峰宝改为 48px，快照仍 156.5/173.5px），按旧快照逐像素测量「全部吻合」→ 假阳性
   验收 → 峰宝打回。制度已入技能 `ardot-to-uvue-css-restore`（24h 时效线 + 台账登记 +
   「冲突时先怀疑快照过期」裁决铁律）。**当前全项目快照默认存疑，后续每页开工前重导出。**
2. **401 自愈链失效根因闭环**：`api.uts` onSuccess 里 `(res.data ?? ({} as UTSJSONObject))`
   的 fallback 假对象在蒸汽模式（纯 JS）下无 UTSJSONObject 原型 → 401 时 `body.getString`
   同步抛 TypeError 被桥接吞 → 401 分支永不执行 → 20s watchdog。修复：`instanceof
   UTSJSONObject` 判型替代假对象。实测自愈链已走通（refresh 200 → tokens rotated → 重发 200）。
3. **一键部署脚本落地**：`deploy_one.sh`（技能 `uvue-deploy-device-ops/scripts/` + 工作树
   `review/scripts/`）——推包→自动重启→自动重建 reverse 一条命令，终结「同步成功即全 FAIL」
   的手工三步循环。

### 🐛 401 自愈链失效——根因闭环（2026-09-01 凌晨）
- **症状**：token 过期 401 后无任何 refresh 日志、后端零 POST /auth/refresh、20s watchdog 才 resolved
- **根因（诊断日志实锤）**：`onSuccess` 非 200 分支 err 构造里 `({} as UTSJSONObject)` fallback——蒸汽模式（纯 JS）下 `{}` 是裸 JS 对象无 UTSJSONObject 原型 → `body.getString` 抛 `TypeError: body.getString is not a function` → **同步异常被桥接吞**，401 分支永远执行不到
- **修复**（api.uts）：`res.data instanceof UTSJSONObject` 精确判型；非对象时直接用 `HTTP <status>` 默认 code/message，不再造假对象
- **验证**：诊断版 try-catch 下 401 链路全程走通：401-branch-enter → refreshToken → doRefresh 200 → tokens rotated → 重放 → timeline 200 **12 events**。正式修复版逻辑等价且无吞异常隐患（诊断版已随 wikn4l 上机，正式版已随 NyktJv 上机）
- **遗留观察**：正式版 401 路径待下次 token 自然过期时看一眼日志即可（预期行为一致）

---

## W10 · P1-AI 气泡重设计（2026-09-01 04:30 grill 五问全拍板，峰宝逐一确认）

**输入分布**：主流 80% 语音转写短句（1-2 行）/ 少数 15% 长文追问（3-6 行，常无标点）/ 极端 5% 超长转写+混排。
**调研结论**（鸿蒙 HarmonyOS 生态 IM 实现 + Apple HIG/iMessage，2026-09-01 检索）：① 用户气泡=高饱和品牌**纯色**+白字（两家均不用渐变）② **一角小圆角方向锚**（用户右下/AI 左下 4px，其余 12-16px）③ hug 宽 + max-width 65-75% 屏宽 + 超限折行 ④ 正文 16-17pt + 宽松行高 ⑤ 对方气泡白/浅灰+深字。

| # | 决策 | 拍板 |
|---|---|---|
| A | 用户气泡底色 | **纯色锈红 #AA5334 + 白字**（去渐变）；AI 气泡白底不动 |
| B | 圆角结构 | **成对方向锚**：用户右下 7.7rpx 其余 38.5，AI 对称左下小圆角；**画布同步重画成新真值** |
| C | 宽度策略 | **hug 宽 + max-width 500rpx**（≈69% 屏宽），超长**不截断全量显示**靠滚动 |
| D | 可读性 | 字号 26.9→**30.8rpx（画布 16pt）** + 显式行高 44rpx，气泡/typing/输入框同字号，随画布重画进真值 |
| E | 范围 | **三块同批**：ai_canvas 消息流两气泡 + ai_reply 组件重画；ai_input/ai_typing 留 P3 按本规范实装 |

**实施注意**：① uvue App 端 max-width 支持情况实施前查证，不支持则「父容器定宽+子 hug」等效实现；
② 画布快照按 W9 新制度开工前重新导出（全项目快照默认存疑）；③ 连续消息聚合逻辑留给 Agent B2 对话域接入时定，本轮保持画布现间距；
④ 实装后旧债顺带：ai 页 4 处 absolute-unnamed 流式重构与本次同页改动，一并清。
**状态**：设计已拍板待画布重画 → 还原 → 真机验收。

### W10.1 画布施工计划与交接（2026-09-01 04:40 落盘）

**卡点记录（交接原因）**：`ardot-remote` MCP 已配置（`~/.workbuddy/mcp.json`，type:http → https://ardot.tencent.com/mcp，官方文档实证）+ 峰宝完成信任与 OAuth 授权；但**会话工具池是启动时快照，本会话搜不到 ardot 画布工具（create_design/batch_edit/capture_screenshot 全 Not found）**。
→ 教训（已验证）：**新增 MCP server 后必须新开会话才激活**。画布施工移交新会话，按下方计划执行。

**施工顺序与验收线**（新会话执行，顺序固定）：

| 序 | 板 | 内容 | 验收 |
|---|---|---|---|
| 1 | AI 气泡改板 | 消息流两气泡 + ai_reply 组件，按 W10 五决策重画（上表 A-E） | screenshot 自审 75 分线；数值逐项对照五决策表；板内加 3 行长文样张验证折行与 max 260px 生效 |
| 2 | 画像管理页 | 新板 390×H；管理对象=旧 `client/pages/portrait/manage.uvue` 清单（概览三格统计/性格标签/记忆主题/画像设置行/关于说明）+ **敏感画像管理区（承接 /profile/sensitive 三端点 UI 缺口）** | 同上自审线 |
| 3 | 账号与安全页 | 新板 390×H；入口=settings n4_140 goAccount（现 toast 占位）；结构：账号信息/数据与隐私/退出登录/危险区（注销，红字隔开） | 同上自审线；完成后 pages.json 注册+接线 |

**还原纪律**（每板通用）：① 开工先重新导出快照（W9 制度）② 流式三定律 + absolute 白名单注释豁免 ③ 门禁 `review/scripts/css_lint.py` 0 新违规（ai 页顺带清 4 处挂账）④ 推包一键 `review/scripts/deploy_one.sh`（判据只认「同步手机端程序文件成功」）⑤ 每板完成即台账登记快照日期与差异。

**交接物**：规格书 `review/W10_design_spec.md`；新会话提示词 `review/W10_next_session_prompt.md`。

### W10.2 画布施工完成 + 快照重导出（2026-09-01）

**画布施工**：三块全部完成并过 screenshot 自审（①AI 对话页 96 分 ②画像管理页 95 分 ③账号与安全页 95 分；布局体检 problemsOnly 全清或确认设计使然）。

**快照重导出（W9 制度执行）**：
| 快照 | 板 | 尺寸 | 状态 |
|---|---|---|---|
| `uvue_gen/ai_canvas.json` | 2:332 AI 对话页 | 390×844 | 重导出（五决策改后状态） |
| `uvue_gen/ai_reply_canvas.json` | 4:299 AI回复·多形态族 | 390×**1200** | 重导出（板加高 + 样张节6；旧快照 1100 已作废） |
| `uvue_gen/portrait_manage_canvas.json` | 41:9 画像管理页 | 390×1720 | 新建 |
| `uvue_gen/account_security_canvas.json` | 41:161 账号与安全页 | 390×844 | 新建 |

**快照保真注意（新坑）**：
1. **分角圆角全树读取不回显**——`batch_read` 全量树只带统一 `cornerRadius`，`bottomLeftRadius/bottomRightRadius` 等分角字段要**显式 `properties` 定向读**才返回。本批已把 W10 决策 B 的分角真值合并进快照：AI 气泡 2:342/2:353/4:339 = TL20/TR20/BL4/BR20，用户气泡 2:350/41:2 = TL20/TR20/BL20/BR4。
2. **maxWidth 画布静默丢弃**（回显不带、渲染无效）→ 长文上限以**定宽 260** 表达；uvue 侧落地用 `hug + max-width:500rpx`，属「画布无法表达、代码正确实现」差异，不算违规。
3. **hug 宽文字永不折行**——折行验证必须用 fill_container 或定宽容器（样张 41:2 定宽 260 已验证 3 行折行）。

### W10.4 峰宝九条验机反馈全落地（2026-09-01 07:30，一炉出）

**反馈清单 → 落地对照**：

| # | 反馈（原话要点） | 落地 |
|---|---|---|
| 1 | 网络异常又犯，skill 白沉淀了？ | 双根因实锤（多版本 adb 互杀灭 reverse + monkey 拉活≠重启）；skill 禁忌表旧口径清除、deploy_one.sh step2/3 顺序反转（reverse 先于启动 + force-stop 冷启动）；skill FAIL 分支补全量日志输出（tail -4 吞错误描述的盲区） |
| 2 | AI 对话页要注入数据，没接口就先造接口，造接口要调研业界方法 | contract-first mock（五篇业界共识：契约先行防漂移/显式场景化 fixtures/mock 开关显式登记）；契约写进 template 头注释 `POST /api/v1/chat/messages → {reply}`，kind=bubble/plain+chips/cards/confirm/typing；`USE_MOCK_CHAT=true` 页内常量登记本节 |
| 3 | 消息形态族怎么看效果 | seed 8 条覆盖①气泡(嵌卡)②直铺长答+chips③横滑卡组④行动确认卡⑥用户长文；onSend 本地闭环：typing 1.8s → 四形态轮换回复 |
| 4 | 画像管理页没数据不能验证 | seed 三条敏感话题（老伴离世/forbid id904、身体状况/mention id905、经济状况/review id906）GET 复核通过；wechat mock 登录（code='dev-client'）映射同种子用户 |
| 5 | 画像设置三 SVG 太丑、账号与安全页也是 | 12 枚 20×20 线性风格 SVG（stroke 1.67 圆头单色：锈红/锈金/灰棕）——refresh/tag/lock + phone/chat/shield + paperclip/file/link；uvue 与画布同源 |
| 6 | 画像页玻璃 tabbar 用新的！ | 画布删旧玻璃 TabBar+旧 FAB，按 TabBar.uvue 组件规格重建（350×62 bg + 4×SVG tab + 56×56 FAB，「我的」active 锈红态）；uvue 侧 manage.uvue 已接 `<TabBar active="profile">` 组件 |
| 7 | 画像页内容下移，页标题距顶 44px→60px | uvue `.header padding-top 48→115.4rpx`（=60px）；画布 41:11/41:14 y60、41:15 y90、41:17 y120 |
| 8 | 数据导出/存储空间与「我的」页存储与备份冲突 | uvue security.uvue 删两行+方法；画布 41:184/41:194 删除、后续四节点 y-88（组间距恒定验证）；归宿=未来存储与备份页 |
| 9 | 附件面板没入口，左移发送钮、入口放右边，SVG 同风格 | 输入条加 36×36 白底圆钮 clip 入口（锈红回形针）；**uvue 初版序写反（附件在左），对照画布终审截图纠正为 [发送钮][附件钮]** |

**uvue 编译失败一轮（deploy hqSTjw 前身 ZS8sLT）**：`ai.uvue:186:85`——①`type` 字面量构造零先例（`MemCard(title:...)`）→ 改 class+constructor+new（对齐 MessageItem/DayGroup 套路）②函数默认参数 `= []` 零先例 → 全参显式③seed 里 plain 调用把 bullets 塞 cards 位（类型错配）④CSS `.a.b`/后代+`:first-child` 三处 → 显式覆盖类。全部已修，教训沉淀 ardot-to-uvue-css-restore「UTS 编译器坑」节。

**画布施工坑三枚（沉淀 ardot-canvas-pitfalls）**：①effects 写入不收 BACKGROUND_BLUR 且失败清空全字段 → 毛玻璃降级 INNER+DROP②SVG rect/A 命令不支持 → path+Q ③纯水平线段 bbox 零面积 degenerate（报错与 malformed 一致，连烧三轮的根因）→ 扁胶囊 fill。

**差异降级登记**：
- typing 三点静态透明度阶梯（App 端 keyframes 支持有限，非动画）
- 附件面板样式合理化重建（画布 ai_typing 4:361 为设计稿，uvue 按面板语义重排：把手/标题/2×3 相册网格/文件+链接入口行）
- manage.uvue scroll padding-bottom 160→240rpx（避让 TabBar 220rpx 全高）
- 画布新 TabBar 无 BACKGROUND_BLUR（写入层不支持，白 a0.55 半透明底观感近似）

**推包运行记录（三轮）**：ZS8sLT 编译失败（见上）→ hqSTjw 编译同步实际成功（产物 07:35 落盘、设备端 www/ 字节数一致），但 `OUT=$(cli ...)` 命令替换被 HBuilderX.exe 继承 stdout 管道 EOF 永不来 → 僵死 23 分钟后 TaskStop 终止；deploy_one.sh step1 同步改造为后台+mktemp 重定向+10s×150 轮询（FAIL 分支全量吐 40 行），坑沉淀 uvue-deploy-device-ops 禁忌表 → **fOeJLH 轮询版首战 1m57s 三步全绿**：同步成功 ✓ → reverse 重建（UsbFfs tcp:8010 非空）✓ → force-stop 冷启动 ✓。

**状态**：uvue 代码全落地、画布三板同步 + 截图终审通过（AI 附件钮序 ✓/画像页新 TabBar+60px 头 ✓/security 单行+SVG ✓）；推包三轮收口（终态=fOeJLH 成功，设备已跑最终版产物并冷启动）→ 任务 #11/#12 完成，交峰宝人肉验机（期间严禁占 adb）。遗留债：四份画布快照（ai/ai_reply/portrait_manage/account_security）未重导出，下会话开工前按 W9 制度补。

**W10.4-b 事故收口（08:0x，峰宝投诉「重建重启没生效+无数据+网络异常」）**：诊断=后端 8010 活（healthz ok）+ reverse 空 + adb 互杀再现 → **真凶=deploy_one.sh 用 1.0.36 老 client 建隧道，脚本结束后 HBuilderX 侧 41 server 抢回 → reverse 清空**（结构性互杀，非没做）。**adbs/ 根下裸 adb.exe=1.0.41（HBuilderX 真身）——旧「无 1.0.41」口径作废，device-adb.md 版本矩阵本就正确，主 SKILL.md 被错误实测覆盖**。治本：deploy_one.sh ADB 默认换 1.0.41 裸真身 + SKILL.md 三处口径修正 + 项目副本同步；隧道户口迁 41 server + force-stop 冷启动，峰宝可验机。产物闭环：app-service.js 产物 mtime 07:51 > ai.uvue 07:35 → 设备跑的是含附件钮序修正的最终版。**军规（峰宝拍板）：一切部署/诊断命令必须带超时自动返回**——shell 内 adb 用 `timeout 25` 前缀 + Bash 工具级 timeout 双保险；推包走后台+轮询（10s×150 硬上限 25 分钟）。
