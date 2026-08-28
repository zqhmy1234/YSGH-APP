# 教训台账（Harness 强制登记 · 2026-08-20 起）

> 规则（程序化强制，见 scripts/lessons.py + review_agent.py check_lessons）：
> 开发阶段每次排查错误并修复后，必须登记一条教训——review_agent 检查失败后
> 未登记新教训会阻断 commit。格式固定，勿手改结构。
>
> 新增：`python scripts/lessons.py add --error "..." --root-cause "..." [--fix "..." --file "..."]`

---

### 2026-08-29 01:24 · commit cbc1751 · ts=1787937879
- **错误**：harness 台账整饬发现：同一'用户故事/缺陷'数字在 AGENTS/08/progress/handoff/feature_list 五处出现三种值（✅41 vs ✅46、A27 vs A32、D-01~D-19 vs 实表含 D-21），session-handoff 头部滞留四天前会话的'当前状态'
- **根因**：多窗口并行追加式文档：每波只更新自己负责的那份快照，无单一事实源与同步义务；handoff 只增不删致旧'当前状态'永存；'Wave4'在开发期与收尾期两义未消歧
- **修复**：建立 docs/决策台账.md（术语/决策/待拍板唯一登记簿）+ AGENTS「当前状态」为数字唯一现行口径；立'改数字五处同步'纪律；progress 历史条目只读+加勘误注记；handoff 重写为现行交接、历史压缩存档（原文入 git）
- **相关文件**：docs/决策台账.md
- **教训**：（无）

---

### 2026-08-29 01:24 · commit cbc1751 · ts=1787937879
- **错误**：整饬会话中 edit 工具（ReplaceFileW）对 init.sh/progress.md/lessons.md 间歇报 EIO(Win32 1175) 且成功后把整个文件写成 CRLF；init.sh 被 autocrlf=true+工具写 CRLF 后 bash 无法执行（set: -; $'\r': command not found）
- **根因**：多窗口/索引器占用工作区文件导致替换式写入失败；Windows 文本工具默认按 CRLF 落盘，而 git 在 autocrlf=true 下 diff 会掩盖换行差异，仓库又缺 .gitattributes 守卫——CRLF 破坏只在 bash 执行时才暴露
- **修复**：init.sh 二进制级还原 LF；新建 .gitattributes（*.sh/*.py eol=lf）；md/json 保持现状由 git 提交时归一；EIO 绕行=临时 .py 脚本做唯一命中断言替换（复用本仓既有模式）
- **相关文件**：init.sh
- **教训**：（无）

---

