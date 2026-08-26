"""F7 冷启动访谈契约（B1-7 产品部三问）"""
from pydantic import BaseModel, Field, field_validator

# R4#13（输入校验）：产品部三问固定键白名单 + 答案长度上限
ANSWER_KEYS = frozenset({"important_person", "life_turn", "proud_thing"})
_ANSWER_MAX_LEN = 2000


class InterviewAnswers(BaseModel):
    answers: dict[str, str] = Field(
        ...,
        description='三问答案：{"important_person": "...", "life_turn": "...", "proud_thing": "..."}',
    )

    @field_validator("answers")
    @classmethod
    def _validate_answer_keys_and_length(cls, v: dict[str, str]) -> dict[str, str]:
        """R4#13：key 白名单（未知键 422，fail-closed）+ 值长度上限（防脏数据/存储膨胀）"""
        unknown = set(v) - ANSWER_KEYS
        if unknown:
            raise ValueError(f"不支持的答案键：{sorted(unknown)}")
        for key, val in v.items():
            if len(val) > _ANSWER_MAX_LEN:
                raise ValueError(f"答案过长（{key} 超过 {_ANSWER_MAX_LEN} 字）")
        return v


class InterviewResult(BaseModel):
    dimensions: dict[str, list[str]]
    confirmation: str
