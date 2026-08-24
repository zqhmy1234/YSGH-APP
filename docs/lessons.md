# 教训台账（Harness 强制登记 · 2026-08-20 起）

> 规则（程序化强制，见 scripts/lessons.py + review_agent.py check_lessons）：
> 开发阶段每次排查错误并修复后，必须登记一条教训——review_agent 检查失败后
> 未登记新教训会阻断 commit。格式固定，勿手改结构。
>
> 新增：`python scripts/lessons.py add --error "..." --root-cause "..." [--fix "..." --file "..."]`

---

### 2026-08-24 18:54 · commit a2993d2 · ts=1787594057
- **错误**：review_agent/api_smoke 峰值内存 ~4-6GB：可用内存 0.9GB 时 git commit 被 SIGKILL（pre-commit hook 跑 review_agent）
- **根因**：smoke 单进程同时加载 SetFit（fp32 2.2GB）+ BGE-M3 fp16（1.7GB）+ reranker（fp32 ~1GB）≈5.5-6GB；本机 TRAE/WorkBuddy/WSL 常驻吃内存；HBuilderX 编译残留 30+ node 进程累积；被 kill 的 commit 留下孤儿 smoke 进程继续占 3GB
- **修复**：①SetFit/reranker 改 fp16（峰值降 ~2GB，测试全过且更快）②smoke 跳过 reranker（RERANKER_MODEL=__disabled__，rerank 由 -m rag 覆盖）③test_agent 加内存探测 + OOM 友好提示（returncode<0 识别）④编译后清理 HBuilderX 残留进程⑤commit 前确认可用内存 ≥4GB
- **相关文件**：backend/app/services/classifier.py, backend/app/services/rerank.py, scripts/test_agent.py
- **教训**：（无）

---

### 2026-08-24 18:18 · commit 3c20c80 · ts=1787591895
- **错误**：review_agent lint 失败：services/upload.py f-string 无占位符（F541）
- **根因**：register_photo_content 的 ValueError 消息写成 f'...' 但无 {} 占位符；lint 阻断 + lessons 门禁要求先登记教训
- **修复**：去掉 f 前缀；lessons 门禁：失败后必须先登记教训再重跑
- **相关文件**：-
- **教训**：（无）

---

### 2026-08-24 17:43 · commit 0ef0ace · ts=1787589788
- **错误**：PowerShell 改 client/config.ts 导致 UTF-8 中文乱码 + 插入错位（编译 Expression expected）
- **根因**：Windows PowerShell 5.1 Get-Content/Set-Content 默认非 UTF-8 无 BOM 读写，-replace 插块位置错误；中文被读成 ANSI 后写回变乱码
- **修复**：见代码
- **相关文件**：-
- **教训**：（无）

---

### 2026-08-24 16:47 · commit 0750c87 · ts=1787586479
- **错误**：review_agent tests 段失败：test_correction 加载 SetFit 模型报 OSError 页面文件太小 (os error 1455)
- **根因**：本机内存/页面文件不足：worker 进程 + review_agent research 段（4.5GB）+ pytest 同时跑模型推理，物理内存耗尽
- **修复**：跑门禁前先停 worker（Get-CimInstance 查 workers.worker 进程 Stop-Process）；pytest 单独跑释放内存后全绿 238 passed
- **相关文件**：scripts/review_agent.py
- **教训**：（无）

---

### 2026-08-24 16:26 · commit 0750c87 · ts=1787585169
- **错误**：消息中心'全部'tab 显示空态（后端实际有 2 条已读消息）
- **根因**：客户端 'all' 拼成 status=all 传后端，后端 status 仅接受 unread/read/archived → 422 空数组
- **修复**：fetchMessages 对 'all'/'' 不传 status 参数（视同全部）；仅 unread/read 才拼 status
- **相关文件**：client/utils/play.ts
- **教训**：（无）

---

### 2026-08-24 16:12 · commit 0750c87 · ts=1787584335
- **错误**：访谈三问不渲染（问题区空白），onLoad 后 questions 数组为空
- **根因**：dataObj(res) 用 getJSON('data') 取对象；后端 /interview/questions 的 data 是裸数组，getJSON 返回 null → 走 resolve([]) 分支；qArr 回退分支永不执行
- **修复**：数组 data 直接用 res.getArray('data') 优先解析，{questions:[...]} 形态回退；不用 dataObj 前置拦截
- **相关文件**：client/utils/play.ts
- **教训**：（无）

---

### 2026-08-24 16:06 · commit 0750c87 · ts=1787584005
- **错误**：uni-app x App 端 uni.chooseImage 不存在（编译过但运行无回调，选择菜单弹出后消失）
- **根因**：uni-app x 的 App 平台只有 uni.chooseMedia（mediaType/sourceType），chooseImage 是 uni-app Vue 版 API 未移植
- **修复**：改用 uni.chooseMedia({count:1, mediaType:['image'], sourceType:['album']})；tempFiles[0].tempFilePath 取路径
- **相关文件**：client/pages/search/search.uvue
- **教训**：（无）

---

### 2026-08-24 14:27 · commit 2353be5 · ts=1787578072
- **错误**：端侧 L1 事件时间窗=扫描时间（21:26）而非照片 EXIF 时间（08-23），端侧 EXIF 兜底失效
- **根因**：android.media.ExifInterface 读不到 PIL 写入的 EXIF（getAttribute 返回 null 无异常）；scan_file 注入场景 DATE_TAKEN=扫描时间
- **修复**：记录为已知限制：真实相机照片 DATE_TAKEN 系统提取可靠；后端 EXIF 权威兜底 contents.taken_at；测试注入场景端侧时间窗偏移可接受
- **相关文件**：client/uni_modules/yishu-photo-watch/utssdk/app-android/index.uts
- **教训**：端侧 EXIF 兜底对 PIL 写入的 EXIF 不生效（scan_file 测试场景时间窗偏移）；真实链路 DATE_TAKEN 可靠

---

### 2026-08-24 14:27 · commit 2353be5 · ts=1787578072
- **错误**：CLI launch 反复卡死 + 华为应用市场反复弹出抢前台，App 安装无法完成（进程 D 状态）
- **根因**：华为增强纯净模式（pure_enhanced_mode_state=1）拦截第三方安装并引导应用市场；HBuilderX 模态弹窗也阻塞 CLI
- **修复**：settings put secure pure_enhanced_mode_state 0 关闭增强纯净模式后安装放行；CLI 静默先查 GUI 弹窗
- **相关文件**：skills/hbuilderx-uniappx-runloop
- **教训**：华为设备真机调试：增强纯净模式会拦截 HBuilderX 安装并反复弹应用市场，先关再 launch

---

### 2026-08-24 14:27 · commit 2353be5 · ts=1787578071
- **错误**：真机上传一直失败（0.2s 内 connect refused 特征），误判为网络/防火墙，实际是 uni.uploadFile 的 res.data 在 App 端是 string，as UTSJSONObject 后调 getJSON 抛异常 → 解析失败被当成上传失败
- **根因**：uploadFile 响应 data 为字符串（与 uni.request 的 UTSJSONObject 不同）；JS 引擎（.ts）无 UTSJSONObject.parse；getJSON/getString 是 UTS 方法字符串上没有
- **修复**：JS 引擎解析 uploadFile 响应用字符串操作（split 提取 id）；uni.request 响应才用 getJSON/getArray；给 fail/解析失败加诊断日志（真机实测定位）
- **相关文件**：client/utils/uploader.ts
- **教训**：uni.uploadFile 的 res.data 是 string（JS 引擎无 UTSJSONObject.parse），解析用字符串操作；诊断日志是排障第一武器

---

### 2026-08-24 13:23 · commit 2353be5 · ts=1787574217
- **错误**：真机上传 0.2s 内全部失败（connect refused 特征），第一波同样代码却成功
- **根因**：本机 Windows 防火墙拦入站 8000（设备 ping 不通本机但本机 ping 通设备）；设备 WiFi 网段虽同段但 AP/防火墙隔离
- **修复**：改用 adb reverse tcp:8000 tcp:8000 USB 隧道（config.ts REAL_DEVICE_HOST='localhost'），绕开 WiFi/防火墙；隧道验证：adb shell curl http://127.0.0.1:8000/healthz
- **相关文件**：client/utils/config.ts
- **教训**：真机联调优先 adb reverse USB 隧道（绕防火墙/WiFi 段）；设备 ping 不通本机即入站被拦

---

### 2026-08-24 13:23 · commit 2353be5 · ts=1787574216
- **错误**：HBuilderX CLI launch 静默卡死（无任何输出），设备始终运行旧代码——真机联调 30 分钟排查
- **根因**：HBuilderX 重启后弹出两个模态对话框（版本更新提示 + uni-app x AI 介绍），模态阻塞了 CLI 的 IPC 通道；GUI 无项目视图但项目树已注册
- **修复**：真机联调前检查 HBuilderX GUI：无模态弹窗 + 项目树可见；用 computer-use 截屏确认并关弹窗（跳过本版/我知道了）；重启 HBuilderX 后需重新 cli project open
- **相关文件**：skills/hbuilderx-uniappx-runloop
- **教训**：HBuilderX 模态弹窗会静默阻塞 CLI launch（无输出=被弹窗卡住），先看 GUI 状态再怪代码

---

### 2026-08-24 12:18 · commit 7cac32f · ts=1787570334
- **错误**：review_agent 门禁 lint 失败：F401 未用 import / E501 长行 / F841 未用变量 / I001 import 排序（api/events.py、schemas/event.py、st_dbscan.py、test_event_sync.py、test_pipeline.py、gen_agg_fixtures.py 共 8 处）
- **根因**：改动跨 6 文件后未先跑 ruff 自查即提交门禁；长行/未用变量在改动中自然产生
- **修复**：提交前先跑 python scripts/review_agent.py 自查；ruff 能自动修的用 --fix；函数内 import 按字母序单块无空行
- **相关文件**：backend/app/api/events.py 等
- **教训**：跨文件改动后先本地跑 ruff/review_agent 再提交，别等 pre-commit 兜底

---

### 2026-08-24 12:05 · commit 7cac32f · ts=1787569503
- **错误**：AGG-016 参考端测试：手写期望断言 3 处错误（深夜归属 23:40 应归前一天而非当天；UTC 23:40 仍触发深夜规则；连拍折叠后仅 2 张不足 min_pts 不成簇）
- **根因**：期望值语义想当然，未先跑参考实现确认行为；夹具本身（Python 实算）始终正确
- **修复**：测试断言改为只锁关键语义，且先以参考实现输出为准再写断言；夹具期望永远由 gen_agg_fixtures.py 实算，不手写
- **相关文件**：backend/tests/test_agg_reference.py
- **教训**：AGG-016 双跑测试：期望值由参考端实算生成，测试断言不得手写期望（先跑后写）

---

### 2026-08-24 10:34 · commit 56c605b · ts=1787564055
- **错误**：门禁 flaky 两处：test_reflow 备份目录 FileExistsError（同微秒两次备份）；api_smoke text-journey 搜索未命中新内容
- **根因**：① _backup_model 微秒时间戳在同一次测试内两次调用时碰撞（概率性）；② 冒烟搜索 q=买咖啡豆 靠语义召回，Qdrant 累积历史数据后新内容被挤出 top-k
- **修复**：① 备份目录存在时自增后缀（-1/-2...，保持可排序）；② 冒烟查询词含唯一 token（sparse 精确命中）+ limit=20 放大召回窗口
- **相关文件**：backend/scripts/reflow_global.py
- **教训**：门禁用例必须与累积数据/时间戳无关：唯一 token 精确匹配 + 防碰撞命名，避免 flaky 阻断提交

---

### 2026-08-24 10:17 · commit 625cb10 · ts=1787563077
- **错误**：review_agent 门禁 tests 段失败：test_queue::test_queue_failure_goes_to_dead 断言 Job.is_failed 为 False
- **根因**：本地调试时启动的后台 RQ worker（python -c ... worker）仍在消费 high/low 队列，抢跑了测试自己入队的 job，破坏 test_queue 的隔离预期
- **修复**：跑 review_agent/全量 pytest 前先确保无外部 RQ worker 运行（查 Win32_Process CommandLine 含 worker 的 python 进程并 kill）
- **相关文件**：scripts/test_agent.py
- **教训**：pytest 前必须清理后台 RQ worker，否则队列类测试（test_queue）会被外部消费者干扰

---

### 2026-08-24 10:11 · commit 625cb10 · ts=1787562667
- **错误**：HBuilderX CLI 运行到真机：标准基座下 App 权限与自建 manifest 不一致
- **根因**：标准调试基座使用基座自身 manifest（含 READ_MEDIA_IMAGES/READ_EXTERNAL_STORAGE），项目 manifest.json 权限不生效；pm grant 需按设备 SDK 选权限名（SDK31 无 READ_MEDIA_IMAGES）
- **修复**：插件按 Build.VERSION.SDK_INT 选择 READ_MEDIA_IMAGES(33+)/READ_EXTERNAL_STORAGE(32-)；pm grant 授权验证
- **相关文件**：client/uni_modules/yishu-photo-watch/utssdk/app-android/index.uts
- **教训**：标准基座调试时以基座权限为准，运行时权限按 SDK 版本选择权限名

---

### 2026-08-24 10:11 · commit 625cb10 · ts=1787562667
- **错误**：真机测试照片 taken_at 全是扫描时间而非 EXIF 拍摄时间
- **根因**：MediaProvider content call scan_file 不提取 EXIF，DATE_TAKEN 写为扫描时间；客户端拿不到相机时间
- **修复**：后端 upload_photo 用 PIL 从文件字节提取 EXIF DateTimeOriginal 并优先于客户端 taken_at（+08 显式解释）；客户端 UTS ExifInterface 读取为兜底
- **相关文件**：backend/app/api/contents.py
- **教训**：照片拍摄时间以服务端 EXIF 解析为准（相机真值），客户端时间不可信

---

### 2026-08-24 10:11 · commit 625cb10 · ts=1787562667
- **错误**：真机首波 E2E：App 首扫把设备全部 9319 张真实照片当新照片全量上传（已强制止损+清库）
- **根因**：游标 last_seen 初始为 0，首扫查询 id>0 命中整个相册；fake 存储 512MB 容量防护随后拦截（防护生效）
- **修复**：emitIncremental 首次启动时游标初始化为当前最大照片 id（只监听后续新增，不导入存量相册）
- **相关文件**：client/uni_modules/yishu-photo-watch/utssdk/app-android/index.uts
- **教训**：相册监听游标必须初始化到当前 max(id)，禁止首扫导入存量相册（隐私红线）

---

### 2026-08-24 08:20 · commit f8ef2bd · ts=1787556035
- **错误**：review_agent 首次门禁 tests 段失败，重跑即全绿（224 passed）
- **根因**：门禁 pytest 运行期间并发执行了 ruff --fix 改写测试文件 import 段，pytest 读到文件中间态；非代码缺陷
- **修复**：门禁运行期间不并发改动被测试导入的文件；失败先重跑确认是否瞬时
- **相关文件**：scripts/review_agent.py
- **教训**：review_agent 运行中禁止并发编辑/格式化被测文件

---

### 2026-08-24 08:14 · commit f8ef2bd · ts=1787555660
- **错误**：本地 E2E 管线图片下载失败（object not found）
- **根因**：fake 存储为进程内单例：uvicorn 与独立 RQ worker 进程各自持有空/非共享内存，跨进程读不到原件
- **修复**：本地全链路验证用单进程 TestClient + 直调 process_content；真实 dev/prod 用 minio/cos 共享存储
- **相关文件**：backend/app/services/external/storage.py
- **教训**：fake 后端仅限单进程测试；跨进程 E2E 需 minio/cos

---

### 2026-08-24 08:14 · commit f8ef2bd · ts=1787555660
- **错误**：python -m app.workers.worker 报 No module named 'app'（本机 LobsterAI runtime python 不加载 cwd/PYTHONPATH）
- **根因**：自定义 python 发行版 sys.path 不含 cwd 与 PYTHONPATH（-c/-m 均如此）；uvicorn 因自身插入 cwd 而幸免
- **修复**：python -c "import sys; sys.path.insert(0, r'D:\GuangH-App\backend'); from app.workers.worker import main; main()" high low
- **相关文件**：backend/app/workers/worker.py
- **教训**：本机启动 RQ worker 必须显式注入 backend 路径，不能依赖 cwd/PYTHONPATH

---

### 2026-08-24 14:38 · commit 3869111 · ts=1787553539
- **错误**：review_agent 用 managed python(3.13.12) 跑时 lint/tests 显示 [skip]（ruff/pytest 未安装），用系统/LobsterAI python 才有依赖；全量 pytest 18 failed 为 redis ConnectionError
- **根因**：项目依赖（fastapi/sqlalchemy/redis 等）装在 LobsterAI runtime site-packages，不在 managed python 环境；yishu-redis 容器依赖 Docker Desktop 引擎，引擎未启动时连接失败
- **修复**：review_agent/test_agent 统一用 LobsterAI runtime python（C:/Users/ghf/AppData/Roaming/LobsterAI/runtimes/python-win/python.exe）执行；跑测试前先 docker ps 确认引擎+容器，引擎未起先启动 Docker Desktop
- **相关文件**：scripts/review_agent.py
- **教训**：（无）

---

### 2026-08-24 11:29 · commit 3869111 · ts=1787542161
- **错误**：review_agent research 段 ModuleNotFoundError: No module named 'research.event_aggregation'（原型验证失败，test-report.json passed=false 但文档仍写'全绿'）
- **根因**：P2-02 重构将 event_aggregation 从 research/ 迁入 backend/app/services/ 后，scripts/test_agent.py run_research_validation 仍用旧 import 路径 research.event_aggregation.run_validation；review_agent 非绿状态未同步到 progress/session-handoff
- **修复**：run_research_validation 改 sys.path.insert(0, ROOT/backend) + from app.services.event_aggregation.run_validation import main；--only research 实测全过（497 张基准）
- **相关文件**：scripts/test_agent.py
- **教训**：（无）

---

### 2026-08-20 17:31 · commit d98d3c8 · ts=1787243472
- **错误**：git commit 被 pre-commit 阻断（lint: migrations 目录 E501/W291）
- **根因**：Alembic 自动生成的迁移文件行超长/尾随空格，ruff 未对 migrations/ 豁免；且 review_agent 未在首次 commit 前完整执行
- **修复**：ruff.toml per-file-ignores 增加 backend/migrations/** = E501/W291/I001/UP；commit 前先跑完整 review_agent 并登记教训
- **相关文件**：backend/migrations/env.py
- **教训**：Alembic 生成文件需在 ruff 配置中豁免；pre-commit 阻断时检查 last-failure.json

---

### 2026-08-20 15:49 · commit 6efbd6b · ts=1787237373
- **错误**：隐私脱敏把代码里的实际路径替换成占位符，导致 run_validation 场景15 真实数据加载为 0 张（500 张校验失败）
- **根因**：脱敏时误改代码逻辑：load_real_photos.py 的 SCREENSHOT_DIR 是实际运行路径（非注释），替换为 <LOCAL_SCREENSHOTS_DIR> 后目录不存在
- **修复**：SCREENSHOT_DIR 改为仅环境变量读取（无 env 时场景15 跳过、返回空列表），本机设 SCREENSHOT_DIR 后全量校验；注释/docstring 才可脱敏，代码逻辑路径需保留可运行
- **相关文件**：research/event_aggregation/load_real_photos.py + run_validation.py
- **教训**：脱敏前区分'注释里的路径'与'代码用的路径'；被替换的路径必须保留功能（环境变量注入），不能只换占位符

---

### 2026-08-20 15:33 · commit 6efbd6b · ts=1787236429
- **错误**：OpenClaw browser 工具不识别 browser.executablePath 配置（报 No supported browser found）
- **根因**：LobsterAI 桌面端定制 browser 插件不走 openclaw 全局配置加载；SIGUSR1 软重启不重载 browser 配置；Tabbit 不在标准 Chrome 发现路径
- **修复**：①browser.profiles.<name>.executablePath 也设置（profile 级优先）②Playwright MCP 用 CHROME_EXECUTABLE_PATH 环境变量指向 Tabbit——已生效（实测进程 playwright_chromiumdev_profile 用 Tabbit.exe）
- **相关文件**：openclaw.json（browser 配置）
- **教训**：LobsterAI 环境浏览器切换用 Playwright MCP 的 CHROME_EXECUTABLE_PATH 最可靠；内建 browser 工具配置可能被桌面端定制覆盖

---

### 2026-08-20 14:38 · commit b5bdc33 · ts=1787233100
- **错误**：接入网址黑名单后测试发现开源色情词表缺'裸聊/招嫖'（之前测试用回退词表通过，加载真实词表后反而漏）
- **根因**：开源词库侧重不同术语体系，色情类无裸聊/招嫖；回退词表与真实词表是互斥关系（存在真实文件就不加载内置词）
- **修复**：加载逻辑改为真实词库 + 内置补充词合并（result[cat] |= fallback），保证高频词不漏
- **相关文件**：backend/app/services/external/sensitive_words.py
- **教训**：开源词表不保证覆盖常用词，内置高频词必须合并加载而非二选一

---

### 2026-08-20 13:03 · commit 4a4d894 · ts=1787227395
- **错误**：Docker 重启后容器 Exited 不自动拉起（--restart unless-stopped 失效）
- **根因**：Docker Desktop 进程退出后，已创建容器保持 Exited 状态，重启策略未生效（restart 策略只在 Docker daemon 运行时触发）
- **修复**：手动 docker start yishu-redis yishu-qdrant；测试前先查 docker ps + 端口
- **相关文件**：AGENTS.md
- **教训**：测试大面积失败先查 docker ps（容器可能 Exited），restart 策略不保证 Docker 重启后拉起

---

### 2026-08-20 13:03 · commit 4a4d894 · ts=1787227394
- **错误**：号码正则在中英文混合文本中匹配失败（\\b 词边界不生效）
- **根因**：Python re 的 \\b 只认 ASCII 边界，中文与数字之间无边界 → 手机号/身份证/银行卡全匹配不到
- **修复**：改用前后向断言 (?<![\d])...(?![\\d])（中文环境 \\b 失效）
- **相关文件**：backend/app/services/external/sensitive_words.py
- **教训**：中文环境正则慎用 \\b，用 (?<!...) / (?!...) 断言替代

---

### 2026-08-19 22:43 · commit 23c0836 · ts=1787175790
- **错误**：护栏 fail-safe 测试失败：真实环境下 moderate('测试内容') 返回 pass=True
- **根因**：原测试用 monkeypatch 假 key 期望调用失败触发 fail-safe，但改 QWEN_GUARD→qwen-flash 后 dashscope SDK 从环境变量读真实 key（绕过 settings），假 key 不再必然失败
- **修复**：测试显式 mock _chat_text 抛异常模拟不可用（不依赖真实 key/SDK 行为）
- **相关文件**：backend/tests/test_asr.py + test_external.py
- **教训**：测试假设'假 key 必然失败'不可靠（SDK 可能从 env 读 key）；fail-safe 测试必须显式 mock 失败路径

---

### 2026-08-19 22:37 · commit 23c0836 · ts=1787175457
- **错误**：文字搜图 hit_rate 0.6667 且 run_eval 污染版 hit_rate 0.6364 均误判为检索层瓶颈
- **根因**：①queries_image.json 用 caption 前 24 字作查询（模板开头'这是一张/该图展示了'弱查询）；②run_eval 文字语料与 corpus-A 图片共用 yishu_benchmark collection，text/route 查询命中图片点导致虚低
- **修复**：①查询生成器改取核心语义词+强化停用词表（hit_rate 0.6667→1.0）；②run_eval 文字评测独立 collection TEXT_BENCH_COLLECTION（0.6364→0.8182）
- **相关文件**：scripts/build_image_index.py + research/rag_benchmark/run_eval.py
- **教训**：评测指标异常先查评测数据/隔离性（测试数据 bug、collection 污染），再查检索质量；修复测试数据本身也是有效调优

---

### 2026-08-19 20:15 · commit 4fac204 · ts=1787166945
- **错误**：with_retry timeout 测试失败：预期 1s 超时实际 3s+（sleep 拖垮）
- **根因**：ThreadPoolExecutor 的 with 语句退出时 shutdown(wait=True) 等待后台超时线程完成，超时未真正生效
- **修复**：改显式 pool.shutdown(wait=False, cancel_futures=True)，超时立即返回
- **相关文件**：backend/app/services/external/retry.py
- **教训**：线程池超时要用 shutdown(wait=False)，with 块会等待后台线程

---

### 2026-08-19 20:10 · commit 97f5ca6 · ts=1787166648
- **错误**：review_agent 首次集成 lessons 强制检查后 lint 8 处错误（DTZ/PLW1510/S607/E501）
- **根因**：lessons.py 新代码未按 ruff 规则写（datetime.now 无 tz、subprocess.run 无 check、超长行）；时区比较用 UTC vs 本地混用导致 check 逻辑不稳
- **修复**：统一 datetime.now().astimezone() + epoch 时间戳比较（ts= 字段）；subprocess 加 check=False；ruff.toml scripts 加 S607
- **相关文件**：scripts/lessons.py
- **教训**：新脚本先过 ruff 再提交；时间比较一律用 epoch 或统一 tz，别混 UTC/本地

---

### 2026-08-19 19:06 · commit 97f5ca6
- **错误**：全量测试大面积失败（15 failed + 3 errors），test_pipeline 单测卡死 CPU 冻结
- **根因**：Docker Desktop 引擎未运行（yishu-redis/yishu-qdrant 容器丢失）+ PG 服务 Stopped 但旧 postgres 进程残留监听异常（psycopg connect 在 wait_conn 阻塞 20s+）；postmaster.pid 残留阻止重启
- **修复**：启动 Docker Desktop 拉 redis/qdrant 镜像重建容器；杀掉残留 postgres 进程删 postmaster.pid 后 pg_ctl start；AGENTS.md 记录基础设施排查顺序（先查 docker ps/端口/PG 进程）
- **相关文件**：AGENTS.md
- **教训**：测试大面积失败先查基础设施（docker ps + 端口 + PG 进程），别先怀疑代码；PG 服务 Stopped 但进程残留 = 伪死状态

---

### 2026-08-19 19:29 · commit 97f5ca6
- **错误**：3 条同天内容被拆成 2 个 L1 事件
- **根因**：aggregate_user 只查 status=done（自身 processing 不入事件）+ 增量触发无同日去重；pipeline day dict 无 start/end 字段
- **修复**：聚合查询不限于 done；同日已有 L1 事件则并入（更新时间窗+追加 items）；start/end 从成员 ts 推导
- **相关文件**：backend/app/services/events.py
- **教训**：增量聚合必须幂等（同日去重），外部管线返回结构先实测再假设字段

---

### 2026-08-19 19:29 · commit 97f5ca6
- **错误**：SetFit 单条分类冷启动 27s（疑似性能灾难）
- **根因**：首次 predict_proba 触发模型 warmup；批量 5 条 27.6s 摊薄到 5.5s/条，且二次调用 0.2s
- **修复**：新增 classify_batch；worker 攒批调用；AGENTS.md 记录性能基线
- **相关文件**：backend/app/services/classifier.py
- **教训**：CPU 推理先测冷/热启动与批量摊薄，别拿单条冷启动当性能结论

---

### 2026-08-19 19:29 · commit 97f5ca6
- **错误**：worker 里 process_content E2E 卡死 5min+（CPU 冻结）
- **根因**：classifier.py 漏设 HF_HUB_OFFLINE=1，setfit→transformers→huggingface_hub 首次使用联网 HEAD huggingface.co（本机不可达）10s×5 重试×多文件
- **修复**：classifier.py 顶部加 os.environ.setdefault('HF_HUB_OFFLINE','1')（与 embedding.py 同模式）
- **相关文件**：backend/app/services/classifier.py
- **教训**：任何加载本地模型的模块都必须设 HF_HUB_OFFLINE=1，且要先于 huggingface_hub 首次使用

---

## 环境陷阱与经验（从 AGENTS.md 迁移 · 2026-08-20 起单一来源）

> 本专区是"踩坑清单"唯一权威来源（AGENTS.md 只保留一行引用）。
> 新坑必登记：`python scripts/lessons.py add --error ... --root-cause ...`（程序化强制）。

### 本机运行时（Windows + LobsterAI python）
1. **python 是 LobsterAI runtime shim**：`sys.path` 不含 cwd/脚本目录（-c 和文件模式都如此）→ 任何脚本开头 `sys.path.insert(0, <backend|repo>)`；`python -m` 模块方式同样需要
2. **PowerShell 管道损坏 UTF-8**：python 输出经 PS 管道再落盘会乱码（JSON 直接坏）→ 脚本内自行写文件，或重定向用 `*>`，读 JSON 用 python 脚本而非 PS `ConvertFrom-Json`
3. **别用 `python -c` 写多行/含引号逻辑**（PS 转义地狱，反复 ParserError）→ 一律写临时 .py 文件执行
4. **psql 交互会挂死**（等密码输入被断 SIGKILL）→ 用 python+sqlalchemy 执行迁移（连接串从 app.core.config settings 取，不回显）
5. **PostgreSQL 服务易停**（postgresql-x64-17 Stopped）→ 门禁/集成测试挂先查 `Get-Service postgresql*`；拉起：`pg_ctl start -D "C:\Program Files\PostgreSQL\17\data"`（无需管理员）
6. **docker 容器**：yishu-qdrant / yishu-redis / yishu-minio（9000）常驻；MinIO 账号 minioadmin/minioadmin

### 测试工程
7. **集成测试实体 ID 必须随机 uuid4 且每次复用变量**：固定 ID / uuid5 确定性 ID 会撞库内残留/归属校验（push_ops 拒 "entity 不属于当前用户"），跨运行残留更难查
8. **内存型 fake 后端必须单例**（跨调用共享状态）：分片上传 fake 每次 new 实例会丢数据
9. **mock 响应结构必须与真实 API 一致**：腾讯 CI 打标真实响应是 `CameraLabels/WebLabels.Labels[].Name`（不是 Result.Tags），先抓真实响应再写 mock，否则单测全绿真机全挂
10. **review_agent 偶发 pytest 失败**：预存在顺序敏感竞态（test_queue/interview），直接全量重跑即绿；真失败先查 PG/Redis 是否在跑

### 外部 API 集成
11. **腾讯云 SDK 用法以源码/官方文档为准**：`ci_auditing_image_batch` 的 DetectType 要 `CiDetectType.PORN | ...` 位掩码（int），传字符串 TypeError
12. **dashscope SDK**：workspace 只能显式传参（`Generation.call(workspace=...)` / `Recognition(model=..., callback=None, workspace=...).call(file=...)`，Recognition 是实例方法）；旧模型名 paraformer-v2/sensevoice-v1 已下线，百炼 2026 清单：fun-asr / qwen3-asr-flash-filetrans
13. **批量外部调用必须加重试+指数退避**：500 张 caption 第一轮 121 张 ConnectionError（10053），无重试只靠重跑，浪费两轮时间；大流量任务不要与下载并发
14. **探测要覆盖每个服务，别用单模型泛化**：qwen-flash 可用 ≠ 百炼全服务可用；ASR 报 Model not found 时先排查 key 类型/API 路径差异（sk-ws- workspace key 与 ASR 服务鉴权是否兼容待验证——用户已确认 ASR 无需单独开通，根因未定，别下结论），再考虑外部因素
15. **不下无证据结论**：区分"我的调用问题"与"外部需操作"；两轮验证不过再定性，避免把推断当事实汇报

### 网络与数据源
16. huggingface.co 不可达；hf-mirror.com / modelscope.cn 可达（模型/数据集优先这两家）；openslr CN 镜像 SSL 失效（AISHELL 用 hf-mirror 官方镜像 `AISHELL/AISHELL-1` 说话人分包）
17. 环境变量优先级：进程 env > .env（infisical run 注入会覆盖 .env）；旧 DASHSCOPE_API_KEY（sk-****（截断））在 shell 残留，真实调用统一 `infisical run` 注入并屏蔽 .env 旧 TENCENT_SECRET_ID 抢占别名

### 模型缓存与下载（2026-08-20 实战）
18. **HF hub 在 Windows 上 snapshots 是实体复制不是符号链接**（无符号链接权限时回退）→ `blobs/`（内容寻址）+ `snapshots/`（版本快照）双份占用；判断在用版本看 `refs/main` 指向哪个 snapshot；`.incomplete` 后缀 = 下载中断残留可删
19. **sentence-transformers 不同版本拉不同权重格式**（旧版 pytorch_model.bin / 新版 model.safetensors）→ 换版本/换库会触发二次下载各留一个 snapshot（bge-m3 实测 2.2GB×2 重复）；下载前先查本地缓存 `~/.cache/huggingface/hub/models--*/`
20. **后台任务杀进程要杀 python 子进程不是 exec session**：exec 的 PID 是管道进程，`Stop-Process` 子 python 后要 `Get-Process python` 确认无残留（否则 3 进程并发抢 CPU 互相拖垮，实测）；后台跑模型任务用 `*>` 重定向日志到文件 + 定期看日志判断进度，不要用管道缓冲（看不到实时输出）
21. **模型资产清单（backend/models/README.md）是防重复下载的第一道闸**：新模型落地前先查清单 + 本地缓存，登记模型名/版本 hash/下载源/加载代码位置/可删性；不要凭记忆判断"有没有下载过"
22. **C 盘清理前先自审**：模型分三类——在用（refs 指向/代码引用）、旧版本残留（可删）、中断残留 `.incomplete`（可删）；删后必须重跑加载测试验证（如 `pytest -m rag`）

