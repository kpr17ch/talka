import json
from unittest.mock import patch, MagicMock

from app.config import Settings
from app.llm_orchestrator import LLMOrchestrator, OrchestratorResult
from app.models import PanelState


def _make_llm_response(voice_response, panels=None):
    body = {"voice_response": voice_response, "panels": panels or {}}
    return {
        "choices": [{"message": {"content": json.dumps(body)}}],
    }


def test_parse_full_structured_response():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="test-key")
    orch = LLMOrchestrator(settings)

    content = json.dumps({
        "voice_response": "Ich habe den Bug gefixt.",
        "panels": {
            "current_task": {"title": "Bug fixen", "steps": ["✅ Analyse", "⏳ Fix", "⬜ Deploy"]},
            "pinboard": ["Server laeuft wieder"],
            "work_notes": ["main.py geaendert"],
        },
    })
    result = orch._parse_response(content)

    assert result.voice_response == "Ich habe den Bug gefixt."
    assert result.panels.current_task is not None
    assert result.panels.current_task.title == "Bug fixen"
    assert len(result.panels.current_task.steps) == 3
    assert result.panels.pinboard == ["Server laeuft wieder"]
    assert result.panels.work_notes == ["main.py geaendert"]


def test_parse_empty_panels():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="test-key")
    orch = LLMOrchestrator(settings)

    content = json.dumps({
        "voice_response": "Alles klar, ich bin bereit.",
        "panels": {"current_task": None, "pinboard": [], "work_notes": []},
    })
    result = orch._parse_response(content)

    assert result.voice_response == "Alles klar, ich bin bereit."
    assert result.panels.current_task is None
    assert result.panels.pinboard == []
    assert result.panels.work_notes == []


def test_parse_missing_voice_response_uses_fallback_text():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="test-key")
    orch = LLMOrchestrator(settings)

    content = json.dumps({"voice_response": "", "panels": {}})
    result = orch._parse_response(content)

    assert "vorbereitet" in result.voice_response


def test_fallback_on_rules_mode():
    settings = Settings(ORCHESTRATOR_MODE="rules", ORCHESTRATOR_VOICE_DETAIL_HINT=False)
    orch = LLMOrchestrator(settings)

    result = orch.process("Einfache Antwort ohne technische Details.")

    assert result.voice_response
    assert isinstance(result.panels, PanelState)
    assert result.panels.current_task is None


def test_fallback_on_missing_api_key():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="")
    orch = LLMOrchestrator(settings)

    result = orch.process("Test input")

    assert result.voice_response
    assert isinstance(result.panels, PanelState)


def test_pinboard_capped_at_five():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="test-key")
    orch = LLMOrchestrator(settings)

    content = json.dumps({
        "voice_response": "Test",
        "panels": {"pinboard": ["a", "b", "c", "d", "e", "f", "g"]},
    })
    result = orch._parse_response(content)

    assert len(result.panels.pinboard) == 5


def test_work_notes_capped_at_three():
    settings = Settings(ORCHESTRATOR_MODE="llm", OPENAI_API_KEY="test-key")
    orch = LLMOrchestrator(settings)

    content = json.dumps({
        "voice_response": "Test",
        "panels": {"work_notes": ["a", "b", "c", "d", "e"]},
    })
    result = orch._parse_response(content)

    assert len(result.panels.work_notes) == 3


@patch("app.llm_orchestrator.httpx.Client")
def test_llm_error_falls_back_to_rules(mock_client_cls):
    mock_client = MagicMock()
    mock_client.__enter__ = MagicMock(return_value=mock_client)
    mock_client.__exit__ = MagicMock(return_value=False)
    mock_client.post.side_effect = httpx.HTTPStatusError(
        "500", request=MagicMock(), response=MagicMock(status_code=500)
    )
    mock_client_cls.return_value = mock_client

    settings = Settings(
        ORCHESTRATOR_MODE="llm",
        OPENAI_API_KEY="test-key",
        ORCHESTRATOR_VOICE_DETAIL_HINT=False,
    )
    orch = LLMOrchestrator(settings)
    result = orch.process("Einfache Antwort.")

    assert result.voice_response
    assert isinstance(result.panels, PanelState)
    assert result.panels.current_task is None


import httpx
