# 忆述光华 · 团队 Git 使用规范（2026-08-26 制定）

> 依据：Conventional Commits 1.0.0（conventionalcommits.org）+ cbeams《How to Write a Git Commit Message》七规则 + GitHub Flow（docs.github.com/github-flow）。
> 适用范围：仓库 zqhmy1234/YSGH-APP（团队所有），全体成员与所有开发 Agent。
> 目的：可读、可追溯、可自动化的提交历史；分支责任清晰；合入门禁统一。

---

## 一、提交信息（Commit Message）——Conventional Commits + 七规则

### 1. 格式（MUST）

```
<type>(<scope>): <description>

[body：为什么改、改了什么（不写怎么改）]

[footer：BREAKING CHANGE / Refs / Reviewed-by 等]
```

### 2. type 词表（MUST 从下列选，禁止自造）

| type | 语义 |
|---|---|
| feat | 新功能（对应 SemVer MINOR） |
| fix | 缺陷修复（对应 SemVer PATCH） |
| perf | 性能优化 |
| refactor | 重构（行为不变） |
| test | 测试新增/修改 |
| docs | 文档（含 harness/交接/规范） |
| chore | 杂项（依赖、构建、配置非 env） |
| ci | CI/门禁流程 |
| style | 格式（ruff/import 排序等，无行为变化） |
| build | 构建系统/依赖清单（requirements 升版归 chore 或 build） |
| merge | 分支合入（仅集成 Agent 使用） |
| revert | 回滚 |

破坏性变更：`feat(api)!: ...` 或 footer `BREAKING CHANGE: ...`（MUST 二选一）。

### 3. scope（SHOULD，小写名词）

组件/模块名，如 `feat(upload)`、`fix(notify)`、`perf(events)`、`ci(workflow)`、`docs(harness)`、`chore(techdebt-p1a)`。
多模块改动用主模块 scope；纯跨模块整合用 `merge` 类型。

### 4. description 规则（MUST 遵守 cbeams 七规则）

1. 主题行与正文空一行分隔。
2. 主题行 ≤72 字符（中文按 1 字符计；英文 ≤50）。
3. 主题行动词用**祈使语气**（"修复"而非"修复了"；英文 "Fix" 而非 "Fixed/Fixes"）。
4. 主题行末尾不加句号。
5. 首字母大写（中文句首名词即可，不强制）。
6. 正文每行 ≤72 字符，解释 **what（改了什么）与 why（为什么）**，不写 how（代码即 how）。
7. 正文空行分段；footer 用 trailer 格式（`Refs: #issue`、`Reviewed-by:`、`BREAKING CHANGE:`）。

### 5. 元数据（SHOULD）

- 关联 issue/PR：footer `Refs: #12`（GitHub 自动链接）。
- 关闭 issue：主题或 body 用 `Closes #12`（仅用户确认时使用）。

### 6. 示例（本项目风格）

```
feat(upload): complete 支持 content_type=voice 直接建 voice 内容

对象从 photos/ 前缀搬到 voice/{user}/（生命周期可分别配置）；
/contents voice 带 cos_key 幂等，旧客户端二次建内容不重复。

Refs: #2
```

```
fix(notify): 关怀频次统计去掉 sent_at 上界，消除双时钟陷阱

上界对比依赖客户端注入时间与 DB 时钟一致，测试/时钟偏差下
streak 恒 0；下界 lookback 防呆足够，未来消息不应参与节流。

Refs: #3
```

---

## 二、分支命名与管理（GitHub Flow 风格 + 职责前缀）

### 1. 命名规范（MUST）

`<kind>/<short-description>`，小写 + 连字符，短且描述性强（GitHub Flow 原则）。

| kind | 用途 | 示例 |
|---|---|---|
| feature/ | 功能开发（单人/单 Agent 独立分支） | `feature/m1-rag`、`feature/b1-profile` |
| waveN-agentX | 并行波次 Agent 分支（本仓既有约定，保留） | `wave4-agentJ`、`wave4-agentK` |
| techdebt/ | 技术债批次 | `techdebt/p1a-config`、`techdebt/p2a-deadcode` |
| fix/ | 缺陷修复 | `fix/ci-pg-init`、`fix/notify-streak` |
| hotfix/ | 生产紧急修复（直接基于 main/develop） | `hotfix/sms-gate` |
| release/ | 发版准备（冻结 + 版本号） | `release/v0.9.0` |
| docs/ | 纯文档 | `docs/git-spec` |

### 2. 主分支职责（MUST）

- `develop`：唯一集成主干。**只有集成 Agent 可 merge/push**；普通 Agent 只 push 自己的 feature/fix 分支。
- `main`：发版分支（当前停更，MVP 发布时启用）。develop → main 仅经 release/ 流程。

### 3. 分支生命周期

1. 每项独立工作开独立分支（一个分支一个主题，GitHub Flow「Make a separate branch for each set of unrelated changes」）。
2. 分支从最新 develop 切出：`git fetch origin && git checkout -b feature/xxx origin/develop`。
3. 工作完成后由集成 Agent 统一 `merge --no-ff` 进 develop（保留合并记录，历史可读）。
4. 合并后由集成 Agent 删除已合并分支（远程与本地）。
5. 冲突处理：分支作者优先解决；集成 Agent 裁决文件域归属。

### 4. 提交粒度（GitHub Flow「每个提交是隔离、完整的变化」）

- 一个提交 = 一个原子变化（改变量 + 加测试可以同提交，但 unrelated 变更必须拆开）。
- 便于 revert：想撤销变量改名时不必连带撤销测试。

---

## 三、合入门禁（MUST，由 review_agent 程序化强制）

1. 快速门禁（commit 前）：`python scripts/review_agent.py`——只查本次提交文件（syntax/lint/secrets/todos）。
2. 全量门禁（声明完成/集成/CI 前）：`python scripts/review_agent.py --full`——仓库级静态 + 全量 pytest + api_smoke + research。
3. 门禁失败：**必须先 `scripts/lessons.py add` 登记教训**，再修再跑（lessons 检查会阻断）。
4. CI 双门禁（GitHub Actions）：Fast Gate（静态 30s）+ Full Gate（全量，含 RAG 分组与 Qdrant 隔离）。
5. 禁止：跳过门禁 `--no-verify`（仅集成 Agent 在合并提交时可豁免，且需理由）；禁止向 develop 直接 push 非集成提交。

---

## 四、PR 与代码评审（团队协作）

- 功能分支完成后开 PR → develop（团队仓库，PR 评审记录在案）。
- PR 描述：变更摘要 + 解决了什么问题 + 关联 issue（`Closes #n`）+ 验收证据（测试/门禁结果）。
- 评审要点：文件域是否越界、是否有未登记教训、错误码/契约是否登记、测试是否覆盖根因。
- PR 合并由集成 Agent 执行（`merge` 类型提交），合并后回填 CI 结论到 PR 评论。

---

## 五、禁止事项（MUST NOT）

- 禁止把真实密钥提交进仓库（fast-gate 密钥扫描会拦）。
- 禁止提交本地环境文件（.env、模型、构建产物、.wt/、.cowork-temp/——.gitignore 已覆盖）。
- 禁止 `git add -A` / `git add .` 后无差别提交（误收他人半成品/脏文件；本仓已发生 2 次，教训在 lessons.md）。
- 禁止改写已推送历史（rebase/amend 已 push 提交）——除非集成 Agent 评估且团队确认。
- 禁止在共享工作区并行编辑同一文件域（文件域所有权见 docs/parallel-dev/13_集成规则）。

---

## 六、本规范落地检查清单

- [ ] 提交信息：type(scope): description + 祈使语气 + ≤72 字符主题
- [ ] 分支名：`<kind>/<描述>`，小写连字符
- [ ] 快速门禁过 + 教训已登记（如有失败）
- [ ] 无 .env/密钥/构建产物混入
- [ ] 无 `git add .` 误收
- [ ] 集成后 PR/issue 回填
