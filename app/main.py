from __future__ import annotations

import asyncio
import base64
import logging
import re
import shutil
from difflib import SequenceMatcher
from pathlib import Path
from threading import Lock
from time import perf_counter, time
from typing import Callable
from uuid import uuid4

from fastapi import BackgroundTasks, FastAPI, File, Form, HTTPException, Request, Response, UploadFile, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import get_settings
from .errors import (
    OpenClawBinaryNotFound,
    OpenClawCancelled,
    OpenClawError,
    OpenClawNonZeroExit,
    OpenClawTimeout,
    STTError,
    TTSError,
    ValidationError,
)
from .llm_orchestrator import LLMOrchestrator
from .logging_setup import configure_logging
from .models import (
    ErrorBody,
    HealthResponse,
    PanelState,
    WakeTurnStartResponse,
    WsTurnError,
    WsTurnProgress,
    WsTurnResult,
    TurnMeta,
    TurnTimings,
    VoiceTurnResponse,
    VoiceTurnCancelResponse,
    VoiceTurnStartResponse,
    VoiceTurnStatusResponse,
)
from .openclaw_client import OpenClawClient
from .orchestrator import Orchestrator
from .rate_limit import RateLimiter
from .stt import STTService
from .turn_ack import build_turn_ack_text
from .tts import TTSService

settings = get_settings()
configure_logging()
logger = logging.getLogger("voice_bridge")

stt_service = STTService(settings)
openclaw_client = OpenClawClient(settings)
orchestrator = Orchestrator(settings)
llm_orchestrator = LLMOrchestrator(settings)
tts_service = TTSService(settings)
rate_limiter = RateLimiter(settings.rate_limit_per_minute)

app = FastAPI(title="Voice Bridge", version="0.1.0")
if settings.cors_origins:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )

@app.on_event("startup")
async def _capture_event_loop():
    global _event_loop
    _event_loop = asyncio.get_running_loop()


def _create_turn_queue(turn_id: str) -> None:
    if _event_loop is None:
        return
    q = asyncio.Queue()
    with turn_queues_lock:
        turn_queues[turn_id] = q


def _push_to_turn_queue(turn_id: str, msg: dict) -> None:
    with turn_queues_lock:
        q = turn_queues.get(turn_id)
    if q and _event_loop:
        _event_loop.call_soon_threadsafe(q.put_nowait, msg)


def _close_turn_queue(turn_id: str) -> None:
    with turn_queues_lock:
        q = turn_queues.get(turn_id)
    if q and _event_loop:
        _event_loop.call_soon_threadsafe(q.put_nowait, None)


def _store_panel_state(conversation_id: str, panels: PanelState) -> None:
    with panel_state_lock:
        panel_state[conversation_id] = {
            "panels": panels.model_dump(),
            "updated_at": time(),
        }


def _get_panel_state(conversation_id: str) -> PanelState | None:
    with panel_state_lock:
        entry = panel_state.get(conversation_id)
    if not entry:
        return None
    return PanelState.model_validate(entry["panels"])


def _cleanup_panel_state() -> None:
    ttl = max(60, settings.panel_state_ttl_seconds)
    now_ts = time()
    with panel_state_lock:
        expired = [cid for cid, e in panel_state.items() if now_ts - e["updated_at"] > ttl]
        for cid in expired:
            panel_state.pop(cid, None)


SUPPORTED_AUDIO_MIME = {
    "audio/webm",
    "audio/wav",
    "audio/x-wav",
    "audio/mpeg",
    "audio/mp4",
    "audio/m4a",
    "audio/x-m4a",
    "audio/ogg",
}

turn_jobs: dict[str, dict] = {}
turn_jobs_lock = Lock()

turn_queues: dict[str, asyncio.Queue] = {}
turn_queues_lock = Lock()
_event_loop: asyncio.AbstractEventLoop | None = None

panel_state: dict[str, dict] = {}
panel_state_lock = Lock()


def _new_timings() -> dict[str, int]:
    return {
        "total": 0,
        "request_prep": 0,
        "stt": 0,
        "user_text_mirror": 0,
        "openclaw": 0,
        "orchestrator": 0,
        "tts": 0,
        "audio_encode": 0,
        "response_build": 0,
    }


def _cleanup_turn_jobs() -> None:
    ttl_seconds = max(60, settings.turn_job_ttl_seconds)
    max_entries = max(50, settings.turn_job_max_entries)
    now_ts = time()

    with turn_jobs_lock:
        expired = [
            turn_id
            for turn_id, entry in turn_jobs.items()
            if now_ts - entry.get("updated_at", now_ts) > ttl_seconds
        ]
        for turn_id in expired:
            turn_jobs.pop(turn_id, None)

        overflow = len(turn_jobs) - max_entries
        if overflow <= 0:
            return

        completed_or_failed = sorted(
            (
                (turn_id, entry.get("updated_at", 0))
                for turn_id, entry in turn_jobs.items()
                if entry.get("status") != "processing"
            ),
            key=lambda item: item[1],
        )
        for turn_id, _ in completed_or_failed[:overflow]:
            turn_jobs.pop(turn_id, None)


def _create_turn_job(*, turn_id: str, conversation_id: str, user_text: str) -> None:
    _create_turn_queue(turn_id)
    now_ts = time()
    with turn_jobs_lock:
        turn_jobs[turn_id] = {
            "turn_id": turn_id,
            "conversation_id": conversation_id,
            "user_text": user_text,
            "status": "processing",
            "created_at": now_ts,
            "updated_at": now_ts,
            "cancel_requested": False,
            "progress_stage": "queued",
            "progress_message": "Aufgabe angenommen.",
            "result": None,
            "error": None,
        }


def _set_turn_job_progress(*, turn_id: str, stage: str, message: str) -> None:
    now_ts = time()
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return
        entry["updated_at"] = now_ts
        entry["progress_stage"] = stage
        entry["progress_message"] = message


def _mark_turn_job_completed(*, turn_id: str, response: VoiceTurnResponse) -> None:
    now_ts = time()
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return
        entry["status"] = "completed"
        entry["updated_at"] = now_ts
        entry["conversation_id"] = response.conversation_id
        entry["progress_stage"] = "completed"
        entry["progress_message"] = "Antwort bereit."
        entry["result"] = response.model_dump()
        entry["error"] = None


def _mark_turn_job_failed(*, turn_id: str, conversation_id: str, error: ErrorBody) -> None:
    now_ts = time()
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return
        entry["status"] = "failed"
        entry["updated_at"] = now_ts
        entry["conversation_id"] = conversation_id
        entry["progress_stage"] = "failed"
        entry["progress_message"] = "Aufgabe fehlgeschlagen."
        entry["result"] = None
        entry["error"] = error.model_dump()


def _mark_turn_job_cancelled(*, turn_id: str, conversation_id: str, message: str = "Vorgang abgebrochen.") -> None:
    now_ts = time()
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return
        entry["status"] = "cancelled"
        entry["updated_at"] = now_ts
        entry["conversation_id"] = conversation_id
        entry["progress_stage"] = "cancelled"
        entry["progress_message"] = message
        entry["result"] = None
        entry["error"] = None


def _request_turn_cancel(turn_id: str) -> tuple[str, str]:
    now_ts = time()
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return ("not_found", "")
        status = entry.get("status")
        if status != "processing":
            return ("already_done", str(entry.get("conversation_id") or ""))
        entry["cancel_requested"] = True
        entry["updated_at"] = now_ts
        entry["progress_stage"] = "cancelling"
        entry["progress_message"] = "Abbruch angefordert."
        return ("cancelled", str(entry.get("conversation_id") or ""))


def _is_turn_cancel_requested(turn_id: str) -> bool:
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return False
        return bool(entry.get("cancel_requested"))


def _get_turn_job(turn_id: str) -> dict | None:
    with turn_jobs_lock:
        entry = turn_jobs.get(turn_id)
        if not entry:
            return None
        return dict(entry)


def _elapsed_ms(start: float) -> int:
    return int((perf_counter() - start) * 1000)


def _error_response(
    *,
    status_code: int,
    turn_id: str,
    conversation_id: str,
    error_class: str,
    message: str,
    timings: dict[str, int],
) -> JSONResponse:
    body = ErrorBody(
        turn_id=turn_id,
        conversation_id=conversation_id,
        error_class=error_class,
        message=message,
        timings_ms=TurnTimings(**timings),
    )
    return JSONResponse(
        status_code=status_code,
        content=body.model_dump(),
        headers={"Server-Timing": _to_server_timing_header(timings)},
    )


def _to_server_timing_header(timings: dict[str, int]) -> str:
    segments = [
        ("prep", "request_prep"),
        ("stt", "stt"),
        ("mirror", "user_text_mirror"),
        ("agent", "openclaw"),
        ("orch", "orchestrator"),
        ("tts", "tts"),
        ("encode", "audio_encode"),
        ("build", "response_build"),
        ("total", "total"),
    ]
    parts: list[str] = []
    for metric_name, field in segments:
        value = timings.get(field)
        if value is None:
            continue
        parts.append(f"{metric_name};dur={value}")
    return ", ".join(parts)


async def _read_and_validate_audio(audio: UploadFile) -> tuple[str, bytes]:
    raw_content_type = (audio.content_type or "").lower().strip()
    raw_content_type = raw_content_type.strip("\"'")
    raw_content_type = raw_content_type.replace("\\;", ";")
    mime_type = raw_content_type.split(";")[0].strip()
    if mime_type not in SUPPORTED_AUDIO_MIME:
        raise ValidationError(f"Unsupported audio content type: {audio.content_type}")

    audio_bytes = await audio.read()
    if not audio_bytes:
        raise ValidationError("Audio upload is empty")
    if len(audio_bytes) > settings.max_audio_bytes:
        raise ValidationError(f"Audio file too large. Max allowed: {settings.max_audio_mb} MB")
    return mime_type, audio_bytes


def _normalize_wake_text(text: str) -> str:
    lowered = text.lower()
    lowered = re.sub(r"[^\w\s]", " ", lowered)
    return " ".join(lowered.split())


def _wake_similarity_score(left: str, right: str) -> float:
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    return SequenceMatcher(None, left, right).ratio()


def _extract_wake_remainder(transcript: str, wake_phrase: str) -> tuple[bool, str]:
    normalized_text = _normalize_wake_text(transcript)
    normalized_wake = _normalize_wake_text(wake_phrase)
    if not normalized_wake or not normalized_text:
        return False, ""

    text_tokens = normalized_text.split()
    wake_tokens = normalized_wake.split()
    wake_len = len(wake_tokens)
    if wake_len == 0 or len(text_tokens) < wake_len:
        return False, ""

    threshold = max(0.0, min(1.0, settings.wake_phrase_similarity_threshold))
    max_offset = max(0, settings.wake_phrase_max_offset_tokens)
    max_start = min(max_offset, max(0, len(text_tokens) - wake_len))

    best_start: int | None = None
    best_score = 0.0
    for start_index in range(max_start + 1):
        candidate = " ".join(text_tokens[start_index : start_index + wake_len])
        score = _wake_similarity_score(candidate, normalized_wake)
        if score >= threshold and score >= best_score:
            best_start = start_index
            best_score = score

    if best_start is None:
        return False, ""

    remainder = " ".join(text_tokens[best_start + wake_len :]).strip()
    return True, remainder


def _build_ack_audio(*, turn_id: str, conversation_out: str, ack_text: str) -> tuple[str | None, str | None]:
    if not ack_text:
        return (None, None)
    try:
        ack_audio_bytes, ack_audio_mime = tts_service.synthesize(ack_text)
        return base64.b64encode(ack_audio_bytes).decode("ascii"), ack_audio_mime
    except TTSError as exc:
        logger.warning(
            "ack_tts_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "error": str(exc),
                }
            },
        )
        return (None, None)


def _enqueue_async_turn(
    *,
    response: Response,
    background_tasks: BackgroundTasks,
    started: float,
    timings: dict[str, int],
    turn_id: str,
    conversation_out: str,
    conversation_id: str | None,
    user_text: str,
) -> VoiceTurnStartResponse:
    user_text_mirror_attempted = False
    user_text_mirror_sent: bool | None = None
    if settings.mirror_user_text_to_telegram:
        user_text_mirror_attempted = True
        background_tasks.add_task(
            _run_deferred_user_text_mirror,
            turn_id=turn_id,
            conversation_id=conversation_out,
            user_text=user_text,
        )

    _create_turn_job(turn_id=turn_id, conversation_id=conversation_out, user_text=user_text)
    background_tasks.add_task(
        _run_async_turn_job,
        turn_id=turn_id,
        conversation_out=conversation_out,
        conversation_id=conversation_id,
        user_text=user_text,
        initial_timings=dict(timings),
        user_text_mirror_attempted=user_text_mirror_attempted,
        user_text_mirror_sent=user_text_mirror_sent,
    )

    timings["total"] = _elapsed_ms(started)
    accounted_ms = timings["request_prep"] + timings["stt"]
    timings["response_build"] = max(0, timings["total"] - accounted_ms)
    response.headers["Server-Timing"] = _to_server_timing_header(timings)

    logger.info(
        "turn_queued",
        extra={
            "extra": {
                "turn_id": turn_id,
                "conversation_id": conversation_out,
                "timings_ms": timings,
                "status": "processing",
                "user_text_len": len(user_text),
            }
        },
    )

    ack_text = build_turn_ack_text(user_text=user_text, settings=settings)
    ack_audio_b64 = None
    ack_audio_mime = None
    if ack_text and settings.turn_ack_tts_enabled:
        ack_audio_b64, ack_audio_mime = _build_ack_audio(
            turn_id=turn_id,
            conversation_out=conversation_out,
            ack_text=ack_text,
        )
    _cleanup_turn_jobs()
    return VoiceTurnStartResponse(
        turn_id=turn_id,
        conversation_id=conversation_out,
        user_text=user_text,
        ack_text=ack_text,
        ack_audio_base64=ack_audio_b64,
        ack_audio_mime=ack_audio_mime,
        poll_after_ms=max(400, settings.turn_poll_after_ms),
    )


def _run_deferred_user_text_mirror(
    *,
    turn_id: str,
    conversation_id: str,
    user_text: str,
) -> None:
    mirror_start = perf_counter()
    try:
        mirror_sent = openclaw_client.mirror_user_text(user_text)
        logger.info(
            "user_text_mirror_completed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "user_text_mirror_sent": mirror_sent,
                    "user_text_mirror_ms": _elapsed_ms(mirror_start),
                }
            },
        )
    except OpenClawError as exc:
        logger.warning(
            "user_text_mirror_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "error": str(exc),
                    "user_text_mirror_ms": _elapsed_ms(mirror_start),
                }
            },
        )
    except Exception as exc:  # pragma: no cover
        logger.exception(
            "user_text_mirror_unexpected_error",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_id,
                    "error_class": exc.__class__.__name__,
                    "user_text_mirror_ms": _elapsed_ms(mirror_start),
                }
            },
        )


def _run_turn_pipeline(
    *,
    turn_id: str,
    conversation_out: str,
    conversation_id: str | None,
    user_text: str,
    timings: dict[str, int],
    started: float,
    user_text_mirror_attempted: bool,
    user_text_mirror_sent: bool | None,
    should_cancel: Callable[[], bool] | None = None,
    on_stage: Callable[[str, str], None] | None = None,
) -> VoiceTurnResponse:
    if should_cancel and should_cancel():
        raise OpenClawCancelled("Turn cancelled before agent execution")

    if on_stage:
        on_stage("agent", "Ich arbeite an der Antwort.")
    openclaw_start = perf_counter()
    oc_result = openclaw_client.ask(
        user_text=user_text,
        conversation_id=conversation_id,
        should_cancel=should_cancel,
    )
    timings["openclaw"] = _elapsed_ms(openclaw_start)
    if oc_result.session_id:
        conversation_out = oc_result.session_id

    if should_cancel and should_cancel():
        raise OpenClawCancelled("Turn cancelled before orchestration")

    if on_stage:
        on_stage("orchestrator", "Ich bereite die Antwort auf.")
    orchestrator_start = perf_counter()
    previous_panels = _get_panel_state(conversation_out) if conversation_out else None
    orch_result = llm_orchestrator.process(oc_result.raw_text, previous_panels)
    speak_text = orch_result.voice_response
    panels = orch_result.panels
    _store_panel_state(conversation_out, panels)
    timings["orchestrator"] = _elapsed_ms(orchestrator_start)

    if should_cancel and should_cancel():
        raise OpenClawCancelled("Turn cancelled before TTS")

    if on_stage:
        on_stage("tts", "Ich spreche die Antwort ein.")
    tts_audio_b64 = None
    audio_mime = None
    tts_start = perf_counter()
    try:
        audio_bytes_out, audio_mime = tts_service.synthesize(speak_text)
        encode_start = perf_counter()
        tts_audio_b64 = base64.b64encode(audio_bytes_out).decode("ascii")
        timings["audio_encode"] = _elapsed_ms(encode_start)
    except TTSError as exc:
        logger.warning(
            "tts_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "error": str(exc),
                }
            },
        )
    timings["tts"] = _elapsed_ms(tts_start)
    timings["total"] = _elapsed_ms(started)
    accounted_ms = (
        timings["request_prep"] + timings["stt"] + timings["openclaw"] + timings["orchestrator"] + timings["tts"]
    )
    timings["response_build"] = max(0, timings["total"] - accounted_ms)

    logger.info(
        "turn_completed",
        extra={
            "extra": {
                "turn_id": turn_id,
                "conversation_id": conversation_out,
                "timings_ms": timings,
                "status": "ok",
                "user_text_len": len(user_text),
                "raw_text_len": len(oc_result.raw_text),
                "speak_text_len": len(speak_text),
                "openclaw_run_id": oc_result.run_id,
                "openclaw_exit_code": oc_result.exit_code,
                "user_text_mirror_attempted": user_text_mirror_attempted,
                "user_text_mirror_sent": user_text_mirror_sent,
            }
        },
    )

    return VoiceTurnResponse(
        turn_id=turn_id,
        conversation_id=conversation_out,
        user_text=user_text,
        raw_text=oc_result.raw_text,
        speak_text=speak_text,
        audio_base64=tts_audio_b64,
        audio_mime=audio_mime,
        timings_ms=TurnTimings(**timings),
        meta=TurnMeta(
            openclaw_exit_code=oc_result.exit_code,
            telegram_deliver_attempted=True,
            user_text_mirror_attempted=user_text_mirror_attempted,
            user_text_mirror_sent=user_text_mirror_sent,
        ),
        panels=panels,
    )


def _error_message_from_exception(exc: Exception) -> str:
    if isinstance(exc, OpenClawCancelled):
        return str(exc) or "Turn cancelled"
    if isinstance(exc, OpenClawNonZeroExit):
        return f"OpenClaw failed with exit code {exc.exit_code}: {exc}"
    if isinstance(exc, OpenClawError):
        return str(exc)
    return "Unexpected server error"


def _run_async_turn_job(
    *,
    turn_id: str,
    conversation_out: str,
    conversation_id: str | None,
    user_text: str,
    initial_timings: dict[str, int],
    user_text_mirror_attempted: bool,
    user_text_mirror_sent: bool | None,
) -> None:
    started = perf_counter() - ((initial_timings.get("request_prep", 0) + initial_timings.get("stt", 0)) / 1000)
    timings = _new_timings()
    timings.update(initial_timings)
    should_cancel = lambda: _is_turn_cancel_requested(turn_id)

    def _on_stage(stage: str, message: str) -> None:
        _set_turn_job_progress(turn_id=turn_id, stage=stage, message=message)
        _push_to_turn_queue(turn_id, WsTurnProgress(
            turn_id=turn_id, stage=stage, message=message,
        ).model_dump())

    try:
        if should_cancel():
            raise OpenClawCancelled("Turn cancelled")
        _set_turn_job_progress(turn_id=turn_id, stage="agent", message="Ich arbeite an der Antwort.")
        _push_to_turn_queue(turn_id, WsTurnProgress(
            turn_id=turn_id, stage="agent", message="Ich arbeite an der Antwort.",
        ).model_dump())
        result = _run_turn_pipeline(
            turn_id=turn_id,
            conversation_out=conversation_out,
            conversation_id=conversation_id,
            user_text=user_text,
            timings=timings,
            started=started,
            user_text_mirror_attempted=user_text_mirror_attempted,
            user_text_mirror_sent=user_text_mirror_sent,
            should_cancel=should_cancel,
            on_stage=_on_stage,
        )
        _mark_turn_job_completed(turn_id=turn_id, response=result)
        _push_to_turn_queue(turn_id, WsTurnResult(
            turn_id=turn_id,
            conversation_id=result.conversation_id,
            voice_response=result.speak_text,
            panels=result.panels or PanelState(),
            audio_base64=result.audio_base64,
            audio_mime=result.audio_mime,
        ).model_dump())
        _close_turn_queue(turn_id)
    except OpenClawCancelled:
        _mark_turn_job_cancelled(turn_id=turn_id, conversation_id=conversation_out)
        _push_to_turn_queue(turn_id, WsTurnError(
            turn_id=turn_id, message="Vorgang abgebrochen.",
        ).model_dump())
        _close_turn_queue(turn_id)
        logger.info(
            "turn_cancelled",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "status": "cancelled",
                }
            },
        )
    except Exception as exc:
        timings["total"] = _elapsed_ms(started)
        accounted_ms = (
            timings["request_prep"] + timings["stt"] + timings["openclaw"] + timings["orchestrator"] + timings["tts"]
        )
        timings["response_build"] = max(0, timings["total"] - accounted_ms)

        error_class = exc.__class__.__name__
        error_message = _error_message_from_exception(exc)

        if not isinstance(exc, (OpenClawError, TTSError, STTError, ValidationError)):
            logger.exception(
                "turn_failed",
                extra={
                    "extra": {
                        "turn_id": turn_id,
                        "conversation_id": conversation_out,
                        "status": "error",
                        "error_class": error_class,
                    }
                },
            )
        else:
            logger.warning(
                "turn_failed",
                extra={
                    "extra": {
                        "turn_id": turn_id,
                        "conversation_id": conversation_out,
                        "status": "error",
                        "error_class": error_class,
                        "error": error_message,
                    }
                },
            )

        _mark_turn_job_failed(
            turn_id=turn_id,
            conversation_id=conversation_out,
            error=ErrorBody(
                turn_id=turn_id,
                conversation_id=conversation_out,
                error_class=error_class,
                message=error_message,
                timings_ms=TurnTimings(**timings),
            ),
        )
        _push_to_turn_queue(turn_id, WsTurnError(
            turn_id=turn_id, message=error_message,
        ).model_dump())
        _close_turn_queue(turn_id)

    _cleanup_turn_jobs()
    _cleanup_panel_state()


@app.websocket("/ws/turn/{turn_id}")
async def ws_turn(websocket: WebSocket, turn_id: str):
    await websocket.accept()

    with turn_queues_lock:
        q = turn_queues.get(turn_id)

    if not q:
        entry = _get_turn_job(turn_id)
        if entry and entry.get("status") == "completed":
            result_payload = entry.get("result")
            if result_payload:
                resp = VoiceTurnResponse.model_validate(result_payload)
                await websocket.send_json(WsTurnResult(
                    turn_id=turn_id,
                    conversation_id=resp.conversation_id,
                    voice_response=resp.speak_text,
                    panels=resp.panels or PanelState(),
                    audio_base64=resp.audio_base64,
                    audio_mime=resp.audio_mime,
                ).model_dump())
        elif entry and entry.get("status") == "failed":
            error_payload = entry.get("error")
            msg = error_payload.get("message", "Unbekannter Fehler") if error_payload else "Unbekannter Fehler"
            await websocket.send_json(WsTurnError(turn_id=turn_id, message=msg).model_dump())
        else:
            await websocket.send_json(WsTurnError(turn_id=turn_id, message="Turn nicht gefunden.").model_dump())
        await websocket.close()
        return

    try:
        while True:
            msg = await asyncio.wait_for(q.get(), timeout=settings.request_timeout_seconds)
            if msg is None:
                break
            await websocket.send_json(msg)
    except asyncio.TimeoutError:
        await websocket.send_json(WsTurnError(turn_id=turn_id, message="Timeout").model_dump())
    except WebSocketDisconnect:
        pass
    finally:
        with turn_queues_lock:
            turn_queues.pop(turn_id, None)
        try:
            await websocket.close()
        except Exception:
            pass


@app.get("/api/health", response_model=HealthResponse)
def health() -> HealthResponse:
    local_stt_available = True
    if settings.stt_provider == "local":
        try:
            import faster_whisper  # noqa: F401
        except ImportError:
            local_stt_available = False

    dependencies = {
        "openclaw_binary": bool(shutil.which(settings.openclaw_bin)),
        "openclaw_target_configured": bool(settings.openclaw_to),
        "user_text_mirror_enabled": settings.mirror_user_text_to_telegram,
        "stt_provider": settings.stt_provider,
        "stt_ready": bool(settings.openai_api_key) if settings.stt_provider == "openai" else local_stt_available,
        "tts_ready": bool(settings.elevenlabs_api_key and settings.elevenlabs_voice_id),
    }

    hard_checks = [
        dependencies["openclaw_binary"],
        dependencies["openclaw_target_configured"],
        dependencies["stt_ready"],
    ]

    status = "ok" if all(hard_checks) else "degraded"
    return HealthResponse(status=status, version=app.version, dependencies=dependencies)


@app.post("/api/voice/turn/start", response_model=VoiceTurnStartResponse)
async def voice_turn_start(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    client_turn_id: str | None = Form(default=None),
):
    started = perf_counter()
    timings = _new_timings()

    turn_id = str(uuid4())
    conversation_out = conversation_id or str(uuid4())
    client_ip = request.client.host if request.client else "unknown"

    if not rate_limiter.allow(client_ip):
        return _error_response(
            status_code=429,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="RateLimited",
            message="Too many requests. Please wait a moment.",
            timings=timings,
        )

    try:
        prep_start = perf_counter()
        mime_type, audio_bytes = await _read_and_validate_audio(audio)
        timings["request_prep"] = _elapsed_ms(prep_start)

        logger.info(
            "turn_started",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "client_turn_id": client_turn_id,
                    "audio_bytes": len(audio_bytes),
                    "content_type": audio.content_type,
                    "normalized_content_type": mime_type,
                    "mode": "async",
                }
            },
        )

        stt_start = perf_counter()
        user_text = stt_service.transcribe(audio_bytes, audio.filename or "voice.webm", mime_type)
        timings["stt"] = _elapsed_ms(stt_start)

        return _enqueue_async_turn(
            response=response,
            background_tasks=background_tasks,
            started=started,
            timings=timings,
            turn_id=turn_id,
            conversation_out=conversation_out,
            conversation_id=conversation_id,
            user_text=user_text,
        )

    except ValidationError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=400,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="ValidationError",
            message=str(exc),
            timings=timings,
        )
    except STTError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=502,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="STTError",
            message=str(exc),
            timings=timings,
        )
    except Exception as exc:  # pragma: no cover
        timings["total"] = _elapsed_ms(started)
        logger.exception(
            "turn_start_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "status": "error",
                    "error_class": exc.__class__.__name__,
                }
            },
        )
        return _error_response(
            status_code=500,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class=exc.__class__.__name__,
            message="Unexpected server error",
            timings=timings,
        )


@app.post("/api/voice/wake/turn/start", response_model=WakeTurnStartResponse)
async def wake_turn_start(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    client_turn_id: str | None = Form(default=None),
    wake_phrase: str | None = Form(default=None),
):
    started = perf_counter()
    timings = _new_timings()
    turn_id = str(uuid4())
    conversation_out = conversation_id or str(uuid4())
    client_ip = request.client.host if request.client else "unknown"

    if not rate_limiter.allow(client_ip):
        return _error_response(
            status_code=429,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="RateLimited",
            message="Too many requests. Please wait a moment.",
            timings=timings,
        )

    try:
        prep_start = perf_counter()
        mime_type, audio_bytes = await _read_and_validate_audio(audio)
        timings["request_prep"] = _elapsed_ms(prep_start)

        active_wake_phrase = (wake_phrase or settings.wake_phrase).strip()

        logger.info(
            "wake_probe_started",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "client_turn_id": client_turn_id,
                    "audio_bytes": len(audio_bytes),
                    "content_type": audio.content_type,
                    "normalized_content_type": mime_type,
                    "wake_phrase": active_wake_phrase,
                }
            },
        )

        stt_start = perf_counter()
        transcript = stt_service.transcribe(audio_bytes, audio.filename or "voice.webm", mime_type)
        timings["stt"] = _elapsed_ms(stt_start)

        wake_detected, remainder = _extract_wake_remainder(transcript, active_wake_phrase)
        if not wake_detected or not remainder:
            timings["total"] = _elapsed_ms(started)
            timings["response_build"] = max(0, timings["total"] - (timings["request_prep"] + timings["stt"]))
            response.headers["Server-Timing"] = _to_server_timing_header(timings)
            return WakeTurnStartResponse(
                transcript=transcript,
                wake_phrase=active_wake_phrase,
                wake_detected=wake_detected,
                turn_started=False,
                turn=None,
            )

        turn_start = _enqueue_async_turn(
            response=response,
            background_tasks=background_tasks,
            started=started,
            timings=timings,
            turn_id=turn_id,
            conversation_out=conversation_out,
            conversation_id=conversation_id,
            user_text=remainder,
        )
        return WakeTurnStartResponse(
            transcript=transcript,
            wake_phrase=active_wake_phrase,
            wake_detected=True,
            turn_started=True,
            turn=turn_start,
        )
    except ValidationError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=400,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="ValidationError",
            message=str(exc),
            timings=timings,
        )
    except STTError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=502,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="STTError",
            message=str(exc),
            timings=timings,
        )
    except Exception as exc:  # pragma: no cover
        timings["total"] = _elapsed_ms(started)
        logger.exception(
            "wake_turn_start_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "status": "error",
                    "error_class": exc.__class__.__name__,
                }
            },
        )
        return _error_response(
            status_code=500,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class=exc.__class__.__name__,
            message="Unexpected server error",
            timings=timings,
        )


@app.get("/api/voice/turn/status/{turn_id}", response_model=VoiceTurnStatusResponse)
def voice_turn_status(turn_id: str):
    _cleanup_turn_jobs()
    entry = _get_turn_job(turn_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Turn not found")

    status = entry.get("status", "processing")
    conversation_out = str(entry.get("conversation_id") or "")
    progress_stage = str(entry.get("progress_stage") or "") or None
    progress_message = str(entry.get("progress_message") or "") or None
    if status == "completed":
        result_payload = entry.get("result")
        if not result_payload:
            raise HTTPException(status_code=500, detail="Turn state invalid")
        return VoiceTurnStatusResponse(
            turn_id=turn_id,
            conversation_id=conversation_out,
            status="completed",
            progress_stage=progress_stage,
            progress_message=progress_message,
            result=VoiceTurnResponse.model_validate(result_payload),
        )
    if status == "failed":
        error_payload = entry.get("error")
        if not error_payload:
            raise HTTPException(status_code=500, detail="Turn state invalid")
        return VoiceTurnStatusResponse(
            turn_id=turn_id,
            conversation_id=conversation_out,
            status="failed",
            progress_stage=progress_stage,
            progress_message=progress_message,
            error=ErrorBody.model_validate(error_payload),
        )
    if status == "cancelled":
        return VoiceTurnStatusResponse(
            turn_id=turn_id,
            conversation_id=conversation_out,
            status="cancelled",
            progress_stage=progress_stage,
            progress_message=progress_message,
        )
    return VoiceTurnStatusResponse(
        turn_id=turn_id,
        conversation_id=conversation_out,
        status="processing",
        progress_stage=progress_stage,
        progress_message=progress_message,
    )


@app.post("/api/voice/turn/cancel/{turn_id}", response_model=VoiceTurnCancelResponse)
def voice_turn_cancel(turn_id: str):
    _cleanup_turn_jobs()
    cancel_status, conversation_out = _request_turn_cancel(turn_id)
    if cancel_status == "not_found":
        return VoiceTurnCancelResponse(turn_id=turn_id, conversation_id="", status="not_found")
    if cancel_status == "already_done":
        return VoiceTurnCancelResponse(
            turn_id=turn_id,
            conversation_id=conversation_out,
            status="already_done",
        )
    return VoiceTurnCancelResponse(
        turn_id=turn_id,
        conversation_id=conversation_out,
        status="cancelled",
    )


@app.post("/api/voice/turn", response_model=VoiceTurnResponse)
async def voice_turn(
    request: Request,
    response: Response,
    background_tasks: BackgroundTasks,
    audio: UploadFile = File(...),
    conversation_id: str | None = Form(default=None),
    client_turn_id: str | None = Form(default=None),
):
    started = perf_counter()
    timings = _new_timings()

    turn_id = str(uuid4())
    conversation_out = conversation_id or str(uuid4())
    client_ip = request.client.host if request.client else "unknown"

    if not rate_limiter.allow(client_ip):
        return _error_response(
            status_code=429,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="RateLimited",
            message="Too many requests. Please wait a moment.",
            timings=timings,
        )

    try:
        prep_start = perf_counter()
        mime_type, audio_bytes = await _read_and_validate_audio(audio)
        timings["request_prep"] = _elapsed_ms(prep_start)

        logger.info(
            "turn_started",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "client_turn_id": client_turn_id,
                    "audio_bytes": len(audio_bytes),
                    "content_type": audio.content_type,
                    "normalized_content_type": mime_type,
                }
            },
        )

        stt_start = perf_counter()
        user_text = stt_service.transcribe(audio_bytes, audio.filename or "voice.webm", mime_type)
        timings["stt"] = _elapsed_ms(stt_start)

        user_text_mirror_attempted = False
        user_text_mirror_sent: bool | None = None

        if settings.mirror_user_text_to_telegram:
            user_text_mirror_attempted = True
            background_tasks.add_task(
                _run_deferred_user_text_mirror,
                turn_id=turn_id,
                conversation_id=conversation_out,
                user_text=user_text,
            )

        turn_response = _run_turn_pipeline(
            turn_id=turn_id,
            conversation_out=conversation_out,
            conversation_id=conversation_id,
            user_text=user_text,
            timings=timings,
            started=started,
            user_text_mirror_attempted=user_text_mirror_attempted,
            user_text_mirror_sent=user_text_mirror_sent,
        )
        response.headers["Server-Timing"] = _to_server_timing_header(timings)
        return turn_response

    except ValidationError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=400,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="ValidationError",
            message=str(exc),
            timings=timings,
        )
    except STTError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=502,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="STTError",
            message=str(exc),
            timings=timings,
        )
    except OpenClawBinaryNotFound as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=500,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="OpenClawBinaryNotFound",
            message=str(exc),
            timings=timings,
        )
    except OpenClawTimeout as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=504,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="OpenClawTimeout",
            message=str(exc),
            timings=timings,
        )
    except OpenClawNonZeroExit as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=502,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="OpenClawNonZeroExit",
            message=f"OpenClaw failed with exit code {exc.exit_code}: {exc}",
            timings=timings,
        )
    except OpenClawError as exc:
        timings["total"] = _elapsed_ms(started)
        return _error_response(
            status_code=502,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class="OpenClawError",
            message=str(exc),
            timings=timings,
        )
    except Exception as exc:  # pragma: no cover
        timings["total"] = _elapsed_ms(started)
        logger.exception(
            "turn_failed",
            extra={
                "extra": {
                    "turn_id": turn_id,
                    "conversation_id": conversation_out,
                    "status": "error",
                    "error_class": exc.__class__.__name__,
                }
            },
        )
        return _error_response(
            status_code=500,
            turn_id=turn_id,
            conversation_id=conversation_out,
            error_class=exc.__class__.__name__,
            message="Unexpected server error",
            timings=timings,
        )


@app.get("/api/version")
def version() -> dict[str, str]:
    return {"version": app.version}


frontend_dir = Path(__file__).resolve().parents[1] / "frontend"
if frontend_dir.exists():
    app.mount("/", StaticFiles(directory=str(frontend_dir), html=True), name="frontend")
