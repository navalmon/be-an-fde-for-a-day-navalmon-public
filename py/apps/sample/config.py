"""Environment-backed application settings for the FDEBench API."""

from functools import lru_cache
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings
from pydantic_settings import SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings shared by all task endpoints."""

    model_config = SettingsConfigDict(env_prefix="FDE_", env_file=".env", extra="ignore")

    service_name: str = "FDEBench Starter"
    default_model_name: str = "gpt-4.1-mini"
    triage_model_name: str = ""
    extract_model_name: str = ""
    orchestrate_model_name: str = ""
    model_base_url: str = ""
    model_api_key: str = Field(default="", repr=False)
    model_api_style: Literal["auto", "chat_completions", "responses"] = "auto"
    model_max_tokens: int = Field(default=1024, gt=0)
    http_timeout_seconds: float = Field(default=45.0, gt=0)
    model_concurrency: int = Field(default=2, gt=0)
    max_retry_attempts: int = Field(default=3, ge=1)
    retry_base_delay_seconds: float = Field(default=1.0, ge=0)
    extract_image_detail: Literal["auto", "low", "high"] = "high"
    extract_image_format: Literal["png", "jpeg"] = "png"
    extract_jpeg_quality: int = Field(default=90, ge=1, le=95)
    extract_image_max_dimension: int = Field(default=3072, ge=512)
    extract_low_contrast_threshold: float = Field(default=32.0, ge=0)
    extract_cache_max_entries: int = Field(default=128, ge=0)

    def model_name_for_path(self, path: str) -> str:
        """Return the configured cost-scoring model name for a scored endpoint path."""
        if path == "/triage" and self.triage_model_name:
            return self.triage_model_name
        if path == "/extract" and self.extract_model_name:
            return self.extract_model_name
        if path == "/orchestrate" and self.orchestrate_model_name:
            return self.orchestrate_model_name
        return self.default_model_name


@lru_cache
def get_settings() -> Settings:
    """Return process-wide settings loaded from environment variables."""
    return Settings()
