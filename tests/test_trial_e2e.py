"""Phase 8: scripted Beth/Aleph trial checks, end to end.

Three scenarios over real SQLite storage, mirroring how the trial
will actually be run against Eden:

* Observer — a cafe run is stored, its position is read back through
  the CLI, and the verifier recomputes the whole run (including a
  determinism re-run) from the raw stream.
* Advisor — a saas run's per-day period summaries verify exactly; a
  deliberately corrupted copy of the stream makes the verifier FAIL
  with the drifted KPI named, proving the check can catch drift.
* Operator — a gym daemon-style run under a demand_spike scenario is
  driven through the Phase 5 command table: a routine action applies,
  an approval-gated action without an approval stamp is rejected, the
  same action with the stamp applies — and the resulting stored run
  verifies (determinism SKIPped, by design: scenario inputs are not
  recoverable from storage).
"""

from __future__ import annotations

import json
import shutil
import sqlite3
from datetime import datetime, timezone

from typer.testing import CliRunner

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.cli import app
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.scenario import ScenarioScheduleEntry
from eden_business_simulator.framework.verify import verify_run
from eden_business_simulator.models import Clock
from eden_business_simulator.storage import load_storage_adapter

UTC = timezone.utc


# ----------------------------------------------------------------------
# Observer trial — cafe
# ----------------------------------------------------------------------


def test_observer_trial_cafe(tmp_path):
    stream_id = "trial_cafe"
    db_path = str(tmp_path / "cafe.db")
    storage = load_storage_adapter("sqlite", db_path, stream_id)
    config = SimulatorConfig(
        business_type="cafe",
        duration_seconds=1e12,  # open-ended, daemon-style
        events_per_second=1.0,
        seed=42,
        max_events=400,
        stream_id=stream_id,
        storage_backend="sqlite",
        storage_uri=db_path,
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
    )
    sim = load_simulator("cafe")
    sim.configure(config)
    sim.initialize(42)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    # One event per simulated half hour from 06:00: 400 events span
    # ~8 simulated days, including plenty of trading hours (the cafe's
    # order lifecycle needs many ticks before orders start paying).
    runner.clock = Clock(
        start_time=datetime(2026, 1, 1, 6, 0, tzinfo=UTC),
        tick_seconds=1800.0,
    )
    assert runner.run() == 400

    # Position read back through the same code path as the CLI verb.
    result = CliRunner().invoke(
        app,
        [
            "position",
            "--stream-id", stream_id,
            "--storage", "sqlite",
            "--storage-uri", db_path,
        ],
    )
    assert result.exit_code == 0, result.output
    position = json.loads(result.output)
    assert position["business_type"] == "cafe"
    assert position["position"]["total_sales"] > 0

    report = verify_run(
        "sqlite", db_path, stream_id, check_determinism_flag=True
    )
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]
    for name in (
        "sequence_integrity",
        "period_summaries",
        "position",
        "determinism",
    ):
        assert report.check(name).status == "PASS", report.check(name).detail
    # The run spans multiple simulated days -> several summaries.
    detail = report.check("period_summaries").detail
    assert not detail.startswith("1 period")


# ----------------------------------------------------------------------
# Advisor trial — saas, plus a corruption proof
# ----------------------------------------------------------------------


def _saas_run(tmp_path):
    stream_id = "trial_saas"
    db_path = str(tmp_path / "saas.db")
    storage = load_storage_adapter("sqlite", db_path, stream_id)
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=1e12,
        events_per_second=1.0,
        seed=42,
        max_events=72,
        stream_id=stream_id,
        storage_backend="sqlite",
        storage_uri=db_path,
        checkpoint_interval_events=10,
        checkpoint_interval_seconds=3600.0,
    )
    sim = load_simulator("saas")
    sim.configure(config)
    sim.initialize(42)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    # One event per simulated hour from midday: 72 events span 4 days.
    runner.clock = Clock(
        start_time=datetime(2026, 1, 1, 12, 0, tzinfo=UTC),
        tick_seconds=3600.0,
    )
    runner.run()
    return db_path, stream_id


def test_advisor_trial_saas(tmp_path):
    db_path, stream_id = _saas_run(tmp_path)

    adapter = load_storage_adapter("sqlite", db_path, stream_id)
    try:
        summaries = [
            r.envelope
            for r in adapter.read_from()
            if r.envelope.event_type == "period_summary"
        ]
    finally:
        adapter.close()
    # One summary per simulated day *closed* by the run. Jan 4 is
    # still open at run end: since restorable-state S4 (R3) the
    # continuous runner checkpoints its open period instead of
    # flushing a final partial, so the last touched day summarises
    # only when a later day closes it (or a resume continues it).
    assert {s.payload["period_start"] for s in summaries} == {
        "2026-01-01",
        "2026-01-02",
        "2026-01-03",
    }
    assert all(s.payload["partial"] is False for s in summaries)

    report = verify_run(
        "sqlite", db_path, stream_id, check_determinism_flag=True
    )
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]
    assert report.check("period_summaries").status == "PASS"
    assert report.check("position").status == "PASS"


def test_advisor_trial_corruption_is_caught(tmp_path):
    db_path, stream_id = _saas_run(tmp_path)
    # Flush WAL so a plain file copy is a complete clone.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    conn.close()
    corrupt_path = str(tmp_path / "saas_corrupt.db")
    shutil.copy(db_path, corrupt_path)

    # Tamper: inflate one payment_succeeded amount inside the stream.
    conn = sqlite3.connect(corrupt_path)
    try:
        rows = conn.execute(
            "SELECT offset, envelope_json FROM event_log "
            "WHERE stream_id = ? ORDER BY sequence",
            (stream_id,),
        ).fetchall()
        target = None
        for offset, envelope_json in rows:
            envelope = json.loads(envelope_json)
            if envelope["event_type"] == "payment_succeeded":
                envelope["payload"]["amount"] += 100.0
                target = (offset, json.dumps(envelope))
                break
        assert target is not None, "run should contain a payment_succeeded"
        conn.execute(
            "UPDATE event_log SET envelope_json = ? WHERE offset = ?",
            (target[1], target[0]),
        )
        conn.commit()
    finally:
        conn.close()

    report = verify_run("sqlite", corrupt_path, stream_id)
    assert not report.ok
    period = report.check("period_summaries")
    assert period.status == "FAIL"
    assert "revenue" in period.detail  # the drifted KPI is named
    position = report.check("position")
    assert position.status == "FAIL"
    assert "paid_revenue_total" in position.detail

    # The untouched original still verifies clean.
    assert verify_run("sqlite", db_path, stream_id).ok


# ----------------------------------------------------------------------
# Operator trial — gym, commands + scenario, daemon-style
# ----------------------------------------------------------------------


def test_operator_trial_gym(tmp_path):
    stream_id = "trial_gym"
    db_path = str(tmp_path / "gym.db")
    scenario = [ScenarioScheduleEntry(name="demand_spike", start_offset_minutes=0)]

    def _config(max_events):
        return SimulatorConfig(
            business_type="gym",
            duration_seconds=1e12,
            events_per_second=1000.0,
            seed=42,
            max_events=max_events,
            stream_id=stream_id,
            storage_backend="sqlite",
            storage_uri=db_path,
            checkpoint_interval_events=10,
            checkpoint_interval_seconds=3600.0,
            scenario_schedule=scenario,
            # No seeded members: every membership in this trial is
            # created by a stream event, so the verifier's gym
            # position recomputation (a pure stream tally) is exact.
            # (Seeded members exist in the position but not the
            # stream; the tally only coincides with the position in
            # churn-heavy regimes where every seeded member happens
            # to be touched by a status event.)
            initial_state_overrides={"initial_members": 0},
        )

    # --- Phase 1 of the trial: the daemon runs and builds state.
    storage = load_storage_adapter("sqlite", db_path, stream_id)
    sim = load_simulator("gym")
    config1 = _config(max_events=40)
    sim.configure(config1)
    sim.initialize(42)
    runner1 = ContinuousRunner(config1, sim, storage, realtime=False)
    # Pin the clock to one simulated hour per event so the run crosses
    # a calendar-day boundary and closes a real period summary (since
    # S4/R3 the continuous runner no longer flushes a partial summary
    # at process end — the open period rides in the checkpoint).
    # Phase 2's runner resumes this clock from the checkpoint.
    runner1.clock = Clock(
        start_time=datetime(2026, 1, 1, 8, 0, tzinfo=UTC),
        tick_seconds=3600.0,
    )
    runner1.run()

    # An operator reads current state before acting: pick a member
    # whose membership is active right now.
    active_member = next(
        m["member_id"]
        for m in sim.memberships.values()
        if m["status"] == "active"
    )

    # --- Commands submitted through the Phase 5 storage table.
    adapter = load_storage_adapter("sqlite", db_path, stream_id)
    try:
        adapter.submit_command(
            stream_id,
            "freeze_membership",
            {"member_id": active_member},
            requested_by="aleph-trial",
            action_id="trial-freeze-1",
            submitted_at="2026-01-01T00:00:00+00:00",
        )
        adapter.submit_command(
            stream_id,
            "adjust_plan_price",
            {"tier": "standard", "new_monthly_fee": 55.0},
            requested_by="aleph-trial",
            action_id="trial-price-1",
            submitted_at="2026-01-01T00:01:00+00:00",
        )
        adapter.submit_command(
            stream_id,
            "adjust_plan_price",
            {"tier": "standard", "new_monthly_fee": 55.0},
            requested_by="aleph-trial",
            approved_by="yakov",
            action_id="trial-price-2",
            submitted_at="2026-01-01T00:02:00+00:00",
        )
    finally:
        adapter.close()

    # --- Phase 2: the daemon resumes (same simulator state, as a
    # long-lived process would hold it) and drains the command table.
    config2 = _config(max_events=30)
    sim.configure(config2)
    storage2 = load_storage_adapter("sqlite", db_path, stream_id)
    ContinuousRunner(config2, sim, storage2, realtime=False).run()

    adapter = load_storage_adapter("sqlite", db_path, stream_id)
    try:
        records = adapter.list_commands(stream_id)
        assert [r["status"] for r in records] == [
            "applied",
            "rejected",
            "applied",
        ]
        assert records[1]["result"]["reason"] == "requires_approval"
        envelopes = [r.envelope for r in adapter.read_from()]
    finally:
        adapter.close()

    types = [e.event_type for e in envelopes]
    assert "scenario_started" in types
    assert "membership_frozen" in types
    assert "plan_price_adjusted" in types
    applied = [e for e in envelopes if e.event_type == "action_applied"]
    assert {e.payload["action_id"] for e in applied} == {
        "trial-freeze-1",
        "trial-price-2",
    }
    rejected = [e for e in envelopes if e.event_type == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "requires_approval"

    # The whole stored run — both daemon phases, commands, scenario —
    # verifies. Determinism is SKIPped by design (scenario markers).
    report = verify_run(
        "sqlite", db_path, stream_id, check_determinism_flag=True
    )
    assert report.ok, [(c.name, c.status, c.detail) for c in report.checks]
    assert report.check("action_audit").status == "PASS"
    assert report.check("position").status == "PASS"
    assert report.check("period_summaries").status == "PASS"
    determinism = report.check("determinism")
    assert determinism.status == "SKIP"
    assert "not recoverable" in determinism.detail
