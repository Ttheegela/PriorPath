import os
from typing import Any, Protocol

from openai import OpenAI

from app.observability import trace_llm

# Chosen 2026-10-02 by grounding-pass rate on the demo flags: v4-flash 11/12, v4-pro 0/12.
DEFAULT_EXPLAIN_MODEL = "deepseek/deepseek-v4-flash"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
PROMPT_VERSION = "explain-v1"


def usage_of(response: Any) -> dict[str, int] | None:
    u = getattr(response, "usage", None)
    if u is None:
        return None
    return {"input": int(u.prompt_tokens or 0), "output": int(u.completion_tokens or 0)}


class LLMClient(Protocol):
    def complete(self, system: str, user: str) -> str: ...


class OpenRouterClient:
    def __init__(self, api_key: str, model: str, timeout: float = 30.0) -> None:
        self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=0)
        self._model = model

    def complete(self, system: str, user: str) -> str:
        meta: dict[str, str | int | float | bool] = {"prompt_version": PROMPT_VERSION}
        with trace_llm("explain", model=self._model, kind="explain", metadata=meta) as span:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                max_tokens=300,
                temperature=0,
            )
            choice = response.choices[0]
            span.end(
                {"ok": choice.finish_reason == "stop", "finish_reason": str(choice.finish_reason)},
                usage_of(response),
            )
            if choice.finish_reason != "stop":  # truncated or filtered: treat as a failed draft
                return ""
            return (choice.message.content or "").strip()


def default_client() -> LLMClient | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterClient(key, os.environ.get("EXPLAIN_MODEL", DEFAULT_EXPLAIN_MODEL))
