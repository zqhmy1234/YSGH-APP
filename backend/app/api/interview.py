"""F7 冷启动访谈 API（B1-7 · 产品部三问）

GET  /api/v1/interview/questions          —— 三问（最重要的人/人生转折/最骄傲的事）
POST /api/v1/interview/answers            —— 提交答案 → 画像维度激活 + 复述确认
GET  /api/v1/interview/profile            —— 画像（冷启动状态）
"""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.common import ApiResponse
from app.schemas.interview import InterviewAnswers, InterviewResult
from app.services.interview import QUESTIONS, get_profile, submit_answers

router = APIRouter(prefix="/api/v1/interview", tags=["interview"])


@router.get("/questions", response_model=ApiResponse)
def interview_questions():
    return ApiResponse(data=QUESTIONS)


@router.post("/answers", response_model=ApiResponse[InterviewResult])
def interview_answers(
    req: InterviewAnswers,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    result = submit_answers(db, user.id, req.answers)
    return ApiResponse(data=InterviewResult(**result))


@router.get("/profile", response_model=ApiResponse)
def interview_profile(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    return ApiResponse(data=get_profile(db, user.id))
