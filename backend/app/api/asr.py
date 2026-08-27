"""ASR 转写 + 内容护栏路由（F3 语音输入 / B5b 护栏 / B5-c 情绪关怀）"""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api import make_router
from app.api.deps import get_current_user
from app.core.errors import ERR_ASR_001, ERR_ASR_002, ApiError
from app.db.models import User
from app.db.session import get_db
from app.schemas.asr import (
    AsrTranscribeResponse,
    GuardCheckRequest,
    GuardCheckResponse,
    GuardrailVerdict,
)
from app.schemas.common import ApiResponse
from app.services.external.asr import (
    AsrError,
    temporary_suffix,
    transcribe,
    validate_audio_bytes,
)
from app.services.external.dashscope import moderate

router = make_router(prefix="/api/v1/asr", tags=["asr"])
# 护栏独立域（P2-06 前缀统一：guard/check 不属于 ASR 域，独立 /api/v1/guard）
guard_router = make_router(prefix="/api/v1/guard", tags=["guard"])

# ASR 校验类错误码（音频参数/格式问题 → 422 ASR_001；其余为服务不可用 → 503 ASR_002）
_ASR_VALIDATION_CODES = {
    "AUDIO_NOT_FOUND",
    "AUDIO_TOO_LARGE",
    "EMPTY_AUDIO",
    "INVALID_AUDIO",
    "UNSUPPORTED_FORMAT",
}


def _asr_error(exc: AsrError) -> ApiError:
    """AsrError → ApiError（R4#1 收口：统一 ASR_001/ASR_002 映射，details 走 to_dict 单一来源）"""
    is_validation = exc.code in _ASR_VALIDATION_CODES
    return ApiError(
        ERR_ASR_001 if is_validation else ERR_ASR_002,
        exc.message,
        http=422 if is_validation else 503,
        details=exc.to_dict(),
    )


def _verify_audio(data: bytes, filename: str | None) -> tuple[bytes, str]:
    """校验大小、扩展名与音频魔数，返回原数据和规范格式。"""
    return data, validate_audio_bytes(data, filename)


@router.post("/transcribe", response_model=ApiResponse[AsrTranscribeResponse])
def transcribe_audio(
    file: UploadFile = File(..., description="M4A/WAV/MP3/AAC 等音频（≤8MB）"),
    preferred: str = Query("auto", pattern="^(auto|funasr|sensevoice|mock)$"),
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """语音转写（API-004 主链路：上传 → 双通道转写 → 护栏审核）

    护栏（B5b）：转写结果先过 dashscope.moderate 再返回；
    拦截时 text 仍返回（供端侧展示），但 passed=false 提示不可下发。
    情绪（B5-c）：SenseVoice 通道产出 emotion，供情绪关怀分层触发。
    """
    try:
        data, audio_format = _verify_audio(file.file.read(), file.filename)
    except AsrError as exc:
        raise _asr_error(exc) from exc

    # 临时文件保留真实扩展名，Fun-ASR Flash 依此构造 format 与 MIME。
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(suffix=temporary_suffix(audio_format), delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        try:
            result = transcribe(tmp_path, preferred=preferred)
        except AsrError as exc:
            raise _asr_error(exc) from exc

        if result.outcome == "no_speech":
            verdict = {"pass": True, "reason": "no-speech"}
        else:
            verdict = moderate(result.text)
        return ApiResponse(
            data=AsrTranscribeResponse(
                text=result.text,
                outcome=result.outcome,  # type: ignore[arg-type]
                channel=result.channel,  # type: ignore[arg-type]
                emotion=result.emotion,
                emotion_confidence=result.emotion_confidence,
                emotion_source=result.emotion_source,
                emotion_model=result.emotion_model,
                confidence=result.confidence,
                duration_ms=result.duration_ms,
                mock=result.mock,
                retryable=result.retryable,
                model=result.model,
                provider_request_id=result.provider_request_id,
                audio_format=result.audio_format,
                source_audio_sha256=result.source_audio_sha256,
                errors=result.errors,
                # B5a Wave4 AgentJ：音频事件/噪音/段级合并（J-1/J-2/J-3）
                audio_events=result.audio_events,
                emotion_bonus=result.emotion_bonus,
                silence_hint=result.silence_hint,
                not_oral=result.not_oral,
                snr_db=result.snr_db,
                noise_weight=result.noise_weight,
                emotion_merge=result.emotion_merge,
                guardrail=GuardrailVerdict(passed=verdict["pass"], reason=verdict["reason"]),
            )
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@guard_router.post("/check", response_model=ApiResponse[GuardCheckResponse])
def guard_check(
    req: GuardCheckRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """内容安全护栏（B5b）：发布/入库前文本审核，fail-safe 默认拦截（决策 #12）"""
    verdict = moderate(req.text)
    return ApiResponse(data=GuardCheckResponse(passed=verdict["pass"], reason=verdict["reason"]))
