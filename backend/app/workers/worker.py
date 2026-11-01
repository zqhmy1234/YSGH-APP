"""RQ Worker 进程入口（P2-02 重构：内容 AI 管线已下沉 services/pipeline.py）

职责：仅进程启动入口（Windows SimpleWorker / Linux Worker 选择）与 RQ 任务注册。

启动命令：
  python -m app.workers.worker [queue...]
（默认队列：high,low——high=P0/P1 语音/照片，low=P2-P4 聚合/批量）

任务函数路径：app.services.pipeline.process_content（RQ pickle 依赖模块路径，
存量队列中旧路径 job 需在低峰期清空/重投，见 refactor-plan P2-02 风险控制）。

A2（P0-2）：启动 with_scheduler=True——RQ Retry 重投的任务先进入
ScheduledJobRegistry，只有 RQ Scheduler 会把到期任务搬回队列；不起 scheduler
则 retry 永不真正重投（配合 pipeline 的 re-raise 语义，缺一不可）。多 worker
部署时按队列 Redis 锁互斥，仅一个进程持有 scheduler 锁，其余自动跳过。
"""
from __future__ import annotations

import logging

logger = logging.getLogger("yishu.worker")


def get_worker_class():
    """Windows 用 SimpleWorker（无 fork），Linux 用默认 Worker"""
    import platform

    if platform.system() == "Windows":
        from rq.worker import SimpleWorker

        return SimpleWorker
    from rq import Worker

    return Worker


def _sweep_orphan_contents() -> None:
    """启动期 + 周期性巡检：把「DB 说 processing、队列里没 job」的残骸关掉。

    ## 为什么必须有（2026-10-31 现场）
    库里 4590 条 `processing` 中 4579 条 >24h（最老两个月前），而 RQ 队列长度是 **0**、
    抽样 200 条在队列里一条 job 都没有 ⇒ 它们是**永远不会被消费的孤儿**：
    既让内容没有打标/向量（搜索永远召回不到），又让「积压」看起来永远清不完。
    根因是入队失败 / job 过期 / worker 崩溃都会留下残骸，而**没有任何回头看的一致性巡检**。

    ⇒ 这里在 worker 启动时跑一次，并每 3 小时再跑一次（守护线程）。
    只处理「早于 30 分钟」的，避免误伤正在跑的任务；判定与清理逻辑复用
    `scripts/close_orphan_contents.py`，保持单一事实源。
    """
    import os
    import threading
    import time

    def _once() -> None:
        try:
            import subprocess
            import sys as _sys
            from pathlib import Path

            backend_dir = Path(__file__).resolve().parents[2]  # backend/
            script = backend_dir / "scripts" / "close_orphan_contents.py"
            if not script.exists():
                logger.warning("孤儿巡检脚本缺失：%s", script)
                return
            # 子进程必须带 PYTHONPATH=backend，否则 `from app.core.config import settings` 直接失败
            env = os.environ.copy()
            env["PYTHONPATH"] = str(backend_dir)
            # 脚本路径来自仓库内固定目录（非外部输入），故豁免 S603；失败由 returncode 判定
            # 而非抛错，故显式 check=False（PLW1510 要求写出来）。
            r = subprocess.run(  # noqa: S603
                [_sys.executable, str(script)],
                capture_output=True,
                text=True,
                timeout=180,
                check=False,
                cwd=str(backend_dir),
                env=env,
            )
            tail = (r.stdout or r.stderr or "").strip().splitlines()[-3:]
            if tail:
                logger.warning("孤儿内容巡检：%s", " | ".join(tail))
        except Exception as exc:  # noqa: BLE001 —— 巡检失败绝不能拖垮 worker
            logger.warning("孤儿内容巡检失败（不影响消费）：%s", exc)

    def _loop() -> None:
        while True:
            time.sleep(3 * 3600)
            _once()

    _once()
    threading.Thread(target=_loop, name="orphan-sweeper", daemon=True).start()


def main() -> None:
    """启动 RQ worker（python -m app.workers.worker [queue...]）"""
    import sys

    from app.core.queue import QUEUE_HIGH, QUEUE_LOW, get_queue, redis

    queues = sys.argv[1:] or [QUEUE_HIGH, QUEUE_LOW]
    # 预导入任务函数（RQ 从模块路径反序列化，确保可导入）
    from app.services import pipeline  # noqa: F401  —— 注册 process_content
    from app.services.events import run_user_aggregation  # noqa: F401  —— 注册聚合任务（F3）

    # BA3（AI 链批）：LLM Key 未配置时启动期警告——chips 打标/照片描述走「配置缺失」
    # 显式路径（AiTaggingError.LLM_NOT_CONFIGURED，log 可查），不 crash 主流程。
    # 开发环境可能没有 Key（Key 走 Infisical，仅真实环境注入）。
    from app.services.llm_ops.base import llm_available
    from app.services.pipeline import tag_content  # noqa: F401  —— 注册 BA3 AI 打标任务（low 队列）

    if not llm_available():
        logger.warning(
            "DASHSCOPE_API_KEY 未配置（或 MOCK_EXTERNAL_AI=true）——"
            "AI chips 打标与照片 AI 描述将跳过（配置缺失路径，主内容 done 不受影响）"
        )

    worker = get_worker_class()(
        [get_queue(q) for q in queues],
        connection=redis,
    )
    logger.info("RQ worker 启动，监听队列: %s", queues)
    # 2026-10-31：启动即巡检孤儿内容（+ 每 3 小时一次），见 _sweep_orphan_contents docstring。
    # 放在 work() 之前：确保「库里写着 processing 但队列里根本没 job」的残骸不会又堆两个月。
    _sweep_orphan_contents()
    # A2（P0-2）：with_scheduler=True —— Retry 重投任务先进 ScheduledJobRegistry，
    # 需 RQ Scheduler 搬回队列才真正重投（此前吞异常 + 无 scheduler = 双重失效）。
    worker.work(with_scheduler=True)


if __name__ == "__main__":
    main()
