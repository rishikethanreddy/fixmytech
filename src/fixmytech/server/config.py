"""Backend configuration from environment variables (and an optional .env file).

Secrets (FLIPVALUE_API_KEY) are SecretStr so they are never printed in logs or reprs.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    env: Literal["development", "production", "test"] = Field(default="development", alias="FIXMYTECH_ENV")
    database_url: str = Field(default="sqlite:///./var/fixmytech.db", alias="FIXMYTECH_DATABASE_URL")
    retention_days: int = Field(default=30, ge=1, le=365, alias="FIXMYTECH_RETENTION_DAYS")
    unsaved_retention_hours: int = Field(default=24, ge=1, le=168, alias="FIXMYTECH_UNSAVED_RETENTION_HOURS")
    rate_limit_per_minute: int = Field(default=240, ge=10, alias="FIXMYTECH_RATE_LIMIT_PER_MINUTE")
    valuation_limit_per_hour: int = Field(default=20, ge=1, alias="FIXMYTECH_VALUATION_LIMIT_PER_HOUR")
    max_body_bytes: int = Field(default=256 * 1024, ge=1024, alias="FIXMYTECH_MAX_BODY_BYTES")
    cors_origins: str = Field(default="", alias="FIXMYTECH_CORS_ORIGINS")
    default_region: str = Field(default="IN", pattern=r"^[A-Z]{2}$", alias="FIXMYTECH_DEFAULT_REGION")
    markets: str = Field(default="IN:INR,US:USD", alias="FIXMYTECH_MARKETS",
                         description="Supported markets as REGION:CURRENCY pairs")
    agent_url: str = Field(default="http://127.0.0.1:8765", alias="FIXMYTECH_AGENT_URL")
    pricing_dir: str = Field(default="var/pricing", alias="FIXMYTECH_PRICING_DIR")
    pricing_allowed_sources: str | None = Field(
        default=None, alias="FIXMYTECH_PRICING_ALLOWED_SOURCES",
        description="Comma-separated source ids approved for display. Unset = all (development only).")
    pricing_demo: bool = Field(default=False, alias="FIXMYTECH_PRICING_DEMO")
    pricing_benchmark_fallback: bool | None = Field(
        default=None, alias="FIXMYTECH_PRICING_BENCHMARK_FALLBACK",
        description="Market benchmark reference when no scraped price exists. Defaults to True in dev/prod.",
    )
    pricing_fresh_days: int = Field(default=30, ge=1, alias="FIXMYTECH_PRICING_FRESH_DAYS")
    pricing_stale_days: int = Field(default=90, ge=1, alias="FIXMYTECH_PRICING_STALE_DAYS")
    trust_proxy_headers: bool = Field(default=False, alias="FIXMYTECH_TRUST_PROXY_HEADERS")
    log_format: Literal["text", "json"] = Field(default="text", alias="FIXMYTECH_LOG_FORMAT")
    frontend_dir: str | None = Field(default=None, alias="FIXMYTECH_FRONTEND_DIR")

    ml_enabled: bool = Field(default=False, alias="FIXMYTECH_ML_ENABLED")
    ml_model_dir: str | None = Field(default=None, alias="FIXMYTECH_ML_MODEL_DIR")

    flipvalue_mode: Literal["disabled", "mock", "live"] = Field(default="disabled", alias="FLIPVALUE_MODE")
    flipvalue_base_url: str | None = Field(default=None, alias="FLIPVALUE_BASE_URL")
    flipvalue_api_key: SecretStr | None = Field(default=None, alias="FLIPVALUE_API_KEY")
    flipvalue_timeout_s: float = Field(default=8.0, gt=0, le=60, alias="FLIPVALUE_TIMEOUT_S")
    flipvalue_max_retries: int = Field(default=2, ge=0, le=5, alias="FLIPVALUE_MAX_RETRIES")
    flipvalue_api_version: str = Field(default="1", alias="FLIPVALUE_API_VERSION")
    # Partner key saved from the Settings page, and the gate for those settings.
    partner_config_path: str = Field(default="var/partner_service.json", alias="FIXMYTECH_PARTNER_CONFIG_PATH")
    admin_token: SecretStr | None = Field(default=None, alias="FIXMYTECH_ADMIN_TOKEN")
    # Development only: lets the MOCK FlipValue answer valuations. Never allowed in production.
    allow_mock_valuation: bool = Field(default=False, alias="FIXMYTECH_ALLOW_MOCK_VALUATION")

    @model_validator(mode="after")
    def _production_rules(self) -> Settings:
        if self.env == "production":
            if self.flipvalue_mode == "mock":
                raise ValueError("FLIPVALUE_MODE=mock is not allowed in production")
            if self.flipvalue_mode == "live" and not (self.flipvalue_base_url or "").startswith("https://"):
                raise ValueError("FLIPVALUE_BASE_URL must use https:// in production")
            if self.allow_mock_valuation:
                raise ValueError("FIXMYTECH_ALLOW_MOCK_VALUATION=true is not allowed in production")
            if self.pricing_demo:
                raise ValueError("FIXMYTECH_PRICING_DEMO=true is not allowed in production")
        if self.default_region not in self.market_currencies:
            raise ValueError("FIXMYTECH_DEFAULT_REGION must be one of FIXMYTECH_MARKETS")
        return self

    @property
    def market_currencies(self) -> dict[str, str]:
        out = {}
        for pair in self.markets.split(","):
            region, _, cur = pair.strip().partition(":")
            if len(region) == 2 and len(cur) == 3:
                out[region.upper()] = cur.upper()
        return out

    @property
    def benchmark_fallback_enabled(self) -> bool:
        if self.pricing_benchmark_fallback is not None:
            return self.pricing_benchmark_fallback
        return self.env != "test"

    @property
    def allowed_pricing_sources(self) -> set[str] | None:
        if not self.pricing_allowed_sources:
            return None
        if self.pricing_allowed_sources.strip().lower() in {"all", "*"}:
            return None
        sources = {s.strip() for s in self.pricing_allowed_sources.split(",") if s.strip()}
        return sources or None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
