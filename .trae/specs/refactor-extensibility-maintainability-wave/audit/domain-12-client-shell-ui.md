# 域⑫ · 客户端壳与 UI 深度审计

> 审计范围：`client/pages/shell`、`components/{TabBar,TabAi,TabIndex,TabProfile}`、`pages.json`、`design_tokens.json`、`uvue_gen/` 生成物
> 性质：**只读审计，未修改任何业务文件**（唯一新增=本报告）。本波严格行为等价，缺陷只登记不修。
> **未编译**（遵守简报：不编译）；未跑真机/像素对拍。
> 范围说明：`uvue_gen/` 位于仓库根（不在 `backend/app|client|scripts|deploy` 四目录内），但任务书明确点名「uvue_gen 生成物」为本域对象，故纳入并单独标注。
> 排除：`client/uni_modules/**`（依赖边界，未深读）、`client/unpackage/**`（构建产物、`client/.gitignore` 已忽略）、`agent/`（全波排除）。

## 覆盖范围与实读清单

**实读文件（路径 : 行数）**

壳与路由：
- `client/pages.json` : 138（全读；16 page 条目 + globalStyle）
- `client/pages/shell/shell.uvue` : 157（全读；模板 1-22 / script 24-147 / style 149-157）
- `client/utils/shell_state.uts` : 24（全读）
- `client/App.uvue` : 112（全读；关键 L94-112 平台约束注释）

Tab 组件：
- `client/components/TabBar/TabBar.uvue` : 216（全读）
- `client/components/TabProfile/TabProfile.uvue` : 457（读 1-200 + defineProps/watch/菜单接线段 69-171）
- `client/components/TabSearch/TabSearch.uvue` : 763（读骨架 1-125 / defineProps / savedSeq watch / filter 映射 108-125、205-320）
- `client/components/TabAi/TabAi.uvue` : 974（读 380-465 预填契约 + defineProps + onMounted/watch；头注 1-10）
- `client/components/TabIndex/TabIndex.uvue` : 1740（读骨架 1-30 / 213-251 import 段 / 328-400 生命周期 / 946-1004 工具与相册监听；函数清单 50+ 条）

样式令牌：
- `client/design_tokens.json` : 64（全读：18 色 + 19 尺寸 + 6 字体 + 6 间距 + 4 效果）
- 32 个 `.uvue` 的 `<style>` 块（全量正则统计，逐文件行数见下）

生成物与生成器：
- `uvue_gen/audit_report.md` : 全读
- `uvue_gen/_missing_icons.json` : 全读
- `uvue_gen/*_gen.uvue` : 14 个（统计/对拍，未逐行读）
- `uvue_gen/{icons,images,panel_redesign_shots}` : 132/6/4 文件（哈希对拍，未逐个读内容）
- `scripts_ardot2uvue.py` : 读 1-45（头注 + 常量段）
- `gen_design_data_v4.py` : 读 1-40
- `scripts/audit_harness.py` : 读 167-267（轴 2 客户端漂移）+ 406-452（体积轴阈值）
- `scripts_audit_assemble.py`（`scripts_assemble.py`）: 常量化引用统计（未深读）

**工具实跑（只读，非编译）**
- `python scripts/audit_harness.py client --skip-openapi` → 扫描 86 客户端源文件；`无代码内死路由 ✓`、`无 .ts 残留引用 ✓`、`USE_MOCK_* 未关闭 2`（`RecordSheet.uvue:493` / `TabAi.uvue:216`）
- `python scripts/audit_harness.py filesize` → 存量超阈 7（client 6：RecordSheet 2208 / TabIndex 1740 / detail 1165 / TabAi 974 / favorites 974 / manage 891）

**未读到 / 未覆盖（明确声明）**
- 未编译、未跑冷编译门、未做真机像素比对（简报要求不编译）。
- `client/uni_modules/**`（4 个自研 UTS 插件）未读；`manifest.json`/`AndroidManifest.xml` 未审（属打包域）。
- `uvue_gen/*_preview.html`（15 个）仅统计存在性，未读内容。
- `.wt/missing-pages`、`.wt/wechat-entry`（**非 git worktree 注册的孤儿目录**，`.gitignore` 忽略）仅做存在性/规模核实，未深读。
- 域⑪（观测与错误）与本域交叉面（`utils/{sentry,log}`）未审。

**`git status --short`（审计时点）**
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
→ 工作区**干净**：无在途修改的业务文件。`git worktree list` 仅 `D:/GuangH-App cec3172 [develop]` 一条。

---

## 关键量化：设计令牌收敛度（口径先定义，再给数）

### 口径定义（三条，互不替代）

| 口径 | 定义 | 结果 |
|---|---|---|
| **A · 机制覆盖率（最严格）** | `design_tokens.json` 在客户端代码中被 `import` / 读取 / 引用的次数 | **0**（全仓唯一引用是 `refactor-ledger.md:46`，非代码）。即**令牌机制覆盖率 = 0%**，所有"命中"均为人工抄写的巧合相等 |
| **B · 颜色字面量按出现次数** | `client/**/*.uvue` 中 hex 字面量总数 / 其中值恰好等于某令牌值者 | **660 / 763 = 86.5%**；**103 次（13.5%）不在令牌集内** |
| **C · 颜色字面量按去重值** | 不同 hex 值数 / 其中等于令牌值者 | 13 / 56 = 23.2%；**43 个不同值不在令牌集内** |

补充计数（同口径 B 的分子来源，命令见证据）：`rgba(` **190** 处（全部未令牌化，令牌用 hex+alpha 表示）、`font-family: "Sarasa Gothic SC"` 硬编码 **336** 处（令牌 `typography.font_family` 存在）、`font-size:` 声明 **329** 处（令牌只有 5 档字号）、`border-radius:` **215** 处（令牌只有 2 档圆角）、长度单位 **2766 rpx + 132 px 双轨并存**（令牌 dimensions 全部以 px=390 宽画布为基准）。

### `<style>` 块体量（口径对账，可复现）
- 严格内层行数（`<style>` 与 `</style>` 之间）= **7359**
- 内层 + 每文件 1 个 `<style>` 开标签 = **7359 + 32 = 7391** ⇒ **与台账 L-12 的「7391 行 / 32 文件」精确吻合**
- 内层 + 首尾标签 = 7423；`.uvue` 总行数 = 15921（32 文件，32 个 style 块，每文件恰 1 块）
- 样式行前 6 名：RecordSheet 856 / TabIndex 678 / detail 591 / TabAi 506 / favorites 458 / manage 426

### 令牌 → 字面量覆盖表（逐令牌，`*.uvue` 内出现次数）

| 令牌 | 值 | 出现次数 | 结论 |
|---|---|---|---|
| text_primary | #3a2e25 | 152 | 抄写命中 |
| text_secondary | #8a7a6a | 94 | 抄写命中 |
| accent_rust | #b05a3a | 96 | 抄写命中 |
| bg_card | #ffffff | 95 | 抄写命中 |
| text_tertiary | #b3a696 | 88 | 抄写命中 |
| accent_amber | #c4913c | 62 | 抄写命中 |
| bg_page | #f8f7f4 | 42 | 抄写命中（另 `pages.json` 5 处 + globalStyle） |
| record_panel | #faf9f5 | 17 | 抄写命中 |
| text_muted | #5a4c40 | 15 | 抄写命中 |
| accent_amber_faint | #c4913c1a | 4 | 抄写命中 |
| accent_amber_light | #c4913c24 | 2 | 抄写命中 |
| bg_glass | #ffffff8c | 1 | 语义相同但写法不同（实装写 `rgba(255,255,255,0.55)`，见 D12-3） |
| record_bg | #1a140f | 1 | 抄写命中 |
| **accent_rust_light** | #b0593b1f | **0** | **令牌零使用** |
| **bg_glass_light** | #ffffff52 | **0** | **令牌零使用** |
| **bg_glass_strong** | #ffffffcc | **0** | **令牌零使用** |
| **photo_overlay_top** | #00000080 | **0** | **令牌零使用** |
| **photo_overlay_bottom** | #f8f7f4ff | **0** | **令牌零使用** |
| **tabbar_y** | 752 | **0** | **令牌零使用**（实装改 `bottom:48px` 锚定，见 D12-2） |
| effects.card_shadow | `0 4 16 #00000008` | 0 | 令牌零使用 |

**根因（重要，决定收敛手法）**：`App.uvue:95-111` 以「已踩坑·勿改回」形式写明——**uvue App 端样式不继承**，且全仓 `var(--x)` 自定义属性计数 = **0**、无 `lang="scss"`、无 stylelint/eslint 配置。即：**令牌收敛不可能靠运行时 CSS 变量或继承实现，只能是构建期/codemod 注入**（例如生成期常量替换 + 棘轮守卫）。这是"收敛度"这一项的技术边界，须在拍板前明确。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D12-1 | `client/design_tokens.json`（全文件）× `client/**/*.uvue`（763 处色字面量） | 重复/令牌机制零覆盖 | P0 | 高 | 中 | 新增工具 | 令牌注入后 763 处字面量归零 + 冷编译 0 error | 批次1（需先定注入方案） | 确认（既有登记 `refactor-ledger.md:46` L-12，本次实读升级：P1 候审 → P0 确认） |
| D12-2 | `client/design_tokens.json:12-16,30-31` × `client/components/TabBar/TabBar.uvue:148,206` | 声明漂移（令牌值本身已漂移） | P1 | 中 | 低 | 行为等价替换 | 令牌值 ↔ 实装值逐项对拍（见证据） | 批次1 | 确认 |
| D12-3 | `client/**/*.uvue`（103 次非令牌色 + 190 处 rgba + 336 处 font-family） | 硬编码未收敛（真缺口） | P1 | 高 | 中 | 新增工具 | 同上 D12-1 口径 B/C 归零 | 批次1（随 D12-1） | 确认 |
| D12-4 | `client/design_tokens.json:6,7,13,17,18,32` | 死码（令牌 6 项零使用） | P2 | 低 | 低 | 删码(需证死) | 逐令牌字面量计数（见覆盖表，6 项 = 0） | 批次3 | 确认 |
| D12-5 | `scripts_ardot2uvue.py:27`（`ROOT_DIR` 硬编码）+ `gen_design_data_v4.py:20`（`DESIGN_DIR` 硬编码） | 门禁缺口/生成器不可复跑 | P0 | 高 | 中 | 行为等价替换 | 改 `ROOT_DIR` 为不存在路径 → 秒失败；无 CI/门禁触发面 | 批次1 | 确认 |
| D12-6 | `uvue_gen/icons/*`（132）× `client/static/icons/*`（95） | 生成物 vs 实装双向漂移 | P1 | 中 | 中 | 删码(需证死) | MD5 对拍：61 同名 / 43 字节全同 / **18 内容不同** | 批次2 | 确认 |
| D12-7 | `uvue_gen/audit_report.md:3,7` | 死证据（引用不存在的对拍器） | P2 | 低 | 低 | 删码(需证死) | 全仓 `Get-ChildItem -Filter scripts_verify_layout.py` → 0 命中 | 批次3 | 确认 |
| D12-8 | `uvue_gen/*_gen.uvue`（14）× `client/**` | 生成物快照分叉无门禁 | P1 | 中 | 中 | 删码(需证死) | 行数对拍 delta 最大 +1567；`audit_harness` 轴2 只扫 `client/` | 批次2 | 确认 |
| D12-9 | `scripts/audit_harness.py:172-180,219-250` | 门禁缺口（三向缺一向 + 生成物不在扫描面） | P1 | 中 | 低 | 新增工具 | 造「pages.json 有、文件无」样例 → 轴 2 不报 | 批次2 | 确认 |
| D12-10 | `client/pages/shell/shell.uvue:15-19,30,34-37,82-100` + `TabBar.uvue:5-30,70-119` | 注册表覆盖度（新增 1 个 tab 需改 ≥6 文件 12+ 点） | P1 | 高 | 中 | 新增工具 | 见「新增 Tab 注册点」清单，逐点核 | 批次1 | 确认 |
| D12-11 | `client/components/TabBar/TabBar.uvue:70-119` | 重复（4 段同形 goX） | P1 | 中 | 低 | 纯移动+兼容再导出 | 抽 `switchTab(tab)` 后行为快照对比 | 批次2 | 确认 |
| D12-12 | `TabIndex.uvue:373-387` / `TabProfile.uvue:93-102` / `TabAi.uvue:460-464` / `TabSearch.uvue:121-125` / `TabProfile.uvue:136-170` | 重复（组件契约样板） | P1 | 中 | 低 | 纯移动+兼容再导出 | 抽 composable 后 4 处调用点等价 | 批次2 | 确认 |
| D12-13 | 12 个 `pages/**/*.uvue` 的 `goBack()`（如 `about.uvue:88-96`、`privacy.uvue:122-130`）× `manage.uvue:393` | 重复（同语义 12+1 实现） | P1 | 中 | 低 | 纯移动+兼容再导出 | 抽 `utils/nav.uts` 后 13 处调用点等价 | 批次2 | 确认 |
| D12-14 | `client/utils/shell_state.uts:8` + `shell.uvue:93` + `TabAi.uvue:391-392` + `shell.uvue:50-60,61-63` | 死码/声明漂移（只写不读 + 零生产者分支） | P2 | 低 | 低 | 删码(需证死) | 见证据（三处 grep 全 0） | 批次3 | 确认 |
| D12-15 | `TabIndex.uvue` 1740 / `RecordSheet.uvue` 2208 / `detail.uvue` 1165 / `TabAi.uvue` 974 / `favorites.uvue` 974 / `manage.uvue` 891 | 巨文件（已冻结基线） | P1 | 高 | 中 | 纯移动+兼容再导出 | `audit_harness filesize` 报告行 | 批次4（顺延，需编译门） | 确认（既有基线白名单 6 项，本域复核无新增） |
| D12-16 | `TabIndex.uvue:951-970` × `TabSearch.uvue:112-118,227-233` × `TabProfile.uvue:113-117` × `favorites.uvue:305` | 可扩展性缺口（硬编码枚举 + 近义双实现） | P2 | 中 | 低 | 行为等价替换 | 抽常量表后逐调用点等价 | 批次3 | 确认 |

**D12-1 证据**：全仓（排除 `.wt/`、`node_modules/`）搜 `design_tokens` → 唯 1 命中 `refactor-ledger.md:46`；`client/` 下 0 命中。实测统计（`client/**/*.uvue` 合并文本）：hex 字面量 **763** 次（6/8 位；3 位短写 0 次），其中 **660** 次的值恰好等于某令牌值（86.5%），**103** 次不在令牌集；去重后 56 个值中仅 13 个等于令牌值。⇒ 令牌文件是"记账副本"，不是事实源：改主题/调色需全量改 32 个 `.uvue`（7359 行样式）。另：`var(--x)` = 0、无 scss/less、无 stylelint/eslint ⇒ 运行时收敛路径不存在（见上文根因）。

**D12-2 证据**：令牌 `accent_rust=#b05a3a` 与 `accent_rust_light=#b0593b1f`——实装里同时出现 `#B0593B`（3 次，把 light 的基色当主色用）与 `#B05A3A1F`（2 次，把主色当 light 基色用），两侧互相串位；令牌 `text_primary=#3a2e25` 出现 152 次，但另有 `#3B2E26`（2 次，G 通道差 1）与 `#3B2E26` 系阴影用色 `rgba(59, 46, 38, 0.1)`（`TabBar.uvue:152`）。尺寸侧：令牌 `tabbar_radius=31`，实装 `TabBar.uvue:148` 为 `border-radius: 36px`；令牌 `tabbar_y=752` 零使用，实装改 `bottom: 48px`（`TabBar.uvue:206`）。⇒ **连"抄写"都在漂移**，证明复制式收敛不可持续。

**D12-3 证据**：103 次非令牌色中含明确语义色，例如 `#EDE5D5`(14) `#E5DCC8`(9) `#F0EBE3`(7) `#E9DFCB`(6) `#E1D3B8`(6) `#F6F1E7`(6) `#E3CCA8`(3) `#FFF7EF`(3) `#8A5A20`(3) `#C7AD87`(3) `#7A8C5A`(2) `#4E7D8C`(1)——均未登记为令牌。`rgba(` **190** 处全未令牌化，典型如 `TabBar.uvue:147` `rgba(255,255,255,0.55)` ≡ 令牌 `bg_glass=#ffffff8c`（同语义、不同写法）。`font-family: "Sarasa Gothic SC"` **336** 处硬编码——由 `App.uvue:102-111` 的"样式不继承"约束决定，属**平台强制重复**，收敛必须走生成期注入。

**D12-4 证据**：逐令牌在 `client/**/*.uvue` 搜字面量：`accent_rust_light`(#b0593b1f) 0 命中、`bg_glass_light`(#ffffff52) 0、`bg_glass_strong`(#ffffffcc) 0、`photo_overlay_top`(#00000080) 0、`photo_overlay_bottom`(#f8f7f4ff) 0、`tabbar_y`(752) 尤其以边界正则 `(?<![\d.])752(?![\d])` 搜 = 0、`effects.card_shadow` 的 `#00000008` = 0。⇒ 18 个色令牌中 5 个、19 个尺寸令牌中 1 个 + 1 个效果令牌**从未被实现使用**。

**D12-5 证据**：`scripts_ardot2uvue.py:27` `ROOT_DIR = 'D:/GuangH-App/.wt/wrap1-agentA2-ui-restore'`——`git worktree list` 只有 `D:/GuangH-App cec3172 [develop]` 一条，该 worktree 已不存在 ⇒ 生成器**开箱即失败**；`:24-26` `SRC_FILE` 默认指向 `C://Users//ghf//.workbuddy//projects/...` 的绝对路径与私有工具产物（`ARDOT_JSON` 环境变量可覆盖，但默认值不可复现）。`gen_design_data_v4.py:20` `DESIGN_DIR = r'C:\Users\ghf\Downloads\忆述光华 · W1 时间轴页方向小样'` 同样是用户本机绝对路径，且 `:12-13` 依赖 `.cowork-temp/svg_parse.py`（临时目录模块）。⇒ 生成链**不可在其他机器/CI 复跑**，而 `client/**` 多个文件头注自称"ardot 直转 v2 · 审计化生成"（`detail.uvue:2`、`TabProfile.uvue:3`、`TabSearch.uvue:3`、`interview.uvue:2`、`messages.uvue:2`、`settings.uvue:2`、`TabAi.uvue:4`），生成体与手写体之间**无任何段标记/CASE 头/DO NOT EDIT**，再生成即静默覆盖手写修复。

**D12-6 证据**：MD5 逐文件对拍 `uvue_gen/icons`(132) × `client/static/icons`(95)：同名 61 个，其中 **43 个字节全同（纯重复）**、**18 个内容不同**——且这 18 个正是 `tab-ai-active/inactive`、`tab-profile-active/inactive`、`tab-search-active/inactive`、`tab-timeline-active/inactive`（**TabBar 全部 8 个图标**）＋ `tab-plus.svg`（中央 +）＋ `i2_302.svg`、`index-search.svg`、`msg-*.svg`×3、`record-*.svg`×4；每对尺寸均不同（例：`tab-timeline-active.svg` gen=186B / client=396B；`tab-plus.svg` gen=297B / client=818B）。`uvue_gen/images` 6 张 PNG 与 `client/static/images` 6 张**字节集合完全相同**（836KB 纯重复）。⇒ "画布真值"与实装资产**谁权威未定义**，且 TabBar 图标恰在漂移集内。

**D12-7 证据**：`uvue_gen/audit_report.md:3` 写「对拍器：scripts_verify_layout.py（容差 2px…）」、`:7` 写「合计：对拍 561 元素，超差 0（0.0%）」；全仓 `Get-ChildItem -Recurse -Filter scripts_verify_layout.py` → **0 命中**（根目录仅有 `scripts_ardot2uvue.py` / `scripts_assemble.py` / `gen_design_data_v4.py`）。⇒ 该验收结论**不可复现**；且其覆盖的 14 个页面中 `index/search/profile/ai/ai_input/ai_reply/ai_typing/press` 对应的老页**已整体退役**（`shell.uvue:7` 注 + 实测 `client/pages/{index,ai,search,profile}` 均不存在），结论已失效。

**D12-8 证据**：行数对拍 gen → 手写：`index_gen 396 → TabIndex 1740`（+1344）、`record_gen 641 → RecordSheet 2208`（+1567）、`ai_gen 252 → TabAi 974`（+722）、`detail_gen 370 → detail 1165`（+795）、`search_gen 460 → TabSearch 763`（+303）、`interview_gen 296 → 673`、`messages_gen 314 → 579`、`settings_gen 230 → 303`、`empty_gen 129 → 170`、`profile_gen 456 → TabProfile 457`（+1）。冻结快照与实装平均分叉 ≈ +600 行/页，且快照仍含 `press_gen`/`ai_input_gen`/`ai_reply_gen`/`ai_typing_gen` 等退役态。`audit_harness.py:174` 的 `CLIENT.rglob("*")` 只在 `client/` 内扫描 ⇒ **生成物目录不在任何门禁扫描面**。

**D12-9 证据**：轴 2 的三向只做了两向——`_registered_pages()`（`audit_harness.py:183-189`）只从 `pages.json` 取 `path` 集合，L224-250 只比对「代码内 `/pages/...` 引用是否在集合内」（且跳过注释）。**无反向检查**：① `pages.json` 条目是否有物理 `.uvue` 文件；② `pages/**` 下是否存在未注册的孤儿页面；③ `uvue_gen/` 内任何引用。实跑 `audit_harness.py client` → `无代码内死路由 ✓`（当前 16/16 确实干净，见下），但该"绿"**不能覆盖**上述三向缺口。

**D12-10 证据**：见下方「新增 Tab 注册点」清单——单个新 tab 需改 **6 个文件、12 个以上位置**，且无一处是"单一注册表"：`VALID_TABS`(shell.uvue:30) 是数组、`everX` 是 4 个独立 `ref`(:34-37)、模板是 4 行手写 `v-if/v-show`(:15-18)、`activate()` 是 3 段 `if` 链(:83-99)、`TabBar` 模板是 5 个手写块 + 4 个 `goX` 函数、`shell_state`/`requestShellTab` 的 tab 参数是**自由字符串**（`shell_state.uts:16`，无枚举约束）。

**D12-11 证据**：`TabBar.uvue:70-119` 的 `goIndex/goAI/goSearch/goProfile` 四段结构完全相同——`if (props.active !== 'x') { if (props.embedded) emit('tab','x') else redirectTo('/pages/shell/shell?tab=x') } else if (props.subpage == true) navigateBack()`，仅 tab 名与注释不同（各段还各带一行"B10 P2：老世界 tab 触点收编进壳"同文注释）。⇒ 新增第 5 个 tab 必须复制第 5 段；且同一组件存在**双行为分叉**（`embedded:true` 用于 shell，缺省用于 `empty.uvue:22`/`manage.uvue:142`），无编译期约束。

**D12-12 证据**（Tab 组件重复样板，举 2 处）：
① **`savedSeq` 契约样板 ×4**：`TabIndex.uvue:383-387`、`TabProfile.uvue:98-102`、`TabAi.uvue:460-464`、`TabSearch.uvue:121-125` 四处逐字相同 —— `watch((): number => props.savedSeq, (n: number): void => { if (n > 0) { onRecordSaved() } })`；且 `onRecordSaved` 四处各写一遍（`TabAi.uvue:384-385` 是**空实现占位**，`TabIndex.uvue:802`/`TabProfile.uvue:132`/`TabSearch.uvue:421` 各自真实）。另 `shownSeq` 样板 ×2（`TabIndex.uvue:373-382`、`TabProfile.uvue:93-97`）。
② **`TabProfile.uvue:136-170` 的 7 个 one-liner 菜单跳转**：`goInterview/goPortraitManage/goFavorites/goSettings/goStorageBackup/goAbout/goMessagesCenter` 全是 `function goX(): void { uni.navigateTo({ url: '/pages/y/y' }) }`（地址硬编码在函数体内，无路由表）。

**D12-13 证据**：12 个页面各自定义 `goBack()`（`about.uvue:88`、`agreement.uvue:76`、`security.uvue:93`、`detail.uvue:381`、`favorites.uvue:504`、`messages.uvue:204`、`manage.uvue:393`、`theme-detail.uvue:207`、`privacy.uvue:122`、`settings.uvue:69`、`storage.uvue:216`、`trash.uvue:175`），其中 **11 份逐字相同**：`const pages = getCurrentPages(); if (pages.length > 1) { uni.navigateBack({delta:1}) } else { uni.reLaunch({ url: '/pages/shell/shell' }) }`，注释亦逐字相同（"铁律：reLaunch/redirectTo 直入时页面栈仅 1 页…"，`privacy.uvue:123`/`settings.uvue:70`/`favorites.uvue:505`/`trash.uvue:176`/`theme-detail.uvue:208` 等）；实跑 `grep "pages.length > 1"` = 11 命中，与 `grep "function goBack"` = 12 命中差的 1 份即 **不一致点**：`manage.uvue:393` 的 `goBack()` 用裸 `uni.navigateBack()`，无栈深兜底 ⇒ 同族语义 12 份实现、缺 util。

**D12-14 证据**（三处全 0）：
① `shell_state.uts:8 export let shellActiveTab = ref('index')` 的**唯一读写点**是 `shell.uvue:28`(import) + `shell.uvue:93`(写)；除编译产物 `client/unpackage/**`（镜像源码）外**零读取**——但其注释宣称"组件读取用于跨 tab 切换判定；shell 写"，属**失实宣称 + 只写不读**。
② `TabAi.uvue:391-392` `PREFILL_KEY_TITLE = 'memTitle'` / `PREFILL_KEY_SUMMARY = 'memSummary'` 定义后**零引用**（全文件仅此 2 行）。
③ shell 的 URL 入参通道 `?type=`（`shell.uvue:61-63`）、`?memTitle=`/`?memSummary=`（`:50-60`）**零生产者**：全仓搜 `shell/shell\?(type|memTitle|memSummary)=` = 0 命中（实测 6 处跳 shell 全部是 `?tab=`，值为 index/ai/search/profile 全合法）；AI 预填实际走 `detail.uvue:502` 的 `setStorageSync('yishu_ai_context_title'…)` → `TabAi.uvue:393-395` 的 storage 三键通道。⇒ `onLoad` 中 `hasPrefill`/`activate('ai')` 与 `searchType` 分支**不可达**（`?type=` 通道另有一处活用在壳内 `requestShellTab('search','type=…')`，非 URL）。

**D12-15 证据**：`audit_harness.py filesize` 实跑输出：`client/components/RecordSheet/RecordSheet.uvue 2208`、`client/components/TabIndex/TabIndex.uvue 1740`（其中 `<script>` 段 211-1059 = **848 行**，含 50+ 个顶层函数）、`client/pages/detail/detail.uvue 1165`、`client/components/TabAi/TabAi.uvue 974`、`client/pages/favorites/favorites.uvue 974`、`client/pages/portrait/manage.uvue 891`，均标「基线已冻结」。**声明漂移提示**：`spec.md:8` 称 `TabSearch.uvue 763` 为巨文件，但阈值 `.uvue ≤800` ⇒ 763 已**不超阈**（非棘轮项）。

**D12-16 证据**：① 事件层级枚举（L1-L3）硬编码于 `TabIndex.uvue:951-970`：`levelText()` 与 `levelTag()` 是**近义双实现**（`level===1 → '日卡片'` vs `'日卡'`，2/3 档返回值完全相同），另有 `level === 1` 判定散落 `TabIndex.uvue:148,601,667`、`timeline.uts:456-458`、`manage.uvue:280`、`theme-detail.uvue:135`。② 内容类型枚举 `'photo'/'voice'/'text'` 以魔法字符串贯穿 ≥10 文件，且**各写一套映射**：`TabSearch.uvue:112-118`（→1/2/3 序号）、`:227-233`（`matchesFilter`）、`TabProfile.uvue:113-117`（统计）、`favorites.uvue:305`（`kindWord`）、`detail.uvue:260,279,296,309`、`RecordSheet.uvue` 多处 `formMode == 'x'`。⇒ 新增一个模态（如"视频"）需逐处改。

---

## 依赖方向与抽象覆盖度结论

**FF1：本域是否存在反向依赖？**

本域**无跨层反向依赖**（无 services↔api 类问题；客户端无分层）。实际观测到的依赖形态：
```
shell.uvue ──→ utils/shell_state.uts（共享 ref 桥）
            ──→ utils/auth.uts（ensureLogin）
Tab{Index,Ai,Search,Profile}.uvue ──→ utils/shell_state.uts（requestShellTab）
TabIndex ──→ utils/{timeline,time,uploader,api,play,agg_runner,event_sync,agg/perf_timing}
         ──→ uni_modules/yishu-photo-watch
TabBar.uvue ──→ 无 utils 依赖（仅 uni.redirectTo/navigateBack 直调）
utils/shell_state.uts ──→ vue（叶子）
```
**方向单向、无环**。但有一处**反向耦合（组件→组件宿主语义）须登记**：`TabBar.uvue:75,88,100,112` 在非 embedded 模式下**自己知道 shell 的路由**（`redirectTo('/pages/shell/shell?tab=…')`）——导航目标从宿主（shell）倒灌进被复用组件；`empty.uvue:22`/`manage.uvue:142` 复用该组件时即隐式依赖 shell 页存在。这是"组件不得知道宿主路由"的抽象泄漏，与 D12-10/D12-11 同源。

**FF2：新增一个 Tab 需改几处？（全部注册点，12 点 / 6 文件）**

| # | 文件:行 | 需做什么 | 缺失后果 |
|---|---|---|---|
| 1 | `client/pages/shell/shell.uvue:30` | `VALID_TABS` 数组加入 tab 名 | tab 被 `onTabTap` 判为未知（`:108` warn） |
| 2 | `client/pages/shell/shell.uvue:34-37` | 新增 `everX = ref(false)` 保活标记 | 组件永不挂载 |
| 3 | `client/pages/shell/shell.uvue:15-18` | 模板加 `<TabX v-if="ready && everX" v-show="activeTab=='x'" …/>` | tab 无内容 |
| 4 | `client/pages/shell/shell.uvue:82-100` | `activate()` 加 `if (tab=='x'){everX=true}` + 需要时加 `shownSeq` 自增分支 | 首切不挂载 / 刷新语义缺失 |
| 5 | `client/pages/shell/shell.uvue:19` | `<TabBar>` 无需改（回调透传），但 `@tab` 路径语义靠 1-4 | — |
| 6 | `client/components/TabBar/TabBar.uvue:5-30` | 模板加 item 块（图标 active/inactive + 文案 + `:class`） | 无入口 |
| 7 | `client/components/TabBar/TabBar.uvue:33-36 + 22/49-56` | 中央 FAB 与 `tabbar-center-placeholder` 宽度/顺序需重排（当前固定"左2+占位+右2"五槽） | 布局错位 |
| 8 | `client/components/TabBar/TabBar.uvue:70-119` | 加 `goX()` 第 N 段（D12-11 样板） | 点击无反应 |
| 9 | `client/static/icons/tab-x-active.svg` + `-inactive.svg` | 新增 2 个图标资产（**且需先定义 uvue_gen/ 侧谁权威**，见 D12-6） | 空白图标 |
| 10 | `client/utils/shell_state.uts:16` | 无强制（`tab: string` 自由串）——**这是缺口而非免改点** | 拼写错误无编译期保护 |
| 11 | `client/pages/<x>/<x>.uvue` + `client/pages.json` | 仅当该 tab 需要独立页（当前 4 tab 全部**不注册 pages.json**，走组件化）；若走页面则须注册并改 `TabBar` 跳转语义 | 路由不存在 |
| 12 | `client/pages/shell/shell.uvue:136-146` `onBackPress` | 若 tab 有自有浮层/优先级（如 ai 的 `aiAttachOpen`），需在此加分支 | 返回键行为错 |

**对照（已泛化、无需改，可作正面参照）**：`components/{EchoSheet,CapsuleSheet,ShareSheet,TagEditPanel,VoiceWave,UploadStatusBanner}` 以 `v-if + props + emit` 契约接入，新增同类浮层**无需改壳**；`pages.json` 的 `globalStyle` 已集中化背景色。

---

## 本域已查无问题项（避免遗漏被误读）

- **三向对拍当前全绿（可复现）**：`pages.json` 条目 **16** = `client/pages/**/*.uvue` 物理文件 **16**；**无「注册但缺文件」**、**无「文件但未注册」**（孤页 0）。逐条跳转目标（`navigateTo/redirectTo/reLaunch/switchTab` 全量枚举）**全部命中已注册页面**，`audit_harness client` 亦判 `无代码内死路由 ✓`。
- **老四页确已彻底退役**：`client/pages/{index,ai,search,profile}` 四路径 `Test-Path` 全 False，`pages.json` 无对应条目 ⇒ `shell.uvue:7` 的"已彻底退役"叙述**属实**（spec 中登记的"shell.uvue:7 陈旧注释"漂移已由 09-23 审计窗修好，本域复核**无需重开**，与 `tasks.md` B6 结论一致）。
- **`shell.uvue` 的 tab 参数防呆正确**：`onLoad`（:46-74）对非法 `tab` 显式 `console.warn` 而不静默，且回落当前 tab；`onTabTap`（:103-109）对未知回调同样显式告警。
- **壳内保活/零销毁设计自洽**：`v-if`(首挂)+`v-show`(切换) 组合保证 `everX` 只单调置真，无"切回重挂"路径；`ready` 闸门（:76-79）确保 `ensureLogin` 后组件才 mount，`ensureLogin` 失败亦放行（不卡白屏），语义有据。
- **`requestShellTab` 桥无环且单一实现**：`shell_state.uts` 是全仓唯一跨 tab 导航桥（`Var(--)`/`defineExpose`/`$refs` 全项目零先例，注释 `:2-4` 说明理由），`TabIndex.uvue:391` 与 `TabProfile.uvue:128` 为仅有两处消费者，未发现第二套桥。
- **图标引用零缺失**：`client/**/*.uvue` 内 `static/icons/*` 引用去重 **86** 个，逐个 `Test-Path` → **缺失 0**。`uvue_gen/_missing_icons.json` 所列 28 个缺图中 24 个至今不在 `client/static/icons`，**但当前代码零引用** ⇒ 属退役画布的遗留清单，不构成运行时缺口（仅 D12-7/D12-8 的证据失效问题）。
- **`unpackage/` 未入库**：`git ls-files client/unpackage` = 0，`client/.gitignore` 含 `unpackage/`、`.hbuilderx/` ⇒ 构建产物未污染仓库（48.6MB 本地，不跟踪）。
- **`pages.json` 自身结构规整**：16 条 `path` 唯一、无重复；`globalStyle` 集中声明背景/导航栏色（`#F8F7F4` 5 处，属令牌值的人工复制，已计入 D12-1，不另开条目）；无 `tabBar` 原生配置残留（B10 回退后由 `TabBar.uvue` 自绘，注释 `TabBar.uvue:67-69` 说明了原生 `hideTabBar` 在 app-uvue Android 失效的真机实锤）。
- **无 3 位 hex 短写**：763 处 hex 全为 6/8 位，无 `#fff` 形式的隐性不一致写法。

---

## 备注（供台账合并去重）

- D12-1 与 `refactor-ledger.md:46` 的 **L-12 为同一条**（P1 候审）——本域实读后：①口径可精确复现（7359 + 32 = 7391）；②补足"机制覆盖率 0%"判据；③升为 `确认`，建议严重度 **P0**。**不重复立项**，只更新该条。
- D12-15 与 `refactor-ledger.md:31` 的 **L-02（TabIndex script 848 行）**及 `audit_harness filesize` 基线白名单为同一批——本域复核**无新增超阈文件**。
- D12-7/D12-8/D12-6 属**新立项**（生成物域，台账此前未覆盖）。