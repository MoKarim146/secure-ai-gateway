import asyncio
from typing import Any

import pytest

from gateway.completions import create_completion
from gateway.config import ModelInfo
from gateway.errors import APIError
from gateway.providers import Completion, ProviderError
from gateway.providers.mock import MockProvider
from gateway.schemas import ChatCompletionRequest


class DownProvider:
    def __init__(self) -> None:
        self.calls = 0

    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        self.calls += 1
        raise ProviderError("down")


def model(id: str, provider: str, tier: int, price: str) -> ModelInfo:
    return ModelInfo.model_validate(
        {
            "id": id,
            "provider": provider,
            "tier": tier,
            "input_price": price,
            "output_price": price,
            "context_window": 100_000,
        }
    )


# Free local models rank first, so the paid mock model is the fallback.
CATALOG = [
    model("local-small", "ollama", 1, "0"),
    model("local-medium", "ollama", 2, "0"),
    model("cloud-small", "mock", 1, "0.15"),
]


def ask(content: str = "Hi!", **extra: Any) -> ChatCompletionRequest:
    return ChatCompletionRequest.model_validate(
        {"messages": [{"role": "user", "content": content}], **extra}
    )


def test_falls_back_to_next_provider_and_skips_the_failed_one() -> None:
    down = DownProvider()
    result = asyncio.run(
        create_completion(ask(), CATALOG, {"ollama": down, "mock": MockProvider()})
    )
    assert result.model.id == "cloud-small"
    # local-medium was not tried: its provider had already failed.
    assert down.calls == 1


def test_all_providers_failing_returns_502() -> None:
    providers = {"ollama": DownProvider(), "mock": DownProvider()}
    with pytest.raises(APIError) as error:
        asyncio.run(create_completion(ask(), CATALOG, providers))
    assert error.value.status_code == 502


def test_no_model_with_required_capability_returns_400() -> None:
    request = ask(response_format={"type": "json_object"})
    with pytest.raises(APIError) as error:
        asyncio.run(create_completion(request, CATALOG, {"mock": MockProvider()}))
    assert (error.value.status_code, error.value.code) == (400, "no_suitable_model")
