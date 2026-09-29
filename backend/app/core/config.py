"""Environment-driven application settings.

Repository YAML files under ``configs/`` describe the planned configuration
surface. Runtime secrets must be injected through environment variables.
"""

from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Minimal settings required to start the v0.5 API."""

    app_name: str = "KeHeng API"
    app_version: str = "0.6.0"
    environment: str = "development"
    allowed_origins: str = "http://localhost:5173,http://127.0.0.1:5173"
    max_upload_mb: int = 20
    agent_mode: Literal["rule", "llm", "fallback"] = "rule"
    retrieval_mode: Literal["hash", "bm25"] = "hash"
    llm_endpoint: str = ""
    llm_model: str = ""
    llm_api_key: str = ""
    llm_timeout_seconds: int = 120

    model_config = SettingsConfigDict(
        env_prefix="KEHENG_",
        env_file=Path(__file__).resolve().parents[2] / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def cors_origins(self) -> list[str]:
        """Return normalized origins from a comma-separated setting."""

        return [
            origin.strip()
            for origin in self.allowed_origins.split(",")
            if origin.strip()
        ]


@lru_cache
def get_settings() -> Settings:
    """Return one immutable-by-convention settings instance per process."""

    return Settings()
