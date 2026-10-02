import base64
import json
import logging
import os
from typing import Any, Protocol

from openai import OpenAI

from app.llm.client import OPENROUTER_BASE_URL

log = logging.getLogger(__name__)
DEFAULT_EXTRACT_MODEL = "google/gemini-2.5-flash-lite"


class VisionError(RuntimeError):
    pass


class VisionClient(Protocol):
    def extract(self, image_jpeg: bytes, schema: dict[str, Any], prompt: str) -> dict[str, Any]: ...


class OpenRouterVisionClient:
    def __init__(self, api_key: str, model: str, timeout: float = 25.0) -> None:
        self._client = OpenAI(api_key=api_key, base_url=OPENROUTER_BASE_URL, timeout=timeout, max_retries=0)
        self._model = model

    def extract(self, image_jpeg: bytes, schema: dict[str, Any], prompt: str) -> dict[str, Any]:
        url = "data:image/jpeg;base64," + base64.b64encode(image_jpeg).decode()
        try:
            response = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "text", "text": prompt},
                            {"type": "image_url", "image_url": {"url": url}},
                        ],
                    }
                ],
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "bill_page", "strict": True, "schema": schema},
                },
                max_tokens=4000,
                temperature=0,
            )
        except Exception as exc:
            log.warning("vision request failed: %s", exc)
            raise VisionError("vision request failed") from exc
        if not response.choices:
            raise VisionError("vision response was empty")
        choice = response.choices[0]
        if choice.finish_reason != "stop":
            raise VisionError(f"vision response incomplete: {choice.finish_reason}")
        try:
            parsed = json.loads(choice.message.content or "")
        except json.JSONDecodeError as exc:
            raise VisionError("vision response was not JSON") from exc
        if not isinstance(parsed, dict):
            raise VisionError("vision response was not a JSON object")
        return parsed


def default_vision_client() -> VisionClient | None:
    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        return None
    return OpenRouterVisionClient(key, os.environ.get("EXTRACT_MODEL") or DEFAULT_EXTRACT_MODEL)
