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

    # Sessions des interfaces web
    session_ttl_hours: int = 24 * 7
    # Protection contre les essais de mots de passe
    login_max_failures: int = 5
    login_lockout_minutes: int = 15

    # Canal temps réel avec les agents (voir docs/agent-protocol.md)
    heartbeat_interval_seconds: int = 10
    heartbeat_timeout_seconds: int = 30
    presence_sweep_seconds: int = 5

    def _database_url(self, driver: str) -> str:
        password = quote(self.db_password, safe="")
        return (
            f"postgresql+{driver}://{self.db_user}:{password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def database_url(self) -> str:
        """Connexion asynchrone, utilisée par le serveur."""
        return self._database_url("asyncpg")

    @property
    def database_url_sync(self) -> str:
        """Connexion synchrone, utilisée par les migrations."""
        return self._database_url("psycopg")

    @property
    def redis_url(self) -> str:
        auth = f":{quote(self.redis_password, safe='')}@" if self.redis_password else ""
        return f"redis://{auth}{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
