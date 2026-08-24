
## 🚀 客户端第三波（2026-08-24 晚 · T-NA/T-TX/T-AU/T-SR/T-PL 多入口+玩法层）

**状态**：✅ 全部真机验证通过（nova 11，提交 e6398cc）｜review_agent 全绿｜pytest 238 passed

### 交付（15 任务 10 项真机验证 PASS）
1. **T-NA-1 四宫格导航**（components/yishu-tabbar）：时间轴/记录/搜索/我的，reLaunch 切换，选中态锈红
2. **T-TX-1/2 文字入口**（pages/record + utils/text_recorder.ts）：POST /contents(text) → 分类异步 job 轮询 → 标签展示 → 点标签三层裁决纠错（correction_log 回写）
3. **T-AU-1/2/3 语音入口**（utils/voice.ts）：uni.getRecorderManager 录 wav → /asr/transcribe 转写 → 可编辑+情绪标签 → voice 入库
4. **T-SR-1/2/3/4 搜索**（pages/search + utils/search_api.ts）：混合结果卡片 + trace 溯源（召回：语义+关键词 · 语义分） + uni.chooseMedia 以图搜图 + degraded 降级黄条
5. **T-PL-1 回响卡片**（首页）：去年今日 GET /echo/today + dismiss 划掉（角贴+泛黄）
6. **T-PL-2 冷启动访谈**（pages/interview）：三层披露 → 三问 → 复述确认 → 可跳过；画像 cold_start_done 生效
7. **T-PL-3 消息中心**（pages/messages）：未读/全部过滤 + 单条已读 + 全部标为已读

### 本波教训（docs/lessons.md +5）
1. uni-app x App 端无 uni.chooseImage，用 uni.chooseMedia
2. UTS setTimeout 自引用箭头函数不可用，轮询用模块级 function+done 回调
3. /interview/questions data 是裸数组；/messages status 仅 unread/read/archived；搜索 trace 结构是 {matched,dense_score,...}
4. uiautomator dump 对 uni-app x 自绘 UI 不可靠，真机定位用截图像素分析（#B05A3A）+ image 坐标交叉验证
5. am start 启动会"未检测到应用资源"，必须 HBuilderX CLI launch；大进程并发（worker+review_agent+pytest）内存不足需先释放

---

## 🚀 客户端第二波 · 第二批（2026-08-24 晚 · S-AG-3/S-SY-4 客户端闭环）

**状态**：客户端代码完成 ✅ 编译通过 ✅；真机 E2E 验证被环境阻塞（HBuilderX 弹窗 → 已解决；设备 USB offline → 待峰宝拔插）

### 客户端（S-AG-3/4 + S-SY-4）
1. **PhotoItem 增加 GPS**（interface.uts + app-android/index.uts）：MediaStore LATITUDE/LONGITUDE 读取（无 GPS=null，端侧按时间窗归组）
2. **uploader.ts 返回 content_id**（UploadedPhoto[]）：上传响应解析 data.id，端侧聚合/事件上云依赖
3. **S-AG-3 端侧聚合运行器**（client/utils/agg_runner.uts）：上传成功照片（本地元数据+content_id）→ UTS ST-DBSCAN（同 AGG-016 同参）→ L1 日卡片事件（client_event_id 幂等键）
4. **S-SY-4 客户端事件上云**（client/utils/event_sync.ts）：POST /events/sync + 指数退避（2s/4s/8s/8s/8s）+ 4xx 停批 + client_event_id 幂等
5. **index 页接线**：监听攒批 → 上传 → 端侧聚合 → 事件上云 → 刷新时间轴（B3-6 端侧 L0/L1 真值闭环）
6. **编译验证**：HBuilderX 编译成功（多轮修复：TS 环境 UTSJSONObject 无 parse/getArray 泛型、无 any、签名类型）

### 环境坑（教训已登记 docs/lessons.md +2）
1. **HBuilderX 模态弹窗静默阻塞 CLI launch**（更新提示 + AI 介绍弹窗）→ computer-use 关弹窗后恢复
2. **Windows 防火墙拦入站 8000**（设备 ping 不通本机）→ 改用 adb reverse USB 隧道（config.ts REAL_DEVICE_HOST=localhost）
3. **设备 USB offline**（待峰宝拔插恢复后执行最终真机验证）

### ✅ 真机 E2E 闭环验证（2026-08-24 21:27 · nova 11，全链路通过）
1. **注入**：10+ 批测试照片 scan_file 注入（新目录 w2tN）→ 观察者触发
2. **上传**：multipart 200 + content_id 解析（修复：uploadFile res.data 是 string，JS 引擎用 split 提取）
3. **端侧聚合**：[yishu] 端侧聚合: 2 张 → 0 簇 → 1 个 L1 事件（UTS ST-DBSCAN 真机运行）
4. **事件上云**：[yishu] 事件上云: accepted=1 dup=0 rejected=0（POST /events/sync，后端需重启加载新路由）
5. **DB 落库**：events 表 generated_by=device + client_event_id 唯一（ev-1787578023265-857538）
6. **时间轴渲染**：首页截图显示端侧提交的 L1 卡片（2026-08-24 · 1条 / 1张照片）
7. **S-SY-5 前台触发**：App 重启后自动恢复监听（onLoad 检查权限自动 startWatch），无 CTA 也触发

### 环境坑（本批再踩，教训 +3）
1. **uni.uploadFile res.data 是 string**（与 uni.request 的 UTSJSONObject 不同）→ JS 引擎用字符串操作解析；诊断日志定位（解析失败被误判为上传失败数小时）
2. **华为增强纯净模式拦截 HBuilderX 安装**（pure_enhanced_mode_state=1）→ 应用市场反复弹窗抢前台 + App 卡 D 状态 → settings put secure pure_enhanced_mode_state 0
3. **端侧 EXIF 兜底对 PIL 写入的 EXIF 不生效**（ExifInterface getAttribute 返回 null 无异常）→ 真实相机照片 DATE_TAKEN 可靠；测试注入场景时间窗偏移（后端 EXIF 权威已兜底 contents）
4. 设备时钟错位（显示 8/25 09:22，实际 8/24 21:21）——第一波已知，上线前校准

### 遗留（后续波次）
- S-XV XView（SQLCipher 随自定义基座波次，标准基座无三方依赖）；S-SY-4 离线 op_log 队列；S-EM-1 模拟器；S-ST-1 STS；S-MO-1 手动操作 UI
- 端侧 EXIF 兼容性（PIL 写入格式）待查
# Session Progress Log — 忆述光华

## 🚀 客户端第二波 · 首批交付（2026-08-24 晚 · W5 起）

**状态**：S-SY-1 / S-SY-2 / S-AG-1 / S-AG-2 ✅（含真机验证）；剩余 S-XV/S-SY-4/5/6/S-ST/S-MO 按依赖序推进

### 后端（S-SY-1/2 全绿 · pytest 234 passed）
1. **S-SY-1 `POST /api/v1/events/sync`**（B3-6 端侧 L0/L1 真值落云）：client_event_id 幂等（同用户部分唯一索引兜底并发）+ 照片归属校验（越权整条 rejected）+ 落库 L1（generated_by=device）+ 变更日志写 offline_queue（其他端增量拉取可见 → M4 端间一致）+ 受影响照片云侧补 L2/L3 候选
2. **S-SY-2 aggregate_user 重构**：默认 mode="l2l3"（云侧只跑 L2/L3，caption/CI 打标保留 _process_photo；L1 由端侧提交）；mode="full" 保留第一波全量管线作基线迁移；修复 _write_upper_candidates 幂等检查按 level>=2（照片挂 L1 不再拦截 L2/L3 候选）
3. **迁移**：events.client_event_id 列 + uq_events_user_client_event 部分唯一索引（alembic a1b2c3d4e5f6 已应用）
4. **测试**：test_event_sync.py 7 项（幂等/越权/空列表/落库+变更日志/L2 触发/重发不重复/API+时间轴/并发唯一索引兜底）+ test_pipeline 聚合契约更新（云侧不再自动建 L1；full 模式基线回归）+ test_agg_reference.py 4 项（参考端语义锁）

### 客户端（S-AG-1/2 ✅ 真机 10/10）
5. **S-AG-1 UTS ST-DBSCAN 算法层**（client/utils/agg/）：agg_config.uts（参数单一来源 ↔ pipeline.py AGG_CONFIG）/ st_dbscan.uts（Photo/DayCard/haversineM/stDbscan/l1DailyAggregate，时区偏移参数化）/ pipeline.uts（RawPhoto/preprocess：连拍折叠+GPS 漂移置空）——纯计算层，无平台依赖
6. **S-AG-2 AGG-016 一致性**：scripts/gen_agg_fixtures.py（Python 同参双跑 → 生成 fixtures.uts 10 用例 57 照片）+ pages/debug/agg-check 自检页（逐用例比对簇成员集合+日卡片）
7. **真机验证（nova 11）**：HBuilderX 编译成功 → 实机运行自检页 **10/10 PASS**（连拍折叠/两天两簇/散片稀疏/无 GPS 归组/深夜归属/漂移修正/稀疏多天/单张/UTC 日界/30 张规模）——截图证据 .cowork-temp/agg7.png
8. **恢复**：临时导航开关已回退（config.ts AGG_CHECK_ON_DEVICE=false），设备已恢复首页

### 教训登记
docs/lessons.md +1：AGG-016 测试断言不得手写期望（先跑参考实现再写断言）

### 下一批（按依赖序）
- S-AG-3 Kotlin 桥接（相册读取/EXIF/定位 → 算法层输入）→ S-AG-4 增量触发
- S-XV XView（SQLCipher 5 表 + 迁移 + 轻量 DAO）
- S-SY-4/5/6 客户端同步协议（op_log 队列/退避/三路触发/LWW）
- S-ST-1 STS 直传分片；S-MO-1 手动操作 UI

## 📱 真机 E2E 全链路验收（2026-08-24 下午 · nova 11 FOA-AL00）

**链路已全通**：相册监听（ContentObserver）→ 游标去重 → 4s 静默窗口攒批 → multipart 上传 → 后端 EXIF → 云侧聚合 → F8 时间轴渲染 ✅

### 验收证据（B-UT/B-UP/B-F8/B-VA）
1. **编译**：HBuilderX 5.15 CLI 全量编译通过（纯 UTS 插件 + utils + 页面，~30s/轮，多轮迭代修复）
2. **运行**：标准调试基座安装→同步→启动成功（onLaunch 3s，页面渲染 234ms）
3. **空状态**（B-F8-3）：截屏验证——标题/副标题/插画/文案/CTA 按钮，配色符合视觉规范 ✅
4. **真实上传**：50 张测试照片 → 观察者分批发现（found 4/1/...）→ 上传 200 OK → 自动刷新时间轴 ✅
5. **EXIF 真值**：后端 PIL 提取 DateTimeOriginal 覆盖客户端时间 → contents taken_at = 08-22(40)/08-23(10) ✅（曾实测 scan_file 污染 DATE_TAKEN → 后端 EXIF 修复）
6. **时间轴渲染**（B-F8-1）：截屏验证 L1 日卡片（“2026-08-22 · 1条 / 40 张照片”等）双卡片结构正确、视觉规范落地 ✅
7. **隐私防护**：首扫游标初始化到 max(id)（不导入存量相册）——事故教训已修复并验证（只收新照片）

### 真机暴露问题（已修 + 教训登记）
1. 首扫全量上传 9319 张存量相册（隐私红线）→ 游标初始化修复
2. scan_file 不提取 EXIF → 后端 EXIF 权威解析（新增 pytest：EXIF 覆盖客户端时间）
3. 并发双 ensureLogin 撞 devices 唯一约束（后端 500）→ _issue_tokens IntegrityError 兜底 + 客户端单飞
4. fake 存储 512MB 容量上限触发（防护生效，误伤后续上传）→ 重启进程即恢复；真实联调用 minio/cos
5. 标准基座权限以基座 manifest 为准；SDK31 用 READ_EXTERNAL_STORAGE（pm grant 验证）

### 遗留说明
- 手机时钟/时区错乱（设备显示 08-25 05:00）→ 日标签偏移一天；设备时间正常后自愈（非代码缺陷）
- L2 语义归并待真实数据（P2-07 已知）；L2 候选结构在 50 张全链路验证中已存在
- 30s 门禁：服务端链路 4.2s 上传 + 5.6s 管线（单进程验证）；设备侧受 WiFi/扫描节奏影响，观察者分批触发（4s 窗口）
- 设备上测试照片目录已清理；后端 dev-client 测试用户数据已清

## 🚀 客户端第一波（2026-08-24 · W3）

**状态**：后端交付完成 ✅ / 客户端代码全部就绪（待 HBuilderX 编译 + 真机验收）

### 后端（B-BE-1/2/3 ✅ 全绿）
- 新增 `POST /api/v1/contents/upload`（multipart file + meta JSON）→ storage 存原件（cos_key）→ contents 落库（photo/processing）→ enqueue_high(process_content)；复用 409 去重 / moderate 护栏 / source 白名单 / GPS 边界
- 校验：图片类型白名单（jpg/jpeg/png/webp/heic/heif）、空文件 422、超 20MB 413、坏 meta 422
- 测试：`backend/tests/test_content_upload.py` 9 项新增全过（成功/去重/未授权/类型/空文件/超限/坏 meta/护栏/HEIC）
- curl 冒烟：上传 200 → 落库；重复哈希 409 CONTENT_002 ✅
- 全链路单进程验证（`.cowork-temp/verify_wave1_server_chain.py`）：50 张生成照片 → upload 4.2s → 管线 5.6s → timeline L1=3 日卡片（20/15/15 与真值一致）✅；L2 候选存在（cloud-proto，语义归并待真实数据，P2-07 已知）

### 客户端（B-CL/B-UT/B-UP/B-F8 代码就绪，编译/真机待峰宝）
- `client/` uni-app x 工程：manifest/pages/main.uts/App.uvue + pages/index/index.uvue（F8 时间轴）
- utils：config.ts（baseURL 开关）/ auth.ts（mock 登录 + EncryptedSharedPreferences + 401 refresh）/ api.ts（统一请求+错误映射+全局 toast）/ uploader.ts（并发≤3+重试2+进度）/ timeline.ts（ISO 解析+日期分组）
- uni_modules/yishu-photo-watch：UTS 插件（Hybrid Mode）——PhotoObserver.kt（ContentObserver + 游标去重 + 4s 静默窗口攒批）、SecurePrefs.kt（EncryptedSharedPreferences）、index.uts 桥接
- 视觉规范 v1 落地：相纸白 #F6F1E7 / 墨褐 #3A2E25 / 锈红 #B05A3A、衬线标题、撕边卡片+底部投影、空状态（空白相纸 SVG）
- `scripts/generate_test_photos.py`：50 张带 EXIF 拍摄时间测试照片（3 天 4 片段，L1/L2 真值已知），--push 注入 MediaScanner

### Harness
- ruff.toml 排除 client/；review_agent 三处扫描（syntax/secrets/todos）加 `_skip_path`（client 非 Python 工具链，B2 决策）
- feature_list.json：F1/F8 置 in-progress + 证据更新

### ⛔ 阻塞/待办（需峰宝/设备）
1. ~~nova 11 adb unauthorized~~ ✅ 已授权（2026-08-24 下午真机 E2E 全链路验收完成）
2. HBuilderX 编译验证：✅ 已通过（纯 UTS 插件 + 标准基座，多轮编译修复）
3. L2 语义归并：待 50 张真实照片 + 事件真值（团队）
4. DASHSCOPE/TENCENT/COS/AMAP/SENTRY key 在 Infisical（本地 .env 为空，mock 模式开发）；真实联调按 skills/infisical-secrets/SKILL.md 注入
5. EncryptedSharedPreferences（SecurePrefs）随自定义基座波次恢复（当前 uni storage 临时）

### 📌 环境经验（2026-08-24）
- 本机 LobsterAI python 不加载 cwd/PYTHONPATH：`python -m app.workers.worker` 报 No module named 'app' → 需 `python -c "import sys; sys.path.insert(0, r'D:\GuangH-App\backend'); from app.workers.worker import main; main()" high low` 启动
- fake 存储为进程内单例：uvicorn 与独立 worker 不共享 → 本地 E2E 用单进程 TestClient 验证；dev 真联调用 minio/cos（docker hub 当前不可达，minio 镜像拉不动）

## 📌 当前状态（2026-08-20）

**质量门禁**：pytest 215 passed（14 deselected，覆盖率 75.20%，2026-08-21 00:42 全量证据）｜ruff 全绿｜review_agent 全绿（2026-08-24 修复 research 段模块路径后恢复）｜教训登记 hook 生效

**测试/基础设施**：PG/Redis/Qdrant 本地运行中（Docker 重启后需手动 `docker start yishu-redis yishu-qdrant`）；BGE-M3 / SetFit / reranker-v2-m3 本地模型就绪；新增 webrtcvad-wheels（长录音分段）

### 🔧 三方审查修复（2026-08-20 · 51 项 checklist）

**P0 安全+数据正确性（7/7 ✅）**：上传 IDOR 归属校验｜敏感词护栏 URL 早退绕过｜wechat/delete 鉴权｜搜索时间过滤 epoch 秒 payload（Qdrant 实测修复）｜照片 caption 先下载再调用｜mock 转写生产拒绝入库｜护栏未配 key 默认拒发（用户拍板）

**P1 技术债+偏离（17/17 ✅）**：同步 naive/aware 时间 500｜分片大小校验｜mock 凭证生产 501｜并发 IntegrityError 竞态｜CORS 白名单｜回响敏感双查（用户拍板：标记+LLM）｜纠错三道噪音闸门（≥3 次一致/3 天回改）｜错误契约统一 ApiError｜常量去重(sync_common/标签词表)｜模型路径 CWD 独立｜N+1 修复(时间轴/merge/回响)｜队列优先级(voice/photo 高优)｜敏感词打码映射修复｜server_version 用户级游标｜updated_at onupdate+interview 单事务｜长录音 VAD 分段(webrtcvad)｜测试质量(恒真断言/SetFit 评估口径)

**P2 架构重构（7/7 ✅，2026-08-20）**：推理移 worker（classify/arbitrate 异步+job 轮询，search 并发信号量）｜worker 拆分（process_content 下沉 services/pipeline.py）｜research 包边界（event_aggregation 移入 backend，删 sys.path hack）｜单例收敛（security 函数内读/correction Qdrant 统一/fake 容量上限+reset）｜Alembic 落地（ORM 唯一权威，check 零漂移，FinetuneJob 纳入 ORM，遗留空表收敛）｜前缀/游标统一（asr 域拆分+guard 独立，删 cursor 死字段）｜事件 L2/L3 候选落库 + 以图搜图 image_vec 生产接线

> ⚠️ P2-01 待办（用户确认时要求）：同步改设计文档（MVP方案_v3/B2/B5a）与 OpenAPI 契约（classify/arbitrate 改异步）
> ⚠️ P2-07 L2/L3 为候选级 draft 落库（generated_by=cloud-proto），LLM 语义归并待真实数据到位

**P3 顺手清理（部分完成 2026-08-20）**：未用依赖已删（openai/slowapi/datasketch/passlib/bcrypt/python-dotenv）｜死代码已删（token_is_valid/_PRESET_SENSITIVE_WORDS/_rule_check）｜conftest.py 建立（27 测试文件 sys.path 样板清除）｜storage fake 容量上限+reset。暂缓：状态枚举化（DB 迁移风险）、22:00 调度固化（待部署决策）、rag 测试 collection 隔离（待 CI 决策）

**设计文档同步（2026-08-20，P2-01 契约变更）**：OpenAPI契约.md 新增「分类与裁决」异步化节（变更前→原因→变更后）+ ASR 域拆分说明 + 39 路径；MVP方案_v3.md 新增「实现变更记录」节（4 项技术变更）；开发决策清单 #9 补落地注记；docs/openapi.json 已重新导出（39 路径）

> 详见 [review-report.md](file:///D:/GuangH-App/review-report.md) 与 [refactor-plan.md](file:///D:/GuangH-App/refactor-plan.md)

### ✅ 已完成功能（对照 MVP F1-F9）

| 功能 | 状态 | 说明 |
|---|---|---|
| F2 文字碎片输入 | ✅ 后端 | SetFit 5 类分类（classify_batch）+ 内容入库管线 |
| F3 语音输入 | ✅ 后端 | ASR 双通道真实转写（WER 9.69%）+ 情绪映射 + 入库管线 |
| F4 分类纠错 | ✅ 后端 | 三层裁决 + 共性纠错微调流水线（≥50 触发） |
| F5 描述性搜索 | ✅ 后端 | BGE-M3+Qdrant RRF + NER + mixed 融合 + 以图搜图 + reranker-v2-m3；RAG 门禁 hit_rate@3=0.8182 / route_acc=1.0 / temporal_acc=1.0；文字搜图 hit_rate@3=1.0 |
| F7 冷启动访谈 | ✅ 后端 | interview API + 画像扩展队列 |
| P2 回响机制 | ✅ 后端 | 去年今日 + 敏感排除 + 每天≤1 |
| B4 数据同步 | ✅ 后端 | LWW/软删/幂等/COS 分片续传/对账 |
| 推送消息中心 | ✅ 后端 | messages + 复盘 22:00 + mock 通道 |
| 微信"找" | ✅ 沙箱 | 消息解析→RAG→回复（真实企微凭证待办） |
| 事件聚合 | ✅ 后端 | L1 日卡片落库 + 用户手动 merge/split/confirm |
| 护栏 | ✅ 后端 | 开源词库 4 类 + 网址黑名单 1.45w + 号码打码 + LLM 检测 + contents 入库接线 |

### 📋 待办（后端可继续做 / 等团队数据）

**可继续做**：
1. 语音 COS 下载接存储层收尾（worker 已用 get_object，待真实 COS 联调）
2. photo 生产 caption 真实调用已验证（Qwen3-VL 5s/张）；图片入库管线接真实 caption
3. 事件 L2/L3 落库（当前只 L1）
4. 搜索降级契约（degraded/errors 前端规范）
5. 备份体系补全（WAL 归档/PITR/Qdrant 快照异地）
6. 性能压测（100 并发 / P95 曲线）
7. OpenAPI 契约持续同步（当前 37 路径）

**等团队（数据/决策/凭证）**：
1. 50 条真实搜索查询（RAG 上线评测集，门禁硬依赖）
2. 100-200 条真实中文碎片（5 类标注，SetFit 校准）
3. 20-50 段真实录音+转写（WER 校准）
4. 50-100 张真实照片 + 3-5 天事件聚合真值
5. 10-20 条纠错样本
6. 产品部拍板：关怀文案库 / 模板骨架池 / 隐私措辞
7. 合规三申请（企微认证/ICP/软著）+ 缺的密钥（微信/企微/Sentry/高德）
8. UTS POC 需 Android 原生人力（全局 Gate）

---

## 最近会话日志

## 2026-08-24 · 远程仓库核对 + review_agent 修复 + 文档台账清理

**已交付**：
1. **远程仓库核对**（origin=zqhmy1234/YSGH-APP）：远程仅 main 分支，HEAD=3869111「MVP 后端全量交付（单提交快照）」，与本地 develop 同 commit，无他人新增修改；本地 7 个 feature/m1-* 分支与 tag v0.1.0-sprint1 均未推送
2. **review_agent research 段修复**：test_agent.py run_research_validation 仍用旧路径 `research.event_aggregation`（P2-02 迁入 backend 后残留）→ 改 `app.services.event_aggregation.run_validation`；`--only research` 实测全过（497 张基准，EXIT=0）
3. **文档台账清理**：pytest 数字统一为 215 passed（210/203/145 均为过期值）；去除 progress.md 重复标题；lessons.md 重复标题清理；feature_list/session-handoff 同步

**发现并记录**：.cowork-temp/test-report.json（8-21 00:42）显示 review_agent 上次实际 passed=false（research 段 blocking），与文档"全绿"表述不符——本次已修复根因。

## 2026-08-20 02:5x · 教训强制 Hook + 生产兜底待办开发

**已交付**：
1. **教训登记程序化强制**（用户要求非提示词级）：scripts/lessons.py（add/recent/check_lessons，epoch 时间戳防时区漂移）+ review_agent 集成——检查失败 → 写 last-failure.json；下次通过前未登记教训 → 阻断 commit（pre-commit 无法绕过）；docs/lessons.md 已登记 6 条实战教训；闭环验证通过
2. **外部 API 统一重试封装**（AGENTS.md #13 教训落地）：services/external/retry.py with_retry（3 次指数退避 + 线程池超时 + 可重试异常判定 5xx/10053/网络类）；应用到 dashscope（_chat_text/image_caption）+ tencent_ci（打标/审核）；test_retry 10 项
3. **事件用户手动操作接线**（B3-5/AGG-013）：merge（成员转移+源软删+confirmed）/ split（拆出建新事件）/ confirm（转正改标题）+ EventEditLog 记录 + API 三端点（原 501）；用户操作优先——操作后 confirmed 不再被算法改动；test_event_ops 7 项
4. **环境修复**：Docker 引擎未起（redis/qdrant 容器重建）+ PG 伪死（残留进程+postmaster.pid）→ 全量测试大面积失败排查（教训已登记）

**验证**：pytest 190 全过 + ruff 全绿 + review_agent 全绿。
**待办**：①语音 COS 下载接存储层（worker 已留接口）②photo 生产 caption 真实调用验证 ③corpus-A 61 张 caption 补齐 ④上线评测集（50 条真实查询，等团队）⑤RAG 门禁调优（hit_rate 0.6667→0.70）

## 2026-08-20 02:00 · AI 管线接线 + 模型清理 + 生产兜底审计

**已交付（用户拍板：P0 文本/语音 → P1 图片 → P2 事件聚合一起接，用户无感知失败）**：
1. **process_content 全类型管线**（worker.py）：text→SetFit 分类；voice→ASR 转写+分类；photo→caption+CI 打标；全部→事件聚合。每步独立 try/except 静默失败（status 仍 done，明细入 extra.error）——替代原占位实现
2. **事件聚合落库**（services/events.py + Event/EventItem ORM）：调 research pipeline（L0+L1）写 L1 日卡片 + event_items；同日去重合并（增量触发不拆事件，E2E 实测 3 条→1 事件）；events API timeline 返回真实数据（原 501）；merge/split/confirm 仍 501
3. **E2E 实测**：text 9.6s（分类 5s+索引 4s）、photo 0.8s；3 条同天内容 → 1 个 L1 事件 items=3
4. **关键 bug 修复**：classifier.py 漏设 HF_HUB_OFFLINE=1 → worker 里先于 embedding 加载时联网卡死（huggingface.co 10s×5 重试×多文件 = 2min+）；SetFit 单条冷启动 27s（warmup），批量 5 条 27.6s 摊薄——已加 classify_batch
5. **C 盘清理**：HF 缓存 8.6GB→2.2GB（删 bge-m3 旧 snapshot 2.2GB + incomplete 1.65GB + grounding-dino 1.8GB + bert-base 841MB + 3 空壳）——用户确认删除
6. **模型资产清单**：backend/models/README.md（在用/可删/下载源/加载位置）；AGENTS.md 环境教训 18-22 条（HF 缓存机制/进程管理/下载纪律）
7. **生产兜底审计**：docs/生产兜底审计与交付差距盘点_20260820.md（已有兜底 5 项 + 缺口：管线已补、外部 API 统一重试/超时未做、降级契约未定）

**验证**：pytest 173 全过 + ruff 全绿 + review_agent 全绿。
**待办**：①外部 API 统一重试封装（dashscope/CI/OCR）②events merge/split/confirm 用户手动操作 ③语音 COS 下载接存储层 ④photo 生产 caption 真实调用（现 mock）⑤corpus-A 61 张 caption 补齐

