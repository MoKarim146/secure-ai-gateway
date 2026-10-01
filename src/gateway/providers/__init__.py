"""The contract every LLM provider adapter implements."""

from dataclasses import dataclass
from typing import Protocol

import httpx

from gateway.schemas import ChatCompletionRequest


class ProviderError(Exception):
    """A failure another provider might not have (timeout, refused connection, 5xx).

    Raising this tells the gateway to fall back to the next candidate model.
    """


# What a failed provider call can raise: transport and HTTP errors, or a reply body that
# isn't JSON or lacks the fields the adapter reads.
CALL_ERRORS = (httpx.HTTPError, LookupError, TypeError, ValueError)


def provider_error(provider: str, exc: Exception) -> ProviderError:
    """Describes a failed call by HTTP status or exception type only.

    Never by response body: an upstream error body can echo prompt content, which must not
    reach the logs.
    """
    if isinstance(exc, httpx.HTTPStatusError):
        return ProviderError(f"{provider}: HTTP {exc.response.status_code}")
    return ProviderError(f"{provider}: {type(exc).__name__}")


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
