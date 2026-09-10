# 波 E 提示词：feature/missing-pages-impl → develop 合并收口

> 用途：交给新 Agent 执行。整份投喂，勿删前置检查段。

---

你是 GuangH-App（忆述光华）项目的发布工程 Agent。任务：把 `feature/missing-pages-impl` 分支合并进 `develop` 并推送远程。**这是 git 手术任务，不是写代码任务。** 动手前必须完整读完本提示词的第一节——这台机器的 git 环境有已实锤的连环陷阱，前人用两次仓库损坏换来的纪律，违反即事故。

## 一、本机 git 铁律（违反=可能毁掉仓库，逐条执行）

1. **第零定律**：本机任何 `git commit` / merge 提交 / `update-ref` 的 ref 更新都可能**静默失败**（退出码 0、输出成功，但分支指针没动）。因此**每次 ref 写操作后必须三通道复核**：`git rev-parse HEAD`、`git log --oneline -1`、`git for-each-ref refs/heads/<分支>` 三者一致才算数。
2. **修 ref 姿势**（复核不一致时）：`git reflog -1 --format=%H` 挖真身 → python 断言 40 位 hex（`re.fullmatch(r'[0-9a-f]{40}', full)`）→ 直写 loose ref 文件 `.git/refs/heads/…`（先 `os.makedirs(os.path.dirname(...), exist_ok=True)`）+ 同步替换 `packed-refs` 对应行（二进制读写、断言锚点唯一、无 CRLF）。**SHA 永远现取，禁止手拼/截断。**
3. **禁 stash**（坏对象环境下 `git stash` 会引爆仓库，本项目已实锤一次）；禁 `git add -A` 于主仓（他窗在途文件会被扫进）；禁 `--no-verify`。
4. **cwd 每条命令必漂移**：任何 git 命令必须以 `cd <目标仓绝对路径> && ` 开头写在同一命令链里。
5. **每步给足超时**（merge/push 60s 起）；**任何命令被 SIGTERM 截断后，下一步先 `git status` + 查 `.git/**/index.lock` 僵死锁再动作**。
6. 动手前先做**保护性快照**：`tar -cf backups/git_rescue_<日期>/pre_merge.tar .git`（主仓）。

## 二、任务现场（2026-09-10 交接时的核实状态，动手时重新验证）

- 主仓 `D:\GuangH-App`：分支 `develop`，交接 tip=`b4d6414`（已推 origin）。**工作区有他窗未提交在途件**——`git status` 可见（历史清单曾含：AGENTS.md、backend/app/services/upload/register.py、client/manifest.json、docs/决策台账.md、progress.md + 若干 untracked）。
- 工作树 `D:\GuangH-App\.wt\missing-pages`：分支 `feature/missing-pages-impl`，交接 tip=`424203b`（已推 origin），工作区应干净。链上约 40 枚提交＝整个「缺失页面实现」战役全部成果（A 批后端缺口、B 批客户端、B10 TabBar 单页容器化 shell+四 tab 组件+老四页删除、真机复验修复、波形/音频引擎 v5、安全深扫七项修复、性能波 B/C）。
- 后端测试基线（工作树内）：**821 passed, 4 skipped**（系统 Python 3.13 直跑，无 venv；测试依赖 Docker Desktop 的 yishu-redis/yishu-qdrant 容器在跑，机器重启即眠——跑测前先 `docker ps` 确认）。
- 客户端编译门：`cd D:/GuangH-App/.wt/missing-pages/client && /d/HBuilderX/cli.exe launch app-android --project <同上路径> --compile true`，判据=「项目 client 编译成功」+ grep error 零命中（需 HBuilderX 进程存活，`cli open` 拉起）。

## 三、硬性前置（不满足就停，向用户报告，绝不强行推进）

1. **他窗在途件必须已收口**：主仓 `git status` 若仍出现已跟踪文件的未提交修改（尤其上面清单中的 5 个），**停止并报告**——merge 会在脏工作区上引发 checkout 冲突误伤或把这些裹进 merge commit。等用户处理完（提交/还原）再继续。untracked 新文件（如 _verify_* 目录）不阻塞。
2. 三通道复核 develop 与 origin/develop 一致；`git fetch origin` 后确认 `origin/feature/missing-pages-impl` 仍是 `424203b` 或其后代。
3. 保护性快照完成且 `tar -tf` 可读。

## 四、执行序列

1. 主仓 `git checkout develop`（前置 1 未清零时这步本身就会闹，正好当探针）→ `git merge --no-ff feature/missing-pages-impl -m "merge(missing-pages): 缺失页面实现战役全量入库 develop——A/B批缺口+B10 单页容器化+真机修复+安全/性能波"`。
2. **预期冲突面**（develop 侧自分支创建后若有新提交）：`client/pages.json`、`client/components/TabBar/TabBar.uvue`、`client/pages/index|ai|search|profile/*`（分支侧已删除、develop 侧若有改动→保留删除）、`docs/远期待办总账.md`、`backend/` 各文件按内容合。冲突解决原则：**分支侧是更新的真值**（含全部修复），develop 侧改动如非明显更新则记录到报告里请用户裁决，不静默丢弃任何一侧的语义。若 merge 中途被 SIGTERM 打断：先查 `git status`（MERGING 态），可 `git merge --abort` 回退重来，abort 后必须复核 develop 仍在 merge 前 tip。
3. merge commit 落定后：**三通道复核 ref**（第零定律——merge 生成的 commit 一样可能被静默吞，loose+packed 双写修）→ `git rev-parse develop` == 预期 merge commit。
4. **验证门（merge 绿之前不 push）**：
   a. 工作树先不要动；在**主仓** develop 上跑后端测试前注意主仓缺 .env 的话按 tests/conftest 要求处理（若主仓 backend 无可用 .env，验证改在工作树跑 `git -C .wt/missing-pages merge --no-ff develop` 方向的镜像验证，或询问用户）。首选：`cd D:/GuangH-App/backend && <系统python> -m pytest tests/ -q --tb=line`，判据 ≥821 passed 零失败。
   b. 客户端编译门（主仓 client，路径换主仓）。
   c. 任一红：不 push，报告差异明细，等用户定夺。
5. `cd D:/GuangH-App && timeout 120 git push origin develop`。push 失败若为网络重置：重试 ≤3 次，仍败则报告（commit 已本地安全，绝不 force）。
6. merge 成功且推远程后：`git log --oneline develop -5` 与远程 `git ls-remote origin refs/heads/develop` 全等，截图式贴进报告。

## 五、收尾（merge 绿之后才做）

1. `docs/决策台账.md` / 远期待办总账登记「波 E 完成」（develop 含缺失页面战役全量），提交走第一、二节全部纪律。
2. `feature/missing-pages-impl` 分支与 worktree **暂不删除**（留一个回归观察窗；删除动作属用户决策，报告里列命令等他点头）。
3. fix/4b 的独有内容（D-18/D-19 插件声明 b23439c + manifest 卡点 3b2faec）**已等效入库**（工作树 3769013/a3da8cb 谱系含外科合入版）——报告中注明即可，不重复 cherry-pick；fix/4b 分支删除同样留给用户。

## 六、交付报告格式

- 前置检查三项逐条证据（命令+输出）
- merge 统计（commits 数、冲突清单、逐冲突处置）
- 验证门输出尾部原文
- 三通道/远程复核表
- 遗留与风险（含建议的后续动作清单）

全程禁止：stash、主仓 add -A、no-verify、force push、删 worktree/分支（除非用户明示）。任何一步判定「继续做会毁东西」时：停下来，报告现状，等用户。
