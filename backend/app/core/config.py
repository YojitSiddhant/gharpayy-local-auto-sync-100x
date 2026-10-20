from functools import lru_cache

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    app_env: str = "development"
    backend_cors_origins: list[str] = ["http://localhost:3000"]
    database_url: str = "mysql+pymysql://username:password@localhost:3306/gharpayy_local_sync"
    model_config = SettingsConfigDict(env_file=(".env", "backend/.env"), extra="ignore")

    @field_validator("backend_cors_origins", mode="before")
    @classmethod
    def parse_origins(cls, value: str | list[str]) -> list[str]:
        return [item.strip() for item in value.split(",") if item.strip()] if isinstance(value, str) else value


@lru_cache
def get_settings() -> Settings:
    return Settings()
