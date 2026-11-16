"""媒体路由：TTS 合成 / ASR 识别 / 音色列表。"""

from __future__ import annotations

from fastapi import APIRouter, File, UploadFile

from ..constants import INTERVIEWER_OPTIONS
from ..schemas import ASRResponse, TTSRequest, TTSResponse
from ..services import media

router = APIRouter()


@router.post("/tts/synthesize", response_model=TTSResponse, summary="文本转语音（含句级时间戳）")
async def tts_synthesize(req: TTSRequest) -> TTSResponse:
    """把面试官文本合成为语音。

    - 返回 `provider="edge"` 时前端播放 `audio_url`；
    - 返回 `provider="browser"` 时前端改用浏览器 `speechSynthesis`（零成本兜底）。
    """
    data = await media.synthesize(req.text, req.voice, req.rate)
    return TTSResponse(**data)


@router.post("/asr/recognize", response_model=ASRResponse, summary="语音转文本（可选云服务）")
async def asr_recognize(audio: UploadFile = File(...)) -> ASRResponse:
    """上传音频做识别。未配置云 ASR 时返回 `provider="browser"`，前端改用 Web Speech API。"""
    content = await audio.read()
    data = await media.recognize(content)
    return ASRResponse(**data)


@router.get("/media/tts/voices", summary="可用音色列表")
async def tts_voices() -> dict:
    return {
        "provider": media.tts_status(),
        "asr_provider": media.asr_status(),
        "voices": [
            {
                "key": opt["key"],
                "label": opt["label"],
                "provider": media.tts_status(),
                "style": opt["style"],
            }
            for opt in INTERVIEWER_OPTIONS
        ],
    }
