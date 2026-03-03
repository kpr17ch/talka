from __future__ import annotations

import re
from typing import Pattern

import httpx

from .config import Settings

VOICE_BLOCK_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(
        r"(?is)(?:^|\n)\s*\[(?:voice|voice_summary)\]\s*:?\s*(.*?)(?=(?:\n\s*\[(?:detail|next|next_steps)\]\s*:?)|\Z)"
    ),
    re.compile(
        r"(?is)(?:^|\n)\s*(?:voice|voice_summary)\s*:\s*(.*?)(?=(?:(?:\n|\s+)(?:detail|next|next_steps)\s*:)|\Z)"
    ),
)

TECHNICAL_PATTERNS: tuple[Pattern[str], ...] = (
    re.compile(r"```"),
    re.compile(r"`[^`\n]+`"),
    re.compile(r"https?://"),
    re.compile(r"(?m)^\s*\|.*\|\s*$"),
    re.compile(r"(?m)^\s*(?:\$|>)\s*"),
    re.compile(
        r"(?m)^\s*(?:npm|npx|pnpm|yarn|pip|python|python3|node|git|ssh|systemctl|uvicorn|pytest|curl|wget)\b"
    ),
    re.compile(r"(?<!\w)(?:[A-Za-z]:\\|/)[^\s]+"),
)


class Orchestrator:
    def __init__(self, settings: Settings):
        self.settings = settings

    def to_speakable(self, raw_text: str) -> str:
        source_text = self._extract_voice_source(raw_text)
        has_technical_details = self._contains_technical_artifacts(raw_text)
        if self.settings.orchestrator_mode == "llm":
            try:
                rewritten = self._rewrite_with_llm(source_text)
                if rewritten:
                    return self._finalize_spoken_text(rewritten, has_technical_details=has_technical_details)
            except Exception:
                pass
        return self._finalize_spoken_text(self._rules(source_text), has_technical_details=has_technical_details)

    @staticmethod
    def _extract_voice_source(raw_text: str) -> str:
        for pattern in VOICE_BLOCK_PATTERNS:
            match = pattern.search(raw_text)
            if not match:
                continue
            block = (match.group(1) or "").strip()
            if block:
                return block
        return raw_text

    @staticmethod
    def _contains_technical_artifacts(raw_text: str) -> bool:
        return any(pattern.search(raw_text) for pattern in TECHNICAL_PATTERNS)

    def _rules(self, text: str) -> str:
        cleaned = text
        cleaned = re.sub(r"```[\s\S]*?```", "", cleaned)
        cleaned = re.sub(r"`[^`\n]+`", " ", cleaned)
        cleaned = re.sub(r"(?im)^\s*(?:voice|voice_summary|detail|next|next_steps)\s*:\s*", "", cleaned)
        cleaned = re.sub(r"(?im)\b(?:voice|voice_summary|detail|next|next_steps)\s*:\s*", " ", cleaned)
        cleaned = re.sub(r"^#{1,6}\s*", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"\[([^\]]+)\]\([^\)]+\)", r"\1", cleaned)
        cleaned = re.sub(r"https?://\S+", " ", cleaned)
        cleaned = re.sub(r"^\s*\|.*\|\s*$", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*[-*+]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(r"^\s*\d+[.)]\s+", "", cleaned, flags=re.MULTILINE)
        cleaned = re.sub(
            r"(?m)^\s*(?:\$|>)\s*(?:npm|npx|pnpm|yarn|pip|python|python3|node|git|ssh|systemctl|uvicorn|pytest|curl|wget)\b.*$",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"(?<!\w)(?:[A-Za-z]:\\|/)[^\s]+", " ", cleaned)
        cleaned = re.sub(r"\n{2,}", "\n", cleaned)
        cleaned = cleaned.replace("\n", " ")
        cleaned = re.sub(r"\s{2,}", " ", cleaned).strip()

        if not cleaned:
            return "Ich habe eine Antwort fuer dich vorbereitet."

        return cleaned

    def _finalize_spoken_text(self, text: str, *, has_technical_details: bool) -> str:
        cleaned = " ".join((text or "").split())
        if not cleaned:
            if has_technical_details and self.settings.orchestrator_voice_detail_hint:
                return "Ich habe das fuer dich vorbereitet. Die technischen Details habe ich dir in Telegram geschickt."
            return "Ich habe eine Antwort fuer dich vorbereitet."

        cleaned = self._limit_sentences(cleaned, max_sentences=max(1, self.settings.orchestrator_voice_max_sentences))
        if has_technical_details and self.settings.orchestrator_voice_detail_hint:
            lower = cleaned.lower()
            if "telegram" not in lower and "details" not in lower:
                cleaned = f"{cleaned} Die technischen Details habe ich dir in Telegram geschickt."

        max_chars = max(0, self.settings.orchestrator_max_speak_chars)
        if max_chars and len(cleaned) > max_chars:
            cleaned = self._truncate(cleaned, max_chars)
        return cleaned

    @staticmethod
    def _limit_sentences(text: str, *, max_sentences: int) -> str:
        if max_sentences <= 0:
            return text
        parts = re.split(r"(?<=[.!?])\s+", text.strip())
        parts = [part.strip() for part in parts if part.strip()]
        if len(parts) <= max_sentences:
            return text.strip()
        return " ".join(parts[:max_sentences]).strip()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text

        window = text[: max_chars + 1]
        split_points = [
            window.rfind(". "),
            window.rfind("! "),
            window.rfind("? "),
            window.rfind("; "),
            window.rfind(": "),
            window.rfind(", "),
            window.rfind(" "),
        ]
        last_split = max(split_points)
        if last_split >= int(max_chars * 0.7):
            truncated = window[:last_split].rstrip()
        else:
            truncated = text[:max_chars].rstrip()
        return f"{truncated} ..."

    def _rewrite_with_llm(self, raw_text: str) -> str:
        if not self.settings.openai_api_key:
            raise ValueError("OPENAI_API_KEY is required for ORCHESTRATOR_MODE=llm")

        endpoint = f"{self.settings.openai_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": self.settings.orchestrator_model,
            "temperature": 0.2,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "Du bist ein Voice-Narrator fuer einen AI-Assistant. "
                        "Erzeuge natuerliches gesprochenes Deutsch in 2 bis 4 kurzen Saetzen. "
                        "Wichtig: Keine Markdown-Syntax, keine Code-Bloecke, keine Dateipfade, keine URLs, "
                        "keine Shell-Kommandos und keine Log-Zeilen. "
                        "Wenn technische Details enthalten sind, sage genau einmal, dass die Details in Telegram stehen."
                    ),
                },
                {"role": "user", "content": raw_text},
            ],
        }

        with httpx.Client(timeout=self.settings.request_timeout_seconds) as client:
            resp = client.post(endpoint, headers=headers, json=payload)
            resp.raise_for_status()
            data = resp.json()

        choice = (data.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        return (message.get("content") or "").strip()
