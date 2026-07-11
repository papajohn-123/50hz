from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    service_role: str = "api"
    database_url: str | None = None
    elexon_base_url: str = "https://data.elexon.co.uk/bmrs/api/v1"
    carbon_intensity_base_url: str = "https://api.carbonintensity.org.uk"
    openrouter_api_key: str | None = None
    openrouter_model: str = "openai/gpt-5.4-mini"
    openrouter_base_url: str = "https://openrouter.ai/api/v1"
    openrouter_daily_call_limit: int = 100
    openrouter_timeout_seconds: float = 20.0
    public_base_url: str = "https://50hz-api-production.up.railway.app"
    worker_poll_seconds: int = 60

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )


@lru_cache
def get_settings() -> Settings:
    return Settings()
