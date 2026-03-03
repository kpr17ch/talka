from app.config import Settings
from app.orchestrator import Orchestrator


def test_rules_strip_markdown_and_links():
    settings = Settings(ORCHESTRATOR_MODE="rules", ORCHESTRATOR_VOICE_DETAIL_HINT=False)
    orch = Orchestrator(settings)

    raw = """
# Titel
- Punkt eins
- Punkt zwei
Mehr Details: https://example.com
```python
print('x')
```
"""
    out = orch.to_speakable(raw)

    assert "#" not in out
    assert "```" not in out
    assert "https://" not in out


def test_voice_block_preferred_over_detail_block():
    settings = Settings(ORCHESTRATOR_MODE="rules", ORCHESTRATOR_VOICE_DETAIL_HINT=False)
    orch = Orchestrator(settings)
    raw = """
[VOICE]
Ich habe den Fehler gefunden und die Loesung umgesetzt.

[DETAIL]
Pfad: /opt/voice-bridge/app/main.py
Command: systemctl restart voice-bridge.service
"""
    out = orch.to_speakable(raw)
    assert "Fehler gefunden" in out
    assert "/opt/voice-bridge" not in out
    assert "systemctl" not in out


def test_inline_voice_and_detail_block():
    settings = Settings(ORCHESTRATOR_MODE="rules", ORCHESTRATOR_VOICE_DETAIL_HINT=True)
    orch = Orchestrator(settings)
    raw = "VOICE: Kurzes Update. DETAIL: /opt/voice-bridge/app/main.py"
    out = orch.to_speakable(raw)
    assert "Kurzes Update." in out
    assert "DETAIL" not in out
    assert "/opt" not in out
    assert "Telegram" in out


def test_technical_details_append_telegram_hint():
    settings = Settings(
        ORCHESTRATOR_MODE="rules",
        ORCHESTRATOR_VOICE_DETAIL_HINT=True,
    )
    orch = Orchestrator(settings)
    raw = "Fix ist fertig. Code liegt in /opt/voice-bridge/app/main.py und Command ist systemctl restart voice-bridge."
    out = orch.to_speakable(raw)
    assert "Telegram" in out


def test_no_sentence_limit_applied():
    settings = Settings(
        ORCHESTRATOR_MODE="rules",
        ORCHESTRATOR_VOICE_DETAIL_HINT=False,
    )
    orch = Orchestrator(settings)
    raw = "Satz eins. Satz zwei. Satz drei. Satz vier."
    out = orch.to_speakable(raw)
    assert out == "Satz eins. Satz zwei. Satz drei. Satz vier."
