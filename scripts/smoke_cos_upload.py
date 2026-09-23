"""COS 分片断电续传实测（S5-03 · 用户要求 COS 与 MinIO 同等验证）

走 services/upload 状态机 + CosStorageBackend（真实 COS）：
init → 传 0、2 片（模拟中断）→ status（缺失 1）→ 补 1 → complete → 读回校验 → 清理。
费用：3 片 × 1KB + 合并对象 ≈0（极小对象，可忽略）。
用法：infisical run --env=dev --silent -- python scripts/smoke_cos_upload.py
"""
# ruff: noqa: E402  （先注入 sys.path 才能 import 仓内 app.*；同 backend/scripts 下各脚本约定）
import os
import sys
import uuid
from pathlib import Path

# 后端根目录：优先环境变量 YISHU_BACKEND_DIR；默认按脚本位置解析（scripts/ 的上一级），
# 不再硬编码 D:\GuangH-App\backend（重构波 B12-4 · D13 硬编码路径族）；解析失败即明确报错，不静默回退。
_BACKEND = os.environ.get("YISHU_BACKEND_DIR", str(Path(__file__).resolve().parent.parent / "backend"))
if not os.path.isdir(_BACKEND):
    raise SystemExit(f"未找到后端目录：{_BACKEND}（可用环境变量 YISHU_BACKEND_DIR 覆盖）")
sys.path.insert(0, _BACKEND)

from app.core.config import settings
from app.db.models import UploadChunk, UploadTask, User
from app.db.session import SessionLocal
from app.services import upload as up
from sqlalchemy import delete as sa_delete

settings.storage_backend = "cos"

CHUNK = 1024
data = bytes(range(256)) * 12  # 3072 bytes → 3 片

db = SessionLocal()
u = User(phone=f"cos-upload-{uuid.uuid4().hex[:8]}", status=1)
db.add(u)
db.commit()
db.refresh(u)
try:
    task = up.init_upload(db, u.id, f"cid-{uuid.uuid4().hex[:10]}", "cos.bin", len(data), CHUNK)
    print("init:", task.id[:8], "chunks:", task.chunk_count, "storage:", task.storage)

    for i in (0, 2):
        part = data[i*CHUNK:(i+1)*CHUNK]
        print("chunk", i, up.upload_chunk(db, task.id, i, part)["status"])

    st = up.get_status(db, task.id)
    print("resume: uploaded=", st["uploaded_chunks"], "missing=", st["missing_chunks"])
    assert st["missing_chunks"] == [1], "应缺失第 1 片"  # noqa: S101

    print("chunk 1", up.upload_chunk(db, task.id, 1, data[CHUNK:2*CHUNK])["status"])
    r = up.complete_upload(db, task.id)
    print("complete:", r["status"], "key:", r["file_key"])

    from app.services.external.storage import CosStorageBackend
    cos = CosStorageBackend()
    got = cos.get_object(r["file_key"])
    assert got == data, "COS 对象内容与原始数据不一致"  # noqa: S101
    print("COS 对象校验: OK", len(got), "bytes")
    assert not cos.object_exists(f"uploads/{task.id}/0.part")  # noqa: S101
    print("staging 清理: OK")
    cos.delete_object(r["file_key"])  # 清理测试对象
    print("测试对象清理: OK")
    print("RESULT: PASS")
finally:
    db.execute(sa_delete(UploadChunk).where(UploadChunk.upload_id == task.id))
    db.execute(sa_delete(UploadTask).where(UploadTask.id == task.id))
    db.delete(u)
    db.commit()
    db.close()
