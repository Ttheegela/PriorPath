from typing import Any


class FakeLLM:
    def __init__(self, replies: list[str] | None = None, error: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.calls: list[str] = []

    def complete(self, system: str, user: str) -> str:
        self.calls.append(user)
        if self.error is not None:
            raise self.error
        return self.replies.pop(0) if self.replies else ""


class FakeVision:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[bytes, str]] = []

    def extract(
        self, image_jpeg: bytes, schema: dict[str, Any], prompt: str, page_no: int | None = None
    ) -> dict[str, Any]:
        self.calls.append((image_jpeg, prompt))
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r  # type: ignore[no-any-return]
