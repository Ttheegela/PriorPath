from types import SimpleNamespace
from typing import Any

import pytest

from app.llm.client import OpenRouterClient


def _reply(content: str, finish: str) -> SimpleNamespace:
    return SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=content), finish_reason=finish)]
    )


@pytest.mark.parametrize(("finish", "expected"), [("stop", "A fine answer."), ("length", "")])
def test_truncated_completion_is_a_failed_draft(
    monkeypatch: pytest.MonkeyPatch, finish: str, expected: str
) -> None:
    client = OpenRouterClient("k", "m")
    monkeypatch.setattr(
        client._client.chat.completions, "create", lambda **kw: _reply("A fine answer.", finish)
    )
    assert client.complete("s", "u") == expected


def test_explain_request_disables_reasoning(monkeypatch: pytest.MonkeyPatch) -> None:
    client = OpenRouterClient("k", "m")
    calls: list[dict[str, Any]] = []

    def create(**kw: Any) -> SimpleNamespace:
        calls.append(kw)
        return _reply("A fine answer.", "stop")

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    assert client.complete("s", "u") == "A fine answer."
    assert calls[0]["extra_body"] == {"reasoning": {"enabled": False}}
    assert calls[0]["max_tokens"] == 300
