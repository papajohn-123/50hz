from datetime import UTC, datetime

from fastapi import FastAPI

from app.config import get_settings


app = FastAPI(
    title="50Hz API",
    description="Britain's electricity system, alive.",
    version="0.1.0",
)


@app.get("/health", tags=["system"])
async def health() -> dict[str, str]:
    return {
        "status": "ok",
        "service": "50hz-api",
        "timestamp": datetime.now(UTC).isoformat(),
    }


@app.get("/v1/meta", tags=["system"])
async def metadata() -> dict[str, object]:
    settings = get_settings()
    return {
        "name": "50Hz",
        "environment": settings.app_env,
        "capabilities": {
            "databaseConfigured": bool(settings.database_url),
            "openRouterConfigured": bool(settings.openrouter_api_key),
        },
    }

