"""
忆光 - 企业微信版启动入口
整合企业微信消息接收服务和定时复盘任务
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
