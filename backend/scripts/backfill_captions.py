"""caption 回填 runner（2026-10-19 · 一次性）

背景：真库有 1949 条「done 但 text 为空」的照片，但绝大多数**根本没有可用的图片字节**：
  · 约 895 条没有 cos_key（合成/种子行）
  · 有 key 的里面，对象存储里真实存在的只有 **49** 条
  · 而这 49 条里还有 **32 条是 18 字节的 `smoke-object-bytes` 占位对象**
    （内容是字面量 `b'smoke-object-bytes'`，测试/烟测遗留）——PIL 无法解码
  ⇒ 真正可回填的只有 **17** 条。

⚠️ **2026-10-26 修正（本节数字全部为实测，勿再引用旧值）**：
  原头部写「真正可回填的只有 **114** 条」，**这是错的**（114 从未在真库上复现过）。
  实测漏斗（`--dry-run` 可复现）：
       done 照片 2348 → text 空 1949 → 有 cos_key 1055 → 对象存在 49 → **PIL 可解码 17**
  另一处旧值「827 条没有 cos_key / 753 条没有文件」同样已过期（库在 2026-10-25 清理过
  2908 条"文件从未存在"的 processing 记录）。

  **为什么要从 `object_exists` 加严到 PIL verify**：`object_exists()` 只判**文件是否存在**，
  对那 32 个 18 字节占位对象返回 True ⇒ 它们会进入回填队列，每条白烧一次 Qwen3-VL
  往返（有真金成本），最后必然失败。加严后 `--limit 5` 抽的是**真能出 caption 的 5 张**，
  而不是「5 张里 4 张白烧」。

为什么不用 `requeue_job` 直接投 RQ：`process_content` 尾部会给每个用户 enqueue
聚合任务（L2/L3，走 LLM）。回填只想要 caption，不想顺带触发上百个用户的聚合；
本脚本在进程内直接调 `process_content`，把聚合任务留在（临时）Redis 队列里不消费，
跑完清理即可。

用法（在 backend/ 下）：
    python scripts/backfill_captions.py --dry-run
    python scripts/backfill_captions.py --limit 5          # 先抽 5 张验质量
    python scripts/backfill_captions.py --limit 200        # 全量（实测上限 17）
"""
from __future__ import annotations

import argparse
import io
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

BACKEND_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BACKEND_DIR))

from app.services.external.storage import get_storage_backend  # noqa: E402
from app.services.pipeline import process_content  # noqa: E402
from app.workers.requeue_job import REQUEUE_DEFAULT_STALE_SECONDS, _stale_candidates  # noqa: E402


def _is_decodable_image(data: bytes) -> bool:
    """字节是否**真的**是一张 PIL 能打开的图片（D-01 加严判据）

    为什么不能只看 `object_exists()`：真库里有 71 个 18 字节的
    `b'smoke-object-bytes'` 占位对象（烟测遗留），`object_exists` 对它们**返回 True**
    ⇒ 回填队列里混进一堆注定失败的行，每条白烧一次 Qwen3-VL 往返。

    `verify()` 只校验文件头结构、**不真正解码像素**（故意如此：回填只需确认"能读"，
    不需要完整解码，省 CPU/内存——本机可用内存约 3GB）。
    ⚠️ 注意 `verify()` 之后 Image 对象即失效，若要再读数据须重新 `open`。
    """
    from PIL import Image

    try:
        Image.open(io.BytesIO(data)).verify()
    except Exception:  # noqa: BLE001 —— 任何解析异常都判"不可用"（不是"可用但怪"）
        return False
    return True


def _select(limit: int) -> tuple[list[str], int, dict[str, int]]:
    """选出**真的能回填**的候选，返回 (ids, 扫描总数, 漏斗计数)

    漏斗计数分三段暴露（对象存在 / 占位不可解码 / 可用），
    因为"49 条里 32 条是占位对象"这件事必须**看得见**——否则下次又会被当成
    "可回填 49 条"写进注释。
    """
    from app.db.session import SessionLocal

    db = SessionLocal()
    try:
        cutoff = datetime.now(UTC) - timedelta(seconds=REQUEUE_DEFAULT_STALE_SECONDS)
        candidates = _stale_candidates(db, cutoff, limit, only_empty_done=True)
        storage = get_storage_backend()
        todo: list[str] = []
        funnel = {"scanned": len(candidates), "has_key": 0, "exists": 0, "undecodable": 0}
        for c in candidates:
            key = (c.cos_key or "").strip()
            if not key:
                continue
            funnel["has_key"] += 1
            if not storage.object_exists(key):
                continue
            funnel["exists"] += 1
            try:
                data = storage.get_object(key)
            except Exception:  # noqa: BLE001 —— 读失败（权限/损坏）同样跳过
                funnel["undecodable"] += 1
                continue
            if not _is_decodable_image(data):
                funnel["undecodable"] += 1
                continue
            todo.append(str(c.id))
        # 先把 id 取出来，避免后续 process_content 另开会话时对象过期
        return todo, len(candidates), funnel
    finally:
        db.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="caption 回填（仅 done 无 text 且**图片字节真的可解码**的照片）")
    parser.add_argument("--limit", type=int, default=200, help="最多处理条数（候选上限）")
    parser.add_argument("--dry-run", action="store_true", help="只统计不处理")
    parser.add_argument("--sleep", type=float, default=0.2, help="每张之间的间隔秒数")
    parser.add_argument("--list-ids", default="", help="把待处理 id 写入文件后退出（分片用）")
    parser.add_argument("--ids-file", default="", help="从文件读取 id（跳过选择，分片用）")
    parser.add_argument("--shard", default="0/1", help="分片 i/n（0 起）")
    args = parser.parse_args()

    ids, scanned, funnel = _select(max(args.limit, 5000))
    print(f"候选 {scanned} 条；有 cos_key {funnel['has_key']} 条；"
          f"对象存在 {funnel['exists']} 条；其中**占位/不可解码** {funnel['undecodable']} 条 "
          f"⇒ 真正可回填 **{len(ids)}** 条（本次最多处理 {args.limit} 条）")
    ids = ids[: args.limit]

    if args.list_ids:
        Path(args.list_ids).write_text("\n".join(ids) + "\n", encoding="utf-8")
        print(f"已写 {len(ids)} 条到 {args.list_ids}")
        return
    if args.ids_file:
        ids = [x.strip() for x in Path(args.ids_file).read_text(encoding="utf-8").splitlines() if x.strip()]
    try:
        si, sn = (int(x) for x in args.shard.split("/", 1))
    except ValueError:
        si, sn = 0, 1
    if sn > 1:
        ids = ids[si::sn]
        print(f"分片 {si}/{sn}：{len(ids)} 条")
    if args.dry_run:
        for cid in ids[:10]:
            print("  [dry-run]", cid)
        return

    ok = failed = empty_after = 0
    started = time.time()
    from app.db.models import Content
    from app.db.session import SessionLocal

    for i, cid in enumerate(ids, 1):
        try:
            res = process_content(cid)
            # process_content 的返回体不含 text ⇒ 用独立会话回读真值（2026-10-19 修正：
            # 原实现读 res["text"] 恒空，把成功误报成 empty）
            check = SessionLocal()
            try:
                row = check.get(Content, cid)
                text = (row.text or "").strip() if row is not None else ""
            finally:
                check.close()
            if text:
                ok += 1
                print(f"[{i}/{len(ids)}] ok {cid[:8]} {text[:40]}")
            else:
                empty_after += 1
                print(f"[{i}/{len(ids)}] empty {cid[:8]} {res}")
        except Exception as exc:  # noqa: BLE001 —— 单条失败不中断整批
            failed += 1
            print(f"[{i}/{len(ids)}] FAIL {cid[:8]} {type(exc).__name__}: {str(exc)[:120]}")
        if args.sleep > 0:
            time.sleep(args.sleep)

    took = time.time() - started
    print(f"完成：ok={ok} empty_after={empty_after} failed={failed} 用时={took:.0f}s")


if __name__ == "__main__":
    main()
