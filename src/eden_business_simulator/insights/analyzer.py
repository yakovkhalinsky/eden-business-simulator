"""Read-only stream analyzer for the insights workflow."""

from __future__ import annotations

import math
import statistics
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Iterator

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.insights.kpi import calculate_kpis
from eden_business_simulator.insights.models import (
    AggregateMetrics,
    AnomalyFlags,
    BusinessKPIs,
    EntityCardinality,
    InsightsReport,
    ReplayFidelity,
    StreamOverview,
    WindowSummary,
)
from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import StoredRecord


_MAX_MISSING_SEQUENCES = 100


def _ensure_utc(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def _format_duration(start: datetime, end: datetime) -> float:
    return round((end - start).total_seconds(), 3)


def _collect_id_values(value: Any, ids_by_key: dict[str, set[str]]) -> None:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key.endswith("_id") and isinstance(nested, str):
                ids_by_key[key].add(nested)
            _collect_id_values(nested, ids_by_key)
    elif isinstance(value, list):
        for item in value:
            _collect_id_values(item, ids_by_key)


def _window_index(dt: datetime, window_minutes: int) -> tuple[datetime, datetime]:
    """Return inclusive window start/end for a timestamp."""
    epoch_minutes = int(dt.timestamp() / 60)
    bucket = epoch_minutes // window_minutes
    start = datetime.fromtimestamp(bucket * window_minutes * 60, tz=timezone.utc)
    end = datetime.fromtimestamp((bucket + 1) * window_minutes * 60, tz=timezone.utc)
    return start, end


class StreamAnalyzer:
    """Analyzes a persisted event stream and builds an :class:`InsightsReport`."""

    def __init__(self, records: Iterator[StoredRecord], window_minutes: int = 1) -> None:
        self.window_minutes = max(1, window_minutes)
        self.records = list(records)
        self.events: list[EventEnvelope] = [
            record.envelope for record in self.records if record.envelope is not None
        ]

    def _sorted_events(self) -> list[EventEnvelope]:
        return sorted(
            self.events,
            key=lambda e: (e.sequence if e.sequence is not None else -1),
        )

    def analyze(
        self,
        storage_backend: str,
        storage_uri: str,
    ) -> InsightsReport:
        overview = self._overview(storage_backend, storage_uri)
        aggregates = self._aggregates(overview)
        entity_cardinality = self._entity_cardinality()
        windows = self._windows()
        anomalies = self._anomalies()
        replay_fidelity = self._replay_fidelity()
        business_kpis = self._business_kpis()

        return InsightsReport(
            generated_at=datetime.now(timezone.utc).isoformat(),
            stream_id=overview.stream_id,
            overview=overview,
            aggregates=aggregates,
            entity_cardinality=entity_cardinality,
            windows=windows,
            anomalies=anomalies,
            replay_fidelity=replay_fidelity,
            business_kpis=business_kpis,
            storage_backend=storage_backend,
            storage_uri=storage_uri,
        )

    def _overview(self, storage_backend: str, storage_uri: str) -> StreamOverview:
        stream_id = ""
        business_type = "unknown"
        first_ts: datetime | None = None
        last_ts: datetime | None = None

        for envelope in self.events:
            if envelope.stream_id:
                stream_id = envelope.stream_id
            if envelope.business_type:
                business_type = envelope.business_type
            ts = _ensure_utc(envelope.timestamp)
            if ts is not None:
                if first_ts is None or ts < first_ts:
                    first_ts = ts
                if last_ts is None or ts > last_ts:
                    last_ts = ts

        duration: float | None = None
        if first_ts is not None and last_ts is not None:
            duration = _format_duration(first_ts, last_ts)

        return StreamOverview(
            stream_id=stream_id,
            business_type=business_type,
            event_count=len(self.events),
            first_timestamp=first_ts.isoformat() if first_ts else None,
            last_timestamp=last_ts.isoformat() if last_ts else None,
            duration_seconds=duration,
        )

    def _aggregates(self, overview: StreamOverview) -> AggregateMetrics:
        counts: dict[str, int] = defaultdict(int)
        for envelope in self.events:
            counts[envelope.event_type] += 1

        eps: float | None = None
        if overview.duration_seconds and overview.duration_seconds > 0:
            eps = round(overview.event_count / overview.duration_seconds, 4)

        return AggregateMetrics(
            events_per_second=eps,
            event_type_counts=dict(counts),
        )

    def _entity_cardinality(self) -> EntityCardinality:
        ids_by_event_type: dict[str, dict[str, set[str]]] = defaultdict(
            lambda: defaultdict(set)
        )
        overall_event_ids: set[str] = set()

        for envelope in self.events:
            if isinstance(envelope.event_id, str):
                overall_event_ids.add(envelope.event_id)
            ids_by_event_type[envelope.event_type]  # ensure entry
            _collect_id_values(envelope.payload, ids_by_event_type[envelope.event_type])

        return EntityCardinality(
            overall_event_ids=len(overall_event_ids),
            per_event_type={
                event_type: {key: len(values) for key, values in ids.items()}
                for event_type, ids in ids_by_event_type.items()
            },
        )

    def _windows(self) -> list[WindowSummary]:
        buckets: dict[datetime, WindowSummary] = {}
        for envelope in self.events:
            ts = _ensure_utc(envelope.timestamp)
            if ts is None:
                continue
            start, end = _window_index(ts, self.window_minutes)
            summary = buckets.get(start)
            if summary is None:
                summary = WindowSummary(start=start.isoformat(), end=end.isoformat())
                buckets[start] = summary
            summary.total += 1
            summary.by_event_type[envelope.event_type] = (
                summary.by_event_type.get(envelope.event_type, 0) + 1
            )

        return [buckets[key] for key in sorted(buckets)]

    def _anomalies(self) -> AnomalyFlags:
        flags = AnomalyFlags()

        # Sequence-level checks (logical order).
        sorted_sequences = [e.sequence for e in self._sorted_events() if e.sequence is not None]
        if sorted_sequences:
            flags.duplicate_sequence_count = len(sorted_sequences) - len(set(sorted_sequences))
            for prev, cur in zip(sorted_sequences, sorted_sequences[1:]):
                if cur > prev + 1:
                    flags.sequence_gap_count += cur - prev - 1

        # Out-of-order sequences relative to storage order.
        previous_sequence: int | None = None
        for record in self.records:
            seq = record.envelope.sequence
            if seq is None:
                continue
            if previous_sequence is not None and seq < previous_sequence:
                flags.out_of_order_sequence_count += 1
            previous_sequence = seq

        missing_fields: dict[str, int] = defaultdict(int)
        previous_ts: datetime | None = None
        for envelope in self.events:
            if not isinstance(envelope.event_id, str) or not envelope.event_id:
                missing_fields["event_id"] += 1
            if envelope.timestamp is None:
                missing_fields["timestamp"] += 1
            if not isinstance(envelope.event_type, str) or not envelope.event_type:
                missing_fields["event_type"] += 1
            if not isinstance(envelope.payload, dict) or envelope.payload == {}:
                missing_fields["payload"] += 1
                flags.empty_payload_count += 1

            ts = _ensure_utc(envelope.timestamp)
            if ts is not None and previous_ts is not None:
                if ts < previous_ts:
                    flags.timestamp_inversion_count += 1
                previous_ts = max(previous_ts, ts)
            elif ts is not None:
                previous_ts = ts

        flags.missing_field_counts = dict(missing_fields)

        # Unknown event types relative to the registered simulator.
        business_type = self.events[0].business_type if self.events else None
        if business_type:
            try:
                simulator = load_simulator(business_type)
                allowed = set(simulator.available_event_types())
                seen = {e.event_type for e in self.events}
                flags.unknown_event_types = sorted(seen - allowed)
            except Exception:
                pass

        # Rate spikes: windows more than 3 std above mean.
        windows = self._windows()
        if len(windows) >= 2:
            counts = [w.total for w in windows]
            mean = statistics.mean(counts)
            std = statistics.pstdev(counts)
            if std > 0:
                threshold = mean + 3 * std
                for window in windows:
                    if window.total > threshold:
                        flags.rate_spike_windows.append(
                            {
                                "start": window.start,
                                "total": window.total,
                                "threshold": round(threshold, 2),
                            }
                        )

        return flags

    def _replay_fidelity(self) -> ReplayFidelity:
        fidelity = ReplayFidelity()
        sorted_events = self._sorted_events()
        sequences = [e.sequence for e in sorted_events if e.sequence is not None]
        if not sequences:
            return fidelity

        sorted_seqs = sorted(sequences)
        fidelity.first_sequence = sorted_seqs[0]
        fidelity.last_sequence = sorted_seqs[-1]
        unique = set(sorted_seqs)
        fidelity.expected_count = fidelity.last_sequence - fidelity.first_sequence + 1
        fidelity.duplicate_sequences = len(sorted_seqs) - len(unique)

        expected = set(
            range(fidelity.first_sequence, fidelity.last_sequence + 1)
        )
        missing = sorted(expected - unique)
        fidelity.sequence_gap_count = len(missing)
        fidelity.missing_sequences = missing[:_MAX_MISSING_SEQUENCES]

        previous_ts: datetime | None = None
        non_monotonic = 0
        for envelope in sorted_events:
            ts = _ensure_utc(envelope.timestamp)
            if ts is None:
                continue
            if previous_ts is not None and ts < previous_ts:
                non_monotonic += 1
                fidelity.timestamp_monotonic = False
            if previous_ts is None or ts > previous_ts:
                previous_ts = ts
        fidelity.non_monotonic_timestamp_count = non_monotonic

        return fidelity

    def _business_kpis(self) -> BusinessKPIs:
        business_type = self.events[0].business_type if self.events else "unknown"
        metrics = calculate_kpis(business_type, self.events)
        return BusinessKPIs(business_type=business_type, metrics=metrics)
