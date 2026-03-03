from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field

import httpx

from .config import Settings
from .models import CurrentTask, PanelState
from .orchestrator import Orchestrator

logger = logging.getLogger("voice_bridge.llm_orchestrator")

SYSTEM_PROMPT = (
    "Du bist ein Kommunikations-Orchestrator fuer einen AI-Assistenten mit zwei Kanaelen:\n"
    "- Telegram fuer ausfuehrliche Details, Code, Logs und Reports\n"
    "- Voice fuer das direkte Gespraech mit dem Nutzer\n\n"
    "Du bekommst die rohe Antwort eines AI-Agenten und musst daraus zwei Dinge extrahieren:\n"
    "1. voice_response: Eine natuerliche gesprochene Nachricht, die Telegram sinnvoll ergaenzt "
    "(kein Vorlesen von Telegram 1:1).\n"
    "2. panels: Strukturierte Daten fuer ein Dashboard.\n\n"
    "Antworte als JSON:\n"
    "{\n"
    '  "voice_response": "...",\n'
    '  "panels": {\n'
    '    "current_task": {"title": "...", "steps": ["✅ Schritt 1", "⏳ Schritt 2", "⬜ Schritt 3"]} oder null,\n'
    '    "pinboard": ["Wichtige Info 1", "..."],\n'
    '    "work_notes": ["Was gerade passiert ist", "..."]\n'
    "  }\n"
    "}\n\n"
    "Regeln:\n"
    "- voice_response: Natuerliches gesprochenes Deutsch ohne starre Satzanzahl. "
    "Halte es kurz bei Status-Updates, werde laenger wenn der Kontext es verlangt.\n"
    "- voice_response: Keine Markdown-Syntax, keine URLs, keine Dateipfade, "
    "keine Shell-Kommandos, keine Code-Bloecke und keine Logzeilen vorlesen.\n"
    "- voice_response: Nutze reinen Plain Text ohne Markdown-Hervorhebung "
    "(kein **, *, _, ~~ oder Backticks).\n"
    "- Wenn der Input technische Details enthaelt, verweise natuerlich auf Telegram "
    "(z. B. Bericht/Details dort geschickt) und erklaere den Kern in Sprache.\n"
    "- Wenn im Input ein [VOICE]-Block enthalten ist, nutze ihn bevorzugt als Grundlage. "
    "Wenn nur [DETAIL] vorliegt, baue daraus eine sprechbare Zusammenfassung.\n"
    "- current_task: Nur befuellen wenn der Agent an einer konkreten Aufgabe arbeitet. "
    "Steps mit ✅/⏳/⬜ Prefix.\n"
    "- pinboard: Kurze, wichtige Infos die der Nutzer im Blick behalten soll. Maximal 5 Eintraege.\n"
    "- work_notes: Was der Agent gerade getan hat. Maximal 3 Eintraege.\n"
    "- Wenn keine strukturierten Infos vorhanden: panels leer lassen (null/leere Arrays)."
)


@dataclass
class OrchestratorResult:
    voice_response: str
    panels: PanelState = field(default_factory=PanelState)


class LLMOrchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._rules_fallback = Orchestrator(settings)

    def process(self, raw_text: str, previous_panels: PanelState | None = None) -> OrchestratorResult:
        if self.settings.orchestrator_mode != "llm" or not self.settings.openai_api_key:
            return self._fallback(raw_text)

        try:
            return self._call_llm(raw_text)
        except Exception as exc:
            logger.warning("llm_orchestrator_failed", extra={"extra": {"error": str(exc)}})
            return self._fallback(raw_text)

    def _call_llm(self, raw_text: str) -> OrchestratorResult:
        endpoint = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.orchestrator_model,
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
            "messages": [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
        }

        with httpx.Client(timeout=self.settings.orchestrator_llm_timeout_seconds) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        content = (message.get("content") or "").strip()

        return self._parse_response(content)

    def _parse_response(self, content: str) -> OrchestratorResult:
        parsed = json.loads(content)

        voice_response = (parsed.get("voice_response") or "").strip()
        if not voice_response:
            voice_response = "Ich habe eine Antwort fuer dich vorbereitet."

        panels_raw = parsed.get("panels") or {}

        current_task = None
        ct_raw = panels_raw.get("current_task")
        if ct_raw and isinstance(ct_raw, dict) and ct_raw.get("title"):
            current_task = CurrentTask(
                title=ct_raw["title"],
                steps=ct_raw.get("steps") or [],
            )

        pinboard = panels_raw.get("pinboard") or []
        if not isinstance(pinboard, list):
            pinboard = []
        pinboard = [str(item) for item in pinboard[:5]]

        work_notes = panels_raw.get("work_notes") or []
        if not isinstance(work_notes, list):
            work_notes = []
        work_notes = [str(item) for item in work_notes[:3]]

        return OrchestratorResult(
            voice_response=voice_response,
            panels=PanelState(
                current_task=current_task,
                pinboard=pinboard,
                work_notes=work_notes,
            ),
        )

    def _fallback(self, raw_text: str) -> OrchestratorResult:
        speak_text = self._rules_fallback.to_speakable(raw_text)
        return OrchestratorResult(voice_response=speak_text, panels=PanelState())
