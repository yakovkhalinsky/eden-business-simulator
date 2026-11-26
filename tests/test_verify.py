"""Phase 8: verifier unit/integration tests.

Runs real simulators into SQLite storage, then verifies the stored
runs with framework.verify — the recomputation must PASS on honest
runs, FAIL with the field/period named on tampered ones, and SKIP
(with reasons) where a check cannot apply.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.cli import app
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.definition import load_definition
from eden_business_simulator.framework.verify import verify_events, verify_run
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

ALL = [
    "cafe", "clinic", "ecommerce", "electrician", "field_service",
    "gym", "logistics", "plumber", "saas",
]
NEAR_MIDNIGHT = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)


def _stored_run(tmp_path, business_type, max_events=150, seed=42):
    """Run a simulator into a fresh SQLite store; return (db, stream)."""
    stream_id = f"verify_{business_type}"
    db_path = str(tmp_path / f"{business_type}.db")
    storage = SqliteStorageAdapter(db_path, stream_id)
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        events_per_second=1.0,
        seed=seed,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="sqlite",
        checkpoint_interval_events=50,
        checkpoint_interval_seconds=3600.0,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.clock = Clock(start_time=NEAR_MIDNIGHT, tick_seconds=1.0)
    runner.run()
    return db_path, stream_id


@pytest.mark.parametrize("business_type", ALL)
def test_verify_honest_run_all_businesses(tmp_path, business_type):
    db_path, stream_id = _stored_run(tmp_path, business_type)
    report = verify_run("sqlite", db_path, stream_id)
    assert report.ok, [
        (c.name, c.status, c.detail) for c in report.checks
    ]
    assert report.check("sequence_integrity").status == "PASS"
    assert report.check("period_summaries").status == "PASS"
    assert report.check("position").status == "PASS"
    # No actions were submitted in these runs.
    assert report.check("action_audit").status == "SKIP"


@pytest.mark.parametrize("business_type", ALL)
def test_verify_reports_unverifiable_fields(tmp_path, business_type):
    db_path, stream_id = _stored_run(tmp_path, business_type, max_events=60)
    report = verify_run("sqlite", db_path, stream_id)
    # Every business has at least one setup-state or otherwise
    # non-stream-derivable position field; it must be named, not
    # silently passed.
    assert report.unverifiable_fields, business_type


@pytest.mark.parametrize("business_type", ["cafe", "gym", "saas"])
def test_verify_determinism_rerun(tmp_path, business_type):
    db_path, stream_id = _stored_run(tmp_path, business_type, max_events=80)
    report = verify_run(
        "sqlite", db_path, stream_id, check_determinism_flag=True
    )
    determinism = report.check("determinism")
    assert determinism.status == "PASS", determinism.detail
    assert report.ok


def test_verify_memory_backend_rejected(tmp_path):
    with pytest.raises(ValueError, match="per-process"):
        verify_run("memory", "memory://", "whatever")


def test_verify_unknown_stream_fails():
    report = verify_run("sqlite", ":memory:", "no_such_stream")
    assert not report.ok
    assert report.check("sequence_integrity").status == "FAIL"


# ----------------------------------------------------------------------
# Check-level behaviour on synthetic event lists
# ----------------------------------------------------------------------


def _event(sequence, event_type, payload=None, event_id=None):
    return {
        "sequence": sequence,
        "event_id": event_id or f"evt_{sequence}",
        "event_type": event_type,
        "business_type": "cafe",
        "timestamp": datetime(2026, 1, 1, tzinfo=timezone.utc),
        "payload": payload or {},
    }


def test_sequence_gap_is_named():
    definition = load_definition("cafe")
    events = [_event(0, "order_taken"), _event(2, "order_taken")]
    report = verify_events(events, definition)
    result = report.check("sequence_integrity")
    assert result.status == "FAIL"
    assert "expected 1, found 2" in result.detail
    assert not report.ok


def test_position_skip_without_stored_position():
    definition = load_definition("cafe")
    report = verify_events([_event(0, "order_taken")], definition)
    assert report.check("position").status == "SKIP"
    assert report.check("period_summaries").status == "SKIP"


def test_action_audit_duplicate_applied_id_fails():
    definition = load_definition("cafe")
    audit = {
        "action_id": "op-1",
        "action_type": "place_supplier_order",
        "actor": "beth",
        "approved_by": None,
        "params": {},
        "effect_event_types": ["supplier_delivery_received"],
    }
    effect = {"ingredient_id": "ing_001", "items": []}
    events = [
        _event(0, "supplier_delivery_received", effect),
        _event(1, "action_applied", audit),
        _event(2, "supplier_delivery_received", effect),
        _event(3, "action_applied", audit),
    ]
    report = verify_events(events, definition)
    result = report.check("action_audit")
    assert result.status == "FAIL"
    assert "op-1" in result.detail and "twice" in result.detail


def test_action_audit_undeclared_action_fails():
    definition = load_definition("cafe")
    events = [
        _event(
            0,
            "action_applied",
            {
                "action_id": "op-9",
                "action_type": "burn_it_down",
                "actor": "mallory",
                "approved_by": None,
                "params": {},
                "effect_event_types": [],
            },
        )
    ]
    report = verify_events(events, definition)
    result = report.check("action_audit")
    assert result.status == "FAIL"
    assert "burn_it_down" in result.detail


def test_action_audit_effect_must_immediately_precede():
    definition = load_definition("cafe")
    events = [
        _event(0, "supplier_delivery_received", {}),
        _event(1, "order_taken", {}),
        _event(
            2,
            "action_applied",
            {
                "action_id": "op-2",
                "action_type": "place_supplier_order",
                "actor": "beth",
                "approved_by": None,
                "params": {},
                "effect_event_types": ["supplier_delivery_received"],
            },
        ),
    ]
    report = verify_events(events, definition)
    result = report.check("action_audit")
    assert result.status == "FAIL"
    assert "immediately precede" in result.detail


# ----------------------------------------------------------------------
# CLI verb
# ----------------------------------------------------------------------


def test_cli_verify_pass_and_exit_code(tmp_path):
    db_path, stream_id = _stored_run(tmp_path, "gym", max_events=60)
    result = CliRunner().invoke(
        app,
        [
            "verify",
            "--stream-id", stream_id,
            "--storage", "sqlite",
            "--storage-uri", db_path,
        ],
    )
    assert result.exit_code == 0, result.output
    assert "PASS sequence_integrity" in result.output
    assert "PASS position" in result.output
    assert "UNVERIFIABLE position." in result.output
    assert "VERDICT: PASS" in result.output


def test_cli_verify_memory_backend_errors(tmp_path):
    result = CliRunner().invoke(
        app, ["verify", "--stream-id", "x", "--storage", "memory"]
    )
    assert result.exit_code == 1
    assert "per-process" in result.output
