import asyncio
import json
from collections.abc import Callable

import httpx
import pytest

from gateway.providers import ProviderError
from gateway.providers.mock import MockProvider
from gateway.providers.ollama import OllamaProvider
from gateway.schemas import ChatCompletionRequest

REQUEST = ChatCompletionRequest.model_validate(
    {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 50, "temperature": 0.2}
)

Handler = Callable[[httpx.Request], httpx.Response]


def ollama_with(handler: Handler) -> OllamaProvider:
    transport = httpx.MockTransport(handler)
    return OllamaProvider(httpx.AsyncClient(base_url="http://ollama", transport=transport))


def test_ollama_translates_request_and_response() -> None:
    sent: dict[str, object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content), path=request.url.path)
        return httpx.Response(
            200,
            json={
                "message": {"role": "assistant", "content": "hi there"},
                "prompt_eval_count": 12,
                "eval_count": 3,
                "done_reason": "stop",
            },
        )

    completion = asyncio.run(ollama_with(handler).complete("m1", REQUEST))

    assert sent["path"] == "/api/chat"
    assert sent["model"] == "m1"
    assert sent["stream"] is False
    assert sent["options"] == {"temperature": 0.2, "num_predict": 50}
    assert completion.content == "hi there"
    assert (completion.prompt_tokens, completion.completion_tokens) == (12, 3)


def server_error(request: httpx.Request) -> httpx.Response:
    return httpx.Response(500, json={"error": "boom"})


def malformed_body(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"unexpected": True})


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


@pytest.mark.parametrize("handler", [server_error, malformed_body, timeout, connection_refused])
def test_ollama_failures_become_provider_errors(handler: Handler) -> None:
    with pytest.raises(ProviderError):
        asyncio.run(ollama_with(handler).complete("m1", REQUEST))


def test_mock_json_mode_returns_valid_json() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
        }
    )
    completion = asyncio.run(MockProvider().complete("mock-small", request))
    assert json.loads(completion.content)["echo"] == "hello"
