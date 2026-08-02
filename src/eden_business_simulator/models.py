"""Shared data models for the Eden Business Simulator."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Optional
from uuid import uuid4

from pydantic import BaseModel, Field


class Actor(BaseModel):
    """A participant in a simulated business."""

    actor_id: str
    actor_type: str
    created_at: datetime
    attributes: dict[str, Any] = Field(default_factory=dict)


class Clock(BaseModel):
    """Simulated clock used to timestamp events.

    The runner advances `now` by `tick_seconds` each cycle. Wall-clock pacing is
    handled separately by the runner so that tests can run deterministically in
    zero wall time.
    """

    start_time: datetime
    now: datetime
    tick_seconds: float = 1.0

    def __init__(
        self,
        start_time: Optional[datetime] = None,
        tick_seconds: float = 1.0,
    ) -> None:
        if start_time is None:
            start_time = datetime.now(timezone.utc)
        super().__init__(
            start_time=start_time,
            now=start_time,
            tick_seconds=tick_seconds,
        )

    def advance(self, seconds: Optional[float] = None) -> None:
        """Advance the simulated clock."""
        if seconds is None:
            seconds = self.tick_seconds
        self.now += timedelta(seconds=seconds)

    def elapsed_seconds(self) -> float:
        return (self.now - self.start_time).total_seconds()


class EventEnvelope(BaseModel):
    """Canonical envelope for every emitted event."""

    event_id: str = Field(default_factory=lambda: str(uuid4()))
    timestamp: datetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    business_type: str
    event_type: str
    payload: dict[str, Any] = Field(default_factory=dict)
    version: str = "1.0"

    @classmethod
    def from_event(
        cls,
        *,
        business_type: str,
        event_type: str,
        payload: dict[str, Any],
        timestamp: datetime,
    ) -> "EventEnvelope":
        return cls(
            timestamp=timestamp,
            business_type=business_type,
            event_type=event_type,
            payload=payload,
        )

    def to_json_line(self) -> str:
        return self.model_dump_json(exclude_none=True)
