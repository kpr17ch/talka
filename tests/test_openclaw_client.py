import pytest

from app.errors import OpenClawEmptyAssistant, OpenClawInvalidJson
from app.openclaw_client import _extract_json, _extract_text


def test_extract_json_with_prefix_suffix_noise():
    payload = _extract_json('noise {"result":{"payloads":[{"text":"OK"}]}} tail')
    assert payload["result"]["payloads"][0]["text"] == "OK"


def test_extract_json_invalid():
    with pytest.raises(OpenClawInvalidJson):
        _extract_json("not-json")


def test_extract_text_raises_on_empty_payloads():
    with pytest.raises(OpenClawEmptyAssistant):
        _extract_text({"result": {"payloads": []}})
