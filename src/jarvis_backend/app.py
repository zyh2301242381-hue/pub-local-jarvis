from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI

from jarvis_backend.api import api_router, websocket_router
from jarvis_backend.native import InProcessNativeClient, NamedPipeNativeClient, NativeClient
from jarvis_backend.orchestrator import OrchestrationService
from jarvis_backend.settings import Settings, get_settings


def create_app(
    settings: Settings | None = None,
    native_client: NativeClient | None = None,
) -> FastAPI:
    config = settings or get_settings()
    client = native_client or (
        InProcessNativeClient()
        if config.native.mode == "fake"
        else NamedPipeNativeClient(
            config.native.pipe_name,
            timeout=config.native.request_timeout_seconds,
            capture_interval_ms=config.native.capture_interval_ms,
        )
    )
    orchestrator = OrchestrationService(config, client)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app.state.orchestrator = orchestrator
        await orchestrator.start()
        try:
            yield
        finally:
            await orchestrator.stop()

    application = FastAPI(
        title=config.name,
        version="0.1.2",
        description="Local-first control plane for AI Jarvis workers and clients.",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    application.state.orchestrator = orchestrator

    application.include_router(api_router)
    application.include_router(websocket_router)
    return application


app = create_app()


def run() -> None:
    settings = get_settings()
    uvicorn.run("jarvis_backend.app:app", host=settings.server.host, port=settings.server.port)
