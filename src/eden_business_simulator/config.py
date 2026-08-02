"""Simulator configuration."""

from __future__ import annotations

from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class SimulatorConfig(BaseModel):
    """Configuration for a single simulator run."""

    business_type: str
    duration_seconds: float = 60.0
    max_events: int = 0
    events_per_second: float = 2.0
    seed: int = 42
    output_mode: Literal["ndjson", "http", "none"] = "ndjson"
    webhook_url: Optional[str] = None
    initial_state_overrides: dict[str, Any] = Field(default_factory=dict)

    # Storage / daemon options
    storage_backend: Literal["sqlite", "ndjson", "memory"] = "sqlite"
    storage_uri: Optional[str] = None
    stream_id: Optional[str] = None
    checkpoint_interval_seconds: float = 30.0
    checkpoint_interval_events: int = 100

    @field_validator("duration_seconds")
    @classmethod
    def duration_must_be_non_negative(cls, value: float) -> float:
        if value < 0:
            raise ValueError("duration_seconds must be non-negative")
        return value

    @field_validator("events_per_second")
    @classmethod
    def rate_must_be_positive(cls, value: float) -> float:
        if value <= 0:
            raise ValueError("events_per_second must be positive")
        return value

    @field_validator("max_events")
    @classmethod
    def max_events_must_be_non_negative(cls, value: int) -> int:
        if value < 0:
            raise ValueError("max_events must be non-negative")
        return value

    @field_validator("checkpoint_interval_events")
    @classmethod
    def checkpoint_interval_events_must_be_positive(cls, value: int) -> int:
        if value <= 0:
            raise ValueError("checkpoint_interval_events must be positive")
        return value

    @model_validator(mode="after")
    def webhook_required_for_http(self) -> "SimulatorConfig":
        if self.output_mode == "http" and not self.webhook_url:
            raise ValueError("webhook_url is required when output_mode is 'http'")
        return self

    @model_validator(mode="after")
    def default_stream_id(self) -> "SimulatorConfig":
        if self.stream_id is None:
            from datetime import datetime, timezone

            now = datetime.now(timezone.utc).strftime("%Y%m%d%H%M%S")
            self.stream_id = f"{self.business_type}_seed{self.seed}_{now}"
        return self

    @model_validator(mode="after")
    def default_storage_uri(self) -> "SimulatorConfig":
        if self.storage_uri is None:
            if self.storage_backend == "sqlite":
                self.storage_uri = "eden_business_simulator.db"
            elif self.storage_backend == "ndjson":
                self.storage_uri = "eden_business_simulator.jsonl"
        return self
