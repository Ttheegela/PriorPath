import os
from typing import Protocol

from openai import OpenAI

DEFAULT_EXPLAIN_MODEL = "deepseek/deepseek-v4-pro"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=0)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        response = self._client.chat.completions.create(
            model=self._model,
            messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
            max_tokens=300,
            temperature=0,
        )
        choice = response.choices[0]
        if choice.finish_reason != "stop":  # truncated or filtered: treat as a failed draft
            return ""
        return (choice.message.content or "").strip()


def default_client() -> LLMClient | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterClient(key, os.environ.get("EXPLAIN_MODEL", DEFAULT_EXPLAIN_MODEL))
