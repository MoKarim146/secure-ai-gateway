"""Application factory: wires settings, providers, database and routes together.

Run with: uvicorn --factory gateway.main:create_app
"""

from collections.abc import AsyncIterator
from contextlib import AsyncExitStack, asynccontextmanager

import httpx
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.exceptions import HTTPException
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from gateway.api import Gateway, router
from gateway.config import Settings, load_catalog
from gateway.errors import install_error_handlers
from gateway.providers import Provider
from gateway.providers.mock import MockProvider
from gateway.providers.ollama import OllamaProvider


class BodySizeLimitMiddleware:
    """Rejects request bodies larger than max_bytes.

    Bytes are counted as they arrive instead of trusting Content-Length, because a chunked
    request doesn't send one.
    """

    def __init__(self, app: ASGIApp, max_bytes: int) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            received += len(message.get("body", b""))
            if received > self.max_bytes:
                raise HTTPException(413, f"Request body is larger than {self.max_bytes} bytes")
            return message

        await self.app(scope, limited_receive, send)


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with AsyncExitStack() as cleanup:
            providers: dict[str, Provider] = {}
            if settings.mock_provider_enabled:
                providers["mock"] = MockProvider()
            if settings.ollama_base_url:
                client = httpx.AsyncClient(
                    base_url=settings.ollama_base_url, timeout=settings.provider_timeout_seconds
                )
                providers["ollama"] = OllamaProvider(await cleanup.enter_async_context(client))

            engine = create_async_engine(settings.database_url, pool_pre_ping=True)
            cleanup.push_async_callback(engine.dispose)

            # Models of switched-off providers are dropped here, so routing and /v1/models
            # never see them.
            catalog = [m for m in load_catalog(settings.models_file) if m.provider in providers]
            app.state.gateway = Gateway(
                catalog=catalog,
                providers=providers,
                sessionmaker=async_sessionmaker(engine, expire_on_commit=False),
            )
            yield

    app = FastAPI(title="Secure AI Gateway", version="0.1.0", lifespan=lifespan)
    app.add_middleware(BodySizeLimitMiddleware, max_bytes=settings.max_request_bytes)
    install_error_handlers(app)
    app.include_router(router)
    return app
