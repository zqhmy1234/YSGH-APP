# `deploy/` 目录说明（S1 卡产出 · 2026-09-21）

> 面向**执行者/排障者**；面向运维的上机步骤与实测校验表由 S3 卡写入 `deploy/RUNBOOK.md`。
> 规格依据：`docs/服务器规格核算_20260921.md`（8核16G / 系统盘 60G / 带宽 5Mbps，COS 直传）。

## 1. 目录布局

```
deploy/
├── docker-compose.yml          # 三个依赖服务（postgres+pgvector / redis / qdrant）+ 可选 backend(profile=app)
├── Dockerfile.backend          # 后端镜像（模型不烘进镜像，只读挂载）
├── initdb/01-extensions.sql    # PG 首次初始化：CREATE EXTENSION vector（alembic 不管扩展）
├── systemd/                    # 宿主侧业务进程托管（api / worker）
│   ├── yishu-api.service
│   └── yishu-worker.service
├── scripts/
│   ├── bootstrap.sh            # 一次性初始化（幂等）：目录 → 依赖服务 → 迁移 → 表断言
│   ├── pull_models.sh          # 模型预置（幂等）：SenseVoice / BGE-M3 / SetFit 就位校验
│   ├── healthcheck.sh          # 三依赖 + API + DB 表 自证
│   ├── deploy_one.sh           # 单命令部署（幂等）：bootstrap → models → systemd → healthcheck
│   ├── backup_pg.sh            # PG 每日备份：dump(-Fc) + 非空/可读校验 + 按天轮转 + WAL 归档状态（§7 S3）
│   ├── check_env_template.py   # 模板 ↔ config.py 一致性判据（§3 契约的唯一可复现校验，S3）
│   └── preflight_pack.ps1      # 出包前置门：全绿才允许云打包（§7；含中文须以 UTF-8 BOM 保存）
├── data/                       # 【运行时生成，已 gitignore】PG/Redis/Qdrant 数据与 WAL
├── logs/                       # 【运行时生成，已 gitignore】
├── .env                        # 【不入库】容器参数 + 宿主后端环境（由 S3 的模板复制而来）
└── download/                   # 内测下载页（E1 卡域，与部署工程无关）
```

## 2. 部署拓扑（当前单机方案）

```
公网 8010 ──► nginx(可选) ──► uvicorn(app.main:app, --workers 1)   [宿主 systemd: yishu-api]
                                └── 读取 deploy/.env（DATABASE_URL/REDIS_URL/QDRANT_URL → 127.0.0.1）
                              python -m app.workers.worker           [宿主 systemd: yishu-worker]
                                └── 同一 .env；含 scheduler（Linux 完整模式）
127.0.0.1:5432 / 6379 / 6333 ──► docker compose 三依赖服务（docker 管理，重启自愈）
```

**为什么业务进程跑宿主而不是容器**：模型资产（3.8–5.3G）与推理内存（单进程 ~1.5G / ~3.3G）是资源的绝对大头，
宿主进程直跑可复用宿主磁盘上的模型目录、避免挂载与镜像层开销；容器只承载三个"无模型"的依赖服务。

## 3. `.env` 变量来源契约（**改一处须同步另一处**）

| 变量 | 归属 | 用途 |
|---|---|---|
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` / `POSTGRES_PORT` | 容器 | compose 起 PG 与 healthcheck |
| `POSTGRES_IMAGE` / `REDIS_IMAGE` / `QDRANT_IMAGE` / `*_MEM_LIMIT` | 容器 | 镜像与内存护栏（可选覆盖） |
| `DATABASE_URL` / `REDIS_URL` / `QDRANT_URL` | **宿主后端** | 指向 `127.0.0.1:5432/6379/6333`，须与上表同实例 |
| `JWT_SECRET` / `REFRESH_TOKEN_HMAC_KEY` / `MEDIA_URL_SECRET` / `SENSEVOICE_MODEL_DIR` / `STORAGE_BACKEND=cos` / `ALLOW_DEVICE_LOGIN` … | **宿主后端** | 见 `backend/.env.example` 与 S3 的生产模板 |

- `deploy/.env` 由 **S3** 的 `deploy/.env.production.template` 复制而来；`bootstrap.sh` 在缺失时会自动生成一个**仅含容器参数**的最小 `.env`（随机 PG 密码）以便先把依赖服务拉起来。
- `deploy/.env` 已被 `.gitignore` 覆盖（`.env` 规则）；**模板文件**需白名单豁免（见 §6）。

## 4. 常用命令

```bash
# 依赖服务
docker compose -f deploy/docker-compose.yml up -d
docker compose -f deploy/docker-compose.yml ps
docker compose -f deploy/docker-compose.yml logs -f postgres

# 一次性初始化 / 全量部署 / 自证
bash deploy/scripts/bootstrap.sh
bash deploy/scripts/pull_models.sh
bash deploy/scripts/deploy_one.sh
bash deploy/scripts/healthcheck.sh
```

## 5. 幂等性契约（验收项，重复执行必须无副作用）

- `bootstrap.sh`：已存在的目录不重建；`compose up -d` 天然幂等；`alembic upgrade head` 重复执行 no-op；表断言只读。
- `pull_models.sh`：模型目录非空即跳过下载（打印 SKIP）；SetFit 只校验存在性，不训练。
- `deploy_one.sh`：`sed` 渲染 systemd unit 到 `/etc/systemd/system/` 后 `daemon-reload` + `restart`（不是 enable+start 叠加）；不产生重复进程。
- **不放大护栏**：`SEARCH_CONCURRENCY=4`、DB 池 `5+10` 是 P0-2 性能修复的既有取值（`08 报告 §1.1.3`），部署脚本不得覆盖。

## 6. `.gitignore` 与"真源必须可入库"（防 D-19 伴生雷重演）

仓库根 `.gitignore` 有 `.env.*` 规则，会**连带吞掉** `deploy/.env.production.template`（S3 的真源文件）——
与 `client/AndroidManifest.xml` 当年被通配吞掉是同一类事故（文件写了、git 里却不存在 = 审查永久盲区）。
故本卡在 `.gitignore` 追加白名单豁免，并在交付报告里给出 `git check-ignore -v` 的验证输出：

```
git check-ignore -v deploy/.env                       # 应命中忽略（含真实密钥，正确）
git check-ignore -v deploy/.env.production.template   # 应不输出（真源可入库）
git check-ignore -v deploy/data/postgres              # 应命中忽略（运行时数据）
```

## 7. 与其它卡的接口

| 卡 | 接口 |
|---|---|
| S3 | ✅ **已完成（2026-09-21）**：`.env.production.template`（含 §3 容器参数节 + 与 `config.py` 对齐的自证命令）、`RUNBOOK.md`（上机手册 + 排障表 + WAL 开启步骤）、`scripts/backup_pg.sh`（dump + 校验 + 轮转 + 归档告警）、`docs/部署就绪包_20260921.md`（自 `175de68` 恢复后更新）。WAL 归档已接线但**默认 off**（`PG_ARCHIVE_MODE=on` 开启，理由与步骤见 RUNBOOK §6） |
| S2 | 新增 `ALLOW_DEVICE_LOGIN`/`POST /api/v1/auth/device` 后，需在 S3 模板与 `backend/.env.example` 同步登记该开关 |
| C3 | ✅ 可完成部分已落成（图标/启动图/自有签名证书，见 `docs/图标与签名证书_20260921.md`）；**云打包待真实地址解冻** |
| E1 | ✅ 下载页已落成 `deploy/download/`（零 JS；5 处 `[替换点]` 待 C3 出包后回填） |
| — | 🔑 **换址清单 `ONE_SWAP.md`**（换域名/服务器地址只改 2 处）；**出包前置门 `scripts/preflight_pack.ps1`**（全绿才允许打包） |

## 8. 已知环境约束（2026-09-21 实测，排障先读）

### 8.1 本套脚本**不能在 Windows 开发机上跑**

本机 `bash` 是 **GNU bash 5.2.21 (x86_64-pc-linux-gnu)**（WSL）。实测根因（**不是"没装 Docker"**）：

```bash
$ bash -c 'command -v docker; docker --version; docker compose version'
/mnt/c/Program Files/Docker/Docker/resources/bin/docker   # ← 通过 /mnt/c 互操作找到了 Windows 的 docker.exe
                                                          # ← 但 docker --version 无输出
                                                          # ← docker compose version 无输出（本次即在此外失败）
```

即：`command -v docker` **成功**（Windows 可执行文件经 `/mnt/c` 可见），但**在该 bash 内执行它无任何输出**
（WSL 下运行 Windows 可执行文件的互操作限制）。同一个命令在 PowerShell 里完全正常（Compose v5.1.3）。
**这不是脚本缺陷，是运行环境不匹配。**

判别命令（一条分清"没装 Docker" vs "WSL 互操作问题"）：

```bash
bash -c 'command -v docker; docker --version; docker compose version'
# 第一条有路径、后两条空白 → WSL 互操作问题（本机情况）
# 第一条也空白               → 真的没装/不在 PATH
```

- ✅ 可在开发机做的验证：`docker compose -f deploy/docker-compose.yml config --quiet`（只解析不连 daemon）、
  `bash -n deploy/scripts/*.sh`（语法）、`git check-ignore`（忽略规则）。
- ❌ 必须在目标 Linux 服务器做的验证：`compose up -d` → 迁移 → `healthcheck.sh` 七项全绿。
- 为让两种失败可区分，`bootstrap.sh` 前置检查已拆成三类报错：CLI 不存在 / 非 Compose v2 / daemon 未运行。

### 8.2 开发机 5432 被原生 PostgreSQL 占用

本机有**原生 PG 服务**监听 5432（`Get-NetTCPConnection -LocalPort 5432` 可见），若在此机强行
`compose up -d`，postgres 容器会因端口冲突起不来。目标服务器上无此问题；若某台既有原生 PG 又想用
容器，改 `deploy/.env` 的 `POSTGRES_PORT`（或先停原生服务），并同步改后端的 `DATABASE_URL`。

### 8.3 未被本卡验证的项（**如实声明，勿当已完成**）

| 项 | 状态 | 谁来验 |
|---|---|---|
| `compose up -d` 三容器实际起得来 | ⬜ 未跑（本机 daemon 未运行/端口冲突） | 上机执行 |
| `alembic upgrade head` 在本 PG16+pgvector 上跑通 | ⬜ 未跑 | 上机执行 |
| `healthcheck.sh` 七项全绿 | ⬜ 未跑（前置未满足） | 上机执行 |
| `bootstrap.sh` 重复执行幂等 | 🟡 静态自证（dir -p / compose up -d / upgrade head 均天然幂等）；实测待上机 | 上机执行 |
| API/worker 进程 RSS、磁盘分账 | ⬜ 未测 | 上机回填 `docs/服务器规格核算_20260921.md` §7 |

### 8.4 待登记教训（主窗 commit 前必跑）

```bash
python scripts/lessons.py add \
  --error "deploy/*.sh 在 Windows 开发机上执行报「未找到 docker compose」，而 PowerShell 里 docker compose version 正常" \
  --root-cause "本机 bash 为 GNU/Linux bash（WSL），PATH 与 Windows 进程隔离，看不到 docker.exe；报错文本与「未装 Docker」完全同形，误导排查方向"
```
（登记动作归主窗——`docs/lessons.md` 不在 S1 卡白名单内。）

### 8.5 换行符（EOL）：`deploy/**` 文本必须 LF

- **实测（2026-09-21）**：新建的 `.service` / `.sql` / `Dockerfile` 默认落成 CRLF。其中 **Dockerfile 是致命的**——
  `RUN apt-get update \<换行>` 这类续行若变成 `...\` + `\r`，构建器会把 `\r` 当作指令内容 → **构建失败**；
  systemd 与 psql 虽能容忍行尾 `\r`，但统一 LF 才能消除"本地能跑、上机怪"的一类歧义。
- 本次已把 `deploy/` 下 **10 个文本文件全部归一为 LF** 并做全量复检（含 `Dockerfile.backend`）。
- **复发风险（建议主窗处理）**：仓库 `.gitattributes` 只立了 `*.sh` / `*.py eol=lf`（决策台账 §4.8），
  **未覆盖** `*.service` / `Dockerfile*` / `*.yml` / `*.sql` / `*.template`。`.gitattributes` 不在 S1 卡白名单内，
  建议追加一行，使 Windows 侧编辑也无法把 CRLF 带进提交：

  ```gitattributes
  deploy/** text eol=lf
  ```

- 自查命令（PowerShell，应无 CRLF 输出）：

  ```powershell
  Get-ChildItem -Recurse -File deploy | ForEach-Object { $c=[IO.File]::ReadAllText($_.FullName); if ($c.Contains([char]13)) { "CRLF $($_.Name)" } }
  ```
