# 波 D 提示词：后端结构重构（巨文件拆分 / 归属校验 helper 收敛 / 精确归属门禁）

> 用途：交给新 Agent 执行。整份投喂。波 D 是**纯工程债偿还、零功能增量**——验收铁律是「测试计数不减、行为全等」，任何"顺手优化逻辑"都是越界。

---

你是 GuangH-App（忆述光华）项目的后端重构 Agent。任务三件，按依赖顺序：② helper 收敛 → ③ 精确归属门禁 → ① 巨文件拆分。全程基线：**全量 pytest ≥820 passed / ruff 全绿 / git fsck 零 error**，每个子项独立可回退提交。

## 〇、先读环境（新 Agent 必读，这台机器有雷）

0.1 **git 铁律**（本项目因违反已炸过两次仓库，逐条执行）：
- 本机 ref 写操作（commit/merge/update-ref）可能**静默失败**（退出码 0 但指针不动）。每次 commit 后三通道复核：`git rev-parse HEAD` + `git log --oneline -1` + `git for-each-ref refs/heads/<分支>` 一致才算。
- 不一致时修法：`git reflog -1 --format=%H` 挖真身 → python `re.fullmatch(r'[0-9a-f]{40}', full)` 断言 → 直写 `.git/refs/heads/<分支>` loose 文件（`os.makedirs(dirname, exist_ok=True)`）+ 替换 `packed-refs` 对应行（二进制读写、锚点唯一断言）。SHA 永不手拼。
- 禁 `git stash`（坏对象环境下引爆仓库，已实锤）；禁主仓 `git add -A`（多窗并行，会扫进他人文件）；禁 `--no-verify`；git 命令一律 `cd <绝对路径> && ` 前缀（cwd 每条命令漂移）；命令给足超时，被 SIGTERM 后先 `git status`+查 `index.lock` 再动。
0.2 **工作树**：所有工作在 worktree `D:\GuangH-App\.wt\missing-pages`（分支 feature/missing-pages-impl）内进行。开工前 `git -C D:/GuangH-App/.wt/missing-pages status --short` 必须为空；非空则停下报告（有窗在途）。若用户已执行「波 E 合并」，改在主仓 develop 上做，其余纪律不变。
0.3 **测试环境**：系统 Python `C:/Users/ghf/AppData/Local/Programs/Python/Python313/python.exe`（无 venv），`cd D:/GuangH-App/.wt/missing-pages/backend && <py> -m pytest tests/ -q --tb=line`。测试直写本地 yishu 主库（teardown 自清），**依赖 Docker Desktop 的 yishu-redis/yishu-qdrant 容器在跑**（`docker ps` 先验；没跑就 `Start-Process "C:\Program Files\Docker\Docker\Docker Desktop.exe"` 等 daemon）。基线：820 passed, 4 skipped（20 deselected=integration 分层默认排除属正常，与基线同参数比）。
0.4 **测试要精准**：改动前先按文件反查受影响测试（`grep -rl "<被改函数名>" tests/`）跑子集，绿了再全量；**别动不动全量回测**（约 3 分钟/轮，每子项收尾+最终各一轮即可）。
0.5 项目纪律：`from __future__ import annotations`、ruff（I001/F401/E702/S108，禁 /tmp 字面量）、lessons 登记 hook（pre-commit 强制）；函数返回**禁止静默**——错误/降级路径必须显式（日志或异常）。

## 一、目标 ②：归属校验 helper 收敛（先做，为 ③ 铺路）

**背景实证**（2026-09-10 安全深扫 P2-5/波D 排查结论）：全项目存在两种"按 id 取本人实体"的实现风格——contents.py 有正例 `_load_alive_content(db, user_id, content_id)`（NotFound 与"不存在/非本人/软删"统一 404，IDOR 零信息量），但 events/capsules/messages/event_items/stats 等各自手写或**漏写**过滤。实测已修两处不一致：`_batch_photo_ids` 补了归属+软删过滤、media/audio 补了软删过滤。现存 7 处 `join(Content)`（events.py×4、event_items.py、stats.py、timeline.py）中 4 处返回聚合数字、依赖「event_items 只挂本人内容」不变量（该不变量已由 sync P2-1 封堵），**不要求你改它们**。

**任务**：
1. 通读 `app/api/` 全部路由中「按路径/查询参数 id 读取或变更单个既有实体」的函数（Content/Event/Capsule/Message/UploadTask 五类），列一张现状表：文件:函数 / 用的哪种写法（_load_alive_content | 手写 where | db.get+手判 | **缺过滤**）。
2. 按实体各建一个统一 loader（放 `app/api/deps.py` 或各域模块内，就近原则）：
   - `load_alive_content(db, user_id, content_id) -> Content`（提升/复用 contents.py 现有的为公共）
   - `load_owned_event(db, user_id, event_id)`、`load_owned_capsule(...)`、`load_owned_message(...)`、`load_owned_task(...)`
   语义全等要求：**HTTP 状态码、错误码（ApiError code）、message 文案、软删/归属四种输入的行为与改造前逐个一致**（先给每个函数补 200/404-IDOR/404-deleted 三态断言的 characterization test，再接线替换；这是防重构变行为的生命线）。
3. 各路由改用 loader；发现"缺过滤"的按安全批模式补双侧钉桩测试（参照 tests/test_l3_voice_chain.py::test_audio_soft_deleted_404 写法）。
4. 提交节奏：每实体一 commit（characterization test + 替换 + 全量绿），message 前缀 `refactor(authz):`。

## 二、目标 ③：精确归属校验静态门禁（CI 测试形式，非正则半成品）

**背景教训**（2026-09-10 波 D 侦察实录）：用正则扫「带 id 参数的路由是否函数体引用 user.id」→ 4 命中全误报（创建型端点无归属概念、`content.user_id != user.id` 写法没被模式认出来）。半成品门禁=负资产，所以搁置。现在有 ② 的统一 loader 做锚点，做**精确版**：
1. 新建 `tests/test_authz_gate.py`（pytest.mark.unit，无 DB）：AST 遍历 `app/api/*.py`：
   - 找所有 `@router.(get|patch|put|delete)` 且路径含 `{*_id}` 或形参含 `*_id: str` 的处理函数；
   - 排除「创建型」白名单（返回新实体 id、无既有归属可读：逐条**人工核对源码后**写进白名单常量，附行内注释说明理由）；
   - 断言函数体满足其一：调用了 ② 的任一 loader / 引用 `user.id` 于 where 语义 / 函数体含显式 `_assert_owner` 类调用；
   - **对残余例外给显式 EXEMPT 列表**（文件+函数+理由三列），禁止加通配兜底。
2. 该测试即"未来任何新端点写漏归属 → CI 红"的焊死点。写进报告「门禁上线说明」。
3. 提交 `test(authz): 归属校验 AST 门禁`，全量绿。

## 三、目标 ①：巨文件拆分（最后做，风险最高）

对象（当前行数）：`app/services/pipeline.py` 708、`app/services/event_aggregation/pipeline.py` 615、`app/services/events/aggregate.py` 516。
1. **每个文件动手前先建安全网**：`git log --oneline -1` 记录基线；为其公共入口函数（process_content / 聚合主函数等）确认已有端到端测试（tests/test_pipeline.py、test_events.py、test_ab_scenarios.py 覆盖在案），缺则先补 characterization 断言（输入→DB 落值/出参结构），绿后再拆。
2. 拆分方式仿 **h1 先例**（`app/services/upload/` 子包化：protocol/register 分文件、`__init__.py` 全量兼容 re-export，消费方零改动）：按「管线阶段/职责」切子模块，**只搬代码不改逻辑**——diff 审查看似大，实质每个函数体逐字不变；旧 import 路径经 `__init__` 兼容层保持可用。
3. 一次只拆一个文件、一个 commit、拆完即：受影响子集测试 → 全量 → ruff → `grep -c "def " 旧新文件` 计数核对函数无丢失；**任一失败立即 revert 该文件**（独立 commit 的意义）。
4. 拆后目标：单文件 ≤ ~350 行；若某文件按职责实在切不开（高内聚），允许停在报告里论证不拆——**诚实结论好过为指标硬切**。

## 四、终局验收（三目标完成后）

1. 全量 `pytest tests/ -q --tb=line` ≥ 820 passed 4 skipped 零失败；ruff 全绿；`git fsck --no-dangling` 零 error。
2. 行为全等总检：安全批七项修复的钉桩（test_wechat P1-1 三条、l3_voice_chain 软删、error_registry 54 码、capsule 并发）逐条仍在且绿。
3. 交付报告：三目标各一节（现状表/门禁误报排除清单/各文件拆前拆后行数图）；提交链 hash 列表；未尽事项与理由（尤其"决定不拆/不改"的每一项）。
4. 若 worktree 工作：完成后**不 push 不 merge**（回主干由波 E/用户统一收口），只 commit 在 feature 分支。主仓工作：正常 commit，push 前问用户。

## 五、红线

- 不碰 `app/services/rag/recall.py`、`photo_content.py`、`wechat/gateway.py`、`sync.py`、`capsule_scan.py`、`media.py`（安全批/近期修复热区，除非 ② 的 loader 接线必须引用它们——只 import 不修改）。
- 不改任何错误码、HTTP 状态码、响应结构（② 的"行为全等"要求优先于代码美观）。
- 不做 pg_fallback 全文索引（已撤销：Qdrant 降级路径优化=伪任务）。
- 拿不准就停：报告现状等用户，别赌。
