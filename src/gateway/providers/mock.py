"""Offline provider with deterministic replies, so tests and demos need no network or keys."""

import json

from gateway.providers import Completion, estimate_tokens
from gateway.schemas import ChatCompletionRequest


class MockProvider:
    async def complete(self, model: str, request: ChatCompletionRequest) -> Completion:
        last = request.messages[-1].content[:200]
        if request.wants_json:
            content = json.dumps({"model": model, "echo": last})
        else:
            content = f"[{model}] You said: {last}"
        prompt = "\n".join(m.content for m in request.messages)
        return Completion(
            content=content,
            prompt_tokens=estimate_tokens(prompt),
            completion_tokens=estimate_tokens(content),
        )
