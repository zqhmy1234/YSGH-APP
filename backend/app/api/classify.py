"""文字碎片分类 API（F2 · M1 Part 3：SetFit 5 类）

POST /api/v1/classify
  {"text": "明天记得取快递"} →
  {"label": "todo", "label_cn": "待办", "confidence": 0.93, "scores": [...]}
"""
from fastapi import APIRouter, HTTPException

from app.schemas.classify import ClassifyRequest, ClassifyResult
from app.schemas.common import ApiResponse
from app.services.classifier import classify

router = APIRouter(prefix="/api/v1/classify", tags=["classify"])


@router.post("", response_model=ApiResponse[ClassifyResult])
def classify_text(req: ClassifyRequest):
    try:
        result = classify(req.text)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return ApiResponse(data=ClassifyResult(**result))
