from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from app.api.router import api_router
from app.core.config import get_settings
from app.core.errors import ApiError
from app.core.exception_handlers import api_error_handler, validation_error_handler
from app.db.session import engine
from app.observability.http import PrometheusMetricsMiddleware, RequestObservabilityMiddleware
from app.observability.logging import configure_logging


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    yield
    await engine.dispose()


def create_app() -> FastAPI:
    settings = get_settings()
    if settings.component_role not in {"api", "test"}:
        raise RuntimeError("FastAPI must run with ALERT_SAGE_COMPONENT_ROLE=api")
    configure_logging(
        service="alert-sage-api",
        environment=settings.environment,
        level=settings.log_level,
    )
    application = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        debug=settings.debug,
        lifespan=lifespan,
    )
    application.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )
    if settings.metrics_enabled:
        application.add_middleware(PrometheusMetricsMiddleware)
    application.add_middleware(RequestObservabilityMiddleware)
    application.add_exception_handler(ApiError, api_error_handler)
    application.add_exception_handler(RequestValidationError, validation_error_handler)
    application.include_router(api_router, prefix=settings.api_v1_prefix)

    if settings.metrics_enabled:

        @application.get("/metrics", include_in_schema=False)
        async def prometheus_metrics() -> Response:
            return Response(
                content=generate_latest(),
                headers={"Content-Type": CONTENT_TYPE_LATEST},
            )

    @application.get("/", tags=["meta"])
    async def service_metadata() -> dict[str, str]:
        return {
            "name": settings.app_name,
            "version": settings.app_version,
            "docs": "/docs",
        }

    return application


app = create_app()
