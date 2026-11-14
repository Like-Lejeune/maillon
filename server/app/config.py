"""Configuration du serveur, lue depuis les variables d'environnement (.env)."""

from functools import lru_cache
from urllib.parse import quote

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    app_env: str = "development"

    db_host: str = "localhost"
    db_port: int = 5432
    db_name: str = "maillon"
    db_user: str = "maillon"
    db_password: str = ""

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_password: str = ""

    models_dir: str = "/models"

    @property
    def database_url(self) -> str:
        password = quote(self.db_password, safe="")
        return (
            f"postgresql+asyncpg://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def redis_url(self) -> str:
        auth = f":{quote(self.redis_password, safe='')}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
