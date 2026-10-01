"""Adapter for the OpenAI Chat Completions API. The gateway already speaks this format."""

from typing import Any

import httpx

from gateway.providers import CALL_ERRORS, Completion, provider_error
from gateway.schemas import ChatCompletionRequest


class OpenAIProvider:
    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        # The client carries base_url and timeout. The key goes on each request instead, so
        # tests can check exactly which headers leave the gateway.
        self._client = client
        self._headers = {"Authorization": f"Bearer {api_key}"}

    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        payload: dict[str, Any] = {
            "model": model,
            "messages": [m.model_dump() for m in request.messages],
        }
        # Reasoning models reject the older max_tokens name. They also reject non-default
        # temperatures, so temperature is not forwarded: sending it would turn every request
        # that sets it into a failed call and a fallback.
        if request.max_tokens is not None:
            payload["max_completion_tokens"] = request.max_tokens
        if request.wants_json:
            payload["response_format"] = {"type": "json_object"}

        try:
            response = await self._client.post(
                "/chat/completions", json=payload, headers=self._headers
            )
            response.raise_for_status()
            data = response.json()
            choice = data["choices"][0]
            return Completion(
                # content is null when the model declines; that is still a valid reply.
                content=choice["message"]["content"] or "",
                prompt_tokens=data["usage"]["prompt_tokens"],
                completion_tokens=data["usage"]["completion_tokens"],
                finish_reason=choice["finish_reason"],
            )
        except CALL_ERRORS as exc:
            raise provider_error("openai", exc) from exc
