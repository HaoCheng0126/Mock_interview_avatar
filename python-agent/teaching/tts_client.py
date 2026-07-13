"""TTS Client — DashScope CosyVoice wrapper for AI classmate voices.

Uses non-streaming HTTP synthesis. Returns audio URL for frontend playback.
Separate from platform TTS (which provides lip-sync for the teacher avatar).
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Voice mapping: classmate persona → CosyVoice voice ID
# Override by setting `voice` in YAML classmates to a CosyVoice ID directly
# CosyVoice v3 child-friendly voices:
#   longanhuan   — 龙安欢 (Child's voice, Benchmark — energetic, gender-neutral child)
#   longxiaokun  — 龙小坤 (young male)
# See: https://help.aliyun.com/zh/model-studio/cosyvoice-voice-list
CLASSMATE_VOICES = {
    "小明": "longanhuan",        # Child's voice — benchmark
    "小红": "longxiaoxia",       # Child-like girl voice
    "小刚": "longxiaokun",       # Young boy voice
    "小美": "longxiaoxia",       # Softer child-like girl voice
    "default": "longanhuan",
}

VOICE_HINTS = {
    "可爱小女孩声音": "longxiaoxia",
    "温柔小女孩声音": "longxiaoxia",
    "活泼小男孩声音": "longanhuan",
    "调皮小男孩声音": "longxiaokun",
}

TEACHER_COSY_VOICE = "longxiaokun"  # 备用：老师独立音色（暂不用，老师走平台 TTS）


class TtsClient:
    """Non-streaming CosyVoice TTS for AI classmates."""

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "cosyvoice-v3-flash",
    ) -> None:
        self._api_key = api_key or os.getenv("DASHSCOPE_API_KEY", "")
        self._model = model

    def synthesize(self, text: str, voice: str = "longanhuan") -> str | None:
        """Synthesize text to speech. Returns audio URL, or None on failure.

        Args:
            text: Chinese text to synthesize (max ~500 chars).
            voice: CosyVoice voice ID.

        Returns:
            Audio download URL string, or None if synthesis failed.
        """
        if not self._api_key:
            logger.warning("TTS: DASHSCOPE_API_KEY not set — skipping synthesis")
            return None
        if not text or not text.strip():
            return None

        try:
            from dashscope.audio.http_tts.http_speech_synthesizer import (
                HttpSpeechSynthesizer,
            )

            result = HttpSpeechSynthesizer.call(
                model=self._model,
                text=text.strip(),
                voice=voice,
                format="mp3",
                sample_rate=24000,
                stream=False,
                api_key=self._api_key,
            )

            if result and result.audio_url:
                logger.debug(
                    "TTS synthesized: voice=%s text=%.40s... url=%s",
                    voice, text.strip(), result.audio_url[:60],
                )
                return result.audio_url
            else:
                logger.warning("TTS synthesis returned no audio_url")
                return None

        except Exception as e:
            logger.error("TTS synthesis failed: %s", e)
            return None

    def get_voice_for(self, classmate_name: str,
                       yaml_voice: str = "") -> str:
        """Get the CosyVoice voice ID for a classmate.

        Priority: yaml_voice (if it looks like a CosyVoice ID) >
                  CLASSMATE_VOICES mapping > default.
        """
        # If YAML voice field contains a known CosyVoice ID, use it directly
        if yaml_voice and yaml_voice.startswith("long"):
            return yaml_voice
        if yaml_voice in VOICE_HINTS:
            return VOICE_HINTS[yaml_voice]
        return CLASSMATE_VOICES.get(classmate_name, CLASSMATE_VOICES["default"])
