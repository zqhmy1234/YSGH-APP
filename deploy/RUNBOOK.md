# 忆述光华 · 上机部署手册（RUNBOOK）

> 生成：2026-09-21（S3 卡）｜面向：第一次把这套系统部署到那台新服务器上的人
> 配套：`deploy/README.md`（目录/拓扑/变量契约）· `docs/服务器规格核算_20260921.md`（规格与实测校验表）· `docs/部署就绪包_20260921.md`（检查清单）
> 原则：**每一步都有验证命令**；验证不过不要往下走。密钥只从 Infisical 取，永不落库、永不回显。

---

## 0. 前置（用户手工，Agent 不能代劳）

1. 买主机：**8 核 16G / 系统盘 60G / 带宽 5Mbps**（中国内地节点，包年包月直接选 1 年）；安全组只放行 **22 + 8010**。
   → **买完先填 `deploy/SERVER_INTAKE.md` 回报单**（§A 访问与网络 + §C 一键采集命令）；信息齐了才能开工，缺项会卡在下面的步骤上。
2. 域名 + 实名认证；**并行提交 ICP 备案**（法定 20 工作日，不阻塞内测）。
3. 生成并保管**自有签名证书**，记录证书 MD5（备案要用）。已生成：`%USERPROFILE%\.yishu-signing\yishu-guanghua-beta.jks`（alias `yishu`，MD5 见 `docs/图标与签名证书_20260921.md`）；**密码须已转入密码管理器**。
4. 确认 Infisical 生产密钥条目可读（`infisical secrets --env=prod`）。
5. 系统准备：Docker Engine + Compose v2、**Python 3.11**、`git`、`openssl`（发行版版本差异的后果见回报单 §B6）。
6. **代码上机（已拍板 2026-09-23）**：走「**工作树打包直传**」，**不用 `git clone`**——本批改动尚未提交、`origin` 里没有 `deploy/`，clone 只会拿到旧代码（连本手册都不存在）。命令见 §1.1；执行方 = Agent 从本机 SSH 驱动。
7. **拿到地址后 → 照 `deploy/ONE_SWAP.md` 走**（换址只有 2 处：客户端 `config.uts` 的 `PROD_BASE_URL` + 服务器 `deploy/.env` 的 `BASE_URL`）；
   换完跑 `powershell -ExecutionPolicy Bypass -File deploy/scripts/preflight_pack.ps1`，**全绿（退出码 0）才允许打包**。

```bash
# 验证
docker compose version && python3 --version && openssl version
```

---

## 1. 首次上机（按序执行，每步带验证）

### 1.1 代码上机 + 建 venv

**不用 `git clone`**（已拍板 2026-09-23）：本批改动（**含整个 `deploy/` 目录**）尚未提交，`origin` 里没有它们 —— clone 只会拿到 `65f40a3` 的旧代码，**连本手册自己都不存在**。改为**工作树打包直传**：

```powershell
# ① 本机（Windows，bsdtar/ssh/scp 均已确认可用）：打包整棵工作树，排除构建产物与模型（模型在服务器上拉）
$tar = Join-Path $env:TEMP 'yishu-deploy.tar.gz'
Set-Location D:\GuangH-App
tar -czf $tar --exclude=client/unpackage --exclude=client/.hbuilderx --exclude=backend/.venv `
  --exclude=backend/models --exclude=.git --exclude=node_modules --exclude=_verify_0905 `
  --exclude=deploy/data --exclude=deploy/logs .
Get-FileHash $tar -Algorithm SHA256    # 记下摘要，服务器侧比对
(Get-Item $tar).Length / 1MB           # 心里有数（预期几十 MB 量级）
# ② 上传
scp -P <SSH端口> $tar <用户>@<公网IP>:/tmp/
```

```bash
# ③ 服务器
sha256sum /tmp/yishu-deploy.tar.gz                # 与 ① 摘要比对；不一致 = 传输损坏，重传
mkdir -p /srv/guangh && tar -xzf /tmp/yishu-deploy.tar.gz -C /srv/guangh
cd /srv/guangh
ls deploy/RUNBOOK.md backend/requirements.txt backend/app/main.py   # 关键文件确实到位（防"打包漏了"）
python3 -m venv backend/.venv
backend/.venv/bin/pip install -r backend/requirements.txt           # 含 torch，约 4–8G 磁盘
```

验证：`backend/.venv/bin/python -c "import fastapi, torch, sentence_transformers, setfit; print('ok')"`

> **服务器上没有 git 历史**（内测期够用）。后续增量更新：重新打包上传覆盖；若要改走 git 流，见 `deploy/ONE_SWAP.md` 与决策台账。

### 1.2 填 `deploy/.env`

```bash
cp deploy/.env.production.template deploy/.env && chmod 600 deploy/.env
infisical run --env=prod -- <你的填充方式>   # 或手工按注释填；**只从 Infisical 取真实值**
```

⚠️ 必填项在模板里是**注释状态**，请显式取消注释并填值——但**不要留成 `KEY=`（空值）**：
`config.py` 的生产兜底拦「默认字符串」，**拦不住空串**（见 §8 已知缺口）。保持注释 → 启动时拒绝启动，才是安全的失败方式。

验证：`grep -c '^[A-Z].*=' deploy/.env` 有输出、`git check-ignore -q deploy/.env` 退出码 0（被忽略）。

### 1.3 起依赖服务 + 迁移

```bash
bash deploy/scripts/bootstrap.sh
```

它做四件事并各自验证：建运行时目录 → 起 postgres/redis/qdrant → 等健康 → `alembic upgrade head` + **断言 6 张关键表存在**。
验证：脚本末尾打印「关键表齐全 ✅」；`docker compose -f deploy/docker-compose.yml ps` 三服务 healthy。

### 1.4 预置模型（约 3.8–5.3G）

```bash
bash deploy/scripts/pull_models.sh
```

验证：脚本打印 SenseVoice 校验通过 + SetFit 已就位；`du -sh backend/models ~/.cache/huggingface` 量级符合核算书 §4。
⚠️ SetFit 目录缺失时分类会**静默降级 mixed**（不 crash 但失去分类能力）——脚本只告警不自动训练（训练依赖真值数据，属人工决策）。

### 1.5 起业务进程 + 自证

```bash
bash deploy/scripts/deploy_one.sh      # bootstrap + models + 渲染 systemd + 重启 + healthcheck
```

验证：`healthcheck.sh` **7/7 PASS**（容器、PG、pgvector 扩展、关键表、Redis、Qdrant、API）。
若失败 → §7 排障表。

---

## 2. 密钥注入纪律

| 项 | 怎么来 | 注意 |
|---|---|---|
| `JWT_SECRET` / `REFRESH_TOKEN_HMAC_KEY` / `MEDIA_URL_SECRET` | 本机 `openssl rand -hex 32`，各自独立 | **三项不可相同**；留空/留默认 → 生产启动拒绝（前两项由 config 兜底，第三项留空会回退 jwt_secret，削弱泄漏面隔离） |
| COS / 百炼 / 高德 / 百度 / Sentry / 阿里内容安全 | Infisical | `skills/infisical-secrets/SKILL.md` |
| 短信 / uni-push / 企微 / 微信开放平台 | 到货后 | 后端已有 501 门控，不阻塞内测 |

验证（**不打印值**）：
```bash
python3 - <<'PY'
import sys; sys.path.insert(0,'backend')
from app.core.config import settings
for k in ("jwt_secret","refresh_token_hmac_key","media_url_secret"):
    v = getattr(settings,k)
    print(k, "SET" if v and not v.startswith("change-me") else "*** MISSING/DEFAULT ***", "len=", len(v or ""))
PY
```

---

## 3. 进程与规格铁律

| 铁律 | 理由 | 违反后果 |
|---|---|---|
| `uvicorn --workers 1` | 模型是进程内单例、不跨进程共享 | 每多 1 个 worker 多 ~1.2G（BGE-M3），16G 机器 OOM |
| RQ worker 单进程且 `with_scheduler=True` | 只 1 进程持 scheduler 锁即可（多进程自动互斥）；缺 scheduler → Retry 永不重投 | 失败任务静默不重试 |
| 不改 `SEARCH_CONCURRENCY=4` / DB 池 `5+10` | P0-2 性能修复的既有护栏 | 搜索雪崩 / 连接池打满 |
| `torch` 线程保持 4 | 代码写死 `OMP_NUM_THREADS=4`；开满核内存峰值超限 | 实测 16G 机器 OOM |

验证：见 `docs/服务器规格核算_20260921.md` §7 第 2/3/4 项（`ps -o rss=` 与线程数）。

---

## 4. 规格实测回填（上机后立刻做，10 分钟）

按 `docs/服务器规格核算_20260921.md` **§7 表**逐项执行并回填 8 个实测值：

```bash
free -m                                                     # 1 内存/swap
ps -o rss= -p "$(pgrep -f 'uvicorn app.main:app')"           # 2 API RSS
ps -o rss= -p "$(pgrep -f 'app.workers.worker')"             # 3 worker RSS
du -sh backend/models ~/.cache/huggingface                   # 5 磁盘分账
```

> 估算与实测偏差 **>30%** → 核算书须出修订版（已在该文写明）。这是"规格结论是否可信"的唯一验收闸。

---

## 5. 定时任务（系统 cron，不进代码调度）

| 时间 | 任务 | 命令 | 说明 |
|---|---|---|---|
| 每日 22:00 | 每日复盘生成 | `python backend/scripts/daily_review.py` | 幂等；**决策台账 §2.6**：系统 cron 而非代码内调度 |
| 每日 03:30 | PG 备份 | `bash deploy/scripts/backup_pg.sh` | 见 `deploy/scripts/backup_pg.sh` 头部 cron 示例；RPO ≤24h |
| 每日（低峰） | 30 天软删物理清理 | `backend/app/workers/cleanup_job.py` | **首跑先 dry-run** 确认影响范围 |
| 每周 | 孤儿对象扫描 | `orphan_scan` | **首跑先 dry-run** |
| 每月/季度 | 恢复演练 | 见 `backup_pg.sh` 末尾附录 | 季度全流程演练（RTO ≤4h） |

验证：`crontab -l`；`tail -20 deploy/logs/backup.log` 出现「备份完成」+「归档结构完整」。

---

## 6. WAL 归档 / PITR（**默认关闭**，按需开启）

**当前口径**：RPO ≤24h 由 §5 的每日 dump 满足（内测期够用）。WAL（RPO ≤5min）**已实现但默认关闭**，原因：
`archive_command` 若因目录属主/磁盘满而失败，PG 会持续保留 WAL 直至 `pg_wal` 打满磁盘——
**"未验证就默认开启"比"默认关闭 + 明确开启步骤"更危险**。

开启步骤（4 步，逐步验证）：

```bash
# 1) 建归档目录并交给 postgres（官方镜像 uid=999）——属主不对是最常见的失败原因
mkdir -p deploy/data/wal-archive
docker compose -f deploy/docker-compose.yml run --rm -T --user root \
  --entrypoint sh postgres -c "mkdir -p /var/lib/postgresql/wal-archive && chown 999:999 /var/lib/postgresql/wal-archive && chmod 700 /var/lib/postgresql/wal-archive"
# 2) 打开开关
echo 'PG_ARCHIVE_MODE=on' >> deploy/.env
# 3) 重建 PG 容器（archive_mode 需重启生效）
docker compose -f deploy/docker-compose.yml up -d postgres
# 4) 验证：强制切一个 WAL 并看归档计数增长
docker compose -f deploy/docker-compose.yml exec -T postgres \
  psql -U postgres -d yishu -c "SELECT pg_switch_wal();" \
  -c "SELECT archived_count, failed_count, last_archived_wal FROM pg_stat_archiver;"
ls -lh deploy/data/wal-archive | tail -3
```

判定：`failed_count=0` 且 `archived_count` 随 `pg_switch_wal()` 增长 + 目录里有文件 = PITR 生效。
**失败计数非 0 就必须立刻处理**（`deploy/scripts/backup_pg.sh` 每次会检查并告警）。

---

## 7. 排障表

| 现象 | 先查 | 处置 |
|---|---|---|
| `healthcheck` 第 1 项 FAIL | `docker compose ... ps` | 容器没起 → `up -d`；Docker daemon 没跑 → 启动 dockerd |
| PG 容器起不来 | `logs postgres` | 端口冲突（宿主已有原生 PG）→ 改 `POSTGRES_PORT` 并同步 `DATABASE_URL` |
| 第 3 项 pgvector FAIL | — | `CREATE EXTENSION vector;`（initdb 只在空数据目录执行；已有数据的库需手工补） |
| 第 4 项 关键表 FAIL | `alembic upgrade head` 输出 | 迁移未跑 / `DATABASE_URL` 指向了别的实例 |
| 第 7 项 API FAIL | `systemctl status yishu-api` | 生产密钥缺失 → 启动时抛 `RuntimeError`（**期望行为**，按 §2 补齐）；`systemd` unit 里 `@DEPLOY_ROOT@` 未渲染 |
| 客户端连不上（HTTP 0） | 安全组 / `ss -ltnp \| grep 8010` / 客户端 `PROD_BASE_URL` | 安全组未放行 8010；或地址/端口写错（端口固定 8010） |
| 录音/上传报错 | `backend/.env` 的 `STORAGE_BACKEND` 与 COS 五变量 | 切 `cos` 后缺 key 会在生产分支直接 5xx（不静默降级） |
| 分类结果全是「混合」 | `backend/models/setfit-classifier/` | 模型缺失 → 降级 mixed；重跑 `pull_models.sh` 并人工确认该目录 |
| 搜索/上传后无后续处理 | `systemctl status yishu-worker` | worker 未起 → 管线只入队不消费 |

---

## 8. 已知缺口与上架前回收清单

| # | 项 | 现状 | 处置 |
|---|---|---|---|
| 1 | **空串密钥不被生产兜底拦截** | `_apply_production_safety` 只拦「默认字符串」，`JWT_SECRET=`（空）会让服务带空签名密钥启动 | **本卡只登记不改码**（`config.py` 属别的卡域）。规避：模板必填项保持注释状态；**建议主窗安派**一次加固（拒绝空/短于 32 字节） |
| 2 | 明文访问 `http://IP:8010` + `usesCleartextTraffic` | 内测期临时口径，省备案等待 | 上架前切 HTTPS 证书并移除开关（决策台账 §1.13） |
| 3 | `ALLOW_DEVICE_LOGIN=true` | 内测期设备码登录通道 | 上架前置回 `false`（决策台账 §1.13） |
| 4 | `DEV_PAGE_ENABLED` / Sentry / 压测门禁 6 项未达标 | 见 `GAP_ANALYSIS_20260903_AppStore上架差距评估.md` | 上架前逐项清零 |
| 5 | **本手册中所有命令未经真机执行** | 本机 Docker/WSL 环境不可用（`deploy/README.md` §8.1） | 上机时按 §1 逐步跑，失败项回填到本文与 README §8.3 |
