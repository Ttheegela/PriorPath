import base64
from types import SimpleNamespace
from typing import Any

import pytest

from app.llm import vision
from app.llm.vision import OpenRouterVisionClient, VisionError


class FakeCompletions:
    def __init__(self, result: Any) -> None:
        self.result = result
        self.kwargs: dict[str, Any] = {}

    def create(self, **kwargs: Any) -> Any:
        self.kwargs = kwargs
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def make(
    result: Any, monkeypatch: pytest.MonkeyPatch
) -> tuple[OpenRouterVisionClient, FakeCompletions, dict[str, Any]]:
    comp = FakeCompletions(result)
    init: dict[str, Any] = {}

    def fake_openai(**kw: Any) -> Any:
        init.update(kw)
        return SimpleNamespace(chat=SimpleNamespace(completions=comp))

    monkeypatch.setattr(vision, "OpenAI", fake_openai)
    return OpenRouterVisionClient("k", "m"), comp, init


def reply(content: str | None, finish: str = "stop") -> Any:
    return SimpleNamespace(
        choices=[SimpleNamespace(finish_reason=finish, message=SimpleNamespace(content=content))]
    )


def test_request_shape_and_parsed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    client, comp, init = make(reply('{"lines": []}'), monkeypatch)
    assert client.extract(b"png", {"type": "object"}, "read") == {"lines": []}
    assert init["max_retries"] == 0 and init["timeout"] == 25.0
    kw = comp.kwargs
    assert kw["temperature"] == 0 and kw["max_tokens"] == 4000 and kw["model"] == "m"
    parts = kw["messages"][0]["content"]
    assert parts[0] == {"type": "text", "text": "read"}
    assert parts[1]["image_url"]["url"] == "data:image/jpeg;base64," + base64.b64encode(b"png").decode()
    rf = kw["response_format"]
    assert rf["type"] == "json_schema" and rf["json_schema"]["strict"] is True
    assert rf["json_schema"]["name"] == "bill_page" and rf["json_schema"]["schema"] == {"type": "object"}


@pytest.mark.parametrize(
    "result",
    [
        reply('{"a": 1}', finish="length"),
        reply("not json"),
        reply(None),
        reply("[1]"),
        SimpleNamespace(choices=[]),
        SimpleNamespace(choices=None),
        RuntimeError("secret sdk text"),
    ],
)
def test_failures_become_vision_error(result: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    client, _, _ = make(result, monkeypatch)
    with pytest.raises(VisionError) as ei:
        client.extract(b"png", {}, "p")
    assert "secret" not in str(ei.value)


def test_default_client_needs_key_and_ignores_empty_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("OPENROUTER_API_KEY", raising=False)
    assert vision.default_vision_client() is None
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("EXTRACT_MODEL", "")
    c = vision.default_vision_client()
    assert isinstance(c, OpenRouterVisionClient) and c._model == vision.DEFAULT_EXTRACT_MODEL
