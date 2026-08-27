"""分片上传服务（S5-03 COS 分片/断电续传 · WP-C · R1#18 拆包）

包结构（R1#18 拆 protocol/register）：
  - protocol.py —— 分片上传协议状态机（init/upload_chunk/get_status/complete）
  - register.py —— 内容注册与协议适配（register_photo_content / _register_voice_content）
本 __init__ 保持 `from app.services import upload as upload_svc` / `from app.services.upload
import ...` 全量兼容（api/upload.py 与测试消费 DEFAULT_CHUNK_SIZE / VALID_UPLOAD_MODES /
MAX_UPLOAD_FILE_SIZE / MIN_CHUNK_SIZE / init_upload / upload_chunk / get_status /
complete_upload / register_photo_content 等）。
"""
from app.services.upload.protocol import (
    DEFAULT_CHUNK_SIZE,
    MAX_CHUNK_COUNT,
    MAX_INLINE_MERGE_BYTES,
    MAX_UPLOAD_FILE_SIZE,
    MIN_CHUNK_SIZE,
    VALID_UPLOAD_MODES,
    complete_upload,
    get_status,
    init_upload,
    upload_chunk,
)
from app.services.upload.register import register_photo_content

__all__ = [
    "DEFAULT_CHUNK_SIZE",
    "MAX_CHUNK_COUNT",
    "MAX_INLINE_MERGE_BYTES",
    "MAX_UPLOAD_FILE_SIZE",
    "MIN_CHUNK_SIZE",
    "VALID_UPLOAD_MODES",
    "complete_upload",
    "get_status",
    "init_upload",
    "register_photo_content",
    "upload_chunk",
]
