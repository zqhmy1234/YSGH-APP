"""真实照片基准加载器（R1#14 迁移 shim · 逻辑单一来源在 scripts/agg_load_real_photos.py）

本模块仅保留为包内兼容转发：`from app.services.event_aggregation.load_real_photos
import RawPhoto, load_screenshots, sample_500` 的存量调用（test_event_aggregation_scripts /
run_validation）继续可用。加载器本体已随 R1#14 移入 `scripts/agg_load_real_photos.py`。
"""
from __future__ import annotations

import sys
from pathlib import Path

# 仓库根入 path（使 scripts 可被导入；pytest 只把 backend/ 加进 sys.path，需手动补根）
_REPO_ROOT = Path(__file__).resolve().parents[4]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from scripts.agg_load_real_photos import RawPhoto, load_screenshots, sample_500  # noqa: E402

__all__ = ["RawPhoto", "load_screenshots", "sample_500"]
