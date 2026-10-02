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
