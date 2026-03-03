from __future__ import annotations

import httpx

from .config import Settings
from .errors import TTSError


class TTSService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def synthesize(self, speak_text: str) -> tuple[bytes, str]:
        if not self.settings.elevenlabs_api_key or not self.settings.elevenlabs_voice_id:
            raise TTSError("ElevenLabs credentials missing")

        endpoint = (
            "https://api.elevenlabs.io/v1/text-to-speech/"
            f"{self.settings.elevenlabs_voice_id}"
        )
        headers = {
            "xi-api-key": self.settings.elevenlabs_api_key,
            "accept": "audio/mpeg",
            "content-type": "application/json",
        }
        payload = {
            "text": speak_text,
            "model_id": self.settings.elevenlabs_model_id,
            "voice_settings": {
                "stability": self.settings.elevenlabs_stability,
                "similarity_boost": self.settings.elevenlabs_similarity_boost,
                "speed": self.settings.elevenlabs_speed,
                "style": self.settings.elevenlabs_style,
            },
        }

        try:
            with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                resp = client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
        except httpx.HTTPError as exc:
            raise TTSError(f"ElevenLabs request failed: {exc}") from exc

        return resp.content, "audio/mpeg"
