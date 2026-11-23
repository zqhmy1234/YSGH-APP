"""ORM 模型（对齐 backend/sql/schema.sql；ORM 为唯一权威，Alembic check 零漂移）

R1#16 拆包（2026-08-27）：按域拆为 models/ 子包——
  auth.py（users/devices/sms_codes）· content.py（contents/correction_log）·
  echo.py（echo_history）· event.py（events/event_items/event_edit_log）·
  profile.py（画像+敏感词域）· sync.py（同步域）· message.py（messages）·
  upload.py（upload_tasks/upload_chunks）· wechat.py（wechat_messages）·
  geo.py（geo_cache）

本 __init__ 保持 `from app.db.models import X` / `import app.db.models` 全量兼容
（存量 80+ 处消费方零改动）；导入本包即注册全部模型到 Base.metadata
（migrations/env.py / check_schema_drift 依赖此语义）。
"""
from app.db.models.auth import Device, SmsCode, User
from app.db.models.capsule import Capsule
from app.db.models.chat import ChatMessage
from app.db.models.content import Content, CorrectionLog
from app.db.models.crisis import CrisisEvent
from app.db.models.echo import EchoHistory
from app.db.models.echo_pref import EchoExclusion, EchoPreference
from app.db.models.event import Event, EventEditLog, EventItem
from app.db.models.finetune import FinetuneJob
from app.db.models.geo import GeoCache
from app.db.models.memory import (
    MemoryBatch,
    MemoryChangeLog,
    MemoryCorrection,
    MemoryDraft,
    MemoryEntry,
    MemoryFactClaim,
)
from app.db.models.memory_agent import KnowledgeCollection, Memory, MemoryCategory
from app.db.models.message import Message
from app.db.models.person import ContentPerson, Person
from app.db.models.persona import UserAIPersona
from app.db.models.profile import (
    ProfileAnnotationPool,
    ProfileDimensionHistory,
    ProfileDimensionPending,
    ProfileL2Evidence,
    ProfileSensitive,
    SensitiveWord,
    UserProfile,
)
from app.db.models.sync import DeletedLog, OfflineQueue, SyncFieldVersion, SyncState
from app.db.models.upload import UploadChunk, UploadTask
from app.db.models.wechat import UserWechatBinding, WechatMessage

__all__ = [
    "Capsule",
    "ChatMessage",
    "Content",
    "ContentPerson",
    "CrisisEvent",
    "CorrectionLog",
    "DeletedLog",
    "Device",
    "EchoExclusion",
    "EchoHistory",
    "EchoPreference",
    "Event",
    "EventEditLog",
    "EventItem",
    "FinetuneJob",
    "GeoCache",
    "KnowledgeCollection",
    "Memory",
    "MemoryBatch",
    "MemoryCategory",
    "MemoryChangeLog",
    "MemoryCorrection",
    "MemoryDraft",
    "MemoryEntry",
    "MemoryFactClaim",
    "Message",
    "OfflineQueue",
    "Person",
    "UserAIPersona",
    "ProfileAnnotationPool",
    "ProfileDimensionHistory",
    "ProfileDimensionPending",
    "ProfileL2Evidence",
    "ProfileSensitive",
    "SensitiveWord",
    "SmsCode",
    "SyncFieldVersion",
    "SyncState",
    "UploadChunk",
    "UploadTask",
    "User",
    "UserProfile",
    "UserWechatBinding",
    "WechatMessage",
]
