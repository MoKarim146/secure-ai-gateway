"""Runs one chat completion: pick candidates, call providers with fallback, shape the reply."""

import asyncio
import logging
import time
import uuid
from collections.abc import Mapping
from dataclasses import dataclass, replace

from gateway.config import ModelInfo
from gateway.errors import APIError
from gateway.privacy import PrivacyPolicy, Redactor
from gateway.providers import Completion, Provider, ProviderError
from gateway.router import select_candidates
from gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ChatMessage,
    Choice,
    TokenUsage,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CompletionResult:
    response: ChatCompletionResponse
    model: ModelInfo
    latency_ms: int
    # Distinct personal data values replaced in what the serving provider received.
    redactions: int = 0


async def create_completion(
    request: ChatCompletionRequest,
    catalog: list[ModelInfo],
    providers: Mapping[str, Provider],
    privacy: PrivacyPolicy,
) -> CompletionResult:
    candidates = select_candidates(request, catalog)
    if not candidates and request.model == "auto":
        raise APIError(
            400,
            "No configured model can handle this request (prompt size or response_format)",
            code="no_suitable_model",
        )
    if not candidates:
        raise APIError(404, f"Model '{request.model}' does not exist", code="model_not_found")

    # Redacted once per request and reused by every untrusted candidate, so a fallback
    # provider sees the same placeholders. Detection is CPU work (an NLP model, for
    # Presidio), so it runs in a thread instead of blocking other requests.
    redactor = Redactor(privacy.detector) if privacy.detector else None
    redacted = await asyncio.to_thread(redactor.redact_request, request) if redactor else request

    failed_providers: set[str] = set()
    for model in candidates:
        # A provider that just failed is likely down for all its models, so skip the rest.
        if model.provider in failed_providers:
            continue
        applied = redactor if model.provider not in privacy.trusted_providers else None
        started = time.perf_counter()
        try:
            completion = await providers[model.provider].complete(
                model.id, redacted if applied else request
            )
        except ProviderError as exc:
            logger.warning("%s failed for model %s, trying next candidate", exc, model.id)
            failed_providers.add(model.provider)
            continue
        latency_ms = round((time.perf_counter() - started) * 1000)
        if applied:
            completion = replace(completion, content=applied.restore(completion.content))
        return CompletionResult(
            _to_response(model.id, completion),
            model,
            latency_ms,
            redactions=applied.count if applied else 0,
        )

    raise APIError(
        502,
        "No provider could complete the request",
        code="upstream_unavailable",
        error_type="server_error",
    )


def _to_response(model_id: str, completion: Completion) -> ChatCompletionResponse:
    return ChatCompletionResponse(
        id=f"chatcmpl-{uuid.uuid4().hex}",
        created=int(time.time()),
        model=model_id,
        choices=[
            Choice(
                message=ChatMessage(role="assistant", content=completion.content),
                finish_reason=completion.finish_reason,
            )
        ],
        usage=TokenUsage(
            prompt_tokens=completion.prompt_tokens,
            completion_tokens=completion.completion_tokens,
            total_tokens=completion.prompt_tokens + completion.completion_tokens,
        ),
    )
