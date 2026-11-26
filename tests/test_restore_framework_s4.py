"""Restorable state S4: KPI period + scenario engine resume (R3/R4).

S1–S3 restored the business, RNG/Faker/IdGenerator and the simulated
clock. Two framework pieces still restarted on resume, and this slice
carries them in the snapshot envelope as siblings of the business
``data``:

* the Phase 2 ``PeriodTracker`` (R3) — its open period now continues
  across a restart, so a checkpoint taken mid-day yields ONE summary
  for that day, identical to an uninterrupted run's. (The continuous
  runner therefore no longer flushes a final partial summary at
  process end; the batch Runner still does.)
* the Phase 6 ``ScenarioEngine`` (R4) — pending/active state now
  survives, so an in-flight scenario neither re-fires its
  ``scenario_started`` nor loses its ending, and a pending one still
  fires at its original offset.

The proofs are the S2/S3 split-run pattern with period summaries
ENABLED and scenarios scheduled: whole and split streams must be
exactly equal on (event_type, payload, envelope timestamp) —
``event_id`` is a fresh uuid per envelope and is not compared. Cafe
and gym stamp no wall-clock payload fields, so no normalisation is
needed here.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pytest

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.definition import load_definition
from eden_business_simulator.framework.kpi import PeriodTracker
from eden_business_simulator.framework.scenario import (
    ScenarioEngine,
    ScenarioScheduleEntry,
)
from eden_business_simulator.framework.verify import verify_run
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

PINNED_START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
TICK_SECONDS = 3600.0  # one simulated hour per event


def _config(
    business: str,
    stream_id: str,
    max_events: int,
    scenario_schedule: list[ScenarioScheduleEntry] | None = None,
    **overrides: Any,
) -> SimulatorConfig:
    kwargs: dict[str, Any] = dict(
        business_type=business,
        duration_seconds=0,  # max_events bounds the run
        events_per_second=1000.0,
        seed=42,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="sqlite",
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
        scenario_schedule=scenario_schedule or [],
    )
    kwargs.update(overrides)
    return SimulatorConfig(**kwargs)


def _run_segment(
    db_path: str,
    business: str,
    stream_id: str,
    max_events: int,
    scenario_schedule: list[ScenarioScheduleEntry] | None = None,
    **overrides: Any,
):
    """Run one runner segment over the db; return the simulator.

    Same contract as the S2/S3 helper: a fresh storage adapter and a
    fresh simulator (configure + initialize) per segment — the real
    daemon resume path. The clock is pinned identically for both
    segments; on a resumed segment the checkpoint's clock replaces
    the pin inside ``run()``.
    """
    storage = SqliteStorageAdapter(db_path, stream_id)
    config = _config(
        business, stream_id, max_events, scenario_schedule, **overrides
    )
    sim = load_simulator(business)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.clock = Clock(start_time=PINNED_START, tick_seconds=TICK_SECONDS)
    runner.run()
    return sim


def _stored_events(db_path: str, stream_id: str) -> list[tuple[Any, ...]]:
    storage = SqliteStorageAdapter(db_path, stream_id)
    try:
        return [
            (
                record.envelope.event_type,
                record.envelope.payload,
                record.envelope.timestamp,
            )
            for record in storage.read_from()
        ]
    finally:
        storage.close()


def _split_run(
    tmp_path: Path,
    business: str,
    first: int,
    second: int,
    scenario_schedule: list[ScenarioScheduleEntry] | None = None,
    **overrides: Any,
):
    whole_db = str(tmp_path / "whole.db")
    split_db = str(tmp_path / "split.db")

    whole_sim = _run_segment(
        whole_db, business, "s", first + second, scenario_schedule,
        **overrides,
    )
    whole_events = _stored_events(whole_db, "s")

    _run_segment(
        split_db, business, "s", first, scenario_schedule, **overrides
    )
    split_sim = _run_segment(
        split_db, business, "s", second, scenario_schedule, **overrides
    )
    split_events = _stored_events(split_db, "s")
    return whole_sim, split_sim, whole_events, split_events, split_db


def _summaries(events: list[tuple[Any, ...]]) -> list[dict[str, Any]]:
    return [p for t, p, _ in events if t == "period_summary"]


# ----------------------------------------------------------------------
# (a) KPI period resume: checkpoint mid-day-2, one summary for the day
# ----------------------------------------------------------------------


def test_split_run_period_summaries_match_uninterrupted(tmp_path):
    # Hourly ticks from Jan 1 08:00: day 1 = events 0..15, day 2 =
    # 16..39, day 3 = 40..59. Splitting after 36 events checkpoints
    # mid-day-2 with that period half-accumulated.
    whole_sim, split_sim, whole_events, split_events, split_db = (
        _split_run(tmp_path, "cafe", first=36, second=24)
    )
    assert split_events == whole_events
    assert split_sim.position_snapshot() == whole_sim.position_snapshot()

    whole_summaries = _summaries(whole_events)
    split_summaries = _summaries(split_events)
    assert split_summaries == whole_summaries
    # Days 1 and 2 closed (day 3 is still open at run end — the
    # continuous runner no longer flushes a final partial, per R3).
    assert [s["period_start"] for s in split_summaries] == [
        "2026-01-01",
        "2026-01-02",
    ]
    day2 = split_summaries[1]
    assert day2["partial"] is False
    # The day-2 summary spans the restart boundary: its sequence
    # range starts in segment 1 and ends in segment 2 (business
    # events before the split point have sequences < 36 plus the
    # day-1 summary's sequence).
    assert day2["sequence_start"] < 36 < day2["sequence_end"]

    # The verifier's per-summary recomputation passes over the
    # resumed run's storage, period check included.
    report = verify_run("sqlite", split_db, "s")
    assert report.ok, [
        (c.name, c.status, c.detail) for c in report.checks
    ]
    assert report.check("period_summaries").status == "PASS"


# ----------------------------------------------------------------------
# (b) Scenario active at checkpoint: started once, ends on time
# ----------------------------------------------------------------------


def test_split_run_active_scenario_resumes_without_duplicate_start(
    tmp_path,
):
    schedule = [
        ScenarioScheduleEntry(
            name="demand_spike",
            start_offset_minutes=0,
            duration_minutes=3000,  # 50 sim-hours: ends mid-segment-2
        )
    ]
    whole_sim, split_sim, whole_events, split_events, _ = _split_run(
        tmp_path, "gym", first=20, second=40, scenario_schedule=schedule
    )
    assert split_events == whole_events
    assert split_sim.position_snapshot() == whole_sim.position_snapshot()

    for events in (whole_events, split_events):
        started = [p for t, p, _ in events if t == "scenario_started"]
        ended = [p for t, p, _ in events if t == "scenario_ended"]
        assert len(started) == 1
        assert len(ended) == 1
        assert started[0]["scenario"] == "demand_spike"
        # Ends at the original ends_at (start + 50h), in both runs.
        assert started[0]["ends_at"] == "2026-01-03T10:00:00+00:00"


# ----------------------------------------------------------------------
# (c) Scenario pending at checkpoint: fires at its original offset
# ----------------------------------------------------------------------


def test_split_run_pending_scenario_fires_at_original_offset(tmp_path):
    schedule = [
        ScenarioScheduleEntry(
            name="demand_spike",
            start_offset_minutes=1500,  # 25 sim-hours in: segment 2
            duration_minutes=600,
        )
    ]
    whole_sim, split_sim, whole_events, split_events, _ = _split_run(
        tmp_path, "gym", first=10, second=40, scenario_schedule=schedule
    )
    assert split_events == whole_events
    assert split_sim.position_snapshot() == whole_sim.position_snapshot()

    for events in (whole_events, split_events):
        started = [
            (i, p)
            for i, (t, p, _) in enumerate(events)
            if t == "scenario_started"
        ]
        assert len(started) == 1
        # Fires at the tick where elapsed time reaches 25h: after 25
        # business events plus the day-1 period summary (index 16),
        # the marker lands at stored index 26.
        assert started[0][0] == 26
        assert started[0][1]["started_at"] == "2026-01-02T09:00:00+00:00"


# ----------------------------------------------------------------------
# (d) Summaries off / no scenarios: envelope sections absent, S3 behaviour
# ----------------------------------------------------------------------


def test_split_run_without_summaries_or_scenarios_is_unchanged(tmp_path):
    whole_sim, split_sim, whole_events, split_events, split_db = (
        _split_run(
            tmp_path,
            "cafe",
            first=30,
            second=50,
            period_summaries_enabled=False,
        )
    )
    assert split_events == whole_events
    assert split_sim.position_snapshot() == whole_sim.position_snapshot()
    assert not _summaries(split_events)

    storage = SqliteStorageAdapter(split_db, "s")
    try:
        snapshot = storage.read_snapshot(ContinuousRunner.SNAPSHOT_KEY)
    finally:
        storage.close()
    assert snapshot is not None
    # No tracker (summaries off) and no scenario engine (no schedule):
    # neither S4 section is written.
    assert "tracker" not in snapshot
    assert "scenario" not in snapshot


def test_snapshot_sections_present_when_enabled(tmp_path):
    db_path = str(tmp_path / "sections.db")
    _run_segment(
        db_path,
        "gym",
        "s",
        10,
        scenario_schedule=[
            ScenarioScheduleEntry(name="demand_spike",
                                  start_offset_minutes=0)
        ],
    )
    storage = SqliteStorageAdapter(db_path, "s")
    try:
        snapshot = storage.read_snapshot(ContinuousRunner.SNAPSHOT_KEY)
    finally:
        storage.close()
    assert snapshot is not None
    assert snapshot["tracker"]["period_date"] == "2026-01-01"
    assert snapshot["tracker"]["event_total"] > 0
    assert [a["name"] for a in snapshot["scenario"]["active"]] == [
        "demand_spike"
    ]
    assert snapshot["scenario"]["pending"] == []
    # Both sections are JSON-safe as stored (no codec-only types).
    json.dumps(snapshot["tracker"])
    json.dumps(snapshot["scenario"])


# ----------------------------------------------------------------------
# Unit round-trips: tracker and engine state through JSON
# ----------------------------------------------------------------------


def _json_round_trip(value: Any) -> Any:
    return json.loads(json.dumps(value))


def test_period_tracker_state_round_trip_continues_the_period():
    definition = load_definition("cafe")
    tracker = PeriodTracker(definition)
    twin = PeriodTracker(definition)
    day1 = datetime(2026, 1, 1, 10, 0, tzinfo=timezone.utc)
    events = [
        ("order_paid", {"total": 12.50, "payment_method": "cash"}),
        ("order_taken", {"total": 12.50}),
        ("order_paid", {"total": 7.50, "payment_method": "card"}),
        ("waste_recorded", {"cost": 2.0}),
    ]
    for seq, (event_type, payload) in enumerate(events):
        for t in (tracker, twin):
            t.observe(event_type, payload, sequence=seq, timestamp=day1)
    restored = PeriodTracker(definition)
    restored.restore_state(_json_round_trip(twin.serialize_state()))
    # Both trackers now close the period identically at the crossing.
    day2 = datetime(2026, 1, 2, 0, 30, tzinfo=timezone.utc)
    assert restored.close_if_crossed(day2) == tracker.close_if_crossed(day2)


def test_scenario_engine_state_round_trip():
    schedule = [
        ScenarioScheduleEntry(
            name="demand_spike", start_offset_minutes=0,
            duration_minutes=600,
        ),
        ScenarioScheduleEntry(
            name="churn_wave", start_offset_minutes=5000,
        ),
    ]
    engine = ScenarioEngine(schedule, seed=42)
    twin = ScenarioEngine(schedule, seed=42)
    start = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
    assert [e["event_type"] for e in engine.transitions(start, 0.0)] == [
        "scenario_started"
    ]
    assert [e["event_type"] for e in twin.transitions(start, 0.0)] == [
        "scenario_started"
    ]
    restored = ScenarioEngine(schedule, seed=42)
    restored.restore_state(_json_round_trip(twin.serialize_state()))

    # Multipliers continue seamlessly and the D10 RNG state matches.
    assert restored.weight_multiplier(
        "check_in_recorded"
    ) == engine.weight_multiplier("check_in_recorded") == 2.5
    restored_rng = restored.scenario_rng("demand_spike")
    engine_rng = engine.scenario_rng("demand_spike")
    assert restored_rng is not None and engine_rng is not None
    assert [restored_rng.random() for _ in range(3)] == [
        engine_rng.random() for _ in range(3)
    ]
    # The pending pack is still pending with its original offset.
    assert [p["name"] for p in restored.serialize_state()["pending"]] == [
        "churn_wave"
    ]
    # At ends_at the restored engine ends the active scenario exactly
    # once and does not re-fire its start.
    ends = start + timedelta(minutes=600)
    events = restored.transitions(ends, 600.0)
    assert [e["event_type"] for e in events] == ["scenario_ended"]
    assert restored.transitions(ends, 600.0) == []
