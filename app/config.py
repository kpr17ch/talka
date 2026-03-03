from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # Server
    voice_bridge_host: str = Field(default="127.0.0.1", alias="VOICE_BRIDGE_HOST")
    voice_bridge_port: int = Field(default=8089, alias="VOICE_BRIDGE_PORT")
    max_audio_mb: int = Field(default=15, alias="MAX_AUDIO_MB")
    request_timeout_seconds: int = Field(default=120, alias="REQUEST_TIMEOUT_SECONDS")
    cors_allow_origins: str = Field(default="http://localhost:8089", alias="CORS_ALLOW_ORIGINS")
    rate_limit_per_minute: int = Field(default=30, alias="RATE_LIMIT_PER_MINUTE")
    turn_job_ttl_seconds: int = Field(default=3600, alias="TURN_JOB_TTL_SECONDS")
    turn_job_max_entries: int = Field(default=500, alias="TURN_JOB_MAX_ENTRIES")
    turn_poll_after_ms: int = Field(default=1200, alias="TURN_POLL_AFTER_MS")
    turn_ack_text: str = Field(default="", alias="TURN_ACK_TEXT")
    wake_phrase: str = Field(default="hey al", alias="WAKE_PHRASE")
    wake_phrase_similarity_threshold: float = Field(
        default=0.8, alias="WAKE_PHRASE_SIMILARITY_THRESHOLD"
    )
    wake_phrase_max_offset_tokens: int = Field(default=2, alias="WAKE_PHRASE_MAX_OFFSET_TOKENS")

    # OpenClaw
    openclaw_bin: str = Field(default="openclaw", alias="OPENCLAW_BIN")
    openclaw_channel: str = Field(default="telegram", alias="OPENCLAW_CHANNEL")
    openclaw_to: str = Field(default="", alias="OPENCLAW_TO")
    openclaw_timeout_seconds: int = Field(default=300, alias="OPENCLAW_TIMEOUT_SECONDS")
    openclaw_process_grace_seconds: int = Field(default=15, alias="OPENCLAW_PROCESS_GRACE_SECONDS")
    mirror_user_text_to_telegram: bool = Field(default=False, alias="MIRROR_USER_TEXT_TO_TELEGRAM")
    user_text_mirror_channel: str = Field(default="telegram", alias="USER_TEXT_MIRROR_CHANNEL")
    user_text_mirror_target: str = Field(default="", alias="USER_TEXT_MIRROR_TARGET")
    user_text_mirror_label: str = Field(default="Kai (Web)", alias="USER_TEXT_MIRROR_LABEL")
    user_text_mirror_max_chars: int = Field(default=1200, alias="USER_TEXT_MIRROR_MAX_CHARS")
    user_text_mirror_timeout_seconds: int = Field(default=30, alias="USER_TEXT_MIRROR_TIMEOUT_SECONDS")

    # STT
    stt_provider: Literal["openai", "local"] = Field(default="openai", alias="STT_PROVIDER")
    openai_api_key: str = Field(default="", alias="OPENAI_API_KEY")
    openai_stt_model: str = Field(default="whisper-1", alias="OPENAI_STT_MODEL")
    openai_base_url: str = Field(default="https://api.openai.com/v1", alias="OPENAI_BASE_URL")
    stt_openai_max_retries: int = Field(default=1, alias="STT_OPENAI_MAX_RETRIES")
    stt_openai_retry_backoff_ms: int = Field(default=250, alias="STT_OPENAI_RETRY_BACKOFF_MS")
    stt_language: str = Field(default="de", alias="STT_LANGUAGE")
    local_whisper_model: str = Field(default="small", alias="LOCAL_WHISPER_MODEL")

    # Orchestrator
    orchestrator_mode: Literal["rules", "llm"] = Field(default="rules", alias="ORCHESTRATOR_MODE")
    orchestrator_model: str = Field(default="gpt-4o-mini", alias="ORCHESTRATOR_MODEL")
    orchestrator_max_speak_chars: int = Field(default=1200, alias="ORCHESTRATOR_MAX_SPEAK_CHARS")
    orchestrator_voice_max_sentences: int = Field(default=4, alias="ORCHESTRATOR_VOICE_MAX_SENTENCES")
    orchestrator_voice_detail_hint: bool = Field(default=True, alias="ORCHESTRATOR_VOICE_DETAIL_HINT")
    orchestrator_llm_timeout_seconds: int = Field(default=15, alias="ORCHESTRATOR_LLM_TIMEOUT_SECONDS")
    panel_state_ttl_seconds: int = Field(default=3600, alias="PANEL_STATE_TTL_SECONDS")

    # ElevenLabs
    elevenlabs_api_key: str = Field(default="", alias="ELEVENLABS_API_KEY")
    elevenlabs_voice_id: str = Field(default="", alias="ELEVENLABS_VOICE_ID")
    elevenlabs_model_id: str = Field(default="eleven_multilingual_v2", alias="ELEVENLABS_MODEL_ID")
    elevenlabs_stability: float = Field(default=0.4, alias="ELEVENLABS_STABILITY")
    elevenlabs_similarity_boost: float = Field(default=0.8, alias="ELEVENLABS_SIMILARITY_BOOST")

    # Logging
    debug_log_full_text: bool = Field(default=False, alias="DEBUG_LOG_FULL_TEXT")

    @property
    def max_audio_bytes(self) -> int:
        return self.max_audio_mb * 1024 * 1024

    @property
    def cors_origins(self) -> list[str]:
        return [part.strip() for part in self.cors_allow_origins.split(",") if part.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
