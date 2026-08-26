"""F7 冷启动访谈契约（B1-7 产品部三问）"""
from pydantic import BaseModel, Field, field_validator
from pydantic_core import PydanticCustomError

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
        """R4#13：key 白名单（未知键 422，fail-closed）+ 值长度上限（防脏数据/存储膨胀）

        PydanticCustomError（非 ValueError）：避免 ctx.error 携带不可序列化异常对象，
        导致 FastAPI 422 信封序列化 500（errors.py validation_error_handler 直序列化 errors）。
        """
        unknown = set(v) - ANSWER_KEYS
        if unknown:
            raise PydanticCustomError(
                "answer_key_invalid", f"不支持的答案键：{sorted(unknown)}", {}
            )
        for key, val in v.items():
            if len(val) > _ANSWER_MAX_LEN:
                raise PydanticCustomError(
                    "answer_value_too_long",
                    f"答案过长（{key} 超过 {_ANSWER_MAX_LEN} 字）",
                    {},
                )
        return v


class InterviewResult(BaseModel):
    dimensions: dict[str, list[str]]
    confirmation: str
