# 全页面资源 ↔ 端点 ↔ 调用 核实矩阵

> 生成时间：2026-08-31 · 峰宝指定最高优先级任务
> 方法：5 个 subagent 并行穿透核实（时间轴组 / 搜索详情记录组 / AI访谈消息组 / 画像设置管理组 / 横切基础设施组）
> 判定铁律：**「有 PATH 常量」≠「有封装函数」≠「有真实触发点」**，三级分别核实，只认最后一级
> 路径：`C` = `client\`（工作树 `.wt\wrap1-agentA2-ui-restore`），`B` = `backend\`

---

## 零、一句话结论

**50 个后端端点：✅ 真实触发 26、⚠️ 有缺陷 5、❌ 未接线 19。**

真机上「卡片没照片 / 详情页空白 / 搜索只剩 mock / 访谈页打不开」四个现象，对应 **4 个独立根因**，其中 3 个是**客户端压根没调端点**，1 个是**入口在还原中丢失**。没有一个是后端能力缺失——后端全都现成。

---

## 一、四个真机现象的 root cause（钉死）

| 现象 | 根因 | 证据 | 性质 |
|---|---|---|---|
| **卡片上没照片** | 三重叠加：① 后端 `EventOut` **没有 photos 字段**（只有 `cover_content_id` + `photo_count`）② `GET /events/{id}/items` 客户端**只在「拆分卡片」流程触发**，`fetchAll` 从不调 ③ `eventPhotoIds`/`localPhotoPath` 唯一填充点在**本次会话的上传回调**内 → 冷启动必空 | `B/app/schemas/event.py:7-25`；`C/utils/event_ops.uts:294` ← 仅 `C/pages/index/index.uvue:405`；`index.uvue:632,652` | 设计缺口 + 接线缺失 |
| **详情页空白** | `event_ops.uts:125` 拼 `GET /contents?content_id=xxx&limit=1`，但后端 `contents.py:347` **只接受 `limit`/`cursor`，无 content_id 过滤** → 参数被静默忽略 → 返回"最新 1 条任意内容"，库空则 resolve(null) → 全字段空 | `C/utils/event_ops.uts:125` vs `B/app/api/contents.py:347` | **客户端调错了 API**（语义错误） |
| **搜索只剩 mock / 筛选无效** | `search.uvue:84 USE_MOCK_DATA=true` → `onLoad:108-111` **无条件灌 6 条 mock**；真实请求仅在「关键词非空 + 回车」时发出；筛选 tab 点击不发请求（条件不满足） | `C/pages/search/search.uvue:84,108-111,268,254` | **mock 开关遮蔽真数据** |
| **访谈页打不开** | live 代码**零跳转入口**。pages.json:49 已注册，但全项目无任何 `navigateTo('/pages/interview/interview')`，只在废弃 `.bak` 里有。旧版入口在 `D:\GuangH-App\client\pages\profile\profile.uvue:20,100-101` 的 `goInterview()`，**在画布直转还原中被整段丢失** | `C/pages/profile/profile.uvue`（全文无 interview 字样）；entry 仅存于 `profile.uvue.tabbar.bak:100-101` | 入口丢失（非页面缺陷） |

### 🔴 额外发现：另一个 mock 开关
`index.uvue:277/279 USE_MOCK_TIMELINE=true` —— events 返回 0 条时会注入 `ev-m1~m4` **假 id**（云端不存在）→ 永远取不到真实成员。
**不关掉它，灌再多真种子数据也看不到。**

---

## 二、图片通路专项（全线未接线，是当前最大阻塞）

### 后端侧：完全可用
```
GET /api/v1/thumbnails/{content_id} + Bearer → HTTP 200, image/jpeg, ~2KB, JPEG 魔数 ✅ 已实测
真机用户 e4134743：81 photo（71 有 thumbnail_key）、15 events、110 event_items
backend/data/storage/ 真实存在，829 个文件，全部 exist=True
```

### 客户端侧：零引用
| 事实 | 证据 |
|---|---|
| `GET /thumbnails/{cid}` **全客户端零引用** | grep `thumbnail` 只命中 `uploader.uts:82,187,195`（是 upload_mode 枚举，与端点无关） |
| 全项目 `<image :src="http...">` **0 命中** | 所有 `<image>` 都指向 `/static/*` 本地资源 |
| token **只能走 header** | `C/utils/api.uts:57` 拼 `Authorization: Bearer`；GET `:136`、rawRequest `:214`、uploadFile `:264` |
| → `<image :src>` 天生带不上 header | 一旦接线必须 `uni.downloadFile({header})` 落临时文件再指向本地 |

### 后端 schema 缺图片字段（三处，决定改造范围）
| Schema | 现状 | 证据 |
|---|---|---|
| `ContentOut` | **无** `cos_key`/`thumbnail_key`/`url` | `B/app/schemas/content.py:37-48` |
| `EventOut` | **无** `photos`/`photo_ids`/`items` | `B/app/schemas/event.py:7-25` |
| `SearchHit` | **无任何图片/缩略图字段**，搜索页模板也**没有照片位** | `B/app/schemas/search.py:19-30` |

**结论**：客户端唯一取图途径就是 `/thumbnails/{cid}`，而它现在一个字都没接。
**已拍板方案**（峰宝 2026-08-31）：预签名 URL 统一抽象 —— 存储层加 `get_download_url(key, ttl)`，COS 模式返回原生 presigned URL，fs 模式返回 HMAC 短时效票据指向后端流式端点；`ContentOut`/`EventItemOut`/`EventOut` 增 URL 字段；双 TTL（缩略图 24h / 原图 15m）。

---

## 三、端点全局对照表（50 项，一个不漏）

图例：✅ 真实触发 ｜ ⚠️ 有缺陷/有封装无触发 ｜ ❌ 未接线

| # | 端点 | PATH 常量 | 封装函数 | 真实触发点 | 结论 |
|---|---|---|---|---|---|
| 1 | POST /auth/wechat | 有(零引用) | `auth.uts:55` 裸 uni.request | `App.uvue:49` | ✅ ⚠️绕 api.uts，无看门狗/无 Sentry |
| 2 | POST /auth/phone | ❌ | ❌ | ❌ | ❌ |
| 3 | POST /auth/sms/send | ❌ | ❌ | ❌ | ❌ |
| 4 | POST /auth/refresh | 有(零引用) | `auth.uts:133` | `api.uts:106,192,283` 401 触发 | ✅ |
| 5 | POST /auth/logout | 有(零引用) | `auth.uts:193` | **零调用** | ❌ 死代码 |
| 6 | POST /contents/upload | ❌ | ❌（仅注释） | ❌ | ❌ |
| 7 | POST /contents | ✅ | `text_recorder.uts:85`；`voice.uts:238` | `RecordSheet:304,518` | ✅ |
| 8 | GET /contents | ❌(字面量) | `event_ops.uts:123` | `detail.uvue:112` | ⚠️ **后端不支持 content_id → 取错内容** |
| 9 | POST /profile/sensitive | ❌ | ❌ | ❌ | ❌ |
| 10 | DELETE /profile/sensitive | ❌ | ❌ | ❌ | ❌ |
| 11 | GET /profile/sensitive | ❌ | ❌ | ❌ | ❌ manage「画像隐私设置」本应用它 |
| 12 | GET /contents/{cid}/events | ❌(字面量) | `event_ops.uts:78` | `detail.uvue:127`；`index:541` | ✅ |
| 13 | **GET /thumbnails/{cid}** | ❌ | ❌ | ❌ | ❌ **完全未接线（最大阻塞）** |
| 14 | GET /events/timeline | 有(零引用) | `timeline.uts:71`(字面量) | `index:295`；`manage:210` | ⚠️ 被 `USE_MOCK_TIMELINE` 遮蔽 |
| 15 | POST /events/sync | ✅ | `event_sync.uts:78` | `index:649` | ✅ |
| 16 | GET /events/{eid}/items | ❌(字面量) | `event_ops.uts:294` | `index:405`（**仅拆分流程**） | ⚠️ fetchAll 不调 |
| 17 | POST /events/merge | 有(零引用) | `event_ops.uts:259` | `index:389` | ⚠️ **有 bug，永远失效**（见 §四） |
| 18 | POST /events/split | 有(零引用) | `event_ops.uts:318` | `index:439` | ✅ |
| 19 | POST /events/confirm | 有(零引用) | `event_ops.uts:227` | `index:366,556` | ✅ |
| 20 | PUT /events/{eid}/cover | ❌ | ❌ | ❌ 全项目零 PUT | ❌ |
| 21 | POST /search/image | ✅ | `search_api.uts:190` | **零触发** | ⚠️ 有封装无触发 |
| 22 | POST /search | ✅ | `search_api.uts:172` | `search.uvue:273` | ✅（被 mock 遮蔽） |
| 23 | POST /classify | 有(零引用) | `text_recorder.uts:101` | `RecordSheet:314` | ✅ |
| 24 | GET /classify/jobs/{id} | ❌ | `text_recorder.uts:122` | `RecordSheet:322` | ✅ |
| 25 | POST /classify/arbitrate | ❌ | `text_recorder.uts:206` | `RecordSheet:344` | ✅ |
| 26 | GET /classify/arbitrate/jobs/{id} | ❌ | `text_recorder.uts:228` | `RecordSheet:352` | ✅ |
| 27 | POST /corrections | 有(零引用) | `text_recorder.uts:189` | `RecordSheet:347,356,361` | ✅ |
| 28 | POST /asr/transcribe | ✅ | `voice.uts:158` | `RecordSheet:479` | ✅ |
| 29 | POST /guard/check | ❌ | ❌ | ❌ | ❌ guard_router = `B/app/api/asr.py:30` |
| 30 | POST /sync/push | ✅ | `sync_client.uts:416` | `App.uvue:57` 2h 定时/网络恢复 | ✅（无手动触发） |
| 31 | GET /sync/pull | ✅ | `sync_client.uts:434` | `runSyncChain:532` | ✅（**首页不触发**） |
| 32 | POST /sync/reconcile | ✅ | `sync_client.uts:474` | **零调用** | ⚠️ 有封装无触发 |
| 33 | GET /echo/today | ✅ | `play.uts:50` | `index:315` | ✅ |
| 34 | POST /echo/{cid}/dismiss | ❌(字面量) | `play.uts:82` | `index:466` | ✅ |
| 35 | GET /interview/questions | ✅ | `play.uts:113` | `interview.uvue:105` | ⚠️ 兜底文案与后端主题不一致 |
| 36 | POST /interview/answers | ✅ | `play.uts:168` | `interview.uvue:171` | ⚠️ **结果零渲染**（dimensions/confirmation 都丢弃） |
| 37 | GET /interview/profile | ✅ | `play.uts:194` | `manage.uvue:179` | ✅（**访谈页自己不调**） |
| 38 | GET /messages | ✅ | `play.uts:243` | `messages.uvue:140,143` | ✅（未读总数拉了不显示） |
| 39 | POST /messages/{id}/read | ❌(字面量) | `play.uts:318` | `messages.uvue:158` | ✅ |
| 40 | POST /messages/read-all | ❌ | `play.uts:327` | **零调用** | ⚠️ 死代码 |
| 41 | POST /wechat/find | ❌ | ❌ | ❌ | ❌ 无绑定 UI |
| 42-43 | GET/POST /wechat/callback | ❌ | ❌ | ❌ | ❌ 服务端回调 |
| 44 | POST /wechat/delete | ❌ | ❌ | ❌ | ❌ |
| 45 | POST /upload/init | ✅ | `upload_protocol.uts:110` | `RecordSheet:279`；`voice.uts:285` | ⚠️ 裸 uni.request，无 401 重放 |
| 46 | POST /upload/chunk | ✅ | `upload_protocol.uts:121` | 同上 | ✅ |
| 47 | PUT /upload/chunk | (共用) | ❌ 恒 POST | ❌ | ⚠️ 后端双 method，端上只走 POST |
| 48 | POST /upload/complete | ✅ | `upload_protocol.uts:133` | 同上 | ⚠️ 裸 uni.request |
| 49 | GET /upload/status | ✅ | `upload_pipeline.uts:83` | 仅断点续传分支 | ✅（仅续传路径） |
| 50 | GET /upload/sts | ❌ | ❌ | ❌ | ❌ |

---

## 四、顺手挖出的 6 个 bug（此前未知）

| # | Bug | 证据 | 影响 |
|---|---|---|---|
| 1 | **`getPrevL1Id` 数组越界写法** | `index.uvue:377` 写 `days.value[i].events.level`，但 `events` 是 `Array<TimelineEvent>`（`timeline.uts:109`）→ 恒 undefined → `===1` 恒 false → 恒返回 `''` | **合并功能永远提示「没有可合并的上一张」** |
| 2 | **manage 天数统计必崩** | `manage.uvue:220` 对 `ev.startTime` 调 `.substring(0,10)`，而 `TimelineEvent.startTime` 声明为 `number`（`timeline.uts:15`，由 `parseIsoMs` 赋值 `:88`）→ number 无 substring | 统计恒 0 或抛错 |
| 3 | **搜索跳详情可能传错 id** | `search.uvue:289 openResult` 用 `item.id`，但 `SearchHitItem` 字段名为 `contentId`（`search_api.uts:22`） | 详情页收到 undefined（存疑，待实测） |
| 4 | **AI 页输入框是 `<text>` 不是 `<input>`** | `ai.uvue:10` | **键盘都拉不起来**，`onSend:68-70` 只弹 toast |
| 5 | **settings 开关是死状态** | `settings.uvue:197` 样式静态写死 `#B05A3A`，`:class` 未绑定；`themeText`/`cacheText` computed 未绑模板（`:29`/`:34` 硬编码） | 点了没反应；且零持久化（无 storage、无后端端点） |
| 6 | **RecordSheet 保存后不回写** | 只 `defineEmits(['close'])`（`:143`），保存成功无任何 emit（`:286` 只关内层表单） | 录入完成上层 timeline **不刷新** |

---

## 五、逐页面资源需求（渲染依赖 ← 数据来源）

### 5.1 时间轴 `pages/index/index.uvue`
| 区块 | 依赖字段 | 现状 |
|---|---|---|
| Hero 氛围层 | 无（`/static/hero-lake.jpg` + 「八月 · 28 条新记忆」） | **全硬编码** |
| 回响卡片 | echoDate/text/place | ✅ `GET /echo/today` |
| 卡片照片条 | `ev.photoIds` + `photoPathOf(cid)` | ❌ 冷启动必空（§一） |
| 卡片封面 | `ev.coverPath` | ❌ 同上 |
| 卡片 title/level/photoCount | ✅ 来自 timeline | ✅ |
| **语音卡 `ev.voice` / 文字卡 `ev.quote`** | **`TimelineEvent` 根本没这两个字段** | ❌ **死代码，永不渲染** |
| 未被消费的 EventOut 字段 | tags / sensitivity / generated_by / lifecycle / emotion / place | `timeline.uts:83-97` 只取 13 字段 |

**首屏只发 2 个请求**：`fetchTimeline` + `fetchTodayEcho`。无 `/events/{id}/items`、无 `/thumbnails`。

### 5.2 搜索 `pages/search/search.uvue`
| 区块 | 依赖 | 现状 |
|---|---|---|
| 筛选 tab | 硬编码 4 标签 | ✅ 本地 `matchesFilter()` 兜底（刚修） |
| 语音卡 | eventTitle + `genWave()` 装饰波形 | 播放钮是**静态 SVG，无音频**，`playVoice` 仅 toast |
| 文字卡 | text/meta | photo 与 text **共用文字卡** |
| **照片卡** | **模板根本没有照片位** | ❌ 后端 SearchHit 也无图片字段 |

### 5.3 详情 `pages/detail/detail.uvue`
| 区块 | 依赖 | 现状 |
|---|---|---|
| 全幅媒体 | — | **硬编码 `/static/images/img4_406.png`** |
| 标题/元信息/正文 | text/takenAt/place | ⚠️ 调错了 API（§一） |
| AI 观察 `aiNote` | — | **恒空 → 整卡隐藏** |
| 相关记忆缩略图 | — | **纯 CSS 渐变，无图** |
| 操作栏 | — | 全部 toast 占位 |

### 5.4 我的 `pages/profile/profile.uvue`
**零 import、零生命周期钩子**（script 段 59-116 行无任何 onLoad/onShow/onMounted）
| 区块 | 现状 |
|---|---|
| 头像字「峰」/ 昵称「峰宝」/ 签名「已记录 128 段回忆 · 连续记录 6 天」 | **硬编码** |
| 统计 128 / 42 / 23 | **硬编码**（后端无统计端点，可用 `GET /contents` 按 type 计数 或 timeline 聚合） |
| 消息中心未读角标 | **模板无角标元素** |
| `goPrivacy` / `goAbout` | toast 占位，无页面 |
| **访谈入口** | **丢失**（§一） |

### 5.5 画像管理 `pages/portrait/manage.uvue`
| 区块 | 依赖 | 现状 |
|---|---|---|
| 画像等级徽章「L2 · 主题级」 | — | **硬编码** |
| 记忆天数/照片数/主题数 | API 派生 | ⚠️ 天数统计崩（bug #2） |
| 性格标签 name | `dimensions` 键名 | ✅ 真 |
| 性格标签 confidence | `60 + (i*5)%30` | ❌ **编造** |
| 画像隐私设置 / 编辑性格标签 | `GET/POST/DELETE /profile/sensitive` | ❌ toast 占位，端点零引用 |

### 5.6 AI `pages/ai/ai.uvue`
**完全静态壳，零请求**。标题/气泡/记忆卡全部硬编码（`:6-42`），不走 `echo/today` 也不走 `search`；输入框是 `<text>`（bug #4）；`onSend` 只 toast。

### 5.7 访谈 `pages/interview/interview.uvue`
| 步骤 | 状态 |
|---|---|
| 进度点 | 硬编码 3 |
| 题目 | ⚠️ 调了，但 API 失败时降级到**主题不一致的兜底**（后端问「最重要的人」，兜底问「最近的小事」） |
| 提交答案 | ⚠️ 调了，但 `dimensions`/`confirmation` **零渲染**，用户看不到任何反馈 |
| 完成卡文案 | **硬编码**「谢谢你的分享，已经开始了解你了」 |
| 画像 | ❌ 本页不调 `GET /interview/profile` |
| 语音答案 | ❌ 写死占位串 `'（语音回答 N 秒）'`，未走 ASR → **画像喂脏数据** |

### 5.8 消息 `pages/messages/messages.uvue`
列表 ✅ / 单条已读 ✅ / 全部已读 ❌（有函数无调用）/ 页级未读总数 ⚠️（拉了不显示，模板零绑定）/ 回响卡 **硬编码**（「去年今天：你在天台看了流星雨」）

### 5.9 设置 `pages/settings/settings.uvue`
三个开关**零持久化**（无 `uni.setStorage`、无后端端点），两个 computed 未绑模板（bug #5）；**无登出行**（`POST /auth/logout` 已封装但零调用）。

### 5.10 空态 `pages/empty/empty.uvue`
纯静态（硬编码文案 + 静态 SVG）。CTA ✅ 符合铁律：`onTakeFirst` 原地监听相册（但回调仅 console.log，无 API）、`goVoice` 拉起 RecordSheet 浮层，**均不跳页**。

### 5.11 调试 `pages/debug/agg-check/agg-check.uvue`
**零网络请求**，纯本地 ST-DBSCAN vs fixtures 自检，**不能用于验证后端联调**。入口缺失（`AGG_CHECK_ON_DEVICE=false` + 无 navigateTo）。

---

## 六、死代码与死页面（可清理清单）

### 6.1 死页面 `pages/record/record.uvue` —— **确证不可达，删除零风险**
- 引用点仅 3 处：① `pages.json:25` 注册（注册 ≠ 可达）② `components/yishu-tabbar/yishu-tabbar.uvue:40` 路由表 ③ `utils/text_recorder.uts:52,63` 注释
- **引用者本身是否可达**：`yishu-tabbar` 被引用**只出现在 `.bak` 文件里**（`profile.uvue.tabbar.bak:42`、`search.uvue.tabbar.bak:75`、`index.uvue.template.bak:180` 等 7 处），**无任何活跃 `.uvue` 使用** → 组件孤立 → record 页不可达
- **能力已 100% 被 RecordSheet 重复实现**：`uploadBatch`/`createTextContent`/`submitClassify`/`voice` 逐项对照（`RecordSheet:279/304/314/389/479`）
- 删除风险：仅 `utils/text_recorder.uts` 注释 + `uni_modules/yishu-photo-watch` 引用随页消失，**无他页依赖**

### 6.2 其他死代码
| 类型 | 清单 |
|---|---|
| 零调用函数 | `auth.uts:193 logout`、`play.uts:327 markAllRead`、`event_ops.uts:373 pendingOpCount`、`uploader.uts:306 pendingPhotoCount`、`voice.uts:103/107/147`、`sync_client.uts:103/122/474/564`、`api.uts:351 parseErrorString` |
| 零引用 PATH 常量 | `contract.uts:18,19,20`（auth 三端点）、`:25` TIMELINE、`:26` CONFIRM、`:27` MERGE、`:28` SPLIT、`:30` CLASSIFY、`:31` CORRECTIONS |
| 孤立组件 | `components/yishu-tabbar/`（已被 `components/TabBar` 取代） |
| 废弃备份文件 | `pages/` 下 **9 个 `.uvue.*.bak`** 残留 |

---

## 七、接线优先级建议（按依赖关系排序）

| 优先级 | 事项 | 理由 |
|---|---|---|
| **P0-1** | 图片通路：存储层 `get_download_url` + Schema 增 URL 字段 + 客户端 `downloadFile` 落本地 | 阻塞所有有图页面；架构级，其他项都依赖它 |
| **P0-2** | 关掉两个 mock 开关（`index:279` / `search:84`） | 不关掉，灌再多真数据也看不到 |
| **P0-3** | `fetchAll` 内批量调 `GET /events/{id}/items` 填 `photoIds` | 卡片照片条的第二个必要条件 |
| **P0-4** | 修 `detail.uvue` 详情数据源（加 `GET /contents/{id}` 或改用 items 端点） | 详情页全白 |
| **P1-1** | 灌真实种子数据（Screenshots 抽 40 图 + 12 文 + 4 音 → 10 事件，跨 30 天） | 让所有页面有真内容可显示 |
| **P1-2** | 补访谈入口（profile 页加 `goInterview()`）+ 修兜底文案 + 结果上屏 + 语音走 ASR | 三类问题一次修完 |
| **P1-3** | RecordSheet 增 `saved` emit → 上层 reload | 录入不刷新 |
| **P1-4** | 修 6 个 bug（§四） | 合并/统计/搜索跳转/AI 输入/settings 开关 |
| **P2-1** | profile 统计接真实数据；manage 接 `/profile/sensitive` | 硬编码数字与 toast 占位 |
| **P2-2** | 清理死页面 record + yishu-tabbar + 9 个 .bak | 交付前一次性清 |

---

## 八、横切风险（架构级，改造时必须遵守）

| 风险 | 证据 | 约束 |
|---|---|---|
| **token 只能在 header** | `api.uts:57` buildHeader；GET `:136`、rawRequest `:214`、uploadFile `:264` | `<image :src>` 天生带不上 → **必须 `uni.downloadFile({header})` 落临时文件**；若走预签名 URL 方案则无此限制 |
| **envelope 不校验 code** | `dataObj()/dataArr()` 只取 `data`，HTTP 200 即成功（`api.uts:319-324`） | 业务错误码会漏判；`/thumbnails` 返回裸 JPEG 非信封，用 api.uts 拉会被裸值守卫 `resolve(null)`（`api.uts:85-89`） |
| **上传 init/complete 绕过 api.uts** | `upload_protocol.uts:72` 裸 `uni.request`；`upload_pipeline.uts:90` 同上 | 无 401 重放、无 5xx Sentry、无看门狗 |
| **auth 三请求绕过 api.uts** | `auth.uts:58/158/195` 裸 uni.request | 同上 |
| **登录 code 硬编码 `'dev-client'`** | `auth.uts:62` | 用 device_id 当 code 会**静默创建全新空用户**（unionid=`mock-unionid-yishu-and`，timeline 0 条）→ 极易误判「数据丢了」 |
| **token 明文存 storage** | `auth.uts:36-46`（TD-P3 降级，非 EncryptedSharedPreferences） | 生产需换 |
| **真机须 adb reverse** | `config.uts:23 REAL_DEVICE_HOST='localhost'` | `adb reverse tcp:8010 tcp:8010`（已建） |
| **契约漂移** | `play.uts:84,320,329` 用路径字面量而非 `contract.uts` 常量 | 改端点时易漏 |

---

## 九、存疑项（待实测确认，未编造）

1. `index.uvue:276` 注释称「后端返回 0 条事件」—— 未验证真机空数据根因（是库里无事件，还是 `get_timeline` 过滤条件过严）。需查 `backend/app/services/events.py::get_timeline`。
2. `search.uvue:289` 用 `item.id` vs `SearchHitItem.contentId` 字段名不一致 —— 疑似 bug #3，需实测。
3. `config.uts:36` 注释称「dev 构建下 profile『关于』区展示调试入口」，但 profile.uvue 无该入口 —— 注释先于实现，还是被还原覆盖？需产品确认。
4. `uploader.uts` 中 `runUploadPipeline` 内是否另有 init/complete 分支未逐行读完。


---

## 迁移与增补记录

### 2026-09-02 · 矩阵迁移 + W0-1 新增端点

- 本矩阵自 wrap1 工作树迁入 `missing-pages`（feature/missing-pages-impl，25 帧实现线唯一施工树）；wrap1 目录留存只读。
- **新增端点 1 条**：`GET /api/v1/media/audio/{content_id}`（W0-1，2026-09-02 落地 backend/app/api/media.py）
  - 鉴权：Bearer `get_current_user`；归属校验（非本人/非语音/无 cos_key 统一 404 MEDIA_003，防 IDOR）
  - 供 W0-2 就地播放接线（uni.downloadFile 带 header；token 只能走 header 的硬约束）
  - 三态判定：⏳ **待真机联调实证**（W2-4 openapi 全量实证 + W0-2 联调时 curl 确认；当前仅导入级+路由序验证）
  - 路由序注意：必须位于 `/{key:path}` 兜底之前（已验证注册序正确）

---

## 六、W2-4 对拍复核（2026-09-05 15:3x，基线=本文件 08-31 五十项 · 实测=openapi 64 方法）

> 方法：openapi.json 全量导出 × 客户端 grep 三级核实（PATH/封装/触发）× 真机行为佐证。
> 结论先行：**基线 50 项中，❌19 → 剩 9（全部有据：远期/服务端回调/无 UI），✅26 → 40，⚠️5 → 4。**
> W1 返工 + W2 端点批次把「三大真机现象」的根因全部清掉了。

### 6.1 接线状态翻转（基线 → 现在）

| 基线# | 项 | 基线 | 现在 | 证据 |
|---|---|---|---|---|
| 8 | GET /contents 支持 content_id | ⚠️ 取错内容 | ✅ | contents.py:367 过滤落位；回声卡→detail 真机正常（_verify_0905/33） |
| 13 | GET /thumbnails 接线 | ❌ 最大阻塞 | ✅ | **Valet Key 票直连**取代端点直调：api.uts:80 + event_ops:155 + search/detail 票 URL；真机缩略图渲染实证（/24） |
| 14 | timeline 被 mock 遮蔽 | ⚠️ | ✅ | USE_MOCK_TIMELINE=false（index.uvue:416） |
| 22 | POST /search 被 mock 遮蔽 | ⚠️ | ✅ | USE_MOCK_DATA=false（search.uvue:119） |
| 9-11 | /profile/sensitive ×3 | ❌ | ✅ | manage.uvue:333/353 接线（W10 画布卡片），GET/DELETE 实证、POST 添加行在位 |
| 新增 | SearchHit.thumbnail_url | ❌ 无字段 | ✅ | 3d5eded（向量召回签票）+ R5 c818530（contents 列表签票） |
| 新增 | W2-1 删除/回收站 4 端点 | —（新） | ✅ | DELETE /contents/{id}（detail 软删）+ GET /trash + restore + 清空，真机全链实证 |
| 新增 | W2-2 收藏 4 端点 | —（新） | ✅ | POST/DELETE/GET favorite + GET /favorites；收藏页诚实回落本地（真事件 551abfc9 实证） |

### 6.2 §四 六 bug 修复复核

| bug | 基线 | 现在 |
|---|---|---|
| 1 getPrevL1Id 越界 | 恒失效 | ✅ 已修（index.uvue:603 向前逐日找 L1） |
| 2 manage substring 崩 | 恒 0/崩 | ✅ 已修（无 substring(0,10) 命中） |
| 3 search openResult 传错 id | 存疑 | ✅ 无碍（真机点搜索结果跳转正常；VoiceResult.id 映射正确） |
| 4 ai 页无 input | 键盘拉不起 | ✅ 已修（ai.uvue 含 1 个 <input>） |
| 5 settings 死开关 | 点了没反应 | ⚠️ **未复核**（下批顺带） |
| 6 RecordSheet 不回写 | 不刷新 | ✅ 已修（emit('saved') ×3 + index @saved）；⚠️ 真机观察：保存后时间轴新条目仍不即时出现（onRecordSaved 链路待查，记观察项） |

### 6.3 仍 ❌/⚠️（9 项，全部有据）

| 端点 | 状态 | 依据 |
|---|---|---|
| POST /auth/phone、/auth/sms/send | ❌ | 无 UI，内测 mock-login，合规后启用 |
| POST /auth/logout | ❌ | 死代码（auth.uts:193 封装零调用；设置页无登出入口） |
| POST /contents/upload | ❌ | 分片链 /upload/init 替代 |
| PUT /events/{eid}/cover | ❌ | 封面切换无 UI 入口 |
| POST /search/image | ⚠️ | 封装在（search_api.uts:250）零触发——以图搜图无入口 |
| POST /guard/check | ❌ | 零引用（护栏走服务端 transcribe 内链） |
| POST /sync/reconcile | ⚠️ | reconcileNow 封装在零调用（建议：App 启动低频触发） |
| POST /messages/read-all | ⚠️ | 封装在零调用（消息中心无「全部已读」按钮） |
| /wechat/callback|find|delete ×4、GET /upload/sts | ❌ | 服务端回调/企微侧 UI/直传凭证（M3 微信波+远期） |

### 6.4 行动项（下批候选）
1. settings 死开关复核（bug5）
2. RecordSheet 保存→时间轴即时刷新断点排查（onRecordSaved）
3. reconcile 启动触发 + read-all 按钮：两个 10 分钟级小接线，攒着顺手做
4. search/image 入口：产品决策（拍照搜记忆入口放哪）
