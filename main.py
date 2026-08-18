import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile

import db_milvus
import db_mysql
import storage
from clip_service import embed_image, embed_text
from ocr_service import ocr_image


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 启动时初始化数据库和向量库（只执行一次）
    db_mysql.init_db()
    db_milvus.get_store()
    yield


app = FastAPI(title="Photo Pipeline MVP", lifespan=lifespan)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.post("/upload")
async def upload(file: UploadFile = File(...), user_id: str = "default"):
    data = await file.read()

    # 1. 存文件
    try:
        rel_path = storage.save_image(data, file.filename or "upload.jpg")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    keep_original = os.getenv("KEEP_ORIGINAL", "true").lower() == "true"

    # 2. OCR
    try:
        text = ocr_image(data)
    except Exception as e:
        if not keep_original:
            storage.delete_image(rel_path)
        raise HTTPException(status_code=502, detail=f"OCR 失败: {e}")

    # 3. CLIP 向量化
    try:
        vector = embed_image(data)
    except Exception as e:
        if not keep_original:
            storage.delete_image(rel_path)
        raise HTTPException(status_code=500, detail=f"向量化失败: {e}")

    # 4. 隐私红线：云端不存原图时，识别完立即删除，后续任何失败都不留原图
    stored_path = rel_path
    if not keep_original:
        storage.delete_image(rel_path)
        stored_path = ""  # 元数据与响应都不再暴露已删除的原图路径

    # 5. 元数据 + 向量入库
    try:
        asset_id = db_mysql.save_asset(user_id, stored_path, text)
        db_milvus.insert_embedding(asset_id, vector)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"入库失败: {e}")

    return {
        "asset_id": asset_id,
        "file_path": stored_path,
        "ocr_text": text,
        "vector_dim": len(vector),
    }


@app.post("/search")
async def search(query: str, top_k: int = 10):
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
                    "ocr_text": asset["ocr_text"],
                }
            )
    return {"query": query, "results": results}
