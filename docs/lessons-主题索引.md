# 教训主题索引（错误与经验按根因族归组）

> **定位**：`docs/lessons.md` 是程序化登记的**时间线台账**（134+ 条，勿手改结构；2026-08-29 起含 harness 工具事故 3 条）；本文件是它的**复盘索引**——同样族的坑只登记过不止一次，说明规则没进流程。每条给"族→根因→规矩→代表台账（日期·commit）"。新增教训后若属已有族，顺手在本表补代表条目。
> **数据**：回源 `docs/lessons.md`（grep `- **错误**`）与 `忆述光华_交付文档/MVP完成度评估_20260827/08` §1.2/§1.4、tracker 19 §4/§5。

## 族1 · 提交门禁卫生（出现 ~25 次，最大单一族）

- **根因**：house-style lint（I001 import 排序、E501 长行、F401/F811/F841、DTZ/S/PLW 系列）每次提交才被抓，反复返工； noqa 码写错等于没写；merge 自动拼接致重复定义。
- **规矩**：提交前先 `ruff check --fix <改动文件>` 再跑快速门禁；merge 后必跑**全量 lint**（快速门禁只查本次文件，查不出拼接重复）；测试占位密钥命名避开 `secret/token/key` 开头（secrets 扫描误报）。
- 代表：08-27 16:39 dd8c6d2（I001 系列）/ 08-27 17:33 07b91f8（merge 双 add 拼块 F811）/ 08-26 16:14 bd754af（noqa 码不匹配）/ 08-25 23:16 fe1b376（S311/F401）。

## 族2 · 真机与 adb 环境（nova 11 / EMUI）

- **根因**：华为生态行为与标准 Android 差异大且**静默**：纯净模式拦 install 零提示；HBuilderX 每次 GUI/launch 互杀 adb server 并丢 reverse（一日 11 次）；console.log 不进 logcat；uni.uploadFile res.data 是 string；writeFile 只收 base64/ArrayBuffer；EmUI 不索引 adb push、同路径 MediaStore 行复用。
- **规矩**：①真机开跑固定前置——关纯净模式 + 统一 system adb v41 + launch 后必补 `adb reverse tcp:8000 tcp:8000` + 端侧 curl healthz 探针；②测试目录名**全局从未用过**（yishu_w3x 前缀）；③注入扳机逐文件 scan_file，不指望 ContentObserver；④App 日志以 HBuilderX 会话 stdout 落盘 evidence/。全量见 `skills/hbuilderx-uniappx-runloop` + `skills/android-media-e2e`。
- 代表：08-28 07:27 125d861×4（reverse 丢失/push 不索引/PS 循环空转/delete 误删文件）/ 08-28 17:54 1964c37（纯净模式空错误）/ 08-24 13:23 2353be5（res.data string 误判网络）/ 08-28 22:54 9e98e09（writeFile 1200002）。

## 族3 · Android 原生能力探测与注册（判死级，两条都是"假通过掩盖真失效"）

- **根因**：D-18 `ClassLoader.getResource('.class')` 探测 WorkManager 恒 false（类全在 dex）→ **正式包后台永不启用**；D-19 UTS 插件 Service 无 manifest/config.json 注册 → FGS 全基座必死。共同教训：**探测与注册都要以"打包产物尸检"为准**（dex/manifest 直查），注释里写"自动回退"≠真回退。
- **规矩**：原生能力验收=重打包后日志 `initBackgroundTasks ok` + `aapt dump manifest` 见 `<service>`；探测用插件自带 marker asset。
- 代表：08-28 18:0x（ck07 定档，tracker 19 D-18/D-19）——批次1 修复项。

## 族4 · 共享工作区 / 并行窗口踩踏（多 Agent harness 特有）

- **根因**：多窗口同仓库：`git add -A` 扫走他人改动；worktree 缺 gitignored 的 .env/models/测试照片；双实例编译抢 logcat/pyc；commit 撞号（同秒时间戳 ID）；G2 --full 混跑 G1 未合代码假失败。
- **规矩**：只 `git add <自己路径>`（禁 -A）；merge 前跑全量 lint；集成门禁必在 merge 后统一跑（在途分支代码不算基线）；实体 ID 用 uuid4 非秒戳；跑门禁前确认无并行重进程（内存 ≥5GB）。
- 代表：08-27 03:49 6b5760b（R6#12 被 -A 扫进）/ 08-26 19:32 bf95ddf（worktree 缺模型/.env）/ 08-27 16:05 232632d（并行撞号 429 flake）/ 08-28 19:36 f5c0c59（环境变量泄漏进 pytest 子进程）。

## 族5 · 契约与 mock 漂移（"mock 宽容 + 增量编译"双掩盖）

- **根因**：白名单收紧未同步客户端构造点（D-01 全量上传 422）；enqueue_unique 迁移丢任务实参、mock 签名 `*args` 宽容（D-02 管线静默断一天）；EXIF 权威只挂 multipart 路径（D-03）；UTS 存量错误被增量 warm cache 掩盖；旧 evidence 不复核被当事实（Wave2"422=旧测试数据"误诊入台账后被真机证伪）。
- **规矩**：契约变更=双端同步点清单；mock 必须锁真实参数个数（禁 *args 宽容）；验收编译一律 `--cleanCache` 全量；引用旧证据先 `git log -S` 回源；RQ job_id 净化（`_safe_job_id_part`）。
- 代表：08-28 00:46 79e8727（D-02 假成功）/ 08-28 01:56（sanitizeKeyId 根因）/ 08-27 22:38 cad1262（warm cache 掩盖）/ 08-24 21:43 365a386（AGG 断言手写 3 处错→先跑参考实现再写断言）。

## 族6 · 默认值与"没测出"的伪装（老人场景安全族）

- **根因**：三层默认值（models/schema/client）把"情绪未测出"联同显示成"平静"（D-16）→ notify 门控永不触发；护栏未配 key 曾默认放行（已拍板改拒发）；`?? 默认值` 客户端再兜一层。
- **规矩**：**测量缺失必须显式返回 null/「未识别」，UI 不渲染**；默认值只允许"安全侧"（拒发/不触发/不伪造）；fail-safe 三查（模型默认值/schema 默认/客户端 ??）作为新字段 review 项。
- 代表：tracker 19 D-16（08-28 15:4x）/ 08-20 13:03 4a4d894（护栏 fail-safe 测试）。

## 族7 · 汇报口径与文档数字债（本次整饬的直接起因）

- **根因**：两维统计（状态×证据级）被合并成单一总数，"数字没变"观感 vs 实质 A 级+8；跨文档数字漂移（02 line119"✅42"笔误、08 附录 ✅41/A27 与终值 ✅46/A32 并存、progress/handoff 头部滞后）；session-handoff 长期滞留 08-25"当前状态"。
- **规矩**：改任何全局数字**五处同步**（AGENTS 快照/08 需授权/progress 速览/handoff 速览/feature_list evidence）；汇报带基线时点+逐条列出移动；**现行值唯一权威=AGENTS.md「当前状态」节**；术语/决策以 `docs/决策台账.md` 为准。
- 代表：08-28 20:53 19669c7（数字质疑复盘）/ 本条目（08-29 整饬）。

## 族8 · 性能与资源（内存/CPU 预算类）

- **根因**：门禁峰值 ~5.5-6GB（三 fp32 模型并载）0.9GB 时 commit 被 SIGKILL；SetFit 冷启动 27s 误判性能灾难；rerank CPU 40s/查询；DB 池 15 打满进程直接崩（未转 503）；C 盘 0 空闲 ENOSPC 连锁。
- **规矩**：commit 前可用内存 ≥4GB；模型 fp16 + smoke 跳过 reranker；池耗尽显式 503、搜索超顶 429（P0-2 已修，复验=F1）；跑重任务前 `Get-PSDrive C` 与 `Get-Process python` 巡检。
- 代表：08-26 22:34 cad1262（压测双炸）/ 08-25 21:38（内存教训族）/ 08-24 12:05（冷启动 27s）。

## 族9 · 外部依赖与供应商（重试/超时/误归因）

- **根因**：无重试 500 张批量 121 张 ConnectionError；dashscope 403 全链路**静默**降级到规则、eval 成绩误归因 LLM；sensevoice 兜底 ModuleNotFoundError 双通道实际单腿；ASR 短超时把热态 3.7s 的 76.9s 音频判死（D-07 族）。
- **规矩**：外部调用必带 with_retry+显式降级标记（degraded 上抛，不许静默）；健康检查暴露单腿；超时随时长伸缩；供应商探测逐服务（qwen-flash 可用≠百炼全服务）。
- 代表：08-25 06:00 24b205f（403 静默降级误归因）/ tracker 19 D-09（单腿）/ D-07。

## 族10 · 客户端 UTS/uni-app x 语言坑（速查表在 skill，此处只立族）

- **规矩**：写 UTS 前先过 `skills/hbuilderx-uniappx-runloop/SKILL.md` 速查表（Unit vs Function0、可选参数不可省、自引用箭头函数、无 DOM/无 chooseImage、生命周期参数 any、easycom 等）。
- 代表：08-26 14:01 4ae3632 / 08-24 16:47 0750c87 / 08-27 21:30 6e2bbd0（view→text 样式）。

## 族11 · Harness 工具自毁（2026-08-29 整饬会话实发，新族）

- **根因**：工具与数据格式**硬耦合且失配走破坏分支**——`lessons.py add()` 用 HEADER 常量 `startswith` 判表头，整饬给表头加一行复盘指针即失配→"旧文件无表头→重建"→静默丢弃 1199 行台账正文（提交后 diff stat -1191 才暴露）；同会话 edit 工具 ReplaceFileW 间歇 EIO(1175)，成功时又把文件整体写成 CRLF，init.sh 从此 bash 不可执行。
- **规矩**：①harness 工具改数据文件一律**不销毁语义**——格式失配只能保守插入+告警，正文永不丢弃（add() 已改三分支，本条即回归用例）；②执行类脚本（.sh）改动后提交前必须 `bash` 实测（.gitattributes `*.sh/*.py eol=lf` 已立）；③大文件批量改写用临时 .py+唯一命中断言（兼避 EIO 与换行污染）；④commit 后必查 diff stat——删除行数远超预期=毁数据信号，立即 `git show HEAD~1:<file>` 回查恢复。
- 代表：lessons 08-29 01:24（CRLF/EIO）· 08-29 01:30（add 毁档→修复）；决策台账 §4.8。

## 统计与治理

- 台账规模：134 条（2026-08-19~08-29）。族1 占比 ~19%——**lint 类本不该占大头**，落实"先 ruff --fix 再门禁"应压到 0；族2+3 合计 ~20% 是 Android 端特性成本，已固化进两个 skill；族7 是纯流程债，本索引+决策台账即为对策。
- 维护：新台账条目出现后**按周归族**（写本文件即可）；某族复发第 3 次→升级为 Working Rules/门禁自动化项，而不是再记一条。
