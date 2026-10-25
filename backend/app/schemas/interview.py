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


class InterviewQuestion(BaseModel):
    """产品部三问（静态文案，GET /api/v1/interview/questions）"""

    key: str
    question: str


class DimensionEvidence(BaseModel):
    """一条画像依据（**画像可解释** · 2026-10-26 task-21 B 方案）。

    为什么现在才有：`display_dimensions()` 把结构化 entry 压平成 `{dim: [值]}`，
    evidence 在那一层被丢掉 —— 于是详情页只能给结论、给不出依据，用户无从判断
    该不该信。本类是该字段的**唯一契约形状**。

    字段取值语义：
      · `text`       —— 用户自己写过的那句话（`profile_annotation_pool.raw_text`
                        或标注时的 `evidence_text`），**不是 AI 再生成的一段话**；
      · `ts`         —— 该依据产生的时刻（ISO-8601）。有了它用户才能判断
                        「很久以前提过一次」还是「反复出现」—— 两者可信度完全不同；
      · `content_id` —— 来源内容 id（**该用户自己的**内容；2026-10-26 全库实测
                        无跨用户情况）。给出去是为了让客户端将来能跳回原始记忆。
    三个字段都可能为 `None`（历史数据缺失），故一律 `str | None` 而非省略字段 ——
    字段形态稳定，客户端不必做存在性判断。
    """

    ts: str | None = None
    text: str = ""
    content_id: str | None = None


class InterviewProfileOut(BaseModel):
    """画像出参（GET /api/v1/interview/profile）

    ⚠️ **`dimensions` 的形状是已发布契约，逐字不变**（`dict[str, list[str]]`）：
    客户端 `utils/play_interview.uts::flattenDimensions` 与 `profile_labels.uts`
    都按 `list[str]` 读。要补信息只能**新增并列字段**，不能改形状（改形状会让
    老客户端崩；新增字段老客户端读到不认识就忽略）。

    `dimension_evidence`（2026-10-26 · task-21 B 方案）：
      有依据的维度 → `{dim: [{ts, text, content_id}]}`；**没有依据的维度不下发 key**
      （即 `{}` 而不是 `{dim: []}`）—— 后者会让客户端误以为"有依据但空了"。
    """

    dimensions: dict[str, list[str]]
    version: int
    cold_start_done: bool
    # 默认空 dict：老客户端请求到本字段也安全；新客户端未升级服务端时也拿不到 None
    dimension_evidence: dict[str, list[DimensionEvidence]] = Field(default_factory=dict)


class ProfileClearOut(BaseModel):
    """画像清除出参（DELETE /api/v1/interview/profile · D07-12）。

    `cleared` 逐表回报删除行数（客户端可如实告诉用户"删掉了什么"），
    `sensitive_topics_kept` = **刻意保留**的敏感话题条数（那是"别再提这个话题"的
    保护性数据，清除画像不应连带移除保护——见 `services/profile_admin` 模块说明）。
    """

    cleared: dict[str, int] = Field(
        default_factory=dict, description="逐表删除行数（表名 → 行数）"
    )
    sensitive_topics_kept: int = Field(0, description="保留的敏感话题条数（保护性数据）")
    total: int = Field(0, description="本次删除总行数")
