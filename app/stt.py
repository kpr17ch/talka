from __future__ import annotations

import tempfile
import time
from functools import lru_cache
from pathlib import Path

import httpx

from .config import Settings
from .errors import STTError


class STTService:
    def __init__(self, settings: Settings):
        self.settings = settings

    def transcribe(self, audio_bytes: bytes, filename: str, content_type: str) -> str:
        provider = self.settings.stt_provider
        if provider == "openai":
            return self._transcribe_openai(audio_bytes, filename, content_type)
        if provider == "local":
            return self._transcribe_local(audio_bytes, filename)
        raise STTError(f"Unsupported STT provider: {provider}")

    def _transcribe_openai(self, audio_bytes: bytes, filename: str, content_type: str) -> str:
        if not self.settings.openai_api_key:
            raise STTError("OPENAI_API_KEY missing for STT provider=openai")

        endpoint = f"{self.settings.openai_base_url.rstrip('/')}/audio/transcriptions"
        headers = {"Authorization": f"Bearer {self.settings.openai_api_key}"}
        files = {"file": (filename, audio_bytes, content_type)}
        data = {
            "model": self.settings.openai_stt_model,
            "language": self.settings.stt_language,
        }

        retries = max(0, self.settings.stt_openai_max_retries)
        backoff_ms = max(0, self.settings.stt_openai_retry_backoff_ms)
        last_error: Exception | None = None
        for attempt in range(retries + 1):
            try:
                with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
                    resp = client.post(endpoint, headers=headers, files=files, data=data)

                if resp.status_code >= 400:
                    body_preview = (resp.text or "").strip().replace("\n", " ")
                    if _is_retryable_stt_status(resp.status_code) and attempt < retries:
                        _sleep_backoff(backoff_ms, attempt)
                        continue
                    raise STTError(f"OpenAI STT request failed ({resp.status_code}): {body_preview[:220]}")

                payload = resp.json()
                text = (payload.get("text") or "").strip()
                if not text:
                    raise STTError("OpenAI STT returned empty text")
                return text
            except httpx.HTTPError as exc:
                last_error = exc
                if attempt < retries:
                    _sleep_backoff(backoff_ms, attempt)
                    continue
                raise STTError(f"OpenAI STT request failed: {exc}") from exc
            except ValueError as exc:
                raise STTError("OpenAI STT returned invalid JSON") from exc

        if last_error:
            raise STTError(f"OpenAI STT request failed: {last_error}") from last_error
        raise STTError("OpenAI STT request failed unexpectedly")

    def _transcribe_local(self, audio_bytes: bytes, filename: str) -> str:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise STTError("faster-whisper not installed; install it for STT_PROVIDER=local") from exc

        model = _get_local_model(self.settings.local_whisper_model)
        suffix = Path(filename or "voice.webm").suffix or ".webm"
        with tempfile.NamedTemporaryFile(suffix=suffix) as tmp:
            tmp.write(audio_bytes)
            tmp.flush()
            segments, _ = model.transcribe(tmp.name, language=self.settings.stt_language)
            text = " ".join(seg.text.strip() for seg in segments if seg.text.strip()).strip()

        if not text:
            raise STTError("Local whisper returned empty text")
        return text


@lru_cache(maxsize=2)
def _get_local_model(model_name: str):
    from faster_whisper import WhisperModel

    return WhisperModel(model_name)


def _is_retryable_stt_status(status_code: int) -> bool:
    return status_code in {408, 409, 425, 429, 500, 502, 503, 504}


def _sleep_backoff(backoff_ms: int, attempt: int) -> None:
    if backoff_ms <= 0:
        return
    delay_ms = backoff_ms * (attempt + 1)
    time.sleep(delay_ms / 1000)
