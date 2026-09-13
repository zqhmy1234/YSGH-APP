# 按钮 → 去向 → 页面重做 三查审计（2026-09-01 补充）

> **触发**：峰宝批评——"记录面板点录音进去的页面很丑很丑"+"按钮跳转逻辑是否完善全面？是不是所有按钮点击后都有可跳转的页面？跳转页面是不是重新设计开发过？"
> **本次标准（升级）**：不再只看"页面实体有没有"，而是逐按钮三查：① 点下去有没有可去的新页面？② 去的页面是不是画布重做过的？③ 没有可去页面的按钮，现状是什么（toast 占位 / 纯本地态 / 无绑定）？
> **基线**：客户端 15 uvue + pages.json 12 路由；画布设计真值源 `design_data_for_agents/` 8 页面；develop @ 99e2cf0。

## 〇、一页结论

**录音界面丑的根因坐实**：画布 05_记录面板**只设计了默认态（四选项弹层）**，没有设计「点进去之后」的 form-layer（录音/文字/照片表单层）——record-box/mic-btn/textarea 全是开发自拼的旧样式（`components/RecordSheet/RecordSheet.uvue:692+`，`background:#faf9f5` 无画布对照）。这不是"重做过了但丑"，是**压根没有画布设计**。

**按钮跳转现状**：12 个路由页面里 **9 个是画布重做过的**（index/search/messages/profile/interview/settings/detail/ai/security），3 个是功能/工具页（empty/portrait-manage/agg-check）。但**大量按钮点下去没有可去页面，只是 toast 占位**——全项目共 **~20 处占位 toast**，集中在 profile/security/detail/messages/ai/portrait 六页。

## 一、录音/文字/照片表单层：无画布设计（最丑，优先修）

| 项 | 现状 | 依据 |
|---|---|---|
| form-layer 顶部导航（‹ / 标题 / 保存） | 开发自拼 | RecordSheet.uvue:692-752 `.form-layer/.form-head`，`#faf9f5` 底 |
| 语音 record-box（🎤大圆钮） | 开发自拼 | :815-847 `.record-box/.mic-btn/.mic-icon`，emoji 🎤/⏹ 作图标 |
| 文字 textarea + 分类标签 | 开发自拼 | :760+ `.text-input`，纯白底卡片 |
| 照片 pick 区 | 开发自拼 | `.photo-box/.photo-empty` |
| 画布 05_记录面板设计 | **仅默认态四选项** | `design_data_for_agents/05_记录面板/design_data.json`（elements 无 form-layer 帧） |

> **结论**：记录面板"点进去之后"的三个表单（语音/文字/照片）全部**无画布设计**。录音是重灾区（emoji 图标 + 无波表/无进度环/无情绪展示框架）。**需画布补设计 3 帧**（语音/文字/照片表单），或至少 1 帧通用 form-layer 规范。

## 二、按钮跳转全面清点（~20 处占位 toast）

### 2.1 我的页 profile（6 菜单，3 占位 + 1 错绑）

| 按钮 | 现状 | 判定 |
|---|---|---|
| 画像管理 | ✅ navigateTo → portrait/manage（功能页） | 有去向 |
| 消息中心 | ✅ navigateTo → messages | 有去向 |
| 设置 | ✅ navigateTo → settings | 有去向 |
| 我的收藏 | ❌ toast「收藏功能开发中」 | **无页面** |
| 存储与备份 | ⚠️ **错绑** goPrivacy → toast「隐私政策开发中」 | **无页面 + 错绑 bug** |
| 关于忆述光华 | ❌ toast「关于页面开发中」 | **无页面** |

### 2.2 账号与安全 security（6 项，4 占位）

| 按钮 | 现状 | 判定 |
|---|---|---|
| 手机号 | ❌ toast「手机号管理开发中」 | 无页面 |
| 微信 | ❌ toast「微信绑定管理开发中」 | 无页面 |
| 隐私权限 | ❌ toast「隐私权限开发中」 | 无页面 |
| 退出登录 | ✅ 真逻辑（确认弹窗 → reLaunch index） | 有去向 |
| 注销账号 | ⚠️ toast「内测期暂不支持自助注销」 | 功能未开 |

### 2.3 记忆详情 detail（6 钮，5 占位/半通）

| 按钮 | 现状 | 判定 |
|---|---|---|
| 收藏 | ⚠️ 纯本地态 `let fav=ref(false)` + toast，无持久化、无后端端点 | 半通 |
| 更多（编辑/删除） | ⚠️ 编辑→toast「待接入」；删除→真逻辑 | 半通 |
| 问 AI | ❌ toast「AI 对话待接入」 | 无页面（AI 对话页已建但未串） |
| 胶囊 | ❌ toast「已加入胶囊」（假操作） | 无功能 |
| 分享 | ❌ toast「分享功能待接入」 | 无页面 |
| 相关事件 | ⚠️ toast 显示标题，无跳转 | 半通 |

### 2.4 消息中心 messages

| 按钮 | 现状 | 判定 |
|---|---|---|
| 回响卡（本周摘要） | ❌ toast「本周记忆摘要待接入」 | **设计有回响卡，点击无实现** |
| 消息条目 | ⚠️ 仅标记已读，无跳转 | 半通（无详情落地） |

### 2.5 AI 对话 ai（附件面板）

| 按钮 | 现状 | 判定 |
|---|---|---|
| 附件-文件 / 粘贴链接 | ❌ toast「内测期即将开放」 | 无功能 |

### 2.6 画像管理 portrait/manage（功能页，无画布对照）

| 按钮 | 现状 | 判定 |
|---|---|---|
| 查看主题 | ❌ toast「查看主题：xxx」 | 无页面 |
| 重新生成画像 | ⚠️ toast「正在重新生成画像...」 | 半通（无真动作） |
| 编辑性格标签 | ❌ toast「编辑性格标签（MVP待实现）」 | 无功能 |
| 画像隐私设置 | ❌ toast「画像隐私设置（MVP待实现）」 | 无功能 |

### 2.7 语音播放全链路（跨页）

| 位置 | 现状 | 判定 |
|---|---|---|
| index playVoice | ❌ toast「播放语音：xxx」（不真播） | 无播放能力 |
| search playVoice | ❌ toast「语音播放待接入音频流」 | 无播放能力 |
| RecordSheet 录音 | ✅ 真录音+转写+入库 | 正常 |

> **结论**：录音能录、能转、能存，但**播放是假的**（两处 toast 占位）——MVP「回听记忆」核心闭环断链。

## 三、跳转页面是否重做过的判定（12 路由对照画布）

| 路由页面 | 画布重做 | 依据 |
|---|---|---|
| pages/index/index | ✅ 重做 | n*_ 标记 + 画布 01 时间轴（357 元素） |
| pages/search/search | ✅ 重做 | 画布 02 搜索（301 元素） |
| pages/messages/messages | ✅ 重做 | 画布 03 消息（133 元素） |
| pages/profile/profile | ✅ 重做 | 画布 04 我的（256 元素） |
| pages/interview/interview | ✅ 重做 | 画布 06 冷启动（64 画布标记） |
| pages/settings/settings | ✅ 重做 | 画布 07 设置 |
| pages/detail/detail | ✅ 重做 | 画布 08 详情（118 标记，但有结构丢失） |
| pages/ai/ai | ✅ 重做 | 画布 ai（56 标记，USE_MOCK_CHAT 仍开） |
| pages/account/security | ✅ 重做 | 画布 41:xxx（38 标记） |
| pages/empty/empty | 🟡 功能页 | 无独立画布帧（9 标记，复用空态设计） |
| pages/portrait/manage | 🟡 功能页 | 无画布对照（8 标记，自建功能页） |
| pages/debug/agg-check | 🟡 工具页 | 内部调试，非用户面 |

> **关键**：12 个路由页面**全部不是旧手写页**（旧 record.uvue/yishu-tabbar 已在 W10.4 移除）。但 **RecordSheet 的 form-layer 三表单层无画布设计** = 用户最常触达的"记录动作"反而是最丑的。

## 四、按新标准列的修复优先级（供排期）

| 序 | 事项 | 类型 | 建议 |
|---|---|---|---|
| F1 | **画布补 form-layer 三帧**（语音/文字/照片表单层设计） | 画布设计 | 先补语音帧（峰宝点名的重灾区）：麦克风按钮视觉/波形/计时/情绪展示框架 |
| F2 | **存储与备份页 + 修错绑**（profile:47 行 @tap=goPrivacy→goStorageBackup） | 页面开发 | 承接 US-42 导出入口 + US-45~50 同步管理 |
| F3 | **隐私政策页 + 关于页** | 页面开发 | 合规上架硬门槛，文案用已出草稿 `docs/隐私政策草稿_v0.1` |
| F4 | **我的收藏页**（后端 favorite 端点 + detail 收藏持久化） | 页面+后端 | detail:84 纯本地态升级 |
| F5 | **语音播放接入**（index/search 两处 toast→真播放） | 功能接线 | MVP「回听」闭环断链，优先级高 |
| F6 | **messages 回响卡/消息详情**（openFeatured toast→真页面） | 页面开发 | 设计有、实现无 |
| F7 | **detail 问AI/分享/胶囊** 归位 | 接线 | AI 对话页已建，detail 应串 navigateTo 而非 toast |
| F8 | security 手机号/微信管理 | 页面开发 | 依赖后端绑定能力，可暂缓 |
| F9 | portrait 查看主题/编辑标签/隐私设置 | 页面开发 | MVP 待实现可标清 |

## 五、证据索引

- 记录面板 form-layer 自拼样式：`client/components/RecordSheet/RecordSheet.uvue:692-880`（.form-layer/.record-box/.mic-btn 无画布对照）
- 画布 05 记录面板仅默认态：`design_data_for_agents/05_记录面板/design_data.json`（elements 无表单层帧）
- 画布 8 页面真值：`design_data_for_agents/_overview.json`（total_pages=8）
- profile 三占位+错绑：`client/pages/profile/profile.uvue:136-154`（goFavorites/goPrivacy/goAbout）
- security 四占位：`client/pages/account/security.uvue:77-86`（onPhone/onWechat/onPrivacy）
- detail 五占位：`client/pages/detail/detail.uvue:156-199`（toggleFav/onMore/askAI/addCapsule/share）
- messages 回响卡占位：`client/pages/messages/messages.uvue:162-163`（openFeatured）
- ai 附件占位：`client/pages/ai/ai.uvue:300-302`（onAttachEntry）
- portrait 四占位：`client/pages/portrait/manage.uvue:390-403`（onThemeTap/onRefreshProfile/onEditTags/onPrivacySettings）
- 语音播放两处占位：`index.uvue:268-274` + `search.uvue:330-335`
