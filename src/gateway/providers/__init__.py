"""The contract every LLM provider adapter implements."""

from dataclasses import dataclass
from typing import Protocol

from gateway.schemas import ChatCompletionRequest


class ProviderError(Exception):
    """A failure another provider might not have (timeout, refused connection, 5xx).

    Raising this tells the gateway to fall back to the next candidate model.
    """


@dataclass(frozen=True)
class Completion:
    content: str
    prompt_tokens: int
    completion_tokens: int
    finish_reason: str = "stop"


class Provider(Protocol):
    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion: ...


def estimate_tokens(text: str) -> int:
    # Rough rule: ~4 characters per token for English. Good enough for routing and the mock;
    # real billing uses the counts providers report. Swap in a tokenizer if estimates drift.
    return max(1, len(text) // 4)
