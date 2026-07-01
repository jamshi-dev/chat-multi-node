"""FastAPI application: wiring, lifespan, middleware, error handling."""

import logging
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api import auth, conversations, ws
from app.config import get_settings
from app.db import dispose_engine, get_sessionmaker
from app.logging_config import configure_logging
from app.services.broadcaster import Broadcaster
from app.services.chat import ChatService

logger = logging.getLogger("app.main")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)

    broadcaster = Broadcaster(settings.redis_url)
    await broadcaster.start()
    app.state.broadcaster = broadcaster
    app.state.chat_service = ChatService(get_sessionmaker(), broadcaster)
    logger.info("startup complete", extra={"app": settings.app_name})

    try:
        yield
    finally:
        # Graceful shutdown: stop accepting work, drain sockets, then close resources.
        logger.info("shutting down: draining sockets")
        await broadcaster.close_all(code=1001)
        await broadcaster.stop()
        await dispose_engine()
        logger.info("shutdown complete")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title=settings.app_name, lifespan=lifespan)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.exception_handler(RequestValidationError)
    async def on_validation_error(
        _request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        return JSONResponse(
            status_code=422,  # Unprocessable Content
            content=jsonable_encoder(
                {"error": "validation_error", "detail": exc.errors()}
            ),
        )

    @app.get("/health", tags=["meta"])
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    app.include_router(auth.router)
    app.include_router(conversations.router)
    app.include_router(ws.router)
    return app


app = create_app()
