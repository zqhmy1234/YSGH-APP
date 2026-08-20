"""F7 冷启动访谈契约（B1-7 产品部三问）"""
from pydantic import BaseModel, Field


class InterviewAnswers(BaseModel):
    answers: dict[str, str] = Field(
        ...,
        description='三问答案：{"important_person": "...", "life_turn": "...", "proud_thing": "..."}',
    )


class InterviewResult(BaseModel):
    dimensions: dict[str, list[str]]
    confirmation: str
