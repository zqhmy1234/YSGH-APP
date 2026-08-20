import hashlib
import os
import time
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import FastAPI, File, HTTPException, UploadFile
from pydantic import BaseModel

import db_milvus
import db_mysql
import event_service
import sensitive_guard
import storage
from clip_service import embed_image, embed_text
from ocr_service import ocr_image


class CheckRequest(BaseModel):
    hashes: list[str]


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库和向量库（只执行一次，老库自动迁移新列）
    db_mysql.init_db()
    db_milvus.get_store()
    yield


app = FastAPI(title="Photo Pipeline MVP", lifespan=lifespan)


def _resolve_original_time(data: bytes, photo_time: str | None) -> str:
    exif = storage.get_exif_datetime(data)
    if exif:
        return exif
    if photo_time:
        return photo_time
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def process_image(
    data: bytes,
    filename: str,
    user_id: str,
    user_consent: bool,
    photo_time: str | None = None,
) -> dict:
    """单张图片完整流程：校验 → 去重 → 向量化 → 敏感拦截 → OCR → 缩略图 → 入库。"""
    try:
        storage.validate(data, filename)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    content_hash = hashlib.sha256(data).hexdigest()
    original_time = _resolve_original_time(data, photo_time)

    # 去重（阶段二）：重复图片不重复 OCR / 不重复入库
    existing = db_mysql.find_asset_by_hash(content_hash)
    if existing:
        return {
            "asset_id": existing["id"],
            "file_path": existing["file_path"] or "",
            "thumb_path": existing["thumb_path"] or "",
            "ocr_text": existing["ocr_text"] or "",
            "vector_dim": 512,
            "content_hash": content_hash,
            "duplicate": True,
            "is_sensitive": bool(existing["is_sensitive"]),
        }

    # 本地向量化（拦截判定与检索共用一次计算，不上云）
    try:
        vector = embed_image(data)
    except Exception as e:
        db_mysql.log_task(content_hash, "clip", type(e).__name__, str(e))
        raise HTTPException(status_code=500, detail=f"向量化失败: {e}")

    # 敏感信息拦截（阶段一，仅证件类；未授权一律不放行）
    is_sensitive = False
    guard = {"score": 0.0, "matched": None}
    if os.getenv("SENSITIVE_BLOCK", "true").lower() == "true":
        try:
            guard = sensitive_guard.check_sensitive(vector)
            is_sensitive = guard["is_sensitive"]
        except Exception as e:
            db_mysql.log_task(content_hash, "guard", type(e).__name__, str(e))
            # 拦截器异常不阻断主流程，但记录日志
    if is_sensitive and not user_consent:
        raise HTTPException(
            status_code=400,
            detail={
                "error": "SENSITIVE_BLOCKED",
                "require_consent": True,
                "matched_type": guard["matched"],
                "score": guard["score"],
                "message": "检测到证件类图片，需用户授权后才可继续处理；即使授权也不会上传云端 OCR",
            },
        )

    # OCR：证件类即使授权也不上云 OCR（隐私红线）
    text = ""
    if not is_sensitive:
        last_err = None
        for attempt in range(2):  # 自动重试 1 次，间隔 1 秒
            try:
                text = ocr_image(data)
                break
            except Exception as e:
                last_err = e
                if attempt == 0:
                    time.sleep(1)
        else:
            db_mysql.log_task(content_hash, "ocr", type(last_err).__name__, str(last_err))
            raise HTTPException(status_code=502, detail=f"OCR 失败: {last_err}")

    # 存储：不存原图 → 只存缩略图；存原图 → 保持原样
    keep_original = os.getenv("KEEP_ORIGINAL", "true").lower() == "true"
    stored_path = ""
    thumb_path = ""
    if keep_original:
        try:
            stored_path = storage.save_image(data, filename)
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        try:
            thumb_path = storage.save_thumbnail(data)
        except Exception as e:
            # 缩略图失败不阻断主流程（无图可展示，但文字与向量仍可用）
            db_mysql.log_task(content_hash, "thumbnail", type(e).__name__, str(e))
            thumb_path = ""

    # 元数据 + 向量入库
    try:
        asset_id = db_mysql.save_asset(
            user_id,
            stored_path,
            text,
            content_hash,
            thumb_path,
            int(is_sensitive),
            original_time,
        )
        db_milvus.insert_embedding(asset_id, vector)
    except Exception as e:
        db_mysql.log_task(content_hash, "db", type(e).__name__, str(e))
        raise HTTPException(status_code=500, detail=f"入库失败: {e}")

    return {
        "asset_id": asset_id,
        "file_path": stored_path,
        "thumb_path": thumb_path,
        "ocr_text": text,
        "vector_dim": len(vector),
        "content_hash": content_hash,
        "duplicate": False,
        "is_sensitive": is_sensitive,
        "original_time": original_time,
    }


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
def upload(
    file: UploadFile = File(...),
    user_id: str = "default",
    user_consent: bool = False,
    photo_time: str | None = None,
):
    data = file.file.read()
    return process_image(data, file.filename or "upload.jpg", user_id, user_consent, photo_time)


@app.post("/upload/batch")
def upload_batch(
    files: list[UploadFile] = File(...),
    user_id: str = "default",
    user_consent: bool = False,
    photo_time: str | None = None,
):
    """批量上传：逐张独立处理，单张失败不影响其他张，同步返回结果数组。"""
    results = []
    for f in files:
        data = f.file.read()
        try:
            r = process_image(data, f.filename or "upload.jpg", user_id, user_consent, photo_time)
            results.append({"filename": f.filename, "status": "success", **r})
        except HTTPException as e:
            results.append(
                {
                    "filename": f.filename,
                    "status": "error",
                    "status_code": e.status_code,
                    "detail": e.detail,
                }
            )
        except Exception as e:
            db_mysql.log_task("", "batch", type(e).__name__, str(e))
            results.append(
                {"filename": f.filename, "status": "error", "status_code": 500, "detail": str(e)}
            )
    return {
        "results": results,
        "success": sum(1 for r in results if r["status"] == "success"),
        "failed": sum(1 for r in results if r["status"] == "error"),
    }


@app.post("/assets/check")
def assets_check(req: CheckRequest):
    """批量查重：客户端上传前先确认哪些图片已存在（增量同步用）。"""
    found = db_mysql.find_assets_by_hashes(req.hashes)
    return {
        "results": [
            {
                "hash": h,
                "exists": h in found,
                "asset_id": found[h]["id"] if h in found else None,
            }
            for h in req.hashes
        ]
    }


@app.post("/events/rebuild")
def events_rebuild(user_id: str = "default"):
    """全量重算事件分组（幂等）。"""
    return event_service.rebuild_events(user_id)


@app.get("/events")
def events_list(user_id: str = "default", limit: int = 50, offset: int = 0):
    rows = db_mysql.list_events(user_id, limit, offset)
    out = []
    for r in rows:
        cover = db_mysql.get_asset(r["cover_asset_id"]) if r["cover_asset_id"] else None
        out.append(
            {
                "event_id": r["id"],
                "start_time": r["start_time"],
                "end_time": r["end_time"],
                "photo_count": r["photo_count"],
                "cover_thumb": (cover or {}).get("thumb_path") or "",
            }
        )
    return {"events": out, "total": len(out)}


@app.get("/events/{event_id}")
def event_detail(event_id: int):
    ev = db_mysql.get_event(event_id)
    if not ev:
        raise HTTPException(status_code=404, detail="事件不存在")
    assets = db_mysql.get_event_assets(event_id)
    return {
        "event": ev,
        "assets": [
            {
                "asset_id": a["id"],
                "thumb_path": a["thumb_path"] or "",
                "original_time": a["original_time"] or "",
                "ocr_text": a["ocr_text"] or "",
                "is_sensitive": bool(a["is_sensitive"]),
            }
            for a in assets
        ],
    }


@app.post("/search")
def search(query: str, top_k: int = 10):
    """以文搜图：用文字向量检索最相似的图片。"""
    qvec = embed_text(query)
    hits = db_milvus.search_embedding(qvec, top_k=top_k)
    results = []
    for h in hits:
        asset = db_mysql.get_asset(h["entity"]["asset_id"])
        if asset:
            results.append(
                {
                    "asset_id": asset["id"],
                    "distance": h["distance"],
                    "ocr_text": asset["ocr_text"] or "",
                    "thumb_path": asset["thumb_path"] or "",
                    "is_sensitive": bool(asset["is_sensitive"]),
                }
            )
    return {"query": query, "results": results}


@app.post("/search_by_image")
def search_by_image(file: UploadFile = File(...), top_k: int = 10):
    """以图搜图：用图片向量检索最相似的图片（相似照片聚合）。"""
    data = file.file.read()
    try:
        storage.validate(data, file.filename or "upload.jpg")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    qvec = embed_image(data)
    hits = db_milvus.search_embedding(qvec, top_k=top_k)
    results = []
    for h in hits:
        asset = db_mysql.get_asset(h["entity"]["asset_id"])
        if asset:
            results.append(
                {
                    "asset_id": asset["id"],
                    "distance": h["distance"],
                    "ocr_text": asset["ocr_text"] or "",
                    "thumb_path": asset["thumb_path"] or "",
                    "is_sensitive": bool(asset["is_sensitive"]),
                }
            )
    return {"results": results}
