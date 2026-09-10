# missing-pages 执行计划 v2（2026-09-04 00:2x 定稿）

> 来源：峰宝 2026-09-03 23:55 九条反馈 + 2026-09-04 00:09 三项侦察（mock 全景 / 语音链路考古 / 占位全景）。
> 本计划取代口头批次①~⑦推进表；状态打勾在 §2 波次总表内维护，完成后同步 `_diff_ledger.md` §W 之后的节。
> **台账地址登记**：见 `_diff_ledger.md` §W2。

---

## 1. 侦察结论摘要（证据已核，全部到文件行号/commit/端点取证）

1. **mock 全景**：ai.uvue 整页 mock 恒开（开关常量已删仅剩注释）；trash.uvue 整页假数据；storage.uvue 用量字节/缓存假值；favorites 空态兜底 12 条假卡；RecordSheet 位置写死「上海·江边」；CapsuleSheet 封存仅本地；audio_player 失败回退测试音（§O P4 峰宝已验收，保留）。已诚实化：index/search 的 USE_MOCK_* 均已 false。
2. **「有端点没接」**：后端 `/api/v1/classify`、`/api/v1/corrections` 存活（openapi 47 路由实测），前端 PATH_CLASSIFY/PATH_CORRECTIONS 零引用。
3. **语音链路考古**：旧链路（录音→wav→POST /asr/transcribe→响应自带情绪字段→UI 情绪 chip）**没死，死的是入口页**。`utils/voice.uts` 完整存活，RecordSheet 组件在用；interview.uvue 自写假录音器（只计秒，interview.uvue:147 占位文案）。情绪识别实况=**SenseVoice 内置 EMO 标签**（backend/app/api/asr.py:96-116 emotion 全字段），全后端无 HuBERT 代码（HuBERT 仅存在于产品定义文档口径）。
4. **⚠️ 悬账实锤**：D-16 修复 `f1f8a3c`（情绪三层默认值，默认 None 不伪造「平静」）只在分支 `fix4b-pre-rebase`，**不在 develop/当前分支**；`models.py:66 emotion: str = "平静"` 旧默认值在 HEAD 复活。D-22 修复 `81424fb` 同样悬空。「已修待复验」口径对当前分支不成立。
5. **占位全景**：17 处诚实 toast 占位 + 3 处 pressable 无 @tap；pages.json 18 条路由无死跳转。
6. **画像域无主题卡根因**：manage.uvue:286-313 主题卡=从 timeline L2 事件标题派生的 MVP 降级，真实数据零命中；28 表画像域 4 表无一等公民「记忆主题」存储。
7. **已实现无需施工**：搜索「照片/语音/文字」再分类=后端 content_types 参数真筛（search.uvue:198-231）；时间轴已接 fetchTimeline（mock 已关）。

---

## 2. 波次总表

| 波 | 名称 | 依赖 | 峰宝拍板 | 状态 |
|---|---|---|---|---|
| **W0** | 悬账回捞（D-16/D-22 cherry-pick） | 无 | — | ⬜ 待批 |
| **W1** | 时间轴卡片占位清除 | 无 | — | ⬜ 待批 |
| **W2** | 访谈录音接真链路 | 后端零改动 | — | ⬜ 待批 |
| **W3** | 导出端点恢复+接线 | 后端改 | — | ⬜ 待批 |
| **W4** | 用户协议页 | 无 | — | ⬜ 待批 |
| **W5** | mock 治理批 | 部分待后端 | classify/corrections 是否本轮接 | ⬜ 待批 |
| **W6** | 占位清账批 | 部分 | 3 处整卡跳转意图 | ⬜ 待批 |
| **W7** | 主题卡数据源 | — | **方案 a/b 待拍板** | ⬜ 待批 |
| **W8** | A 方案闪屏根治 | 独立波次 | 已拍板（A 方案） | ⬜ 待批 |

**建议顺序**：W0 → W1 → W2 → W3/W4 并行 → W5/W6 穿插 → W7（等拍板）→ W8。

---

## 3. 波次明细

### W0 悬账回捞（最优先，无依赖）
- **目标**：把 rebase 时丢失的 D-16/D-22 修复接回当前分支。
- **动作**：cherry-pick `f1f8a3c`（models.py 情绪默认 None + 三层默认值）+ `81424fb`（D-22 degraded 标记）；冲突按当前 HEAD 手工合并；grep 验 `emotion: str = "平静"` 不复现。
- **验收**：后端测试受影响用例全绿；真机录音→情绪字段非「平静」伪造（真实标签或 None）。
- **联动**：AGENTS.md「D-16 已修待复验」口径纠偏——修的是「回捞后待复验」。

### W1 时间轴卡片占位清除（纯前端）
- **目标**：卡片媒体「有多少是多少」，消灭空白占位格。
- **改动**：index.uvue:65/133 两处 photo-strip——渲染前 filter 掉 `photoPathOf(cid)==''` 的 id；`photoCount` 展示值对齐实际可渲染数（若无封面无照片条，meta 行照片文案同步收敛）。
- **验收**：真机时间轴无空白格；「N 张照片」与可见照片条数一致。

### W2 访谈录音接真链路（后端零改动）
- **目标**：回答「录音之后怎么设计」——录音→转写→情绪→答案真文本。
- **改动**：interview.uvue 引入 `utils/voice.uts`：startRecording→startRecord（wav），stopRecording→stopRecord→transcribeWav→`answers[k]=res.text`（复用 RecordSheet.uvue:983-1072 现成写法）；录音中 UI 沿用现有「正在录音 X″」；转写失败走诚实 toast + 允许文字输入兜底（textAnswer 已存在）；情绪 chip 可选展示 res.emotion。
- **守卫**：voice.uts 500ms 最短录音守卫；转写失败静默路径必须出 toast（不静默）。
- **验收**：真机三问全流程——录音→松手→答案区显示真转写文本→提交→后端 answers 收到文本。

### W3 导出端点恢复+接线
- **目标**：总账 A9 关单。
- **改动**：后端重挂 `/api/v1/export`（按 US-42 拍板链路：全量记忆数据打包下载）；storage.uvue:186 onExport 改真调用+下载进度/完成 toast；privacy.uvue:87「（即将上线）」文案同步去掉。
- **验收**：真机导出→得到文件→内容可解析；A9 总账打 ✅ 注明落地位置。

### W4 用户协议页
- **目标**：about 页「用户协议」从诚实 toast 变真页。
- **改动**：协议文案起草（峰宝审后定稿）+ 新建 `pages/about/agreement.uvue`（复用 privacy.uvue 版式与滚动结构）+ about.uvue:70 改 navigateTo。
- **验收**：真机 about→用户协议→内容页可读可回退。

### W5 mock 治理批
- **可立即修（无依赖）**：
  - favorites.uvue:314-317 空态 12 条假卡**删除**（有真数据路径，假卡误导）；
  - config.uts:29 MOCK_EXTERNAL_AI 死开关删除；
- **需拍板**：classify/corrections 前端接线（端点活但触发 UI 属 B 域分类确认/纠错流程，需定哪个页面哪个交互触发）；
- **待后端（登记总账，不在本计划施工）**：ai.uvue 整页（等 B2 对话域端点）；trash.uvue（等回收站端点）；storage 字节用量（并 A9）；RecordSheet 真定位（等定位服务）；CapsuleSheet 封存（等胶囊端点）。
- **保留不动**：audio_player 测试音回退（§O P4 已验收）；interview 兜底题（网络失败合理兜底）。

### W6 占位清账批
- **17 处诚实 toast 逐项三分类**（接真 / 保留进远期总账 / 删假交互）：
  - 接真（随对应波次）：导出→W3；用户协议→W4；
  - 待峰宝拍板：收藏云同步（detail:293/favorites:439）、记忆编辑（detail:304，远期账）、删除记忆（detail:312）、补充说明 PATCH（detail:369）、绑定管理×3（security，users/me）、附件选择（ai:302）、自定义日期（CapsuleSheet:93）、卡片图（ShareSheet:82）、开源许可页（about:85）；
  - 社交分享灰显项（ShareSheet:87）维持灰显。
- **3 处 pressable 无 @tap（需峰宝定意图）**：echo-card 整卡（index:28，候选=进「去年今日」回响详情）、pending 卡整卡（index:55，候选=进 detail）、profile-card 整卡（manage:17，候选=进画像详情/大图）。
- **验收**：本批完成后 `_diff_ledger.md` 占位清单全行有归宿；总账新增/关单同步。

### W7 主题卡数据源（等拍板方案 a/b）
- **方案 a（推荐，不加表）**：后端把 L2 事件聚合产物出主题数据（B3 聚合本来就是主题级来源），manage 主题卡改读真主题端点；theme-detail 挂真数据。前置=确认 B3 聚合在真实数据下能产出主题。
- **方案 b（加表）**：新增轻量 `memory_theme` 表 + 画像标注产出主题；前端接口不变。涉及 Schema 变更与迁移，动库需峰宝点头。
- **验收**：画像管理页出现 ≥1 张真主题卡可点进 theme-detail 看真数据（补批次⑤ F9a 验收前置）。

### W8 A 方案闪屏根治
- **内容**：四 Tab redirectTo 切页闪屏 → 单页组件化根治（峰宝已拍板 A 方案）。
- **前置**：W0~W7 全部收口后独立开工；开工前先录屏取证闪屏帧（基线对比）。
- **验收**：四 Tab 切换无闪屏；RecordSheet/各弹层在组件化宿主下回归正常。

---

## 4. 拍板待决项（开工前须清零）

1. **W7 主题数据源**：a（不加表，L2 聚合）/ b（加 memory_theme 表）。
2. **W6 三处整卡跳转意图**：echo-card / pending 卡 / profile-card 点了去哪。
3. **W5 classify/corrections**：本轮接还是登记总账等 B 域。
4. **W4 协议文案**：我起草后峰宝审（是否需要产品部定稿口径）。
5. **本计划整体**：波次顺序认不认。

## 5. 不施工清单（有意保留，防误修）

- audio_player 测试音回退（§O P4 拍板保留，仅 404/网络失败时触发）；
- interview 兜底题（拉取失败兜底，真题目接口正常时被覆盖）；
- 搜索本地二次过滤（幂等设计，注释已说明原因）；
- 社交分享灰显（UI 本灰显）；
- 「查看关联记忆」CTA 不渲染（总账 A10 挂账）；
- PATH 常量绕过（auth/timeline/event_ops 硬编码 URL）技术债：登记不施工，待 B 域统一收口。

## 6. 流程军规（每波通用，违者返工）

1. 每波完成→deploy_one.sh 上机→峰宝复验→**当场登记台账**（登记债铁律）；
2. 同文件多刀必须串行 + 每刀 grep 验刀（铁律十）；
3. 涉弹层/圆角改动画布截图对拍（cornerRadius 丢字段铁律）；
4. 后端改动过 review_agent 门禁 + 受影响测试全绿；测试依赖 Docker Desktop 需运行；
5. 完成项回写远期总账打 ✅；口径变化五处同步（AGENTS/08 文档/progress/handoff/feature_list）。

## 7. 自审漏项检查记录（2026-09-04 00:2x）

对照信息源逐项核对：峰宝两轮反馈 12 条 + 三份侦察报告 + 原批次⑥⑦ + A 方案 + 远期总账 A 系。

| 检查项 | 判定 |
|---|---|
| 回响+路由关单 / F1 补登 / F9c 追认 | 已在 §W 登记，无需波次 ✓ |
| 搜索再分类 | 已实现，侦察结论 §1.7 记录，不设波次 ✓ |
| 时间轴占位 / 导出 / 用户协议 / 访谈 / 闪屏 / 主题卡 | W1/W3/W4/W2/W8/W7 ✓ |
| mock 全景（ai/trash/storage/favorites/RecordSheet 位置/CapsuleSheet/死开关） | W5 立即修 + 待后端登记，无遗漏 ✓ |
| 语音链路 / 情绪识别 / D-16 悬账 | W2 + W0 ✓；HuBERT 口径备注：交付文档只读不改，实况以 §1.3 为准 |
| 17 处占位 toast + 3 处无绑定 | W6 全行收录，无遗漏 ✓ |
| classify/corrections 零接线 | W5 需拍板项 ✓ |
| openapi 47 路由 × 前端触达矩阵 | `_resource_endpoint_matrix.md` 在 W5/W6 收口后回刷 ✓ |
| 批次⑤ F9a 验收 | 依赖 W7 产出主题卡，已在 W7 验收标准注明 ✓ |
| 批次⑦ 基线回归 | 并入 W8 验收（四 Tab 切换）✓ |
| 数据库重设计 | 结论=不推倒重设；唯一决策点 W7 方案 a/b ✓ |

**自审结论**：无漏项。风险两点：① W0 cherry-pick 跨分支冲突需手工核对；② W7 方案 a 依赖 B3 聚合真实产出，若聚合产物仍为空则方案 a 无效，届时回退方案 b。
