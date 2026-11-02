# PR：76 条走查收口 + 跨模态检索（A+B+C）

> 分支：`feat/walkthrough-76-crossmodal` → 目标 `develop`
> 提交数：22 ｜ 改动：135 files, +13816 / −1292
> 分支头 SHA：`8e8774f0c1ad3f9e1b8a89df73f01f7313a377ac`

## 一、这个 PR 做了什么

两条主线：**真机走查收口**（76 条清单从 45 通过推到 60 通过）+ **跨模态检索**（用户新需求，照片/音频/文字三模态互搜）。

### 1. 跨模态检索 A+B+C（新功能）

| 方案 | 内容 | 关键文件 |
|---|---|---|
| **A** 照片 → 全模态 | 以图搜图新增 caption 文本路，与 `image_vec` 路 **RRF 跨量纲融合**（不取最大分——余弦 0.5~0.7 与 RRF 0.016 差 30 倍，取最大会让文本路永远输且静默退化） | `backend/app/services/rag/image.py`、`rag/recall.py` |
| **B** 音频 → 全模态 | 新增 `POST /api/v1/search/audio`（multipart → ASR → 复用 `rag.search`）；客户端 `searchByAudio` + 「语音搜」入口（复用既有 `utils/voice.uts` 录音状态机） | `rag/audio.py`(新)、`api/search.py`、`client/utils/search_api.uts`、`TabSearch.uvue` |
| **C** 文字 → 全模态 | `_route_query` 三态（image/mixed/text）+ 显式 `intent` 覆盖；含图词但有实词走 mixed 双路，纯图指代仍 image，`intent=image` 显式仍只回照片（回归保护） | `rag/intent.py`、`rag/__init__.py` |

**端到端实证（合并后重启后端实测，非仅单测）**：

| 查询模态 | 调用 | 结果 |
|---|---|---|
| 文字 | `POST /search {"q":"拍的语音"}` | `intent=mixed` → 语音 1 + 照片 4 |
| 音频 | `POST /search/audio`（真实 wav） | 200，ASR 转写「Yeah。」→ 语音 1 + 照片 2 |
| 照片 | `POST /search/image`（真实 jpg） | 200 → 照片 8 + 语音 1 + 文字 1 |

**已知限制**：照片→文字/语音靠 caption 语义近似命中，非像素级（写入侧 text/voice 点没有 `image_vec`，不改 Qdrant schema 无解）；图搜多一次 sparse 编码 + 一次 Qdrant 查询，P95 待真机复测；`TabSearch.uvue` 908 行（structure WARN 非阻断）。

### 2. 走查收口与修复

- **8 项根因级修复**：检索相关性地板（dense 0.55 + sparse 救援 0.01 + PG exact 豁免）、`process_content` 应用层 900s 硬超时、pending 按钮 spacer 抬离 tab 拦截带、画像主题数 `Map.forEach`、回响卡恒渲染 + 镜像 ref 等
- **走查清单**：`docs/真机走查清单_105_20261030.md` → **60 通过 / 6 部分验证 / 1 无法判定 / 8 待验**
- **真机剩余项**已整理成可执行清单：`docs/剩余待验项_真机复验清单_20261102.md`（含"为什么模拟器关不掉"的系统级注入限制说明）
- 台账：`docs/客户端交互链路勘错_20261030.md` §4.22–§4.50（逐条证据 + 截图名）

### 3. 顺带修复

- 走查遗留 110 个文件批量收口（分页 offset/has_more 契约、`profile.locked_tags` 全链路、客户端搜索防串/失败态、导出真实路径）
- lint 清理（ruff 自动修 19 项 + 手工修 E501/S104/PLW0603/S603/PLW1510/PLW0108/B007/UP031）；`embedding_server` 默认改绑 `127.0.0.1`
- `.gitignore` 补齐 `_verify/`（575MB 走查截图，原先只忽略了 `_verify_*/`）、`backend/runs/`、`.codebuddy/`

## 二、验证状态（如实）

| 验证 | 结果 |
|---|---|
| 快速门禁 `review_agent.py` | ✅ 通过（每次提交均过 pre-commit hook） |
| **client 编译门** | ✅ 通过（含语音搜 UTS/Vue 改动，39876 ms） |
| 跨模态/检索定向套件 | ✅ **77 passed**（`test_crossmodal_search` + `test_rag` + `test_image_search` + `test_search_filters` + `test_p2_pagination_20261030`） |
| 合并后回归 | ✅ `test_crossmodal_search + test_rag` = 48 passed |
| **全量门禁 `--full`** | ⚠️ **未跑成**：被内存预检拒绝（可用 2.35 GB < 3 GB 阈值）。已实测"关模拟器 3.94GB / 停 embedding 3.29GB"仍不够——本机 15.7GB 被 IDE/浏览器/WSL 占 ~13GB，属**机器容量问题，非代码问题**。合入前建议在空闲机器上补跑一次 |
| 真机 | ⚠️ 未验：本轮全部在模拟器（emulator-5554）+ 106 正式包上完成；剩余 8 项需真机（麦克风/系统 UI 注入限制） |

## 三、合并前检查清单（reviewer 视角）

- [ ] `python scripts/review_agent.py --full` 在空闲机器补跑（本 PR 只过了快速门禁 + 定向套件）
- [ ] `docs/openapi.json` 已按本分支代码重导（含新增 `/api/v1/search/audio`）
- [ ] `scripts/audit_harness_baseline.json` 取主仓版本（分支版本会误报 structure）
- [ ] 关注 `TabSearch.uvue` 行数（908 > 800 阈值，structure 仅 WARN；建议后续把语音搜抽 composable）
- [ ] 真机复验：`docs/剩余待验项_真机复验清单_20261102.md` 中 8 项

## 四、风险与回滚

- 跨模态改动集中在检索域，**未改 Qdrant schema、未重建索引** ⇒ 回滚只需 revert 本 PR，无数据迁移
- 客户端新增「语音搜」入口与 `searchByAudio`，旧入口行为不变
- 若图片双路导致 P95 超门禁，可先关文本路（`rag/image.py` 内单点降级开关），图片路行为与改动前一致
