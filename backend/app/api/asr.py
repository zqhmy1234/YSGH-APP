"""ASR 转写 + 内容护栏路由（F3 语音输入 / B5b 护栏 / B5-c 情绪关怀）"""
from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import APIRouter, Depends, File, Query, UploadFile
from sqlalchemy.orm import Session

from app.api.deps import get_current_user
from app.db.models import User
from app.db.session import get_db
from app.schemas.asr import (
    AsrTranscribeResponse,
    GuardCheckRequest,
    GuardCheckResponse,
    GuardrailVerdict,
)
from app.schemas.common import ApiResponse
from app.services.external.asr import transcribe
from app.services.external.dashscope import moderate

router = APIRouter(prefix="/api/v1", tags=["asr"])

# 上传上限：长录音 VAD 分段后单段 ≤ 60s（16kHz 16bit 单声道 ≈ 2MB）
_MAX_AUDIO_BYTES = 8 * 1024 * 1024


def _verify_audio(data: bytes) -> bytes:
    """大小/魔数校验（仅接受 wav/pcm；返回原数据）"""
    if len(data) == 0:
        raise ValueError("空音频文件")
    if len(data) > _MAX_AUDIO_BYTES:
        raise ValueError(f"音频超过 {_MAX_AUDIO_BYTES // 1024 // 1024}MB 上限")
    if not (data.startswith(b"RIFF") and data[8:12] == b"WAVE"):
        raise ValueError("仅支持 wav 格式（FunASR/SenseVoice 均要求 wav 16kHz 16bit）")
    return data


@router.post("/asr/transcribe", response_model=ApiResponse[AsrTranscribeResponse])
def transcribe_audio(
    file: UploadFile = File(..., description="wav 音频（16kHz 16bit 单声道，≤8MB）"),
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
        data = _verify_audio(file.file.read())
    except ValueError as exc:
        return ApiResponse(code="ASR_001", message=str(exc), data=None)

    # 临时文件落盘（双通道 SDK 均要求本地路径）
    tmp_path: Path | None = None
    try:
        with NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
            tmp.write(data)
            tmp_path = Path(tmp.name)
        result = transcribe(tmp_path, preferred=preferred)
        verdict = moderate(result.text)
        return ApiResponse(
            data=AsrTranscribeResponse(
                text=result.text,
                channel=result.channel,  # type: ignore[arg-type]
                emotion=result.emotion,
                confidence=result.confidence,
                duration_ms=result.duration_ms,
                mock=result.mock,
                guardrail=GuardrailVerdict(passed=verdict["pass"], reason=verdict["reason"]),
            )
        )
    finally:
        if tmp_path is not None and tmp_path.exists():
            tmp_path.unlink(missing_ok=True)


@router.post("/guard/check", response_model=ApiResponse[GuardCheckResponse])
def guard_check(
    req: GuardCheckRequest,
    _user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """内容安全护栏（B5b）：发布/入库前文本审核，fail-safe 默认拦截（决策 #12）"""
    verdict = moderate(req.text)
    return ApiResponse(data=GuardCheckResponse(passed=verdict["pass"], reason=verdict["reason"]))
