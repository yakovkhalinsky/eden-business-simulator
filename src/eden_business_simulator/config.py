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
    output_mode: Literal["ndjson", "http"] = "ndjson"
    webhook_url: Optional[str] = None
    initial_state_overrides: dict[str, Any] = Field(default_factory=dict)

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

    @model_validator(mode="after")
    def webhook_required_for_http(self) -> "SimulatorConfig":
        if self.output_mode == "http" and not self.webhook_url:
            raise ValueError("webhook_url is required when output_mode is 'http'")
        return self
