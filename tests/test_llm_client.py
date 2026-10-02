from types import SimpleNamespace

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
