"""
企业微信消息推送工具
注意：主动推送已改为通过 WSClient.send_message() 经 WebSocket 发送
此文件仅保留工具注册占位，实际推送逻辑在 daily_review_service.py 中
"""
import logging

logger = logging.getLogger(__name__)
