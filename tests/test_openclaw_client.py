import pytest

from app.errors import OpenClawEmptyAssistant, OpenClawInvalidJson
from app.config import Settings
from app.openclaw_client import OpenClawClient, _extract_json, _extract_text


def test_extract_json_with_prefix_suffix_noise():
    payload = _extract_json('noise {"result":{"payloads":[{"text":"OK"}]}} tail')
    assert payload["result"]["payloads"][0]["text"] == "OK"


def test_extract_json_invalid():
    with pytest.raises(OpenClawInvalidJson):
        _extract_json("not-json")


def test_extract_text_raises_on_empty_payloads():
    with pytest.raises(OpenClawEmptyAssistant):
        _extract_text({"result": {"payloads": []}})


def test_build_agent_message_includes_role_prompt_by_default():
    settings = Settings()
    client = OpenClawClient(settings)

    msg = client._build_agent_message("Bitte pruefe die letzten Logs")

    assert "persoenliche AI-Assistent" in msg
    assert "Nutzeranfrage" in msg
    assert "Bitte pruefe die letzten Logs" in msg


def test_build_agent_message_can_disable_role_prompt():
    settings = Settings(OPENCLAW_ROLE_PROMPT_ENABLED=False)
    client = OpenClawClient(settings)

    msg = client._build_agent_message("Kurze Antwort")

    assert msg == "Kurze Antwort"
