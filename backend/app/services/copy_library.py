"""文案库加载服务（US-21 兜底 · Wave1-B2）

数据源：`docs/copy_library/*.json`（C2 产出：schema.json 定义 + care_copy.json 内容；
6 场景键 sad_ask/sad_respond/angry/late_night/day2/day3，含占位符规范/候选数组）。

加载器职责（契约 C8）：
  - 读 JSON → 最小 schema 校验（键对齐 6 场景、title/body 非空）
  - 文件缺失 / 解析失败 / schema 不符 → 回退 notify.py 内置 CARE_TEMPLATES 占位
    （**不报错、不 500**）
  - lru_cache 缓存（文件变更需重启生效，注释说明）
  - notify.py 仅改消费点：maybe_send_emotion_care 内经 get_template(scene) 取文案，
    触发逻辑（0.7 阈值 / 深夜 22-05 / 回看 3 天 / 频次递减 / _SAD_REASON_MARKERS）零改动
"""
from __future__ import annotations

import json
import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger("yishu.copy_library")

# 文案库目录（相对仓库根解析：backend/app/services/copy_library.py → parents[3] = 仓库根）
_REPO_ROOT = Path(__file__).resolve().parents[3]
COPY_LIBRARY_DIR = _REPO_ROOT / "docs" / "copy_library"

# 6 场景键（对齐 notify.CARE_TEMPLATES 契约；数据缺键 → 该场景回退内置）
CARE_SCENES = ("sad_ask", "sad_respond", "angry", "late_night", "day2", "day3")

# 数据文件名约定（C2）：schema.json=定义（加载器不读）、care_copy.json=内容；
# 兼容旧名/别名：找不到 care_copy.json 时扫描 *.json（排除 schema/template_pool）
_CARE_COPY_FILENAME = "care_copy.json"
_SKIP_FILENAMES = {"schema.json", "template_pool.json"}


def _fallback_templates() -> dict[str, dict[str, str]]:
    """回退源：notify.py 内置 CARE_TEMPLATES（懒加载，避免 notify↔copy_library 循环 import）"""
    from app.services.notify import CARE_TEMPLATES

    return CARE_TEMPLATES


def _read_care_json() -> dict | None:
    """读数据文件：优先 care_copy.json，缺失则扫描 *.json；解析失败跳过。
    返回顶层 dict；任何失败返回 None（不抛异常，走回退）。
    """
    if not COPY_LIBRARY_DIR.is_dir():
        return None
    ordered = [COPY_LIBRARY_DIR / _CARE_COPY_FILENAME]
    ordered += sorted(
        p
        for p in COPY_LIBRARY_DIR.glob("*.json")
        if p.name not in _SKIP_FILENAMES and p.name != _CARE_COPY_FILENAME
    )
    for path in ordered:
        if not path.is_file():
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            logger.warning("copy_library 文件解析失败（跳过回退扫描）: %s", path)
            continue
        if isinstance(data, dict):
            return data
    return None


def _normalize(raw: dict) -> dict[str, list[dict]]:
    """最小 schema 校验 + 规范化：{scene: [{title, body}, ...]}。

    接受两种数据形态：{scene: {title, body}} 或 {scene: {candidates: [{title,body},...]}}
    （C8 占位符规范/候选数组）。缺 title/body 的条目丢弃；非 6 键忽略。
    """
    out: dict[str, list[dict]] = {}
    for scene in CARE_SCENES:
        val = raw.get(scene)
        if not isinstance(val, dict):
            continue
        cands = val.get("candidates")
        if isinstance(cands, list):
            picked = [c for c in cands if isinstance(c, dict) and c.get("title") and c.get("body")]
        else:
            picked = [val] if val.get("title") and val.get("body") else []
        if picked:
            out[scene] = [{"title": c["title"], "body": c["body"]} for c in picked]
    return out


@lru_cache(maxsize=1)
def _candidates() -> dict[str, list[dict]]:
    """数据文件候选（仅文件命中，缓存；文件变更需重启生效——lru_cache 语义）"""
    raw = _read_care_json()
    return _normalize(raw) if raw else {}


def reload_care_templates() -> None:
    """清缓存（测试注入数据路径/部署热更后调用）；下次 get_template 重读文件"""
    _candidates.cache_clear()


def load_care_templates() -> dict[str, list[dict]]:
    """加载文案库候选：{scene: [{title, body}, ...]}（仅文件命中；无数据 → {}）

    供测试/潜在轮换逻辑直接使用；notify 消费走 get_template（带内置回退）。
    """
    return _candidates()


def get_template(scene: str) -> dict | None:
    """按场景取文案：数据候选优先（取首条），缺该场景/无数据 → 回退内置 CARE_TEMPLATES。

    返回 {title, body} 或 None（场景既无数据也不在内置，正常不会发生）。
    """
    cands = _candidates().get(scene)
    if cands:
        return cands[0]
    return _fallback_templates().get(scene)


def get_care_templates() -> dict[str, dict[str, str]]:
    """合并模板全集：{scene: {title, body}}（数据优先、缺键回退内置；供上层整体消费）"""
    return {scene: (get_template(scene) or {}) for scene in CARE_SCENES}
