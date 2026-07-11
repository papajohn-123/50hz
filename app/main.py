from datetime import UTC, datetime

from fastapi import FastAPI
from sqlalchemy import text

from app.api.routes import router as api_router
from app.config import get_settings
from app.db import database_session
from app.runtime import lifespan


app = FastAPI(
    title="50Hz API",
    description="Britain's electricity system, alive.",
    version="0.1.0",
    lifespan=lifespan,
)
app.include_router(api_router)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str | bool]:
    settings = get_settings()
    database_healthy = False
    if settings.database_url:
        try:
            async with database_session() as session:
                await session.execute(text("SELECT 1"))
            database_healthy = True
        except Exception:
            database_healthy = False
    return {
        "status": "ok",
        "service": "50hz-api",
        "role": settings.service_role,
        "database": database_healthy,
        "timestamp": datetime.now(UTC).isoformat(),
    }


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
