"""HTTP routes. They only wire requests to completions, router and usage."""

from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from gateway.completions import create_completion
from gateway.config import ModelInfo
from gateway.errors import APIError
from gateway.providers import Provider
from gateway.schemas import (
    ChatCompletionRequest,
    ChatCompletionResponse,
    ModelCard,
    ModelList,
    UsageSummary,
)
from gateway.usage import record_usage, usage_summary


@dataclass(frozen=True)
class Gateway:
    """Everything the routes need, built once at startup by main.create_app."""

    catalog: list[ModelInfo]
    providers: dict[str, Provider]
    sessionmaker: async_sessionmaker[AsyncSession]


def get_gateway(request: Request) -> Gateway:
    gateway: Gateway = request.app.state.gateway
    return gateway


GatewayDep = Annotated[Gateway, Depends(get_gateway)]


async def get_session(gateway: GatewayDep) -> AsyncIterator[AsyncSession]:
    async with gateway.sessionmaker() as session:
        yield session


SessionDep = Annotated[AsyncSession, Depends(get_session)]

router = APIRouter()
v1 = APIRouter(prefix="/v1")


@router.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@v1.post("/chat/completions")
async def chat_completions(
    body: ChatCompletionRequest, gateway: GatewayDep, session: SessionDep
) -> ChatCompletionResponse:
    if body.stream:
        raise APIError(400, "Streaming is not supported yet", code="stream_not_supported")
    result = await create_completion(body, gateway.catalog, gateway.providers)
    usage = result.response.usage
    await record_usage(
        session, result.model, usage.prompt_tokens, usage.completion_tokens, result.latency_ms
    )
    return result.response


@v1.get("/models")
async def list_models(gateway: GatewayDep) -> ModelList:
    cards = [ModelCard(id="auto", owned_by="gateway")]
    cards += [ModelCard(id=m.id, owned_by=m.provider) for m in gateway.catalog]
    return ModelList(data=cards)


@v1.get("/usage")
async def get_usage(session: SessionDep) -> UsageSummary:
    return await usage_summary(session)


router.include_router(v1)
