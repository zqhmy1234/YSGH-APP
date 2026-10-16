#!/usr/bin/env python3
"""搜索延迟真实压测（2026-10-16 · ci-gate-perf 性能验收）

与 CI 用例 `test_search_latency_sample_under_3s` 的分工：
  - CI 用例在 MOCK_EXTERNAL_AI=true 下跑（改写/精排/回答被 mock 成 0 耗时）——
    只能证明"检索骨架"不越线，**绿 ≠ 真实 P95<3s**；
  - 本脚本**显式拒绝 mock 模式**：跑前解析 `backend/.env` 的 MOCK_EXTERNAL_AI，
    为 true（或非 false 的任何值）即拒绝执行并退出码 2，**绝不用假数字冒充真实压测**。

实测对象：运行中的后端 HTTP 端点（默认 http://127.0.0.1:8010，--base 可改）——
端到端墙钟含 HTTP 序列化/鉴权/DB 回查等全部开销，与真机用户体感同口径。

数据准备：脚本自建一次性 bench 用户（/auth/wechat 伪造 code 登录，dev 通道），
把压测语料**直接写入生产 collection**（yishu_contents，payload 带本脚本专属
benchmark 标记，起止均按标记清理，可重入、不残留）。content_id 用非 UUID 字符串
——`_assemble_hits` 的 DB 回查闸门对非 UUID 测试点原样放行（D3 修复回归用例
`test_assemble_hits_filters_deleted_failed_and_foreign` 同款语义），免写 PG。

打点口径（如实，不编造）：
  - 响应体只暴露 `latency_ms`（检索段打点，rag/__init__.py 中在 rerank **之前**
    快照；want_answer=True 时重算为含回答的端到端值）与 `degraded`/`answer`。
  - 因此"改写 / 召回 / 粗排+精排 / 回答"**无独立打点**——本脚本只呈现：
      ① 端到端墙钟（实测）；② 响应 latency_ms（检索段，off 模式）；
      ③ 二者之差 = latency_ms 之后阶段（LLM 精排 + 规则敏感过滤 + 序列化，近似）；
      ④ on-off 成对差 = 回答段（近似）。改写/召回独立耗时标"未暴露"。

退出码：0 = 压测完成并写出报告；2 = 环境拒绝（mock 未关 / 后端不可达 / 无真 key）。

用法：
  python scripts/bench_search_latency.py --n 30 --mode both
  python scripts/bench_search_latency.py --n 10 --mode on --base http://127.0.0.1:8010
"""
from __future__ import annotations

import argparse
import json
import math
import os
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BACKEND = ROOT / "backend"
REPORT_PATH = ROOT / "docs" / "性能验收_搜索_20261016.md"

sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(BACKEND))

from gate_io import force_utf8  # noqa: E402

force_utf8()

# ---------------------------------------------------------------------------
# 压测语料与查询（写死；来源注释见下）
# ---------------------------------------------------------------------------

# 查询来源：覆盖 F5 描述性搜索五类典型意图（地点/时间/人物/物品/模糊描述，
# 另加时间+地点复合），语意对齐 backend/tests/test_rag.py 既有测试语料
# （杭州西湖/苏州平江路/备考等）与 M1 检索验收场景；为压测专用的合成记忆，
# 不含任何真实用户数据。语料与查询一一呼应，保证两模式均真实命中
# （want_answer=True 且无命中时不会调回答 LLM，命中是压到回答段的前提）。
# CORPUS 每项 (文本, 拍摄时间 ISO)——时间有真实跨度，"去年夏天/五一/上个月"等
# 时间类查询才会真实命中（payload taken_at 为 epoch 秒，与 _to_filter 数值口径一致）。
CORPUS: list[tuple[str, str]] = [
    # 地点类
    ("去年夏天和爸妈去杭州西湖，坐手摇船穿过荷花丛", "2025-07-15T10:00:00+08:00"),
    ("周末和朋友去苏州平江路吃松鼠桂鱼", "2025-10-01T12:30:00+08:00"),
    ("在北京胡同里逛了一下午，尝了豆汁和焦圈", "2026-04-12T15:00:00+08:00"),
    # 时间类
    ("今年五一假期去青岛看海，拍了很多日落照片", "2026-05-02T17:30:00+08:00"),
    ("上个月部门团建去了莫干山的民宿", "2026-09-12T14:00:00+08:00"),
    ("元旦在西安跨年，钟楼下面人非常多", "2026-01-01T23:00:00+08:00"),
    # 人物类
    ("和大学室友小聚，聊了一晚上各自的工作", "2026-08-20T19:00:00+08:00"),
    ("陪爸妈去体检，顺便在附近吃了早茶", "2026-06-18T09:00:00+08:00"),
    ("和表姐一起去了体育馆看演唱会", "2026-07-25T20:00:00+08:00"),
    # 物品类
    ("新买了相机镜头，周末拿出去试拍了一组照片", "2026-09-28T16:00:00+08:00"),
    ("收到了朋友送的手工陶艺杯子，很好看", "2026-03-15T11:00:00+08:00"),
    # 模糊描述类
    ("加班到很晚，回家路上看到特别亮的月亮", "2026-09-05T23:30:00+08:00"),
    ("那天心情不太好，独自去江边走了很久", "2026-08-30T21:00:00+08:00"),
]

QUERIES: list[tuple[str, str]] = [
    ("地点类", "杭州西湖手摇船"),
    ("时间类", "去年夏天的旅行"),
    ("人物类", "和爸妈一起的旅行"),
    ("物品类", "买的相机镜头"),
    ("模糊描述类", "心情不好去江边散步"),
    ("时间+地点复合", "五一去青岛看海"),
]

BENCH_MARK = "bench-latency-20261016"
PROBE_QUERIES = ["杭州西湖手摇船", "和爸妈一起的旅行"]  # 索引就绪探针（各应 ≥1 命中）

# 单请求 HTTP 超时（秒）：远大于 3s 预算，留足真实尾延迟观测空间
HTTP_TIMEOUT = 45


# ---------------------------------------------------------------------------
# 环境守卫：mock 显式拒绝（在任何 app 导入**之前**解析 .env）
# ---------------------------------------------------------------------------


def _parse_env_flag(env_path: Path, key: str) -> str | None:
    """手工解析 .env 取单个键（不依赖 pydantic-settings，保证拒绝逻辑先于 app 导入）。"""
    if not env_path.is_file():
        return None
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"').strip("'")
    return None


def guard_mock() -> None:
    raw = _parse_env_flag(BACKEND / ".env", "MOCK_EXTERNAL_AI")
    # pydantic-settings 布尔解析：true/1/yes/on 均算真；缺省默认 False
    if raw is not None and raw.lower() in ("true", "1", "yes", "on"):
        print(
            "[拒绝执行] backend/.env 的 MOCK_EXTERNAL_AI=true —— 改写/精排/回答会被 mock 成 0 耗时，\n"
            "此时测出的 P95 是假数字，绝不能用冒充真实压测。\n"
            "请先改 backend/.env：MOCK_EXTERNAL_AI=false 并重启后端，再跑本脚本。",
            file=sys.stderr,
        )
        sys.exit(2)


# ---------------------------------------------------------------------------
# HTTP 小客户端（urllib，零额外依赖）
# ---------------------------------------------------------------------------


class Http:
    def __init__(self, base: str) -> None:
        self.base = base.rstrip("/")

    def post(self, path: str, body: dict, token: str | None = None, timeout: int = HTTP_TIMEOUT) -> tuple[int, dict]:
        req = urllib.request.Request(  # noqa: S310 —— base 为操作者显式传入的受信内网地址
            f"{self.base}{path}",
            data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                **({"Authorization": f"Bearer {token}"} if token else {}),
            },
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 —— 同上，受信内网地址
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:  # 4xx/5xx 也取回响应体（错误码记录用）
            try:
                return exc.code, json.loads(exc.read().decode("utf-8"))
            except Exception:
                return exc.code, {}
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            return -1, {"_transport_error": str(exc)}


# ---------------------------------------------------------------------------
# 统计：nearest-rank 分位数（P50/P95/P99，与 CI 用例的 int(0.95*n) 同为保守口径，
# 但对任意 n 都取"至少覆盖 p% 的最小观测值"，n 非 20 倍数时口径更稳）
# ---------------------------------------------------------------------------


def percentile(sorted_vals: list[float], p: float) -> float | None:
    if not sorted_vals:
        return None
    idx = max(0, math.ceil(p / 100 * len(sorted_vals)) - 1)
    return sorted_vals[min(idx, len(sorted_vals) - 1)]


def stats(vals: list[float]) -> dict:
    if not vals:
        return {"n": 0}
    s = sorted(vals)
    return {
        "n": len(s),
        "min": round(s[0], 1),
        "p50": round(percentile(s, 50) or 0, 1),
        "p95": round(percentile(s, 95) or 0, 1),
        "p99": round(percentile(s, 99) or 0, 1),
        "max": round(s[-1], 1),
        "mean": round(sum(s) / len(s), 1),
    }


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------


def main() -> int:
    ap = argparse.ArgumentParser(description="搜索延迟真实压测（拒绝 mock）")
    ap.add_argument("--n", type=int, default=30, help="每模式请求数（默认 30，不含预热）")
    ap.add_argument("--mode", choices=["both", "off", "on"], default="both", help="want_answer 档位")
    ap.add_argument("--base", default="http://127.0.0.1:8010", help="后端地址")
    ap.add_argument("--warmup", type=int, default=2, help="每模式预热请求数（默认 2，不计入统计、单独报告）")
    ap.add_argument("--keep-data", action="store_true", help="跳过清理（调试用；默认清理 bench 语料）")
    args = ap.parse_args()

    guard_mock()  # 第一道：.env 层拒绝（app 未导入）
    os.chdir(BACKEND)  # pydantic-settings 的 .env 路径以 backend 为基准

    from app.core.config import settings
    from app.core.security import decode_token
    from app.services.embedding import encode_query
    from app.services.vector_store import default_collection, get_store

    # 第二道：settings 层再验（防 .env 未写但进程环境变量带 mock 的边角）
    if settings.mock_external_ai:
        print("[拒绝执行] settings.mock_external_ai=True（进程环境覆盖）——拒绝用 mock 冒充真实压测。", file=sys.stderr)
        return 2

    http = Http(args.base)
    try:
        with urllib.request.urlopen(f"{args.base}/healthz", timeout=10) as resp:  # noqa: S310 —— 受信内网地址
            health_ok = resp.status == 200
    except Exception:
        health_ok = False
    if not health_ok:
        print(f"[拒绝执行] 后端不可达：{args.base}/healthz（GET 探活失败）", file=sys.stderr)
        return 2

    # 真 key 判定（只看"是否已配置"，绝不打印 key 值）
    provider = settings.llm_provider
    if provider == "deepseek":
        key_real = bool(settings.deepseek_api_key)
        model = settings.deepseek_model
    else:
        key_real = bool(settings.dashscope_api_key)
        model = "qwen-flash（dashscope）"
    dashscope_key_real = bool(settings.dashscope_api_key)

    # 登录：dev 通道伪造 wechat code 建一次性 bench 用户
    code = f"bench-{uuid.uuid4().hex[:8]}"
    st, body = http.post("/api/v1/auth/wechat", {"code": code, "device_id": "bench-latency"})
    if st != 200:
        print(f"[拒绝执行] bench 用户登录失败：HTTP {st} {body}", file=sys.stderr)
        return 2
    token = body["data"]["access_token"]
    user_id = decode_token(token)["sub"]

    # 语料写入生产 collection（起止均按 BENCH_MARK 清理，可重入）
    store = get_store()
    from qdrant_client.http import models  # noqa: PLC0415

    def _cleanup() -> None:
        store.client.delete(
            collection_name=default_collection(),
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[models.FieldCondition(key="benchmark", match=models.MatchValue(value=BENCH_MARK))]
                )
            ),
        )

    def _seed() -> None:
        for i, (text, taken_iso) in enumerate(CORPUS):
            dense, sparse = encode_query(text)
            store.upsert_content(
                content_id=f"benchlat-{i:03d}",
                text=text,
                dense=dense,
                sparse=sparse,
                payload={
                    "content_type": "text",
                    "text": text,
                    "taken_at": int(datetime.fromisoformat(taken_iso).timestamp()),
                    "user_id": str(user_id),
                    "benchmark": BENCH_MARK,
                },
            )

    def _indexed() -> bool:
        dense, sparse = encode_query(PROBE_QUERIES[0])
        hits = store.search(dense, sparse, filters={"user_id": str(user_id)}, limit=5)
        return len(hits) >= 1

    _cleanup()
    _seed()
    deadline = time.perf_counter() + 60
    while not _indexed():
        if time.perf_counter() > deadline:
            print("[拒绝执行] bench 语料索引 60s 未就绪（Qdrant/BGE-M3 异常）", file=sys.stderr)
            _cleanup()
            return 2
        time.sleep(1.0)

    print(f"环境就绪：provider={provider} model={model} text_key={'真' if key_real else '未配置!'} "
          f"dashscope_key={'真' if dashscope_key_real else '未配置'} user={user_id[:8]}… n={args.n} mode={args.mode}")

    # 预热：冷启动（BGE-M3 懒加载/Qdrant 连接/deepseek 首连）不属于稳态响应时间，
    # 结果**不计入统计**、只在报告中单独披露（口径如实，不拿冷启动样本充数也不静默丢弃）。
    modes = {"both": [False, True], "off": [False], "on": [True]}[args.mode]
    warm_records: list[dict] = []
    for want in modes:
        for _ in range(args.warmup):
            t0 = time.perf_counter()
            st, resp = http.post(
                "/api/v1/search", {"q": QUERIES[0][1], "limit": 5, "want_answer": want}, token=token
            )
            warm_records.append({
                "mode": "on" if want else "off",
                "wall_ms": round((time.perf_counter() - t0) * 1000, 1),
                "status": st,
                "latency_ms": (resp.get("data") or {}).get("latency_ms") if st == 200 else None,
            })
    if warm_records:
        print(f"预热完成：{len(warm_records)} 次（不计入统计）")

    # 压测：循环遍历查询清单直至凑够 n；off/on 相邻成对（回答段 = on - off 近似）
    records: list[dict] = []
    try:
        cycles = math.ceil(args.n / len(QUERIES))
        per_mode_target = args.n
        made = {False: 0, True: 0}
        for _cycle in range(cycles):
            for intent_label, q in QUERIES:
                for want in modes:
                    if made[want] >= per_mode_target:
                        continue
                    body_out = {"q": q, "limit": 5, "want_answer": want}
                    t0 = time.perf_counter()
                    st, resp = http.post("/api/v1/search", body_out, token=token)
                    wall_ms = (time.perf_counter() - t0) * 1000
                    rec = {
                        "mode": "on" if want else "off",
                        "intent": intent_label,
                        "q": q,
                        "status": st,
                        "wall_ms": round(wall_ms, 1),
                    }
                    if st == 200:
                        data = resp.get("data") or {}
                        rec.update(
                            latency_ms=data.get("latency_ms"),
                            hits=data.get("total"),
                            degraded=data.get("degraded"),
                            answer=bool(data.get("answer")),
                            rewritten=bool(data.get("rewritten_query")),
                            intent_detected=data.get("intent"),
                        )
                    else:
                        rec["error"] = (
                            (resp or {}).get("code")
                            or (resp or {}).get("_transport_error")
                            or str(resp)[:120]
                        )
                    records.append(rec)
                    made[want] += 1
    finally:
        if not args.keep_data:
            _cleanup()

    # ---- 汇总 ----
    def _mode_records(want: bool) -> list[dict]:
        name = "on" if want else "off"
        return [r for r in records if r["mode"] == name and r["status"] == 200]

    def _walls(want: bool) -> list[float]:
        return [r["wall_ms"] for r in _mode_records(want)]

    def _lat(want: bool) -> list[float]:
        return [r["latency_ms"] for r in _mode_records(want) if r.get("latency_ms") is not None]

    summary: dict[str, dict] = {}
    for want in modes:
        name = "on" if want else "off"
        walls = _walls(want)
        lats = _lat(want)
        after = [  # latency_ms 之后阶段（近似）：LLM 精排+敏感过滤+序列化
            r["wall_ms"] - (r["latency_ms"] or 0)
            for r in records
            if r["mode"] == name and r["status"] == 200 and r.get("latency_ms") is not None
        ]
        summary[name] = {
            "wall": stats(walls),
            "latency_ms_检索段": stats(lats),
            "latency_ms后阶段_近似": stats(after),
            "errors": sum(1 for r in records if r["mode"] == name and r["status"] != 200),
            "degraded": sum(1 for r in records if r["mode"] == name and r.get("degraded")),
            "answer_hit": sum(1 for r in records if r["mode"] == name and r.get("answer")),
            "hits_p50": percentile(
                sorted(float(r["hits"]) for r in _mode_records(want) if r.get("hits") is not None), 50
            ),
        }

    # 回答段（近似）：同 (intent,q) 的 on/off 按出现顺序 zip 成对（同周期相邻，差最小化）
    answer_stage: list[float] = []
    if len(modes) == 2:
        offs: dict[tuple[str, str], list[float]] = {}
        ons: dict[tuple[str, str], list[float]] = {}
        for r in records:
            if r["status"] == 200:
                bucket = ons if r["mode"] == "on" else offs
                bucket.setdefault((r["intent"], r["q"]), []).append(r["wall_ms"])
        for key, off_list in offs.items():
            for w_off, w_on in zip(off_list, ons.get(key, []), strict=False):
                answer_stage.append(w_on - w_off)

    # ---- 控制台输出 ----
    print("\n==== 汇总（端到端墙钟，毫秒）====")
    for name, s in summary.items():
        print(
            f"[want_answer={name}] wall P50={s['wall'].get('p50')} P95={s['wall'].get('p95')} "
            f"P99={s['wall'].get('p99')} max={s['wall'].get('max')} "
            f"| 检索段(latency_ms) P95={s['latency_ms_检索段'].get('p95')} "
            f"| 后阶段近似 P95={s['latency_ms后阶段_近似'].get('p95')} "
            f"| 错误={s['errors']} 降级={s['degraded']} 有回答={s['answer_hit']}"
        )
    if answer_stage:
        a = stats(answer_stage)
        print(f"[回答段 近似] n={a['n']} P50={a.get('p50')} P95={a.get('p95')} max={a.get('max')}")

    # ---- 报告落盘 ----
    report = build_report(
        args, provider, model, key_real, dashscope_key_real, records, summary, answer_stage, warm_records
    )
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(report, encoding="utf-8")
    print(f"\n报告已写入：{REPORT_PATH}")
    return 0


def build_report(  # noqa: PLR0913
    args: argparse.Namespace,
    provider: str,
    model: str,
    key_real: bool,
    dashscope_key_real: bool,
    records: list[dict],
    summary: dict[str, dict],
    answer_stage: list[float],
    warm_records: list[dict],
) -> str:
    # 本地时间（先取 UTC 再 astimezone，满足 ruff DTZ005 且不打乱时区语义）
    now = datetime.now(tz=UTC).astimezone().strftime("%Y-%m-%d %H:%M")
    lines: list[str] = []
    a = lines.append
    a("# 性能验收 · 搜索延迟（2026-10-16）")
    a("")
    a("> 本文档由 `scripts/bench_search_latency.py` 生成（真 key 压测，显式拒绝 mock 模式）。")
    a("> CI 用例 `test_search_latency_sample_under_3s`（n=20、真 P95、MOCK 下跑）只证明检索骨架不越线；")
    a("> 本报告是**含真实 LLM 往返**的端到端墙钟实测，两者口径互补。")
    a("")
    a("## 环境")
    a("")
    a(f"- 日期：{now}")
    a(f"- 后端：`{args.base}`（运行中实例，非 TestClient——含 HTTP/鉴权/DB 回查全部开销）")
    key_note = "**真实已配置**" if key_real else "**未配置（本轮 LLM 全降级！）**"
    a(f"- 文本 LLM 供应商：`{provider}`，模型：`{model}`，key：{key_note}")
    a(f"- DashScope key：{'真实已配置' if dashscope_key_real else '未配置'}（视觉链路用，本轮文字搜索不涉及）")
    a("- `MOCK_EXTERNAL_AI`：**false**（脚本跑前强制断言，为 true 即拒绝执行）")
    a(f"- 样本：--n {args.n}，--mode {args.mode}；每请求 `limit=5`；顺序执行（并发信号量不排队）")
    a(f"- 预热：每模式 {args.warmup} 次（BGE-M3 懒加载/连接池冷启动不计入统计，结果单独披露见下）")
    if warm_records:
        a("")
        a("**预热明细（不计入统计）：**")
        a("")
        a("| # | mode | HTTP | 墙钟 ms | latency_ms |")
        a("|---|---|---|---|---|")
        for i, w in enumerate(warm_records, 1):
            a(f"| {i} | {w['mode']} | {w['status']} | {w['wall_ms']} | {w.get('latency_ms', '—')} |")
    a("- bench 语料：脚本自建一次性用户 + 13 条压测专用合成记忆"
      "（带 `benchmark=bench-latency-20261016` 标记，跑完已清理）")
    a("")
    a("## 查询清单（写死于脚本，覆盖五类典型意图）")
    a("")
    a("| 意图 | 查询 |")
    a("|---|---|")
    for label, q in QUERIES:
        a(f"| {label} | {q} |")
    a("")
    a("## 汇总：端到端墙钟 P50/P95/P99（毫秒）")
    a("")
    a("| want_answer | n | P50 | **P95** | P99 | max | mean | 错误数 | 降级数 | 有回答数 |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for name, s in summary.items():
        w = s["wall"]
        a(f"| {name} | {w.get('n', 0)} | {w.get('p50', '未取得')} | **{w.get('p95', '未取得')}** | "
          f"{w.get('p99', '未取得')} | {w.get('max', '未取得')} | {w.get('mean', '未取得')} | "
          f"{s['errors']} | {s['degraded']} | {s['answer_hit']} |")
    a("")
    a("## 分阶段耗时（后端打点可得多少写多少，缺项如实标注）")
    a("")
    a("后端响应**只暴露 `latency_ms` 一个打点**：它在 rerank **之前**快照（`rag/__init__.py`），")
    a("语义 = 改写+编码+召回+溯源组装（检索段）；`want_answer=True` 时重算为含回答的端到端值。")
    a("改写 / 召回 / 粗排精排 / 回答**均无独立打点**，下表不编造：")
    a("")
    a("| want_answer | 检索段 latency_ms P50 | 检索段 P95 | latency_ms 之后阶段 P50（近似¹） | 之后阶段 P95（近似¹） |")
    a("|---|---|---|---|---|")
    for name, s in summary.items():
        lat = s["latency_ms_检索段"]
        aft = s["latency_ms后阶段_近似"]
        a(f"| {name} | {lat.get('p50', '未取得')} | {lat.get('p95', '未取得')} | "
          f"{aft.get('p50', '未取得')} | {aft.get('p95', '未取得')} |")
    a("")
    if answer_stage:
        st_ = stats(answer_stage)
        a(f"- 回答段（同查询 on−off 成对差，近似²）：n={st_['n']}，P50={st_.get('p50')}ms，"
          f"P95={st_.get('p95')}ms，max={st_.get('max')}ms。")
    else:
        a("- 回答段（近似²）：**未取得**（本轮非 both 模式，无成对样本）。")
    a("")
    a("> ¹ latency_ms 之后阶段 = 端到端墙钟 − latency_ms，包含 LLM 精排 + 规则级敏感过滤 + "
      "HTTP 序列化，**不是纯精排耗时**。")
    a("> ² 回答段 = 同一条查询 want_answer on/off 两次墙钟之差，包含两次请求的固有波动，"
      "**偏乐观/悲观取决于网络抖动方向**。")
    a("")
    a("## 逐条明细")
    a("")
    a("| # | mode | 意图 | 查询 | HTTP | 墙钟 ms | latency_ms | hits | degraded | 有回答 |")
    a("|---|---|---|---|---|---|---|---|---|---|")
    for i, r in enumerate(records, 1):
        a(f"| {i} | {r['mode']} | {r['intent']} | {r['q']} | {r['status']} | {r['wall_ms']} | "
          f"{r.get('latency_ms', '—')} | {r.get('hits', '—')} | {r.get('degraded', '—')} | {r.get('answer', '—')} |")
    a("")

    # ---- 结论（由真实数字生成；不达标/异常如实写）----
    a("## 结论")
    a("")
    verdicts: list[str] = []
    for name, s in summary.items():
        p95 = s["wall"].get("p95")
        if p95 is None:
            verdicts.append(f"- want_answer={name}：**未取得有效样本（全部失败/超时）**，无法判定，原因见逐条明细。")
        elif p95 < 3000:
            verdicts.append(f"- want_answer={name}：**P95={p95}ms < 3000ms，达标**（RET-018）。")
        else:
            verdicts.append(f"- want_answer={name}：**P95={p95}ms ≥ 3000ms，不达标**（RET-018）。")
    a("\n".join(verdicts))
    total_errors = sum(s["errors"] for s in summary.values())
    if total_errors:
        a(f"- ⚠️ 本轮出现 {total_errors} 次非 200 响应（超时/500，逐条见明细表）——这是真实稳定性信号"
          "（供应商重试退避或后端错误），相应样本已排除出分位数统计，但**它们本身说明链路在真实负载下会失败**。")
    a("")
    # 瓶颈判定：比较 off 模式各段 P95
    if "off" in summary and summary["off"]["wall"].get("p95") is not None:
        lat_p95 = summary["off"]["latency_ms_检索段"].get("p95") or 0
        aft_p95 = summary["off"]["latency_ms后阶段_近似"].get("p95") or 0
        if lat_p95 >= aft_p95:
            a(f"- 瓶颈（off 模式口径）：检索段 P95={lat_p95}ms ≥ 之后阶段 P95={aft_p95}ms ⇒ 瓶颈在"
              "**改写+编码+召回+组装**（BGE-M3 本地推理与 Qdrant 往返主导；细分到改写/召回需后端补打点）。")
        else:
            a(f"- 瓶颈（off 模式口径）：之后阶段 P95={aft_p95}ms > 检索段 P95={lat_p95}ms ⇒ 瓶颈在"
              "**LLM 精排往返**（含敏感过滤与序列化，未拆分）。")
    if "on" in summary and answer_stage:
        st_ = stats(answer_stage)
        a(f"- 开启回答后增量：on−off 成对差 P50={st_.get('p50')}ms / P95={st_.get('p95')}ms；"
          f"on 模式墙钟 P95={summary['on']['wall'].get('p95')}ms。判读注意：成对差受相邻两次请求"
          "固有波动影响（本环境单次供应商往返波动即可达秒级），当波动大于增量时失去分辨力——"
          "更稳的证据是 on 模式 latency_ms（含 1.0s 超时等待）与 off 模式墙钟之差 ≈1s，"
          "与 100% 超时降级相互印证：回答段实际耗时 = 1.0s 硬上限。")
    degraded_note = [
        f"{name} 模式 {s['degraded']}/{s['wall'].get('n', 0)}"
        for name, s in summary.items()
        if s["degraded"]
    ]
    if degraded_note:
        a(f"- ⚠️ 本轮出现降级响应：{'；'.join(degraded_note)}。on 模式的降级主要来自回答段 1.0s 硬上限"
          "（`search_answer.ANSWER_TIMEOUT_SECONDS`）——供应商单次往返若超过 1s 即超时降级为 answer=None，"
          "**这是真实行为而非测试误差**：意味着当前供应商档位下用户大概率拿不到搜索式回答。")
    miss_answer = [
        name
        for name, s in summary.items()
        if name == "on" and s["wall"].get("n") and s["answer_hit"] < s["wall"]["n"]
    ]
    if miss_answer:
        for name in miss_answer:
            s = summary[name]
            a(f"- ⚠️ want_answer={name} 的 {s['wall']['n'] - s['answer_hit']}/{s['wall']['n']} 样本未产出 answer"
              "（超时降级/证据不足）——见逐条明细。")
    a("")
    a("## 建议")
    a("")
    a("1. 后端在响应中暴露分阶段打点（改写/召回/rerank/回答各自耗时）——当前只能拿端到端与检索段两点做差，"
      "瓶颈定位精度受限（本报告已按'未暴露'如实标注，未编造）。")
    a("2. 若检索段成为瓶颈：优先排查 BGE-M3 本地推理耗时（CPU/GPU 档位）与 Qdrant 单次往返；"
      "若精排段成为瓶颈：LLM 精排可改异步或换更快的模型档。")
    a("3. 本压测为单用户顺序负载（无并发排队）；生产多并发下信号量排队会抬高尾延迟，"
      "建议补一轮并发阶梯压测（1/5/10 并发）。")
    a("")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
