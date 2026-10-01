"""Adapter for a local Ollama server, using its native /api/chat endpoint."""

from typing import Any

import httpx

from gateway.providers import Completion, ProviderError
from gateway.schemas import ChatCompletionRequest


class OllamaProvider:
    def __init__(self, client: httpx.AsyncClient) -> None:
        # The client carries base_url and timeout, and is shared so connections are reused.
        self._client = client

    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
            "stream": False,
        }
        options: dict[str, Any] = {}
        if request.temperature is not None:
            options["temperature"] = request.temperature
        if request.max_tokens is not None:
            options["num_predict"] = request.max_tokens
        if options:
            payload["options"] = options
        if request.wants_json:
            payload["format"] = "json"

        try:
            response = await self._client.post("/api/chat", json=payload)
            response.raise_for_status()
            data = response.json()
            return Completion(
                content=data["message"]["content"],
                prompt_tokens=data.get("prompt_eval_count", 0),
                completion_tokens=data.get("eval_count", 0),
                finish_reason="length" if data.get("done_reason") == "length" else "stop",
            )
        # Timeouts, refused connections, HTTP errors and malformed bodies all mean "try the
        # next provider". Only the exception type goes in the message: the body could echo
        # prompt content, which must not reach the logs.
        except (httpx.HTTPError, KeyError, ValueError) as exc:
            raise ProviderError(f"ollama: {type(exc).__name__}") from exc
