import asyncio
import json
from collections.abc import Callable, Mapping
from typing import Any

import httpx
import pytest

from gateway.providers import Provider, ProviderError
from gateway.providers.anthropic import DEFAULT_MAX_TOKENS, JSON_INSTRUCTION, AnthropicProvider
from gateway.providers.mock import MockProvider
from gateway.providers.ollama import OllamaProvider
from gateway.providers.openai import OpenAIProvider
from gateway.schemas import ChatCompletionRequest

REQUEST = ChatCompletionRequest.model_validate(
    {"messages": [{"role": "user", "content": "hello"}], "max_tokens": 50, "temperature": 0.2}
)
CONVERSATION = ChatCompletionRequest.model_validate(
    {
        "messages": [
            {"role": "system", "content": "Be brief."},
            {"role": "user", "content": "hello"},
            {"role": "assistant", "content": "hi"},
            {"role": "user", "content": "thanks"},
        ],
        "temperature": 0.2,
        "response_format": {"type": "json_object"},
    }
)

Handler = Callable[[httpx.Request], httpx.Response]


def client_with(handler: Handler, base_url: str) -> httpx.AsyncClient:
    return httpx.AsyncClient(base_url=base_url, transport=httpx.MockTransport(handler))


def ollama_with(handler: Handler) -> OllamaProvider:
    return OllamaProvider(client_with(handler, "http://ollama"))


def openai_with(handler: Handler) -> OpenAIProvider:
    return OpenAIProvider(client_with(handler, "http://openai/v1"), api_key="sk-test")


def anthropic_with(handler: Handler) -> AnthropicProvider:
    return AnthropicProvider(client_with(handler, "http://anthropic"), api_key="sk-ant-test")


Sent = dict[str, Any]


def recording(sent: Sent, reply: Mapping[str, Any]) -> Handler:
    """A fake upstream that keeps what it was sent and answers with `reply`."""

    def handler(request: httpx.Request) -> httpx.Response:
        sent.update(json.loads(request.content), path=request.url.path, headers=request.headers)
        return httpx.Response(200, json=reply)

    return handler


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


def null_fields(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, json={"message": None, "choices": None, "content": None})


def not_json(request: httpx.Request) -> httpx.Response:
    return httpx.Response(200, text="<html>proxy error</html>")


def timeout(request: httpx.Request) -> httpx.Response:
    raise httpx.ReadTimeout("slow", request=request)


def connection_refused(request: httpx.Request) -> httpx.Response:
    raise httpx.ConnectError("refused", request=request)


FAILURES = [server_error, malformed_body, null_fields, not_json, timeout, connection_refused]
ADAPTERS = [ollama_with, openai_with, anthropic_with]


@pytest.mark.parametrize("handler", FAILURES)
@pytest.mark.parametrize("adapter", ADAPTERS)
def test_failures_become_provider_errors(
    adapter: Callable[[Handler], Provider], handler: Handler
) -> None:
    with pytest.raises(ProviderError):
        asyncio.run(adapter(handler).complete("m1", REQUEST))


@pytest.mark.parametrize("adapter", ADAPTERS)
def test_provider_error_names_the_status_but_never_echoes_the_body(
    adapter: Callable[[Handler], Provider],
) -> None:
    def leaky(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, json={"error": "cannot parse: jane@example.com"})

    with pytest.raises(ProviderError) as error:
        asyncio.run(adapter(leaky).complete("m1", REQUEST))
    assert str(error.value).endswith(": HTTP 400")
    assert "jane" not in str(error.value)


OPENAI_REPLY = {
    "choices": [
        {"index": 0, "message": {"role": "assistant", "content": "hi"}, "finish_reason": "length"}
    ],
    "usage": {"prompt_tokens": 9, "completion_tokens": 2, "total_tokens": 11},
}


def test_openai_translates_request_and_response() -> None:
    sent: Sent = {}
    completion = asyncio.run(
        openai_with(recording(sent, OPENAI_REPLY)).complete("gpt-x", CONVERSATION)
    )

    assert sent["path"] == "/v1/chat/completions"
    assert sent["headers"]["authorization"] == "Bearer sk-test"
    assert sent["model"] == "gpt-x"
    assert sent["messages"] == [m.model_dump() for m in CONVERSATION.messages]
    assert sent["response_format"] == {"type": "json_object"}
    # Reasoning models reject non-default temperatures, so it is never forwarded.
    assert "temperature" not in sent
    assert (completion.content, completion.finish_reason) == ("hi", "length")
    assert (completion.prompt_tokens, completion.completion_tokens) == (9, 2)


def test_openai_sends_max_tokens_under_its_current_name() -> None:
    sent: Sent = {}
    asyncio.run(openai_with(recording(sent, OPENAI_REPLY)).complete("gpt-x", REQUEST))
    assert sent["max_completion_tokens"] == 50
    assert "max_tokens" not in sent


def test_openai_null_content_becomes_an_empty_reply() -> None:
    reply = {**OPENAI_REPLY, "choices": [{"message": {"content": None}, "finish_reason": "stop"}]}
    completion = asyncio.run(openai_with(recording({}, reply)).complete("gpt-x", REQUEST))
    assert completion.content == ""


def anthropic_reply(stop_reason: str = "end_turn") -> dict[str, Any]:
    return {
        "content": [
            {"type": "thinking", "thinking": "", "signature": "sig"},
            {"type": "text", "text": "hi "},
            {"type": "text", "text": "there"},
        ],
        "stop_reason": stop_reason,
        "usage": {"input_tokens": 9, "output_tokens": 2},
    }


def test_anthropic_translates_request_and_response() -> None:
    sent: Sent = {}
    completion = asyncio.run(
        anthropic_with(recording(sent, anthropic_reply())).complete("model-x", CONVERSATION)
    )

    assert sent["path"] == "/v1/messages"
    headers = sent["headers"]
    assert headers["x-api-key"] == "sk-ant-test"
    assert headers["anthropic-version"] == "2023-06-01"
    # System messages move to the top-level field; JSON mode becomes an instruction there.
    assert sent["system"] == f"Be brief.\n\n{JSON_INSTRUCTION}"
    assert sent["messages"] == [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": "hi"},
        {"role": "user", "content": "thanks"},
    ]
    assert sent["max_tokens"] == DEFAULT_MAX_TOKENS  # required by Anthropic, unset by client
    assert "temperature" not in sent
    # Thinking blocks are dropped; text blocks are joined.
    assert (completion.content, completion.finish_reason) == ("hi there", "stop")
    assert (completion.prompt_tokens, completion.completion_tokens) == (9, 2)


def test_anthropic_passes_max_tokens_and_omits_empty_system() -> None:
    sent: Sent = {}
    asyncio.run(anthropic_with(recording(sent, anthropic_reply())).complete("model-x", REQUEST))
    assert sent["max_tokens"] == 50
    assert "system" not in sent


@pytest.mark.parametrize(
    ("stop_reason", "finish_reason"),
    [("end_turn", "stop"), ("max_tokens", "length"), ("refusal", "content_filter")],
)
def test_anthropic_stop_reasons_map_to_openai_finish_reasons(
    stop_reason: str, finish_reason: str
) -> None:
    handler = recording({}, anthropic_reply(stop_reason))
    completion = asyncio.run(anthropic_with(handler).complete("model-x", REQUEST))
    assert completion.finish_reason == finish_reason


def test_mock_json_mode_returns_valid_json() -> None:
    request = ChatCompletionRequest.model_validate(
        {
            "messages": [{"role": "user", "content": "hello"}],
            "response_format": {"type": "json_object"},
        }
    )
    completion = asyncio.run(MockProvider().complete("mock-small", request))
    assert json.loads(completion.content)["echo"] == "hello"
