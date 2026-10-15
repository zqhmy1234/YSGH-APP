"""入库时逆地理补全（GPS 坐标 → 地名）。

## 为什么独立成钩子（2026-10-04）

此前逆地理编码只写在 `pipeline_photo._process_photo` 里，于是**只有照片**有地点：
文字与语音虽然 `ContentCreate` 同样接受 `gps_lat/gps_lng`、也会走
`process_content`，但两条处理器都不做逆地理 ⇒ `contents.place` 恒为空 ⇒
详情页/回响的地点行对非照片内容永远空白。

本模块把该逻辑收敛为**单一实现**，三条content_type 共用：

|content_type | 处理器                | 调用方                     |
|-------------|---------------------|--------------------------|
| `photo`     | `_process_photo`     | `pipeline_photo.py`（复用）   |
| `text`      | `_process_text`      | `pipeline.py`（新增一行）      |
| `voice`     | `_process_voice`     | `pipeline.py`（新增一行）      |

符合既有约束：`pipeline.py` 被波 D 冻结，「Wave0 钩子 = 唯一扩展入口」，
所以新增能力一律落`pipeline_ext/`，不在冻结文件里内联逻辑。

## 失败语义

拿不到地名就**留空**（`place` 保持 None），**绝不写占位文案**。
这与客户端的诚实空态是同一口径：定位是增强项，不该让用户看到编造的地点。
调用点各自 `try/except` 包裹，逆地理失败不影响内容本身的浏览与检索。
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

logger = logging.getLogger("yishu.pipeline_ext.place")


def enrich_place_on_ingest(db: Session, content: Any) -> str | None:
    """GPS → 地名，写回 `content.place`。

    返回补上的地名；未补（无坐标 / 已有值 / 调用失败）返回 None。
    幂等：已有 `place` 时直接跳过，不重复调高德（geohash 缓存 ≤30 天）。
    """
    lat = getattr(content, "gps_lat", None)
    lng = getattr(content, "gps_lng", None)
    if lat is None or lng is None:
        return None
    if getattr(content, "place", None):
        return None

    from app.services.external.amap import get_place

    place = get_place(db, lat, lng)
    if not place:
        return None
    content.place = place
    logger.info("逆地理补全 content=%s place=%s", getattr(content, "id", "?"), place)
    return place
