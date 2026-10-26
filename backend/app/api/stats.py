"""统计域端点（R9-7 · Q5 拍板：hero 副标题 API 化）

GET /api/v1/stats/daily-summary —— hero 副标题数据源（前端 onLoad 拉取，
替代 R8 本地 timeline 全量计算）。

## 2026-10-26 口径修正（**假状态修复**：hero 数字与列表不同源）

原实现 `count` 数的是「事件经 event_items 关联的 distinct 内容数」，而时间轴列表
（`TabIndex` / `entries_api.fetchEntryFeed`）读的是 **`contents`**。两张表在本库
长期脱钩（实测 2026-10-26：全库 `event_items` 仅 **4** 行，而 `contents` **9779** 行），
后果是同一屏上「上面的数字」与「下面能滚到的内容」来自互不相交的数据源：

  主账号 `e4134743-…`：旧口径 count = **0** ⇒ hero 显示「暂无新记忆」，
  而列表实际可渲染 **281** 条（本月 208 条）。

这是一条**假状态**（「没有新记忆」这句话本身是假的），比数字偏小更贵：用户会以为
记忆丢了。故 `count` 改为与列表**同源同口径**——`contents` 表、当前自然月、
且只数列表真能渲染的类型（`text` / `photo`；`voice` 在 `entries_api.feedAssemble`
里被显式跳过，数进来就又对不上了）。

`latest_event_text` / `latest_event_time` 同样改取**最新一条内容**（不再取事件）：
否则「N 月 · X 条新记忆」的月份来自事件、条数来自内容，两个字段各说各话。
字段名保持不变（`ApiResponse[dict]`，schema 无字段级约束 ⇒ openapi 快照不受影响），
但语义已在下方 docstring 写明为「最新一条记忆」。
"""
from fastapi import Depends
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import Content, User
from app.db.session import get_db
from app.schemas.common import ApiResponse

router = make_router(prefix="/api/v1/stats", tags=["stats"])

# 列表真能渲染的内容类型。`entries_api.feedAssemble` 只产 text/photo 两种条目
# （voice 要接播放管道/波形，属独立工作面并**显式 continue 跳过**）⇒ 计数必须同集合，
# 否则「数字」与「能滚到的条数」再次脱钩。
_LISTABLE_TYPES: tuple[str, ...] = ("text", "photo")


@router.get("/daily-summary", response_model=ApiResponse[dict])
def daily_summary(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """hero 副标题三字段（count / latest_event_text / latest_event_time）

    口径（2026-10-26 起，与时间轴列表**同源**）：
      · count：当前自然月内、未软删、类型属 `_LISTABLE_TYPES` 的 `contents` 条数；
      · latest_event_text：最新一条记忆的可读文案（文字取正文首行；照片取
        `ai_description`，缺失退化「照片记忆」）——**不再是事件标题**；
      · latest_event_time：该条记忆的时刻（`taken_at` 优先、缺失回退 `created_at`）。

    无内容时返回 count=0 + 两个 None —— 前端据此清空/退化为「暂无新记忆」，
    此时那句话是**真的**（旧口径下它是假的：明明有 281 条）。
    """
    month_start = db.execute(select(func.date_trunc("month", func.now()))).scalar()
    # 记忆时刻：照片用拍摄时刻（与列表 `entries_api` 的 taken_at 优先同口径），缺失回退记录时刻
    content_at = func.coalesce(Content.taken_at, Content.created_at)

    count_row = db.execute(
        select(func.count(func.distinct(Content.id))).where(
            Content.user_id == user.id,
            Content.deleted_at.is_(None),
            Content.content_type.in_(_LISTABLE_TYPES),
            func.date_trunc("month", content_at) == month_start,
        )
    ).scalar()

    latest = db.execute(
        select(
            Content.content_type,
            Content.text,
            Content.ai_description,
            content_at.label("at"),
        )
        .where(
            Content.user_id == user.id,
            Content.deleted_at.is_(None),
            Content.content_type.in_(_LISTABLE_TYPES),
        )
        .order_by(content_at.desc(), Content.id.desc())
        .limit(1)
    ).first()

    latest_text: str | None = None
    latest_time: str | None = None
    if latest is not None:
        latest_text = _summary_text(latest.content_type, latest.text, latest.ai_description)
        # 文案取不到（如照片描述生成失败）⇒ 如实置 None，让前端退化为「暂无新记忆」
        # 而不是用「照片记忆」这种占位词假装有一条可读记忆。
        if latest.at is not None:
            latest_time = latest.at.isoformat()

    # BA1（迁移 f2a3b4c5d6e7）：用量字节聚合——该用户未删除内容 size_bytes 求和
    # （NULL 不参与 SUM；全空 → None → 归 0，与前端「无数据清零」口径一致）
    total_bytes = db.execute(
        select(func.coalesce(func.sum(Content.size_bytes), 0)).where(
            Content.user_id == user.id, Content.deleted_at.is_(None)
        )
    ).scalar()
    return ApiResponse(
        data={
            "count": int(count_row or 0),
            "latest_event_text": latest_text,
            "latest_event_time": latest_time,
            "total_bytes": int(total_bytes or 0),
        }
    )


def _summary_text(content_type: str, text: str | None, ai_description: str | None) -> str | None:
    """把一条内容压成 hero 副标题可用的短文案（≤40 字，单行）。

    照片**不取** `text`：照片的 `text` 是 `caption\\nocr_text` 拼接串（检索要整串），
    取它等于把截图界面词（「此电脑 / 本地磁盘 / (D:)」）搬到首页副标题上——
    正是详情页 2026-10-26 E-1 修掉的同一个病根的第三个受害面。
    照片优先 `ai_description`（人话），缺失则返回 None（不编占位词）。
    """
    raw = ai_description if content_type == "photo" else text
    if not raw:
        return None
    line = raw.strip().splitlines()[0].strip() if raw.strip() else ""
    if not line:
        return None
    return line[:40]
