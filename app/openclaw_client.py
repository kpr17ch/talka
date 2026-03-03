import json
import subprocess
import time
from dataclasses import dataclass
from typing import Callable

from .config import Settings
from .errors import (
    OpenClawBinaryNotFound,
    OpenClawCancelled,
    OpenClawEmptyAssistant,
    OpenClawInvalidJson,
    OpenClawNonZeroExit,
    OpenClawTimeout,
)

OPENCLAW_ROLE_PROMPT = """Du bist der persoenliche AI-Assistent von Kai.
Du arbeitest auf deinem Laptop (OpenClaw) und kommunizierst ueber zwei Kanaele:
- Telegram: ausfuehrliche Details, Artefakte, Code, Logs, Reports
- Voice: natuerliches Gespraech mit Orientierung, Prioritaeten und naechsten Schritten

Wichtig:
- Lies Telegram-Inhalte nicht 1:1 vor.
- Wenn Inhalte lang oder technisch sind, erklaere in Voice die Bedeutung und verweise auf Telegram.
- Wenn passend, sage natuerlich, dass du einen ausfuehrlichen Bericht in Telegram geschickt hast.
- Im [VOICE]-Abschnitt nur Plain Text: keine Markdown-Formatierung (kein **, *, _, ~, Backticks).
- Wenn die Anfrage laenger oder arbeitsintensiv ist: sende zuerst eine sehr kurze Bestaetigung
  (z. B. "Alles klar, ich kuemmere mich darum und melde mich mit Updates."), arbeite dann weiter
  und gib sinnvolle Zwischenupdates plus finale Rueckmeldung.
- Wenn die Anfrage sehr kurz/einfach ist: antworte direkt ohne extra Bestaetigungsnachricht.
- Pflicht: Pruefe zu Beginn jeder Session deine eigenen Memory-/MD-Dateien zur Arbeitsweise.
  Wenn diese Regeln dort fehlen oder abweichen, aktualisiere sie sofort.
- Gib danach genau einmal in [DETAIL] eine kurze Bestaetigung im Format:
  "MEMORY_SYNC: Regeln aktualisiert." (oder "MEMORY_SYNC: bereits aktuell.").

Bevorzugtes Antwortformat:
[VOICE]
<sprechbare Nachricht fuer den Character>

[DETAIL]
<ausfuehrliche Nachricht fuer Telegram; kann Code/Logs/Links enthalten>

[NEXT]
<naechster Schritt oder konkrete Rueckfrage>

Wenn eine Sektion nicht noetig ist, lasse sie weg.
"""


@dataclass
class OpenClawResult:
    raw_json: dict
    raw_text: str
    session_id: str | None
    run_id: str | None
    exit_code: int


def _extract_json(stdout: str) -> dict:
    body = stdout.strip()
    if not body:
        raise OpenClawInvalidJson("OpenClaw returned empty stdout")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        start = body.find("{")
        end = body.rfind("}")
        if start == -1 or end == -1 or end <= start:
            raise OpenClawInvalidJson("OpenClaw stdout is not valid JSON")
        try:
            return json.loads(body[start : end + 1])
        except json.JSONDecodeError as exc:
            raise OpenClawInvalidJson(f"OpenClaw JSON parse failed: {exc}") from exc


def _extract_text(payload: dict) -> str:
    result = payload.get("result") or {}
    payloads = result.get("payloads") or []
    for entry in payloads:
        if not isinstance(entry, dict):
            continue
        text = (entry.get("text") or "").strip()
        if text:
            return text
    raise OpenClawEmptyAssistant("OpenClaw returned no assistant text")


def _extract_session_id(payload: dict) -> str | None:
    result = payload.get("result") or {}
    meta = result.get("meta") or {}
    agent_meta = meta.get("agentMeta") or {}
    session_id = agent_meta.get("sessionId")
    return str(session_id) if session_id else None


class OpenClawClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    def _process_timeout(self, timeout_seconds: int) -> int:
        grace = max(1, self.settings.openclaw_process_grace_seconds)
        return max(1, timeout_seconds) + grace

    def _send_message(
        self,
        *,
        channel: str,
        target: str,
        message: str,
        timeout_seconds: int,
        context_label: str,
    ) -> bool:
        if not target:
            raise OpenClawNonZeroExit(f"{context_label} target is empty", 2)
        text = " ".join((message or "").split())
        if not text:
            return False
        args = [
            self.settings.openclaw_bin,
            "message",
            "send",
            "--json",
            "--channel",
            channel,
            "--target",
            target,
            "--message",
            text,
        ]

        try:
            proc = subprocess.run(
                args,
                capture_output=True,
                text=True,
                timeout=self._process_timeout(timeout_seconds),
                check=False,
            )
        except FileNotFoundError as exc:
            raise OpenClawBinaryNotFound(f"OpenClaw binary not found: {self.settings.openclaw_bin}") from exc
        except subprocess.TimeoutExpired as exc:
            raise OpenClawTimeout(f"{context_label} timeout after {timeout_seconds}s") from exc

        if proc.returncode != 0:
            stderr = (proc.stderr or "").strip()
            stdout = (proc.stdout or "").strip()
            error_message = stderr or stdout or f"{context_label} exited with non-zero code"
            raise OpenClawNonZeroExit(error_message, proc.returncode)
        return True

    def mirror_user_text(self, user_text: str) -> bool:
        target = (self.settings.user_text_mirror_target or self.settings.openclaw_to).strip()
        message = self._build_user_mirror_message(user_text)
        return self._send_message(
            channel=self.settings.user_text_mirror_channel,
            target=target,
            message=message,
            timeout_seconds=self.settings.user_text_mirror_timeout_seconds,
            context_label="OpenClaw mirror",
        )

    def send_assistant_ack(self, ack_text: str) -> bool:
        return self._send_message(
            channel=self.settings.openclaw_channel,
            target=self.settings.openclaw_to.strip(),
            message=ack_text,
            timeout_seconds=self.settings.turn_ack_telegram_timeout_seconds,
            context_label="OpenClaw ACK send",
        )

    def _build_user_mirror_message(self, user_text: str) -> str:
        text = " ".join(user_text.strip().split())
        label = self.settings.user_text_mirror_label.strip()
        if label:
            text = f"{label}: {text}"
        max_chars = max(80, self.settings.user_text_mirror_max_chars)
        if len(text) > max_chars:
            text = text[: max_chars - 4].rstrip() + " ..."
        return text

    def _build_agent_message(self, user_text: str, *, ack_already_sent: bool = False) -> str:
        text = user_text.strip()
        if ack_already_sent:
            text = (
                f"{text}\n\n"
                "Systemhinweis: Die initiale Start-Bestaetigung wurde bereits separat gesendet. "
                "Wiederhole keine Start-Bestaetigung und starte direkt mit dem inhaltlichen Update."
            )
        if not self.settings.openclaw_role_prompt_enabled:
            return text
        return f"{OPENCLAW_ROLE_PROMPT}\n\nNutzeranfrage:\n{text}"

    def ask(
        self,
        user_text: str,
        conversation_id: str | None = None,
        ack_already_sent: bool = False,
        should_cancel: Callable[[], bool] | None = None,
    ) -> OpenClawResult:
        args = [
            self.settings.openclaw_bin,
            "agent",
            "--json",
            "--deliver",
            "--message",
            self._build_agent_message(user_text, ack_already_sent=ack_already_sent),
            "--timeout",
            str(self.settings.openclaw_timeout_seconds),
        ]

        if conversation_id:
            args.extend(["--session-id", conversation_id])
            args.extend(["--reply-channel", self.settings.openclaw_channel])
            args.extend(["--reply-to", self.settings.openclaw_to])
        else:
            args.extend(["--channel", self.settings.openclaw_channel])
            args.extend(["--to", self.settings.openclaw_to])

        try:
            proc = subprocess.Popen(
                args,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
        except FileNotFoundError as exc:
            raise OpenClawBinaryNotFound(f"OpenClaw binary not found: {self.settings.openclaw_bin}") from exc

        timeout_seconds = self._process_timeout(self.settings.openclaw_timeout_seconds)
        deadline = time.monotonic() + timeout_seconds
        stdout = ""
        stderr = ""
        try:
            while True:
                if should_cancel and should_cancel():
                    if proc.poll() is None:
                        proc.kill()
                    raise OpenClawCancelled("OpenClaw request cancelled")
                if time.monotonic() > deadline:
                    if proc.poll() is None:
                        proc.kill()
                    raise OpenClawTimeout(f"OpenClaw timeout after {self.settings.openclaw_timeout_seconds}s")
                try:
                    stdout, stderr = proc.communicate(timeout=0.25)
                    break
                except subprocess.TimeoutExpired:
                    continue
        except (OpenClawCancelled, OpenClawTimeout):
            raise

        if proc.returncode != 0:
            message = (stderr or "").strip() or (stdout or "").strip() or "OpenClaw exited with non-zero code"
            raise OpenClawNonZeroExit(message, proc.returncode)

        payload = _extract_json(stdout)
        text = _extract_text(payload)
        return OpenClawResult(
            raw_json=payload,
            raw_text=text,
            session_id=_extract_session_id(payload),
            run_id=payload.get("runId"),
            exit_code=proc.returncode,
        )
