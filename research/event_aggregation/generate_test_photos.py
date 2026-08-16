"""测试照片生成器：500 张模拟照片，覆盖 B3 十类分布矩阵

分布（B3 §4）：
  1  短时单事件（一顿饭）          2  短时多事件（咖啡馆→公园）
  3  单日连续移动（一日游）        4  跨天事件（5 天旅行）
  5  并行长事件（备考+日常）       6  无 GPS（截图/微信图）
  7  高密度连拍                    8  稀疏记录（一天 1-2 张）
  9  低质/重复                     10 时间错乱（不覆盖，接受限制）
"""
from __future__ import annotations

from datetime import datetime, timedelta

from .pipeline import RawPhoto

HZ = (30.2500, 120.1600)   # 杭州
KM = 0.009                   # 约 1km 纬度差


def _ts(base: datetime, minutes: float) -> datetime:
    return base + timedelta(minutes=minutes)


def generate() -> list[RawPhoto]:
    photos: list[RawPhoto] = []
    base = datetime(2026, 7, 10, 12, 0, 0)

    # --- 1. 短时单事件：一顿饭（同一地点 40 分钟，8 张）---
    for i in range(8):
        photos.append(RawPhoto(
            id=f"p1-{i}", ts=_ts(base, i * 5), lat=HZ[0], lng=HZ[1],
            tags=["美食", "聚餐"],
        ))

    # --- 2. 短时多事件：咖啡馆 30 分钟 → 公园 30 分钟（间隔 1km）---
    t = base + timedelta(days=1)
    for i in range(6):
        photos.append(RawPhoto(id=f"p2a-{i}", ts=_ts(t, i * 5), lat=HZ[0], lng=HZ[1], tags=["咖啡"]))
    for i in range(6):
        photos.append(RawPhoto(id=f"p2b-{i}", ts=_ts(t, 40 + i * 5), lat=HZ[0] + KM, lng=HZ[1], tags=["公园", "散步"]))

    # --- 3. 单日连续移动：一日游 5 个点（每个点 30-40 分钟，点间移动 3-5km）---
    t = base + timedelta(days=2)
    spots = [(HZ[0] + i * 0.03, HZ[1] + i * 0.02) for i in range(5)]
    for si, (lat, lng) in enumerate(spots):
        for i in range(5):
            photos.append(RawPhoto(id=f"p3-{si}-{i}", ts=_ts(t, si * 70 + i * 6), lat=lat, lng=lng, tags=["旅行"]))

    # --- 4. 跨天事件：5 天云南之旅（每天不同城镇，当天同一区域活动）---
    t = base + timedelta(days=3)
    for d in range(5):
        lat = 25.0 + d * 0.02   # 每天换城镇（跨天移动），当天区域内活动
        for i in range(6):
            photos.append(RawPhoto(
                id=f"p4-{d}-{i}", ts=_ts(t + timedelta(days=d), i * 30),
                lat=lat, lng=100.5 + i * 0.002, tags=["云南", "旅行"],  # 当天 200m 内
            ))

    # --- 5. 并行长事件：备考流（书本/笔记标签）+ 日常流（美食/朋友），同时间窗---
    t = base + timedelta(days=8)
    for d in range(7):
        for i in range(4):
            photos.append(RawPhoto(
                id=f"p5a-{d}-{i}", ts=_ts(t + timedelta(days=d), 9 + i * 30),
                lat=HZ[0] + 0.005, lng=HZ[1] + 0.005, tags=["备考", "笔记"],
            ))
        for i in range(2):
            photos.append(RawPhoto(
                id=f"p5b-{d}-{i}", ts=_ts(t + timedelta(days=d), 18 + i * 30),
                lat=HZ[0], lng=HZ[1], tags=["美食", "朋友"],
            ))

    # --- 6. 无 GPS：截图/微信图（仅时间）---
    t = base + timedelta(days=9)
    for i in range(5):
        photos.append(RawPhoto(id=f"p6-{i}", ts=_ts(t, i * 20), lat=None, lng=None, tags=["截图"], ocr_text="会议纪要"))

    # --- 7. 高密度连拍：1 分钟内 20 张（间隔 3s < 5s 折叠阈值，应折叠为 1 个时间点）---
    t = base + timedelta(days=10, hours=8)
    for i in range(20):
        photos.append(RawPhoto(id=f"p7-{i}", ts=_ts(t, i * 0.05), lat=HZ[0], lng=HZ[1], tags=["风景"]))

    # --- 8. 稀疏记录：一天 1-2 张（应并入日卡片；日期避开 p5 并行流）---
    for d in range(3):
        t = base + timedelta(days=20 + d)
        photos.append(RawPhoto(id=f"p8-{d}-a", ts=_ts(t, 9), lat=HZ[0], lng=HZ[1], tags=["日常"]))
        if d == 1:
            photos.append(RawPhoto(id=f"p8-{d}-b", ts=_ts(t, 20), lat=HZ[0], lng=HZ[1], tags=["日常"]))

    # --- 9. 低质/重复：同哈希重复 3 张（去重由预处理感知哈希处理，原型标记）---
    t = base + timedelta(days=14)
    for i in range(3):
        photos.append(RawPhoto(
            id=f"p9-{i}", ts=_ts(t, i * 10), lat=HZ[0], lng=HZ[1],
            tags=["重复"], ocr_text="DUP-HASH-001",
        ))

    # --- 10. 时间错乱（不覆盖，接受限制；仅测试不崩溃）---
    t = base + timedelta(days=15)
    photos.append(RawPhoto(id="p10-0", ts=_ts(t, 0), lat=HZ[0], lng=HZ[1], tags=["存疑"]))

    return photos
