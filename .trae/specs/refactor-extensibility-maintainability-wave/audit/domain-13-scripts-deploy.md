# 域13 · 脚本与部署（scripts / backend/scripts / deploy）深度审计

> Phase A 只读筛查。**未修改任何业务文件**；仅新增本报告。
> 范围：`scripts/**`（.py/.ps1/.mjs/.sql）、`backend/scripts/*`、`deploy/**`（Dockerfile / compose / RUNBOOK / README / systemd / initdb / scripts / .env 模板）。

## 覆盖范围与实读清单

### `git status --short`（审计时点，分支 develop @ cec3172）
```
?? .codebuddy/
?? .trae/specs/refactor-extensibility-maintainability-wave/audit/
```
结论：本域文件**全部已入库、无在途改动**；仅审计目录本身为未跟踪。

### 实读文件（路径 + 有效行数）

**`deploy/**`（全量读完）**
- `deploy/Dockerfile.backend`(49) · `docker-compose.yml`(161) · `RUNBOOK.md`(163) · `README.md`(169)
- `deploy/.env.production.template`(179) · `ONE_SWAP.md`(144) · `SERVER_INTAKE.md`(54)
- `deploy/initdb/01-extensions.sql`(17) · `deploy/systemd/yishu-api.service`(39) · `yishu-worker.service`(39)
- `deploy/scripts/`: `bootstrap.sh`(153) `deploy_one.sh`(102) `pull_models.sh`(83) `healthcheck.sh`(117) `backup_pg.sh`(104) `check_env_template.py`(78) `preflight_pack.ps1`(233)
- `deploy/download/index.html`(152，抽查占位注释)

**`scripts/**`（读完全部 .py/.ps1/.mjs/.sql）**
- `review_agent.py`(417) `test_agent.py`(336) `audit_harness.py`(564) `audit_security.py`(141) `lessons.py`(200)
- `check_schema_drift.py`(372) `api_smoke_cases.py`(289) `validate_truth_data.py`(282) `gen_agg_fixtures.py`(316)
- `build_image_index.py`(163) `build_truth_corpus.py`(178) `eval_image_search.py`(78) `run_wer_bench.py`(136)
- `check_dashscope.py`(61) `check_dashscope_matrix.py`(372) `warm_hf_models.py`(80) `subset_fonts.py`(185)
- `smoke_cos.py`(149) `smoke_cos_upload.py`(60) `generate_test_photos.py`(130) `agg_generate_photos.py`(170) `agg_load_real_photos.py`(155)
- `_merge_l0_refine.py`(73) `_merge_l1_refine.py`(109) `_expand_l1_and_gen_inputs.py`(117)
- `backup_pg.ps1`(55) `e2e_agent_chat.ps1`(123) `realdevice/r2_4b_verify.ps1`(83) `test_auth_singleflight.mjs`(141) `setup_pg.sql`(35)

**`backend/scripts/*`（全部读完）**
- `prepare_sensevoice.py`(32) `train_setfit.py`(157) `reflow_global.py`(201) `measure_correction_gain.py`(181)
- `backfill_orphan_events.py`(76) `backfill_thumbnails.py`(56) `daily_review.py`(66) `reinject_missing_photos.py`(137) `seed_echo_today.py`(136) `wecom_sandbox.py`(158)

**交叉实读**：`.github/workflows/ci.yml`(547) · `backend/tests/test_cli_scripts.py`(183) · `backend/app/core/config.py`(抽查 34-40) · `.gitignore`(抽查) · `backend/app/services/event_aggregation/{generate_test_photos,load_real_photos}.py`（shim）

### 未读 / 未覆盖
- `deploy/download/style.css`(290) / `assets/*.svg` / `qr/*` — 静态下载页美术件，非逻辑件，未逐行读。
- `scripts/pf_backup/*`（字体/图片二进制备份）、`scripts/realdevice/evidence/*`（50 份历史证据）— 二进制/证据归档，未读。
- 明确排除：`.wt/**`、`.workbuddy/**`、`_tmp/**`、`agent/**`（本波排除域）。

---

## 条目

| # | 位置(文件:行) | 类型 | 严重度 | 收益 | 风险 | 可回滚性 | 验证手段 | 批次建议 | 状态 |
|---|---|---|---|---|---|---|---|---|---|
| D13-1 | `.github/workflows/ci.yml:73` | 门禁缺口 | P1 | 恢复客户端类型门禁有效性 | 现为"零文件编译=恒绿"假通过 | 行为等价替换 | 本地 `tsc -p` 换 `*.uts` 后真报错 | B-CI 批 | 确认 |
| D13-2 | `scripts/test_auth_singleflight.mjs:17,26,87` | 死码/门禁缺口 | P1 | 恢复 single-flight 单测 | 引用已改名文件，测试恒不可用 | 行为等价替换 | `node --test` 现报 ERR_MODULE_NOT_FOUND | B-CI 批 | 确认 |
| D13-3 | `deploy/scripts/check_env_template.py`（全） | 门禁缺口 | P1 | 模板↔config 漂移自动拦住 | 唯一判据但无任何门禁调用 | 新增工具 | `grep -rn check_env_template .github scripts` = 0 | B-CI 批 | 确认 |
| D13-4 | `scripts/audit_security.py:102-118` | 声明漂移 | P1 | 安全审计能覆盖生产备份 | 查开发件+错误目录，RPO 检查恒通过 | 行为等价替换 | 无 dump 时 `fail=False` 直接绿 | B7 批 | 确认 |
| D13-5 | `ci.yml`（全） | 门禁缺口 | P2 | 明确哪些脚本无自动执行 | 多个审计/冒烟脚本仅人工跑 | 新增工具 | CI 步骤清单比对 | B-CI 批 | 确认 |
| D13-6 | `scripts/_merge_l0_refine.py:9-11,25,68` | 死码 | P2 | 清一次性脚本 | 输入片段 0 个，跑则空转+覆盖主文件 | 删码(需证死) | `Get-ChildItem docs -Filter _l0_refine_*.json` = 0 | 清理批 | 确认 |
| D13-7 | `scripts/_merge_l1_refine.py:13-15,93-96` · `_expand_l1_and_gen_inputs.py:6-9,76-87` | 死码/幂等 | P2 | 减少 cwd 依赖脚本 | 一次性原地覆盖、断言硬编码、无 dry-run | 删码(需证死) | 输入仍在(193)但流程已完结 | 清理批 | 候审 |
| D13-8 | `backend/scripts/reinject_missing_photos.py`（全） · `seed_echo_today.py`（全） | 幂等 | P2 | 数据注入可复跑 | 每次随机抽图并 INSERT 新内容，不可复跑 | 新增工具 | 读 L94-118 无 upsert 键 | 清理批 | 确认 |
| D13-9 | `_merge_l0_refine.py:9-11` | 硬编码路径 | P2 | 去机器绑定 | `D:\GuangH-App\docs\...` 绝对路径入库 | 行为等价替换 | 读 L9-11 | 清理批 | 确认 |
| D13-10 | `reinject_missing_photos.py:25` · `seed_echo_today.py:31` | 硬编码路径 | P2 | 去个人路径 | `C:\Users\ghf\Pictures\Screenshots` 入库 | 行为等价替换 | 读 L25 / L31 | 清理批 | 确认 |
| D13-11 | `scripts/smoke_cos_upload.py:11` | 硬编码路径 | P2 | 与同批 B7 改造一致 | B7 漏网绝对路径（同批 smoke_cos.py 已相对化） | 行为等价替换 | 读 L11 | B7 批 | 确认 |
| D13-12 | `scripts/backup_pg.ps1:15` | 硬编码路径 | P2 | 去机器绑定 | 默认 `D:\GuangH-App\backups` | 行为等价替换 | 读 L15 | 清理批 | 确认 |
| D13-13 | `scripts/setup_pg.sql:13` | 硬编码凭据 | P2 | 消除明文口令 | dev 口令 `yishu_app_2026` 明文入库 | 行为等价替换 | 读 L13；CI `ci.yml:211,350` 同串 | B7 批 | 候审 |
| D13-14 | `backend/scripts/wecom_sandbox.py:29` | 声明漂移 | P2 | 注释与代码一致 | 文件头写 `Token=***` 而代码明文 `QDG6eK` | 行为等价替换 | 读 L4-5 vs L29 | 清理批 | 确认 |
| D13-15 | `deploy/scripts/preflight_pack.ps1:183` | 声明漂移 | P2 | 检查项指向真实证书 | 查 `client/ci-test.jks`，真证书在仓外 | 行为等价替换 | 读 L183 vs RUNBOOK L14 | B-CI 批 | 确认 |
| D13-16 | `scripts/smoke_cos.py:43` | 硬编码路径 | P2 | 占位语义明确 | `SCREENSHOT_DIR` 默认 `"<LOCAL_SCREENSHOTS_DIR>"` 恒不存在→静默回退 | 行为等价替换 | 读 L43-44 | B7 批 | 候审 |
| D13-17 | `deploy/scripts/backup_pg.sh`（全） vs `scripts/backup_pg.ps1`（全） | 重复 | P1 | 备份语义单源 | 同"每日 PG 备份"两套：轮转/校验口径不同 | 行为等价替换 | 两文件保留策略 L91 vs L48 | B7 批 | 确认 |
| D13-18 | `bootstrap.sh:14-21` · `deploy_one.sh:22-32` · `healthcheck.sh:16-26` · `backup_pg.sh:23-38` · `pull_models.sh:17-27` | 重复 | P1 | 部署脚本样板单源 | `SCRIPT_DIR/REPO_ROOT`+`log/warn/die`+`.env` grep 五份手抄 | 新增工具 | 五处逐行比对 | B7 批 | 确认 |
| D13-19 | `_merge_l0_refine.py` vs `_merge_l1_refine.py` | 重复 | P2 | 合并逻辑单源 | 同语义（读片段→按 id 对齐→替换→追加 note→写回→断言） | 删码(需证死) | 两文件 L19-70 / L30-106 | 清理批 | 确认 |
| D13-20 | `smoke_cos*.py` / `check_dashscope*.py` / `build_image_index.py` / `eval_image_search.py` / `run_wer_bench.py` / `gen_agg_fixtures.py` 等 ≥10 处 | 重复 | P2 | 脚本引导单源 | `sys.path.insert(backend)` + `reconfigure(utf-8)` 头部逐份复制 | 新增工具 | 各文件前 30 行 | 清理批 | 确认 |
| D13-21 | `docker-compose.yml:149-153` vs `backend/app/core/config.py:34-40` | 声明漂移 | P1 | app profile 容器真能连依赖 | 覆盖 `POSTGRES_HOST` 等，但 config 只认 `DATABASE_URL` → 覆盖失效 | 行为等价替换 | config.py 仅 database_url/redis_url/qdrant_url | B7 批 | 确认 |
| D13-22 | `deploy/Dockerfile.backend:39` vs L9-10 注释 | 声明漂移 | P1 | 镜像不含模型/venv | 无 `.dockerignore`，`COPY backend/ ./` 会烘入 models/.venv/storage | 行为等价替换 | Test-Path .dockerignore=False；.gitignore:79,148 | B7 批 | 确认 |
| D13-23 | `Dockerfile.backend:45` vs `docker-compose.yml:159` | 声明漂移 | P2 | HF 缓存挂载生效 | 镜像 `USER appuser` 而挂载点指向 `/root/.cache` | 行为等价替换 | 读两处 | B7 批 | 确认 |
| D13-24 | `deploy/scripts/bootstrap.sh:124-125` vs `systemd/*.service:21` | 声明漂移 | P1 | 迁移库=运行库 | 迁移读 `backend/.env`，运行读 `deploy/.env` | 行为等价替换 | 读 L124 注释与 unit L21 | B7 批 | 确认 |
| D13-25 | `deploy/RUNBOOK.md:151-161` | 门禁缺口 | P1 | 定时任务随部署落地 | 声称"系统 cron"但仓内无 crontab/timer 交付件 | 新增工具 | `Get-ChildItem -Include *.timer,*crontab*`=0 | B7 批 | 确认 |
| D13-26 | `deploy/README.md:16-20` | 声明漂移 | P2 | 目录树与实物一致 | `scripts/` 只列 4 个，漏 backup_pg.sh/check_env_template.py/preflight_pack.ps1 | 行为等价替换 | 读 L16-20 vs 实目录 | 清理批 | 确认 |
| D13-27 | `scripts/e2e_agent_chat.ps1:46-58,12` | 声明漂移 | P2 | 端口口径一致 | 用 8000（与 ONE_SWAP「固定 8010」冲突）且依赖排除域 `agent/` | 行为等价替换 | 读 L47,58（8300/8000） | 清理批 | 候审 |
| D13-28 | `scripts/eval_image_search.py:4` | 声明漂移 | P2 | 文档与代码一致 | docstring 写 `content_types=[image]`，代码 L42 用 `["photo"]` | 行为等价替换 | 读 L4 vs L42 | 清理批 | 确认 |
| D13-29 | `scripts/realdevice/r2_4b_verify.ps1:5-7,57` | 硬编码路径 | P2 | 电池组可移植 | 硬编码设备序列号 `.wt/fix4b` 路径与 `aapt` | 行为等价替换 | 读 L5-7 | 清理批 | 候审 |
| D13-30 | `backend/scripts/*`(10) 与 `scripts/*` 同名目录 | 边界方向 | P2 | 消除 `scripts.*` 同名歧义 | 两目录均无 `__init__.py`，`import scripts.X` 由 sys.path 顺序决定 | 新增工具 | test_cli_scripts.py:22-26 依赖此路径注入 | B7 批 | 确认 |

### 关键条目证据

**D13-1（CI 客户端 tsc 门禁已被 UTS 迁移掏空）**：读 `ci.yml:50-73`，临时 tsconfig 的 `"include"` 为 `["env.d.ts", "../../client/utils/*.ts"]`；而 `client/utils/` 下现存 `auth.uts`（`Glob client/utils/auth.*` → 仅 `.uts`），5.24 迁移已把 `.ts`→`.uts`。`tsc` 匹配 0 个文件 → `code=0` → 打印 `client-tsc 通过（0 errors）`（ci.yml:188-190）。该门禁本已 `continue-on-error: true`，现更是**恒真通过**。`scripts/audit_harness.py` 轴2 只对 `client/**` 查 `.ts` 残留（L253-267），**不覆盖 `.github/workflows/` 与 `scripts/*.mjs`**，故无人发现。

**D13-2（single-flight 单测已失效）**：`test_auth_singleflight.mjs:26` 的 registerHooks 仅把相对导入补成 `base + '.ts'`；L87/L96/L120/L130 均 `import('./auth.ts', CLIENT_UTILS)`。仓库内该文件已改名 `auth.uts`（同 D13-1 证据）→ 解析失败。外部引用检索：`Select-String -Pattern test_auth_singleflight` 全仓仅命中 `docs/lessons.md:1071`，**任何 CI/workflow 均未调度它**。即"客户端唯一 JS 单测"事实上无人跑、且已跑不通。

**D13-3（环境模板自证脚本无门禁）**：`check_env_template.py:33-74` 是"模板↔config 字段对齐"的唯一判据（文件头 L4-6 自述"此前只是口头承诺"）。全仓检索 `check_env_template` 仅命中 `docs/` 与别的域审计报告，`ci.yml`/`review_agent.py` 均无调用 → 该判据回到"靠人记得跑"。

**D13-4（安全审计的备份域指向开发件）**：`audit_security.py:102` `backup_script = REPO/"scripts"/"backup_pg.ps1"`，L108 `dumps = sorted((REPO/"backups").glob("*.dump"))`。而生产备份件是 `deploy/scripts/backup_pg.sh`（写入 `deploy/backup/pg/`，见该文件 L28）。`fail = age_hours>24 and newest>0`（L115）→ 仓库 `backups/` 无 dump 时 `newest=0`，**永远 fail=False**。RPO≤24h 检查在生产备份陈旧时也不会报警。

**D13-17（备份双实现）**：`deploy/scripts/backup_pg.sh`：`pg_dump -Fc` → `.part` 原子改名 → `pg_restore -l` 结构性校验（L53-74）→ `-mtime +KEEP_DAYS` 轮转（L91）→ `pg_stat_archiver` 告警（L78-88）。`scripts/backup_pg.ps1`：`pg_dump -Fc` → SHA256 旁挂文件（L43-45）→ 按**个数**保留 7 份（L48）。两者同语义（每日 PG 备份，参数名 `DbName/DbUser/KEEP_DAYS` 近似）却异实现：轮转口径（时间 vs 个数）、校验手段（pg_restore vs SHA256）不一致。

**D13-18（部署脚本样板五份手抄）**：`grep` 同形片段确认五处：`bootstrap.sh:14-21`、`deploy_one.sh:22-32`、`healthcheck.sh:16-26`、`backup_pg.sh:23-38`、`pull_models.sh:17-27` 各自重复 `SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"` + `DEPLOY_DIR/REPO_ROOT` 推导 + `log/warn/die` 三函数 + `grep -E '^POSTGRES_USER=' "${DEPLOY_DIR}/.env" | tail -1 | cut -d= -f2-` 的 `.env` 解析（后者在 bootstrap/healthcheck/backup 三份重复）。无语义差异，纯复制粘贴式新增范式。

**D13-21（compose 环境覆盖失效）**：`docker-compose.yml:149-153` 注释自称"覆盖宿主侧 URL：容器内走服务名"，实际只设 `POSTGRES_HOST/REDIS_HOST/QDRANT_HOST`；`config.py:34-40` 仅定义 `database_url/redis_url/qdrant_url`（无 `*_HOST` 字段）→ 容器仍读 `.env` 里 `127.0.0.1` 形态 URL，`--profile app` 起后端将连不上依赖。旁证：`check_env_template.py:27-28` 把 `POSTGRES_` 前缀整族**豁免**为"非 config 键"，故此漂移恰好落在该自证脚本的盲区外。

**D13-22（Dockerfile COPY 范围 vs 注释承诺）**：`Dockerfile.backend:8-10` 明写"**不把模型烘进镜像**：BGE-M3+SetFit+SenseVoice 以只读挂载进入"；L39 却是 `COPY backend/ ./`（全包）。`Test-Path .dockerignore` = **False**，`.gitignore:79`(backend/models/)、`:148`(backend/.venv/)、`:110`(backend/data/storage/) 说明这些目录常见于本地构建上下文 → 会被一并烘入镜像，与注释及 compose 只读挂载（L158）的目的相悖。

**D13-24（迁移库 ≠ 运行库）**：`bootstrap.sh:124` 注释"运行 alembic upgrade head（... DATABASE_URL 读自 backend/.env）"，L125 实为 `( cd backend && python -m alembic upgrade head )`（靠 backend/.env/环境变量）；而 API 与 worker 由 systemd `EnvironmentFile=@DEPLOY_ROOT@/deploy/.env`（`yishu-api.service:21`）驱动。两份 URL 若不指向同实例，则"迁移已跑"与"运行库缺表"同时为真——正是 compose 头部 L9-13「两份 URL 必须指向同一实例」所警示的失误。

**D13-25（RUNBOOK 定时任务无交付件）**：`RUNBOOK.md:151-161` 表格列出每日 22:00 `daily_review.py`、03:30 `backup_pg.sh`、30 天清理、孤儿扫描、季度演练，并注明"系统 cron，不进代码调度"、验证手段 `crontab -l`。仓库检索 timer/crontab 文件 = **0**（唯一 systemd 单元是 api/worker 两个 `.service`，无 `.timer`），`deploy_one.sh` 亦不安装任何定时任务。→ 换新机时"部署完成"与"定时任务已装"之间存在人工缝隙。

**D13-30（两个 `scripts` 目录同名歧义）**：`scripts/` 与 `backend/scripts/` 均无 `__init__.py`（`git ls-files` 输出无 `__init__.py`），二者在 Python 命名空间下都映射为 `scripts.*`。`backend/tests/test_cli_scripts.py:24-26` 先插 `_ROOT` 再插 `_BACKEND`（后者落到 sys.path[0]），故 `from scripts.wecom_sandbox import ...`（L38）实际命中 `backend/scripts/wecom_sandbox.py`；而 `from scripts.run_wer_bench import ...`（L94）命中 `scripts/run_wer_bench.py`。同一模块名指向两目录，解析结果依赖插入顺序——新增同名文件即静默改变解析目标。

---

## 依赖方向与抽象覆盖度结论

**FF1 · 本域是否存在反向依赖？——存在 1 处（app 包 → 仓库根工具目录）**
- 边：`backend/app/services/event_aggregation/generate_test_photos.py:17` `from scripts.agg_generate_photos import generate`；`load_real_photos.py:17` `from scripts.agg_load_real_photos import RawPhoto, load_screenshots, sample_500`（两者均为 R1#14 迁移 shim，逻辑已迁至 `scripts/`）。
- 反向性：**运行时包（backend/app）反向依赖开发工具目录（仓库根 scripts/）**；跨层方向与"开发脚本不入运行时代码包"的迁移意图相反（shim 文件头 L5 自述该意图）。
- 部署后果（与 D13-22 COPY 范围叠加）：镜像内 `/app` 只有 `backend/` 内容（`COPY backend/ ./`），shim 用 `parents[4]`（= 容器内 `/`）把 `/` 入 path 后 `import scripts.*` 将 ImportError → 容器内该模块不可用。`--preload`/被 `run_validation` 引用时会暴露。
- 其余方向正常：`deploy/**` 只经 CLI/环境变量调用后端，无 import 边。

**FF2 · 新增"一个脚本/一个部署件"需改哪几处？——注册点全列（脚本生态无注册表，靠文档与 CI 手工登记）**
- 新增**客户端静态门禁类**脚本 → 需改：① `.github/workflows/ci.yml`（加 step）② 本域 `deploy/README.md`/`RUNBOOK.md` 若涉部署则同步 ③ `scripts/review_agent.py` 若欲入提交门禁（`main()` L356-364 的 checks dict）。
- 新增**deploy 脚本** → 需改：① `deploy/README.md:16-20` 目录布局 ② `RUNBOOK.md` 对应步骤段 ③ 若为部署序一环则 `deploy_one.sh:55-85` 串接 ④ `deploy/scripts/bootstrap.sh:145-153` 的"下一步"提示。
- 新增**env 变量** → 需改：① `deploy/.env.production.template` ② `deploy/README.md` §3 表格（L42-47）③ 若属 config 字段则 `backend/app/core/config.py` ④ 自证 `check_env_template.py` 自动覆盖（唯一自动化点）。`docker-compose.yml` 头部 L9 注明"改一处须同步另一处"。
- 新增**systemd 单元** → 需改：① `deploy/systemd/*.service`（占位符 `@DEPLOY_ROOT@`/`@RUN_USER@`）② `deploy_one.sh:77-78` `install_systemd` 两次调用需加第三次 ③ `RUNBOOK.md` §3 进程铁律表。
- **结论**：本域**无注册表抽象**，全部靠"文档两处 + 脚本一处"人工同步；目前只有 env 模板一处有自动判据（且未接门禁，见 D13-3）。

**FF3 · 幂等性现状（逐部署件结论）**
- 幂等已声明且实现合理：`bootstrap.sh`（`mkdir -p` / `compose up -d` / `CREATE EXTENSION IF NOT EXISTS` / `upgrade head` / 只读表断言）、`pull_models.sh`（目录非空即 SKIP）、`deploy_one.sh`（`sed` 渲染 + `daemon-reload` + `restart` 而非 enable+start 叠加）、`healthcheck.sh`（纯只读 7 项）、`backup_pg.sh`（`.part` 原子改名 + `-mtime` 轮转）。
- **不幂等**：`reinject_missing_photos.py`、`seed_echo_today.py`（随机抽图 + 新增 contents 行，无 upsert 键）；`_merge_*` / `_expand_*`（原地覆盖主 JSON，无 dry-run、断言硬编码）。
- 复跑风险点：`_merge_l1_refine.py` 断言 `len(d2)==193`（L93）与 `_merge_l0_refine.py` 断言 `==51`（L68）——对输入规模硬编码，重跑时若规模变化即 AssertionError。

**FF4 · 硬编码凭据/路径/bucket 域名总览**
- **bucket/域名**：本域**未发现**硬编码 COS bucket 名、`myqcloud.com`、`cos.ap-*` 域名或生产 IP（`smoke_cos.py` 只打印 `settings.cos_bucket`，L38）。`deploy/.env.production.template:58` 用 `<公网IP>` 占位、`ONE_SWAP.md` 明令"模板保持占位"。→ **该项查无问题**。
- **凭据**：仅 `setup_pg.sql:13`（dev 口令）与 `wecom_sandbox.py:29`（官方文档测试值）两处明文，均非生产密钥（见 D13-13/D13-14）。
- **硬编码路径**：4 处机器绑定（D13-9～D13-12）+ 2 处占位（D13-16）+ 1 处真机序列号（D13-29）；B7 批已清大部分，`smoke_cos_upload.py:11` 为漏网。

---

## 本域已查无问题项（避免遗漏被误读）

1. **生产凭据链路合规**：`deploy/.env.production.template` 全篇**不含任何真实值**（必填项保持注释态，L16-19 说明 fail-closed 理由）；`deploy/.env` 被 `.gitignore:165` 忽略、`.env.production.template` 经 `!` 白名单豁免（`.gitignore:171`）——与 `deploy/README.md:74-84` 的验证命令一致。
2. **依赖服务端口绑定**：`docker-compose.yml` 三服务端口全部 `127.0.0.1:` 前缀（L60/92/114-115），符合"不对公网暴露"口径（L15-17）。
3. **不放大性能护栏**：`deploy/**` 无任何处设置 `SEARCH_CONCURRENCY` / DB 池参数；`RUNBOOK.md:129` 与 `deploy/README.md:72` 均显式声明"不得覆盖"，实现了"文档约束 + 无代码违背"。
4. **单进程铁律一致**：`Dockerfile.backend:49`、`docker-compose.yml:160`、`yishu-api.service:24` 三处 `--workers 1` 一致；worker unit 说明 scheduler 必要性（L4-8）。
5. **ENV 变量对齐有真源**：`check_env_template.py` 的判定规则（真缺口/过期项/豁免族）设计正确，脚本本体无缺陷——问题仅在"无人自动跑"（D13-3），非逻辑错。
6. **备份脚本自身健壮性**：`backup_pg.sh` 有半截文件防误认（`.part`→校验→`mv`，L53-74）、WAL 失败计数告警（L83-87）、恢复演练附录（L96-104）；`healthcheck.sh` 采集全部结果后统一退出（L14-15，刻意 `set -uo pipefail` 不用 `-e`）。
7. **测试照片生成器非重复**：`generate_test_photos.py`（50 张 Pillow 像素图，供上传链）与 `agg_generate_photos.py`（500 张 `RawPhoto`，供聚合算法）**语义不同**，非 D13-17 类重复；两者 docstring 已互相区分。
8. **`well-formed` 的 shim 通过测试**：`test_cli_scripts.py` 显式登记了"哪些脚本不补测及理由"（L7-12），豁免理由（需真实模型/外部服务/DB）成立。
9. **`smoke_cos.py` 敏感值纪律**：只打印 `bool(secret)` 与 bucket 名（L38-40），不回显密钥值。
10. **`preflight_pack.ps1` 的 BOM/编码约定**：文件头 L14-16 明确"本文件需 UTF-8 BOM（PowerShell 5.1）"，与 `deploy/README.md:151-168` 的 EOL=LF 约定分开陈述，未混淆。