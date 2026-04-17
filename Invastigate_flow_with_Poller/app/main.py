from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.core import get_settings, logger, init_db, close_db
from app.routes import (
    health_router,
    normalization_router,
    correlation_router,
    error_analysis_router,
    rca_router,
    recommendation_router,
    orchestrator_router,
    traces_router,
    analyze_router,
    monitor_router,
)
from app.routes.poller import router as poller_router, get_poller


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logger.info(
        "Starting %s v%s [env=%s, model=%s]",
        settings.app_name, settings.app_version,
        settings.app_env, settings.openai_model,
    )

    # Initialize database
    await init_db()

    # Start the AIOps poller (if enabled)
    poller = get_poller()
    await poller.start()

    yield

    # Stop poller and close DB
    await poller.stop()
    await close_db()
    logger.info("Shutting down")


def create_app() -> FastAPI:
    settings = get_settings()

    app = FastAPI(
        title=settings.app_name,
        version=settings.app_version,
        description="Multi-Agent Observability & Correlation System",
        lifespan=lifespan,
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Individual agent endpoints
    app.include_router(health_router)
    app.include_router(normalization_router)
    app.include_router(correlation_router)
    app.include_router(error_analysis_router)
    app.include_router(rca_router)
    app.include_router(recommendation_router)

    # Full pipeline orchestrator
    app.include_router(orchestrator_router)

    # Trace history
    app.include_router(traces_router)

    # Frontend entry point (DB-first, then pipeline)
    app.include_router(analyze_router)

    # Poller admin
    app.include_router(poller_router)

    # Real-time monitor (SSE + trigger endpoint)
    app.include_router(monitor_router)

    return app


app = create_app()
