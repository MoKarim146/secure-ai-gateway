import asyncio
from typing import Any

import pytest

from gateway.completions import create_completion
from gateway.config import ModelInfo
from gateway.errors import APIError
from gateway.privacy import PrivacyPolicy
from gateway.privacy.regex import RegexDetector
from gateway.providers import Completion, Provider, ProviderError
from gateway.providers.mock import MockProvider
from gateway.schemas import ChatCompletionRequest

NO_PRIVACY = PrivacyPolicy(detector=None)


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
        create_completion(ask(), CATALOG, {"ollama": down, "mock": MockProvider()}, NO_PRIVACY)
    )
    assert result.model.id == "cloud-small"
    # local-medium was not tried: its provider had already failed.
    assert down.calls == 1


def test_all_providers_failing_returns_502() -> None:
    providers = {"ollama": DownProvider(), "mock": DownProvider()}
    with pytest.raises(APIError) as error:
        asyncio.run(create_completion(ask(), CATALOG, providers, NO_PRIVACY))
    assert error.value.status_code == 502


def test_no_model_with_required_capability_returns_400() -> None:
    request = ask(response_format={"type": "json_object"})
    with pytest.raises(APIError) as error:
        asyncio.run(create_completion(request, CATALOG, {"mock": MockProvider()}, NO_PRIVACY))
    assert (error.value.status_code, error.value.code) == (400, "no_suitable_model")


class EchoProvider:
    """Keeps every message it is sent and replies with the last one."""

    def __init__(self) -> None:
        self.seen: list[str] = []

    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        self.seen += [m.content for m in request.messages]
        return Completion(f"Noted: {request.messages[-1].content}", 1, 1)


PRIVATE = "Email jane@example.com about IBAN NL91 ABNA 0417 1643 00."
REDACTED = "Email <EMAIL_1> about IBAN <IBAN_1>."
REDACT = PrivacyPolicy(RegexDetector())


def test_untrusted_provider_gets_placeholders_and_the_reply_is_restored() -> None:
    echo = EchoProvider()
    result = asyncio.run(create_completion(ask(PRIVATE), CATALOG, {"ollama": echo}, REDACT))
    assert echo.seen == [REDACTED]
    assert result.response.choices[0].message.content == f"Noted: {PRIVATE}"
    assert result.redactions == 2


def test_trusted_provider_gets_the_original() -> None:
    echo = EchoProvider()
    policy = PrivacyPolicy(RegexDetector(), trusted_providers=frozenset({"ollama"}))
    result = asyncio.run(create_completion(ask(PRIVATE), CATALOG, {"ollama": echo}, policy))
    assert echo.seen == [PRIVATE]
    assert result.redactions == 0


def test_fallback_from_a_trusted_provider_still_redacts_for_the_next_one() -> None:
    echo = EchoProvider()
    policy = PrivacyPolicy(RegexDetector(), trusted_providers=frozenset({"ollama"}))
    providers: dict[str, Provider] = {"ollama": DownProvider(), "mock": echo}
    result = asyncio.run(create_completion(ask(PRIVATE), CATALOG, providers, policy))
    assert result.model.id == "cloud-small"
    assert echo.seen == [REDACTED]
    assert result.response.choices[0].message.content == f"Noted: {PRIVATE}"


def test_redaction_off_sends_everything_as_is() -> None:
    echo = EchoProvider()
    result = asyncio.run(create_completion(ask(PRIVATE), CATALOG, {"ollama": echo}, NO_PRIVACY))
    assert echo.seen == [PRIVATE]
    assert result.redactions == 0
