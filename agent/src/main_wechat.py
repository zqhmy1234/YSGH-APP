"""忆光 - 企业微信版启动入口

整合企业微信消息接收服务和定时复盘任务。

## ⚠️ 接线状态：未接线（2026-10-06 核实，见 docs/功能对齐计划_20261006.md W3）

本入口**不在任何部署/启动脚本里**（deploy/systemd 只有 yishu-api / yishu-worker，
deploy/docker-compose.yml 也无本进程）⇒ 它当前**不会随服务启动**，仅可手工拉起。
设计上企微客服（F6）由 **backend** 承载（微信客服 kf + msg_id 幂等回调，
`backend/app/services/wechat/service.py`），本文件是企业微信「智能机器人」
WSClient 长连接——**两条不同的产品线**，且本链**无 msg_id 幂等**，若误上线会
破坏 M3 的"不丢消息 99.9%"。

在接线之前，已知的三个缺口（修复前请勿上线）：

1. **`wechat_users` 表不存在** —— 本仓迁移与 schema.sql 都没有它，用户身份
   落不了库、每日回顾查不到推送对象（`_record_user` 现已升级为 ERROR 级日志）。
2. **语音消息不转写** —— 原 Coze ASRClient 已随依赖瘦身移除；收到语音会
   诚实回告用户（见 `wechat_service._on_voice`）。设计里的语音承载方是
   backend 的 FunASR/SenseVoice 双通道（App 已在用）。
3. **每日回顾依赖 `wechat_users`** —— `daily_review_service` 同样查不到人，
   22:00 定时任务会空转。

若决定保留本链，接线顺序应为：建 `wechat_users` 迁移 → 补 msg_id 幂等 →
把语音接到 backend ASR → 加入 deploy/。若决定不保留，整链可删
（main_wechat.py / services/wechat_service.py / services/daily_review_service.py / tools/wechat_push_tools.py）。
"""
import os
import sys
import asyncio
import logging

# 添加项目根目录到路径
project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from services.wechat_service import run_wechat_service
from services.daily_review_service import start_review_scheduler

logger = logging.getLogger(__name__)


async def main():
    """主函数：同时运行消息服务和定时任务"""
    logger.info("=" * 50)
    logger.info("忆光 - 个人AI记忆助手（企业微信版）启动中...")
    logger.info("=" * 50)

    # 检查必要的环境变量
    required_vars = [
        "WECHAT_BOT_ID",
        "WECHAT_BOT_SECRET",
    ]

    missing_vars = [var for var in required_vars if not os.getenv(var)]
    if missing_vars:
        logger.error(f"缺少必要的环境变量: {', '.join(missing_vars)}")
        logger.error("请在 .env 文件中配置企业微信智能机器人参数 (BOT_ID + SECRET)")
        return

    # 创建任务
    tasks = [
        run_wechat_service(),  # 企业微信消息接收服务
        start_review_scheduler(),  # 定时复盘任务
    ]

    # 同时运行所有任务
    logger.info("所有服务已启动")
    logger.info(f"  - 消息服务: WebSocket 长连接")
    logger.info(f"  - 定时回顾: 每晚 {os.getenv('REVIEW_HOUR', '22')}:{os.getenv('REVIEW_MINUTE', '0').zfill(2)} 自动推送")
    await asyncio.gather(*tasks)


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
    )

    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("服务已停止")
    except Exception as e:
        logger.error(f"服务运行异常: {e}")
