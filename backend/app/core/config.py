"""Application settings, read from environment variables (and an optional .env file)."""

from __future__ import annotations

from datetime import time
from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

AlignmentMethod = Literal["hourly_mean", "start_period"]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # --- Elexon -------------------------------------------------------------
    elexon_base_url: str = "https://data.elexon.co.uk/bmrs/api/v1"
    elexon_timeout_seconds: float = Field(default=30.0, gt=0)
    elexon_max_retries: int = Field(default=3, ge=0)
    elexon_backoff_base_seconds: float = Field(default=0.5, ge=0)
    # Upper bound on how long a Retry-After header may make us wait.
    elexon_max_retry_after_seconds: float = Field(default=60.0, ge=0)
    # Long ranges are split into requests of at most this span.
    elexon_fuelhh_chunk_days: int = Field(default=14, ge=1)
    elexon_windfor_chunk_days: int = Field(default=7, ge=1)

    # --- Storage / runtime ----------------------------------------------------
    database_url: str = "sqlite:///./data/app.db"
    cors_origins: str = "http://localhost:5173"
    log_level: str = "INFO"

    # --- Analysis ---------------------------------------------------------------
    business_timezone: str = "Europe/London"
    default_analysis_window_days: int = Field(default=28, ge=1, le=366)
    # WINDFOR publications are fetched from this many days before the day
    # preceding the first delivery date, so the forecast that was current at
    # the decision cutoff is always captured.
    forecast_publication_lookback_days: int = Field(default=7, ge=1)
    # Day-ahead decision cutoff: forecasts for delivery day D must have been
    # published at or before this wall-clock time on D-1. Deliberately has no
    # default - it is a business decision, not a market fact.
    decision_cutoff_time: time | None = None
    decision_cutoff_timezone: str = "Europe/London"
    # How hourly WINDFOR values are compared with half-hourly FUELHH actuals.
    # See README "Temporal alignment".
    forecast_alignment_method: AlignmentMethod = "hourly_mean"
    # Minimum matched observations before empirical error ranges are shown.
    min_samples_for_error_range: int = Field(default=30, ge=1)
    # Number of forecast-level bands for level-conditioned error ranges.
    error_range_level_bands: int = Field(default=5, ge=2, le=20)
    # Minimum complete delivery days before a day-total energy range is shown.
    # Lower than the hourly minimum because a month only has ~30 days.
    min_days_for_daily_energy_range: int = Field(default=20, ge=1)
    default_histogram_bins: int = Field(default=20, ge=2, le=200)

    @field_validator("decision_cutoff_time", mode="before")
    @classmethod
    def _blank_is_none(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        value = value.strip()
        if not value:
            return None
        # Accept a single-digit hour ("9:00"), which pydantic's time parser rejects.
        return f"0{value}" if len(value.split(":")[0]) == 1 else value

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
