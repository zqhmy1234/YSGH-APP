"""存储对象键的**唯一布局来源**（D02-7 / B11 声明漂移）。

原状：`photos/` 命名空间存在**两套键布局**——
  · `services/upload/protocol._final_key` → `photos/{uid}/{yyyymm}/{hex12}_{name}`
  · `services/photo_content`（multipart 路径）→ `photos/{uid}/{hex32}{ext}`（**无 yyyymm 段**）
⇒ 使 `media_url` / `thumbnails` 文档里「`photos/{user_id}/{yyyymm}/{filename}`」的描述
对 **multipart 上传**不成立（同一命名空间两种形态）。

现收敛为**单一布局** `photos/{uid}/{yyyymm}/{hex12}_{safe_name}`，两条上传路径共用本函数。

读取侧无需改动（**保留历史键读取**）：对象键按值存取；且唯一"解析键"的地方
`thumbnails.derive_thumbnail_key` 只把**首段目录**替换为 `thumbnails/`（prefix 级、与布局无关）。
媒体键前缀白名单仍是 `photos/`，不变。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone


def photo_object_key(user_id: str, file_name: str) -> str:
    """照片原件对象键（**唯一布局**）：`photos/{uid}/{yyyymm}/{hex12}_{safe_name}`。

    `file_name` 会被清洗为 `[A-Za-z0-9._-]`（其余剔除）；全被剔除时回退 `file`。
    """
    safe = "".join(c for c in file_name if c.isalnum() or c in "._-") or "file"
    now = datetime.now(timezone.utc)
    return f"photos/{user_id}/{now:%Y%m}/{uuid.uuid4().hex[:12]}_{safe}"
