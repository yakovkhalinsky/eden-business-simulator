"""Read-only report models for the insights workflow."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any


def _iso(dt: datetime | None) -> str | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.isoformat()


@dataclass
class StreamOverview:
    """High-level facts about the analyzed stream."""

    stream_id: str
    business_type: str
    event_count: int
    first_timestamp: str | None = None
    last_timestamp: str | None = None
    duration_seconds: float | None = None


@dataclass
class AggregateMetrics:
    """Read-only aggregate statistics."""

    events_per_second: float | None = None
    event_type_counts: dict[str, int] = field(default_factory=dict)


@dataclass
class EntityCardinality:
    """Unique entity counts extracted from payload *_id keys."""

    overall_event_ids: int = 0
    per_event_type: dict[str, dict[str, int]] = field(default_factory=dict)


@dataclass
class WindowSummary:
    """Event counts inside a single time window."""

    start: str
    end: str
    total: int = 0
    by_event_type: dict[str, int] = field(default_factory=dict)


@dataclass
class AnomalyFlags:
    """Flags for data-quality and behavioural anomalies."""

    sequence_gap_count: int = 0
    duplicate_sequence_count: int = 0
    out_of_order_sequence_count: int = 0
    missing_field_counts: dict[str, int] = field(default_factory=dict)
    timestamp_inversion_count: int = 0
    unknown_event_types: list[str] = field(default_factory=list)
    rate_spike_windows: list[dict[str, Any]] = field(default_factory=list)
    empty_payload_count: int = 0


@dataclass
class ReplayFidelity:
    """Sequence and timestamp checks relevant to replay."""

    first_sequence: int | None = None
    last_sequence: int | None = None
    expected_count: int | None = None
    missing_sequences: list[int] = field(default_factory=list)
    duplicate_sequences: int = 0
    timestamp_monotonic: bool = True
    non_monotonic_timestamp_count: int = 0


@dataclass
class BusinessKPIs:
    """Business-domain specific key performance indicators."""

    business_type: str
    metrics: dict[str, Any] = field(default_factory=dict)


@dataclass
class InsightsReport:
    """Full output of the insights workflow."""

    generated_at: str
    stream_id: str
    overview: StreamOverview
    aggregates: AggregateMetrics
    entity_cardinality: EntityCardinality
    windows: list[WindowSummary]
    anomalies: AnomalyFlags
    replay_fidelity: ReplayFidelity
    business_kpis: BusinessKPIs
    storage_backend: str
    storage_uri: str

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["generated_at"] = self.generated_at
        return data
