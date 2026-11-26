"""Restorable state S5: verifier resume-integrity check + gym
position-field reclassification.

The verifier's ``resume_integrity`` check (framework/verify.py)
restores a stored run's *final* checkpoint into a fresh simulator and
proves it reproduces: the full state re-serialises identically, the
restored position equals the position stored at that checkpoint, the
tracker/scenario sections round-trip, and a short continuation
generates events without error. Storage retains only the latest
snapshot, so earlier checkpoints are unprovable after the fact — the
check names that limit in its PASS detail rather than claiming more.

The gym reclassification: gym seeds its member pool at initialize()
with no stream events, so members_by_status / active_count /
frozen_count / cancelled_count are not reliably stream-derivable and
are now reported UNVERIFIABLE with the seeded-pool reason (the same
treatment class_count/booking_count already had).
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.definition import load_definition
from eden_business_simulator.framework.scenario import ScenarioScheduleEntry
from eden_business_simulator.framework.verify import (
    _recompute_gym,
    unverifiable_reason,
    verify_events,
    verify_run,
)
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

PINNED_START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
SEEDED_POOL_REASON = "member pool seeded at initialize before the stream starts"
GYM_SEEDED_FIELDS = [
    "members_by_status",
    "active_count",
    "frozen_count",
    "cancelled_count",
]


def _run_segment(db_path, business, stream_id, max_events, schedule=None):
    """One daemon segment: fresh adapter + fresh simulator, so a
    second segment exercises the real checkpoint resume path."""
    storage = SqliteStorageAdapter(db_path, stream_id)
    config = SimulatorConfig(
        business_type=business,
        duration_seconds=0,
        events_per_second=1000.0,
        seed=42,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="sqlite",
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
        scenario_schedule=schedule or [],
    )
    sim = load_simulator(business)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.clock = Clock(start_time=PINNED_START, tick_seconds=3600.0)
    runner.run()
    return sim


def _split_run(tmp_path, business, schedule=None):
    """Two segments over one store: segment 2 resumes segment 1."""
    db_path = str(tmp_path / f"{business}.db")
    stream_id = f"s5_{business}"
    _run_segment(db_path, business, stream_id, 25, schedule)
    _run_segment(db_path, business, stream_id, 25, schedule)
    return db_path, stream_id


# ----------------------------------------------------------------------
# resume_integrity on genuinely resumed runs
# ----------------------------------------------------------------------


@pytest.mark.parametrize("business", ["cafe", "gym"])
def test_resume_integrity_passes_on_split_run(tmp_path, business):
    db_path, stream_id = _split_run(tmp_path, business)
    report = verify_run("sqlite", db_path, stream_id)
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]
    result = report.check("resume_integrity")
    assert result.status == "PASS", result.detail
    # The PASS detail states the limit of what was proven.
    assert "earlier checkpoints are overwritten" in result.detail


def test_resume_integrity_passes_with_scenario_markers(tmp_path):
    # A scenario run is exactly the shape the determinism check must
    # SKIP; resume integrity is the check that covers it instead.
    schedule = [ScenarioScheduleEntry(name="demand_spike", start_offset_minutes=0)]
    db_path, stream_id = _split_run(tmp_path, "cafe", schedule)
    report = verify_run(
        "sqlite", db_path, stream_id, check_determinism_flag=True
    )
    assert report.check("determinism").status == "SKIP"
    result = report.check("resume_integrity")
    assert result.status == "PASS", result.detail
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]


def test_resume_integrity_fails_on_tampered_checkpoint(tmp_path):
    db_path, stream_id = _split_run(tmp_path, "cafe")
    # Corrupt the stored checkpoint: a full_state format_version this
    # build cannot interpret (the runner itself would refuse it).
    adapter = SqliteStorageAdapter(db_path, stream_id)
    try:
        snapshot = adapter.read_snapshot("latest")
        snapshot["data"]["full_state"]["format_version"] = 999
        adapter.write_snapshot("latest", snapshot)
    finally:
        adapter.close()
    report = verify_run("sqlite", db_path, stream_id)
    result = report.check("resume_integrity")
    assert result.status == "FAIL", result.detail
    assert "restoring the final checkpoint" in result.detail
    assert not report.ok


def test_resume_integrity_skips_legacy_snapshot(tmp_path):
    db_path, stream_id = _split_run(tmp_path, "cafe")
    # Downgrade the stored snapshot to a pre-S1 legacy one (R2 shape:
    # no format marker, summary-only data).
    adapter = SqliteStorageAdapter(db_path, stream_id)
    try:
        snapshot = adapter.read_snapshot("latest")
        del snapshot["snapshot_format"]
        snapshot["data"] = {"event_count": 50}
        adapter.write_snapshot("latest", snapshot)
    finally:
        adapter.close()
    report = verify_run("sqlite", db_path, stream_id)
    result = report.check("resume_integrity")
    assert result.status == "SKIP", result.detail
    assert "legacy summary-only" in result.detail


def test_resume_integrity_skips_without_snapshot():
    definition = load_definition("cafe")
    events = [
        {
            "sequence": 0,
            "event_id": "evt_0",
            "event_type": "order_taken",
            "business_type": "cafe",
            "timestamp": PINNED_START,
            "payload": {},
        }
    ]
    report = verify_events(events, definition)
    result = report.check("resume_integrity")
    assert result.status == "SKIP"
    assert "no checkpoint snapshot" in result.detail


# ----------------------------------------------------------------------
# Gym seeded-pool reclassification
# ----------------------------------------------------------------------


def test_gym_member_fields_have_seeded_pool_reason():
    for field_name in GYM_SEEDED_FIELDS:
        assert unverifiable_reason("gym", field_name) == SEEDED_POOL_REASON


def test_gym_recompute_no_longer_tallies_members():
    events = [
        {
            "event_type": "membership_enrolled",
            "payload": {"membership_id": "m1"},
        },
        {
            "event_type": "membership_frozen",
            "payload": {"membership_id": "m1"},
        },
        {
            "event_type": "payment_failed",
            "payload": {"amount": 25.0},
        },
    ]
    recomputed = _recompute_gym(events)
    assert recomputed == {
        "failed_payments_count": 1,
        "failed_payments_amount": 25.0,
    }


def test_gym_seeded_fields_reported_unverifiable(tmp_path):
    # A default gym run (member pool seeded at initialize): the four
    # seeded-pool fields must be named unverifiable, while the
    # stream-derivable position fields still verify exactly.
    db_path = str(tmp_path / "gym.db")
    stream_id = "s5_gym_classification"
    _run_segment(db_path, "gym", stream_id, 60)
    report = verify_run("sqlite", db_path, stream_id)
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]
    assert report.check("position").status == "PASS"
    for field_name in GYM_SEEDED_FIELDS:
        assert field_name in report.unverifiable_fields
