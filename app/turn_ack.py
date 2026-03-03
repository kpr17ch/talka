from __future__ import annotations

import re

from .config import Settings

LONG_TASK_KEYWORDS = re.compile(
    r"(?i)\b("
    r"analys\w*|untersuch\w*|debug\w*|beheb\w*|fix\w*|refactor\w*|implement\w*|"
    r"baue\w*|erstell\w*|schreib\w*|deploy\w*|merge\w*|review\w*|test\w*|"
    r"dokumentier\w*|optimier\w*|plan\w*|vergleich\w*|recherchier\w*|"
    r"konfigurier\w*|aufsetz\w*|bericht\w*|workflow\w*|roadmap\w*|zwischenschritt\w*"
    r")\b"
)

TECHNICAL_OR_COMPLEX_CUES = re.compile(
    r"```|`[^`\n]+`|https?://|(?<!\w)(?:[A-Za-z]:\\|/)[^\s]+|^\s*(?:[-*+]\s+|\d+[.)]\s+)",
    flags=re.MULTILINE,
)


def build_turn_ack_text(*, user_text: str, settings: Settings) -> str:
    if not _should_send_ack(user_text=user_text, settings=settings):
        return ""

    custom = (settings.turn_ack_text or "").strip()
    if custom:
        return custom

    normalized = user_text.lower()
    if any(term in normalized for term in ("bericht", "analyse", "plan", "mehrere", "schritte")):
        return "Alles klar, ich kuemmere mich darum. Ich gebe dir Zwischenupdates und melde mich mit dem Ergebnis."
    return "Alles klar, ich kuemmere mich darum und melde mich, sobald ich fertig bin."


def _should_send_ack(*, user_text: str, settings: Settings) -> bool:
    mode = settings.turn_ack_mode
    if mode == "off":
        return False
    if mode == "always":
        return True

    text = (user_text or "").strip()
    if not text:
        return False

    word_count = len(re.findall(r"\S+", text))
    if len(text) >= max(40, settings.turn_ack_auto_min_chars):
        return True
    if word_count >= max(6, settings.turn_ack_auto_min_words):
        return True
    if TECHNICAL_OR_COMPLEX_CUES.search(text):
        return True

    keyword_hits = len(LONG_TASK_KEYWORDS.findall(text))
    if keyword_hits >= 2:
        return True
    if keyword_hits >= 1 and any(term in text.lower() for term in ("bitte", "ausfuehrlich", "detailliert", "komplett")):
        return True
    return False
