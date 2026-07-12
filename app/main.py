from datetime import UTC, datetime, timedelta

from fastapi import FastAPI, HTTPException, status
from sqlalchemy import text
from starlette.middleware.gzip import GZipMiddleware

from app.api.routes import router as api_router
from app.api.models import MobileFreshness
from app.api.presenter import present_current
from app.config import get_settings
from app.db import database_session, get_session_factory
from app.exporting.api import router as export_router
from app.game.api import router as game_router
from app.http_cache import ConditionalJSONMiddleware
from app.intelligence.api import router as intelligence_router
from app.legal import router as legal_router
from app.observability import RequestObservabilityMiddleware
from app.rate_limit import RateLimitMiddleware
from app.runtime import lifespan
from app.persistence import GridReadRepository
from app.source_health.api import router as source_health_router


app = FastAPI(
    title="50Hz API",
    description="Britain's electricity system, alive.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)
app.include_router(intelligence_router)
app.include_router(export_router)
app.include_router(game_router)
app.include_router(legal_router)
app.include_router(source_health_router)
app.add_middleware(ConditionalJSONMiddleware)
app.add_middleware(GZipMiddleware, minimum_size=1_000)
app.add_middleware(RateLimitMiddleware)
# Registered last so request IDs and the single structured record also cover
# early 429 responses, conditional 304s, compression, and handled errors.
app.add_middleware(RequestObservabilityMiddleware, service_version=app.version)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str | bool]:
    settings = get_settings()
    database_healthy = await _database_is_healthy()
    return {
        "status": "ok" if database_healthy else "degraded",
        "service": "50hz-worker" if settings.service_role == "worker" else "50hz-api",
        "role": settings.service_role,
        "database": database_healthy,
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/ready", tags=["system"])
async def readiness() -> dict[str, str | bool]:
    settings = get_settings()
    if not await _database_is_healthy():
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Database is unavailable",
        )

    if settings.service_role == "worker":
        runtime = getattr(app.state, "worker_runtime", None)
        if runtime is None or runtime.task.done():
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="Ingestion worker task is not running",
            )
        # Give a first deployment time to populate an empty database. After the
        # grace period, required source cadence is checked through the exact
        # presentation freshness rules used by the app.
        if datetime.now(UTC) - runtime.started_at > timedelta(minutes=5):
            try:
                current = await GridReadRepository(get_session_factory()).get_current()
                snapshot = present_current(current)
            except Exception as error:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Required grid data is unavailable",
                ) from error
            if snapshot.freshness is MobileFreshness.STALE:
                raise HTTPException(
                    status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                    detail="Required grid data is stale",
                )

    return {
        "status": "ready",
        "role": settings.service_role,
        "database": True,
        "timestamp": datetime.now(UTC).isoformat(),
    }


async def _database_is_healthy() -> bool:
    if not get_settings().database_url:
        return False
    try:
        async with database_session() as session:
            await session.execute(text("SELECT 1"))
        return True
    except Exception:
        return False


@app.get("/v1/meta", tags=["system"])
async def metadata() -> dict[str, object]:
    settings = get_settings()
    return {
        "name": "50Hz",
        "environment": settings.app_env,
        "role": settings.service_role,
        "capabilities": {
            "databaseConfigured": bool(settings.database_url),
            "openRouterConfigured": bool(settings.openrouter_api_key),
        },
    }
