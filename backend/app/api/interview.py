"""F7 冷启动访谈 API（B1-7 · 产品部三问）

GET    /api/v1/interview/questions        —— 三问（最重要的人/人生转折/最骄傲的事）
POST   /api/v1/interview/answers          —— 提交答案 → 画像维度激活 + 复述确认
GET    /api/v1/interview/profile          —— 画像（冷启动状态）
DELETE /api/v1/interview/profile          —— 清除画像（真删除服务端数据；D07-12）
"""
from fastapi import Depends
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.interview import (
    InterviewAnswers,
    InterviewProfileOut,
    InterviewQuestion,
    InterviewResult,
    ProfileClearOut,
)
from app.services.interview import QUESTIONS, get_profile, submit_answers
from app.services.profile_admin import clear_profile

router = make_router(prefix="/api/v1/interview", tags=["interview"])


@router.get("/questions", response_model=ApiResponse[list[InterviewQuestion]])
def interview_questions(user: User = Depends(get_current_user)):
    """三问（静态文案）——TD-P3 L1（审查低危）：补鉴权，与全站鉴权约定一致

    内容为静态三问（信息面极小），但公开端点与全站鉴权约定不一致；客户端
    api.ts 已自动携带 Bearer（无 token 时 401 → 自动登录重放），补鉴权无兼容影响。
    """
    return ApiResponse(data=QUESTIONS)


@router.post("/answers", response_model=ApiResponse[InterviewResult])
def interview_answers(
    req: InterviewAnswers,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = submit_answers(db, user.id, req.answers)
    return ApiResponse(data=InterviewResult(**result))


@router.get("/profile", response_model=ApiResponse[InterviewProfileOut])
def interview_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ApiResponse(data=get_profile(db, user.id))


@router.delete("/profile", response_model=ApiResponse[ProfileClearOut])
def interview_profile_clear(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """清除本人画像（**真删除服务端数据** · D07-12）。

    契约（与客户端「清除画像数据」面板对齐）：
    - 删除：画像维度值 / 维度历史 / 未命中枚举的原始回答 / 低置信候选池 / **证据锚点**；
    - **保留**：敏感话题（`profile_sensitive`）—— 它是"别再提这些话题"的保护性数据，
      清除画像不应连带移除保护；保留条数在响应里如实回报；
    - **幂等**：无画像数据时各计数为 0，仍返回 200（重复点击/重试安全）；
    - 只清**本人**数据（user_id 只取自 token）。

    说明：客户端此前该按钮**只清本地**（弹窗文案却承诺"删除全部画像维度与证据锚点"，
    属失实宣称）——本端点落地后，文案与行为一致。
    """
    return ApiResponse(data=ProfileClearOut(**clear_profile(db, str(user.id))))
