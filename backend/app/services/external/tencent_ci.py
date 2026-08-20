"""腾讯云数据万象（CI）图片能力（WP-E 2026-08-19）

- image_detect_label(image_key)：图片标签（打标，~0.0015 元/次）→ 场景/物体标签落库
  （F1 L2 场景标签、搜索标签增强）
- image_audit(image_key)：图片内容审核（敏感识别，S4-03：命中不进云端镜像）

凭证复用 COS（TENCENT_SECRET_ID/SECRET_KEY + COS_BUCKET/COS_REGION，config 别名读取已对齐）。
测试策略：mock 单测（monkeypatch CosS3Client）+ 真图冒烟（征求用户同意后执行，微量费用）。
"""
from __future__ import annotations

import logging

from app.core.config import settings
from app.services.external.retry import with_retry

logger = logging.getLogger("yishu.tencent_ci")


def _client():
    from qcloud_cos import CosConfig, CosS3Client

    if not (settings.tencent_secret_id and settings.tencent_secret_key and settings.cos_bucket):
        raise RuntimeError("腾讯云未配置：TENCENT_SECRET_ID/TENCENT_SECRET_KEY/COS_BUCKET")
    config = CosConfig(
        Region=settings.cos_region,
        SecretId=settings.tencent_secret_id,
        SecretKey=settings.tencent_secret_key,
    )
    return CosS3Client(config)


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def image_detect_label(image_key: str, min_confidence: int = 50) -> list[str]:
    """图片标签：返回 Tags 列表（如 ['截图','课程表','人像']，按置信度过滤）"""
    client = _client()
    resp = client.ci_image_detect_label(
        Bucket=settings.cos_bucket,
        Key=image_key,
        Scenes="camera,web",
    )
    # 真实响应结构：{CameraLabels: {Labels: [{Name, Confidence, FirstCategory, SecondCategory}]}, WebLabels: {...}}
    tags: list[str] = []
    for section in ("CameraLabels", "WebLabels"):
        labels = ((resp.get(section) or {}).get("Labels") or []) if isinstance(resp, dict) else []
        for tag in labels:
            try:
                conf = int(tag.get("Confidence", 0))
            except (TypeError, ValueError):
                conf = 0
            name = tag.get("Name")
            if name and conf >= min_confidence and name not in tags:
                tags.append(str(name))
    return tags


@with_retry(retries=3, backoff=(1, 2, 4), timeout=30)
def image_audit(image_key: str) -> dict:
    """图片内容审核（同步批量审核）：返回 {pass: bool, labels: [...]}

    命中任一敏感标签（Porn/Illegal/Teenager/Advertise 等）→ pass=False。
    S4-03：微信收图入库前调用，命中则不进云端镜像。
    """
    from qcloud_cos.cos_comm import CiDetectType

    client = _client()
    resp = client.ci_auditing_image_batch(
        Bucket=settings.cos_bucket,
        Input=[{"Object": image_key}],
        DetectType=CiDetectType.PORN | CiDetectType.ILLEGAL | CiDetectType.TEENAGER,
        Async=0,
    )
    jobs = (resp.get("JobsDetail") or []) if isinstance(resp, dict) else []
    labels: list[str] = []
    blocked = False
    for job in jobs:
        for section in ("PornInfo", "IllegalInfo", "TeenagerInfo"):
            info = job.get(section) or {}
            hit = str(info.get("HitFlag", 0))
            if hit and hit != "0":
                blocked = True
                labels.append(f"{section}:{info.get('Label', hit)}")
    return {"pass": not blocked, "labels": labels}
