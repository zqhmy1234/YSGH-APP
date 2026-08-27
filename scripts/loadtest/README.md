# scripts/loadtest · 并发压测（Agent C1，MVP 收尾 · 性能压测域）

针对 **§7.1 性能门禁 18 项中"并发压测 100 并发 / P95 曲线"**（报告 §7.2 U7，唯一
非数据/非 key 阻塞项）。压测对象 = 本地后端（uvicorn + Docker redis/qdrant + PG yishu）。

## 测什么（4 条既有 openapi 路径，无新契约）

| # | 路径 | 方法 | 请求 | 说明 |
|---|---|---|---|---|
| P1 | `/api/v1/auth/wechat` | POST | `{"code": "loadtest-p1-<n>", "device_id": ...}` | 认证链路（dev mock 通道，10 个 code 轮换） |
| P2 | `/api/v1/events/timeline` | GET | `?level=1&status=confirmed` | 核心读路径（聚合 + `_batch_counts` 计数批量） |
| P3 | `/api/v1/search` | POST | `{"q": <查询词>, "limit": 20}` | 描述性检索（召回+重排+LLM 精排，**门禁路径**） |
| P4 | `/api/v1/search/image` | POST | multipart 图片（3 张小图轮换） | 以图搜图（caption 缓存命中/未命中两态） |

**不压**（README 排除理由）：上传链路（multipart 分片重 IO，非本门禁项）、ASR 转写
（模型推理重、限流 20/40 低，另属功能门禁）、微信/短信（key 阻塞）。

## 指标（全部产出）

延迟分位 P50/P90/**P95**/P99/AVG/MAX(ms) + QPS + 错误率 + 超时率 + 429 占比 + 成功率；
曲线 `results/p95_curve_<tag>.png`、`results/qps_curve_<tag>.png`。

门禁对照：**search P3/P4 的 P95 < 3000ms（M1/M2 硬门禁，真实档 100 并发）**；
错误率 < 1%、成功率 ≥ 99%、超时率 < 0.1%（100 并发档）。

## 前置环境（必做）

1. Docker 容器：`yishu-redis`(6379)、`yishu-qdrant`(6333-6334) 已启动；PG 5432 `yishu` 库可用。
2. Python 依赖（本机已装）：httpx、matplotlib（曲线）、PIL + numpy（造图）、psutil（资源采样，可选）。
3. 后端依赖可用（可直接 `python -c "import app.main"`，脚本复用 backend 目录）。

## 一键跑法（3 步）

```powershell
# ① 起后端（压测态：非 reload，贴近生产；限流放宽——env 注入，不碰 backend/.env，测后还原=杀掉进程即还原）
#    mock 档（验证吞吐/稳定性/连接池，排除外部依赖抖动）：
$env:MOCK_EXTERNAL_AI="true";  $env:RATE_LIMIT_AUTH_IP="100000"; $env:RATE_LIMIT_AUTH_USER="100000"
$env:RATE_LIMIT_SEARCH_IP="100000"; $env:RATE_LIMIT_SEARCH_USER="100000"
cd backend; uvicorn app.main:app --port 8000
#    真实档（门禁对照档）：去掉 $env:MOCK_EXTERNAL_AI="true"（.env 已是 false，DASHSCOPE key 已配），
#    同样注入上面 4 个 RATE_LIMIT_* 环境变量后启动。

# ② 造数（隔离压测用户 loadtest-seed；幂等，已造数则跳过；--force 换新用户重新造数）
cd scripts/loadtest; python seed_data.py

# ③ 压测
python loadtest.py --mode mock --levels 1,10,50,100 --tag mock_20260828          # mock 档全梯度
python loadtest.py --mode real --levels 100 --tag real_20260828 --pid <后端PID>  # 真实档 100 并发（门禁档）
```

选跑：`--probe` 追加 200 并发探测拐点（报告标注"参考"）；`--pid <PID>` 采样后端 CPU/内存
（每 2s → `results/resource_<tag>.csv`）。日志用 `2>&1 | Tee-Object` 留存。

## 压测方法要点（与报告 §2 一致）

- **选型**：httpx.AsyncClient + asyncio.Semaphore——零新重依赖、结果可控、CI/本地一致；不用 locust。
- **并发控制**：`sem = asyncio.Semaphore(level)`；每 worker 循环取任务（路径 × 参数轮换）。
- **每档流程**：`warmup → timed run → 统计`：
  - warmup：每路径 10 次（P4 每张查询图 1 次填充 caption 缓存），**不计入统计**。
  - timed run：每档 30s（100 并发档 60s），或固定请求数（1→50 / 10→200 / 50→500 / 100→500，取先到者）。
  - 档位：**1 / 10 / 50 / 100**（1=基线校准，100=门禁档）；可选 200 探测拐点。
- **计时口径**：客户端侧（请求发出→响应完成，真实用户体验口径），不含服务端内部计时。
- **超时**：单请求 timeout=5s（httpx）；超时计入超时率、**不计入延迟分位**。
- **429 处理**：压测态限流已放宽（`RATE_LIMIT_*_IP/USER=100000`，window 60s，env 注入不提交），
  429 单独统计、不计入错误率；若出现 429（未放宽跑出来的情况），报告中标注"压测态限流已放宽"。
- **路径隔离**：每条路径独立跑（同并发档下分别压），保证"search 在 100 并发下"是真实的
  100 个并发 search 请求（而非混合负载下的 ~25 个），对硬门禁更严格；参数在路径内轮换。

## 数据准备与隔离（seed_data.py）

- 压测用户 `loadtest-seed`（dev mock 登录，unionid=mock-unionid-loadtest-seed）——与真机/冒烟数据隔离。
- 文字 80 条（含查询词库 10 个）、照片 20 张（合成小图，PIL 生成）、L1 事件 2~3 轮（events/sync）。
- 造数阶段 mock 外部 AI（零费用）；向量入生产 collection 但按 user_id 隔离，不污染他人检索。
- 压测后清理：数据留在 loadtest 用户下（与 api_smoke 历史数据同先例，残留可接受）；
  如要彻底清除需手动删除该用户内容（无批量删除 API，登记为契约需求可选）。

## 产物

```
results/run_<tag>_<level>_<path>.json  每档完整统计（含 warmup 延迟、MAX 时间戳）
results/summary_<tag>.csv              路径 × 档 分位/QPS 汇总（画图/留档）
results/p95_curve_<tag>.png            P95 曲线（X=并发档，4 路径）
results/qps_curve_<tag>.png            QPS 曲线
results/resource_<tag>.csv             后端 CPU/内存采样（--pid 时）
assets/                                生成素材（照片/查询图，不入库，运行时生成）
```

控制台每档打印：P50/P90/P95/P99/AVG/MAX + QPS + 错误率 + 超时率 + 429 占比。

## 已知设计约束（观察点，非缺陷）

- search 内部全局 `SEARCH_CONCURRENCY=4` 信号量（recall.py，P2-01 防线程池耗尽）——
  高并发下 search 请求在信号量上排队，吞吐存在天然上限；报告据此分析瓶颈（见 07 报告口径）。
- BGE-M3 本地模型（~1.2GB 内存，OMP 线程 4）首个请求加载，warmup 已覆盖。
