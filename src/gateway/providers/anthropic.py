"""Adapter for the Anthropic Messages API, translating from and to the OpenAI chat format."""

from typing import Any

import httpx

from gateway.providers import CALL_ERRORS, Completion, provider_error
from gateway.schemas import ChatCompletionRequest

API_VERSION = "2023-06-01"
# Anthropic requires max_tokens; OpenAI-style clients usually leave it out. It is a ceiling,
# not a target, so a generous default only stops runaway replies.
DEFAULT_MAX_TOKENS = 16_000
# Anthropic has no "any JSON object" mode: its structured outputs need a full schema. So the
# catalog doesn't give these models the json capability and routing never relies on this
# instruction. It only applies when a client names an Anthropic model and asks for JSON.
JSON_INSTRUCTION = "Reply with a single valid JSON object and nothing else."
FINISH_REASONS = {"max_tokens": "length", "refusal": "content_filter"}


class AnthropicProvider:
    def __init__(self, client: httpx.AsyncClient, api_key: str) -> None:
        # Same split as the OpenAI adapter: the client carries base_url and timeout.
        self._client = client
        self._headers = {"x-api-key": api_key, "anthropic-version": API_VERSION}

    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        # Anthropic takes system instructions as one top-level field, not as messages.
        system = [m.content for m in request.messages if m.role == "system"]
        if request.wants_json:
            system.append(JSON_INSTRUCTION)
        payload: dict[str, Any] = {
            "model": model,
            "max_tokens": request.max_tokens or DEFAULT_MAX_TOKENS,
            "messages": [m.model_dump() for m in request.messages if m.role != "system"],
        }
        if system:
            payload["system"] = "\n\n".join(system)
        # temperature is not forwarded: current Anthropic models reject sampling parameters.

        try:
            response = await self._client.post("/v1/messages", json=payload, headers=self._headers)
            response.raise_for_status()
            data = response.json()
            return Completion(
                # Text blocks only: models that think return thinking blocks too.
                content="".join(b["text"] for b in data["content"] if b["type"] == "text"),
                prompt_tokens=data["usage"]["input_tokens"],
                completion_tokens=data["usage"]["output_tokens"],
                finish_reason=FINISH_REASONS.get(data["stop_reason"], "stop"),
            )
        except CALL_ERRORS as exc:
            raise provider_error("anthropic", exc) from exc
