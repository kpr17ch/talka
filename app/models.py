from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel


class TurnTimings(BaseModel):
    total: int
    request_prep: int = 0
    stt: int
    user_text_mirror: int = 0
    openclaw: int
    orchestrator: int
    tts: int
    audio_encode: int = 0
    response_build: int = 0


class TurnMeta(BaseModel):
    openclaw_exit_code: int | None = None
    telegram_deliver_attempted: bool = True
    user_text_mirror_attempted: bool = False
    user_text_mirror_sent: bool | None = None


class CurrentTask(BaseModel):
    title: str
    steps: list[str] = []


class PanelState(BaseModel):
    current_task: CurrentTask | None = None
    pinboard: list[str] = []
    work_notes: list[str] = []


class VoiceTurnResponse(BaseModel):
    turn_id: str
    conversation_id: str
    user_text: str
    raw_text: str
    speak_text: str
    audio_base64: str | None
    audio_mime: str | None
    timings_ms: TurnTimings
    meta: TurnMeta
    panels: PanelState | None = None


class VoiceTurnStartResponse(BaseModel):
    turn_id: str
    conversation_id: str
    user_text: str
    ack_text: str
    ack_audio_base64: str | None = None
    ack_audio_mime: str | None = None
    status: Literal["processing"] = "processing"
    poll_after_ms: int = 1200


class ErrorBody(BaseModel):
    turn_id: str
    conversation_id: str
    error_class: str
    message: str
    timings_ms: TurnTimings


class VoiceTurnStatusResponse(BaseModel):
    turn_id: str
    conversation_id: str
    status: Literal["processing", "completed", "failed", "cancelled"]
    progress_stage: str | None = None
    progress_message: str | None = None
    result: VoiceTurnResponse | None = None
    error: ErrorBody | None = None


class VoiceTurnCancelResponse(BaseModel):
    turn_id: str
    conversation_id: str
    status: Literal["cancelled", "already_done", "not_found"]


class WakeTurnStartResponse(BaseModel):
    transcript: str
    wake_phrase: str
    wake_detected: bool
    turn_started: bool
    turn: VoiceTurnStartResponse | None = None


class WsTurnProgress(BaseModel):
    type: Literal["progress"] = "progress"
    turn_id: str
    stage: str
    message: str


class WsTurnResult(BaseModel):
    type: Literal["result"] = "result"
    turn_id: str
    conversation_id: str
    voice_response: str
    panels: PanelState
    audio_base64: str | None = None
    audio_mime: str | None = None


class WsTurnError(BaseModel):
    type: Literal["error"] = "error"
    turn_id: str
    message: str


class HealthResponse(BaseModel):
    status: str
    version: str
    dependencies: dict[str, Any]
