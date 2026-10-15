"""Application settings, read from environment variables and the optional .env file."""

from functools import lru_cache

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Typed configuration for the whole application.

    Values come from environment variables first, then from ``.env``.
    Secrets use ``SecretStr`` so they are never printed in logs or reprs.
    """

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "Retraction Contamination Tracker"

    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: SecretStr = SecretStr("")
    neo4j_database: str = "neo4j"

    openalex_api_key: SecretStr = SecretStr("")

    llm_provider: str = ""
    llm_api_key: SecretStr = SecretStr("")
    llm_model: str = ""


@lru_cache
def get_settings() -> Settings:
    """Return the settings, built once and reused (cached)."""
    return Settings()
