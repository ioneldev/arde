"""A scripted stand-in for the model, so tests run the real loop with no network."""

from pydantic import BaseModel

from arde.application.ports import LLMClient, LLMRequest, LLMResponse


class FakeLLM(LLMClient):
    def __init__(self, script: list[LLMResponse]) -> None:
        self._replies = iter(script)
        self.requests: list[LLMRequest] = []

    def complete(self, request: LLMRequest) -> LLMResponse:
        self.requests.append(request)
        reply = next(self._replies, None)
        if reply is None:
            raise RuntimeError(f"The script ran out on request {len(self.requests)}")
        return reply

    def parse[T: BaseModel](self, request: LLMRequest, schema: type[T]) -> tuple[T, LLMResponse]:
        raise NotImplementedError("FakeLLM.parse waits for the classifier, the first caller")
