"""缩略图下发路由（B4 · Wave3 AgentG · audit #1 缺口修复）

GET /api/v1/thumbnails/{content_id} —— 返回缩略图 JPEG 字节

契约（供 Windows 端 / 客户端列表加载）：
- 默认拉缩略图（本端点）；原图按需（/api/v1/contents/{id} 或直链）——"默认缩略图 + 原图按需"
- 归属校验：他人内容 → 404（与其它内容路由一致，防 IDOR）
- 懒生成兜底：首次访问时若无 thumbnail_key 则按需生成后返回（上传完成即入队预生成，
  此兜底保证既有照片/跨进程存储也能即时下发）
- 不可用（非照片/无原件/解码失败）→ 404，客户端回退原图显示
- Cache-Control: public, max-age=86400（缩略图内容确定性，可浏览器/CDN 缓存）

⚠️ 需集成 Agent 在 backend/app/main.py 接线：
    from app.api.thumbnails import router as thumbnails_router
    app.include_router(thumbnails_router)
（本文件不改共享的 main.py；接线后由集成 Agent 重导出 OpenAPI）
"""
import logging

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.core.errors import ApiError
from app.db.models import User
from app.db.session import get_db
from app.services import thumbnails

logger = logging.getLogger("yishu.thumbnails")

router = APIRouter(prefix="/api/v1/thumbnails", tags=["thumbnails"])

# 缩略图确定性内容 → 客户端/CDN 可缓存 1 天
CACHE_CONTROL = "public, max-age=86400"


@router.get("/{content_id}")
def get_content_thumbnail(
    content_id: str,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """返回照片缩略图 JPEG（归属校验 + 懒生成兜底）"""
    try:
        data, content_type = thumbnails.get_thumbnail_bytes(db, content_id, user.id)
    except KeyError as exc:
        raise ApiError("THUMB_001", f"缩略图不可用: {exc}", http=404) from exc
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": CACHE_CONTROL},
    )
