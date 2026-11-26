"""Daemon command input tests (Phase 5).

Covers the storage command table on all three backends (submit ->
pending -> resolve -> list/filter, and the applied-id union used for
restart idempotency), ContinuousRunner command polling (commands
submitted to storage are applied between ticks through the normal
action pipeline and resolved on their records), idempotency across a
simulated daemon restart, and the ``command`` / ``commands`` CLI verbs
— the Beth/Aleph integration surface.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.cli import app
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage import load_storage_adapter
from eden_business_simulator.storage.base import StorageAdapter

TS_SUBMIT = "2026-01-01T00:00:00+00:00"
TS_RESOLVE = "2026-01-01T00:05:00+00:00"
RUN_ID = "cafe_cmd_run"
SETUP_EVENTS = 19  # cafe setup: shift + staff + menu + deliveries

cli_runner = CliRunner()


# ----------------------------------------------------------------------
# Backend fixtures
# ----------------------------------------------------------------------


def _make_adapter(backend: str, tmp_path, run_id: str = RUN_ID) -> StorageAdapter:
    if backend == "memory":
        return load_storage_adapter("memory", "memory://", run_id)
    if backend == "sqlite":
        return load_storage_adapter("sqlite", str(tmp_path / "eden.db"), run_id)
    if backend == "ndjson":
        return load_storage_adapter("ndjson", str(tmp_path / "events.jsonl"), run_id)
    raise AssertionError(backend)


def _reopen(backend: str, tmp_path, adapter: StorageAdapter) -> StorageAdapter:
    return load_storage_adapter(backend, adapter.uri, adapter.stream_id)


BACKENDS = ["memory", "sqlite", "ndjson"]


# ----------------------------------------------------------------------
# Storage semantics, per backend
# ----------------------------------------------------------------------


@pytest.mark.parametrize("backend", BACKENDS)
def test_command_lifecycle_per_backend(backend, tmp_path):
    adapter = _make_adapter(backend, tmp_path)
    try:
        first = adapter.submit_command(
            RUN_ID,
            "place_supplier_order",
            {"ingredient_id": "ing_001", "qty": 5},
            requested_by="aleph-run-1",
            submitted_at=TS_SUBMIT,
        )
        second = adapter.submit_command(
            RUN_ID,
            "adjust_price",
            {"item_id": "item_0001", "new_price": 9.0},
            requested_by="aleph-run-1",
            approved_by="yakov",
            action_id="op-price-1",
            submitted_at=TS_SUBMIT,
        )
        # Deterministic per-run counter ids; action_id defaults to the
        # command id when not supplied.
        assert (first, second) == ("cmd_000001", "cmd_000002")
        pending = adapter.pending_commands(RUN_ID)
        assert [c["command_id"] for c in pending] == [first, second]
        assert pending[0]["action_id"] == "cmd_000001"
        assert pending[0]["status"] == "pending"
        assert pending[0]["result"] is None
        assert pending[0]["submitted_at"] == TS_SUBMIT
        assert pending[1]["action_id"] == "op-price-1"
        assert pending[1]["approved_by"] == "yakov"

        adapter.record_command_result(
            first,
            "applied",
            {"status": "applied", "effect_event_types": ["supplier_delivery_received"]},
            TS_RESOLVE,
            run_id=RUN_ID,
        )

        assert [c["command_id"] for c in adapter.pending_commands(RUN_ID)] == [second]
        records = adapter.list_commands(RUN_ID)
        assert [c["command_id"] for c in records] == [first, second]
        assert records[0]["status"] == "applied"
        assert records[0]["result"]["effect_event_types"] == [
            "supplier_delivery_received"
        ]
        assert records[0]["resolved_at"] == TS_RESOLVE
        assert [c["command_id"] for c in adapter.list_commands(RUN_ID, status="applied")] == [first]
        assert [c["command_id"] for c in adapter.list_commands(RUN_ID, status="pending")] == [second]
        assert adapter.list_commands(RUN_ID, status="rejected") == []
        # Runs are isolated.
        assert adapter.list_commands("some_other_run") == []
    finally:
        adapter.close()

    if backend != "memory":
        # Durable backends re-derive the same state on reopen (for
        # NDJSON this is the submit/resolution fold).
        reopened = _reopen(backend, tmp_path, adapter)
        try:
            records = reopened.list_commands(RUN_ID)
            assert [c["command_id"] for c in records] == [first, second]
            assert records[0]["status"] == "applied"
            assert records[1]["status"] == "pending"
            # The counter continues from the stored rows, not from 0.
            third = reopened.submit_command(
                RUN_ID, "clock_staff_in", {"staff_id": "staff_01"},
                submitted_at=TS_SUBMIT,
            )
            assert third == "cmd_000003"
        finally:
            reopened.close()


@pytest.mark.parametrize("backend", BACKENDS)
def test_applied_action_ids_union_per_backend(backend, tmp_path):
    adapter = _make_adapter(backend, tmp_path)
    try:
        applied_cmd = adapter.submit_command(
            RUN_ID, "place_supplier_order", {"ingredient_id": "ing_001", "qty": 5},
            action_id="op-from-command", submitted_at=TS_SUBMIT,
        )
        rejected_cmd = adapter.submit_command(
            RUN_ID, "adjust_price", {"item_id": "item_0001", "new_price": 9.0},
            action_id="op-rejected", submitted_at=TS_SUBMIT,
        )
        adapter.record_command_result(
            applied_cmd, "applied", {"status": "applied"}, TS_RESOLVE, run_id=RUN_ID
        )
        adapter.record_command_result(
            rejected_cmd, "rejected",
            {"status": "rejected", "reason": "requires_approval"},
            TS_RESOLVE, run_id=RUN_ID,
        )
        # An action applied by a scripted runner in an earlier process:
        # no command record, only the stored audit event.
        adapter.append(
            EventEnvelope(
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                business_type="cafe",
                event_type="action_applied",
                payload={"action_id": "op-from-event", "action_type": "clock_staff_in"},
                stream_id=RUN_ID,
            )
        )
        # A rejection audit event must not count as applied.
        adapter.append(
            EventEnvelope(
                timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
                business_type="cafe",
                event_type="action_rejected",
                payload={"action_id": "op-rejected-event"},
                stream_id=RUN_ID,
            )
        )
        assert adapter.applied_action_ids(RUN_ID) == {
            "op-from-command",
            "op-from-event",
        }
    finally:
        adapter.close()


# ----------------------------------------------------------------------
# Runner integration
# ----------------------------------------------------------------------


def _cafe_config(run_id: str, backend: str, uri: str, max_events: int = 3):
    return SimulatorConfig(
        business_type="cafe",
        duration_seconds=3600.0,
        events_per_second=1000.0,
        max_events=max_events,
        seed=7,
        stream_id=run_id,
        storage_backend=backend,  # type: ignore[arg-type]
        storage_uri=uri,
        checkpoint_interval_events=1000,
        checkpoint_interval_seconds=3600.0,
        period_summaries_enabled=False,
    )


def _cafe_runner(storage, run_id, backend="memory", uri="memory://", max_events=3,
                 in_service=False):
    config = _cafe_config(run_id, backend, uri, max_events=max_events)
    sim = load_simulator("cafe")
    sim.configure(config)
    sim.initialize(config.seed)
    if in_service:
        # Drive the setup events so the menu exists (the runner's own
        # clock is separate; these events are not persisted).
        from eden_business_simulator.models import Clock

        clock = Clock(tick_seconds=1.0)
        for _ in range(SETUP_EVENTS):
            sim.next_event(clock)
            clock.advance()
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    return runner, sim


@pytest.mark.parametrize("backend", ["memory", "sqlite"])
def test_runner_applies_pending_command(backend, tmp_path):
    uri = "memory://" if backend == "memory" else str(tmp_path / "eden.db")
    storage = _make_adapter(backend, tmp_path)
    command_id = storage.submit_command(
        RUN_ID, "place_supplier_order", {"ingredient_id": "ing_001", "qty": 5},
        requested_by="beth", submitted_at=TS_SUBMIT,
    )
    runner, sim = _cafe_runner(storage, RUN_ID, backend=backend, uri=uri)
    assert runner.run() == 3

    if backend == "sqlite":
        # The runner closed its storage; reopen to inspect the stream.
        storage = _reopen(backend, tmp_path, storage)
    try:
        records = [r.envelope for r in storage.read_from()]
        types = [e.event_type for e in records]
        assert "supplier_delivery_received" in types
        applied = [e for e in records if e.event_type == "action_applied"]
        assert len(applied) == 1
        assert applied[0].payload["action_id"] == command_id
        assert applied[0].payload["actor"] == "beth"
        # Effect first, audit immediately after (D5 ordering).
        assert types.index("action_applied") == types.index("supplier_delivery_received") + 1
        (record,) = storage.list_commands(RUN_ID)
        assert record["status"] == "applied"
        assert record["result"]["effect_event_types"] == ["supplier_delivery_received"]
        assert record["resolved_at"] is not None
    finally:
        storage.close()


def test_runner_rejects_approval_gated_command_without_approval(tmp_path):
    storage = _make_adapter("sqlite", tmp_path)
    storage.submit_command(
        RUN_ID, "adjust_price", {"item_id": "item_0001", "new_price": 9.0},
        requested_by="beth", submitted_at=TS_SUBMIT,
    )
    runner, _ = _cafe_runner(
        storage, RUN_ID, backend="sqlite", uri=str(tmp_path / "eden.db"),
        in_service=True,
    )
    runner.run()
    storage = _reopen("sqlite", tmp_path, storage)
    try:
        (record,) = storage.list_commands(RUN_ID)
        assert record["status"] == "rejected"
        assert record["result"]["reason"] == "requires_approval"
        rejected = [
            r.envelope for r in storage.read_from()
            if r.envelope.event_type == "action_rejected"
        ]
        assert len(rejected) == 1
        assert rejected[0].payload["reason"] == "requires_approval"
    finally:
        storage.close()


def test_runner_applies_approval_gated_command_with_approval(tmp_path):
    storage = _make_adapter("sqlite", tmp_path)
    storage.submit_command(
        RUN_ID, "adjust_price", {"item_id": "item_0001", "new_price": 9.0},
        requested_by="beth", approved_by="yakov", submitted_at=TS_SUBMIT,
    )
    runner, sim = _cafe_runner(
        storage, RUN_ID, backend="sqlite", uri=str(tmp_path / "eden.db"),
        in_service=True,
    )
    runner.run()
    item = next(i for i in sim.menu.all() if i["item_id"] == "item_0001")
    assert item["price"] == 9.0
    storage = _reopen("sqlite", tmp_path, storage)
    try:
        (record,) = storage.list_commands(RUN_ID)
        assert record["status"] == "applied"
        assert record["result"]["effect_event_types"] == ["menu_price_adjusted"]
    finally:
        storage.close()


# ----------------------------------------------------------------------
# Restart idempotency
# ----------------------------------------------------------------------


def test_restart_idempotency_sqlite(tmp_path):
    db = str(tmp_path / "eden.db")
    # --- First daemon process: applies the command once.
    storage1 = _make_adapter("sqlite", tmp_path)
    storage1.submit_command(
        RUN_ID, "place_supplier_order", {"ingredient_id": "ing_001", "qty": 5},
        requested_by="beth", action_id="op-1", submitted_at=TS_SUBMIT,
    )
    runner1, _ = _cafe_runner(storage1, RUN_ID, backend="sqlite", uri=db, max_events=2)
    runner1.run()  # runner closes its storage

    # --- Second daemon process over the same storage: a fresh
    # simulator instance (its applied-id set starts empty) and a new
    # command reusing action_id "op-1".
    storage2 = _make_adapter("sqlite", tmp_path)
    storage2.submit_command(
        RUN_ID, "place_supplier_order", {"ingredient_id": "ing_001", "qty": 5},
        requested_by="beth", action_id="op-1", submitted_at=TS_SUBMIT,
    )
    runner2, sim2 = _cafe_runner(storage2, RUN_ID, backend="sqlite", uri=db, max_events=2)
    stock_before = sim2.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    runner2.run()

    storage3 = _make_adapter("sqlite", tmp_path)
    try:
        records = storage3.list_commands(RUN_ID)
        assert [r["status"] for r in records] == ["applied", "rejected"]
        assert records[1]["result"]["reason"] == "duplicate_action_id"
        effects = [
            r.envelope for r in storage3.read_from()
            if r.envelope.event_type == "supplier_delivery_received"
        ]
        assert len(effects) == 1  # no double effect across the restart
        rejected = [
            r.envelope for r in storage3.read_from()
            if r.envelope.event_type == "action_rejected"
        ]
        assert rejected[-1].payload["reason"] == "duplicate_action_id"
    finally:
        storage3.close()
    stock_after = sim2.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    assert stock_after == stock_before  # the duplicate moved no stock


# ----------------------------------------------------------------------
# CLI verbs
# ----------------------------------------------------------------------


def _cli_storage_args(tmp_path):
    return ["--storage", "sqlite", "--storage-uri", str(tmp_path / "eden.db")]


def test_cli_command_then_commands_then_runner_drains(tmp_path):
    args = _cli_storage_args(tmp_path)
    result = cli_runner.invoke(
        app,
        [
            "command", "cafe", "--run-id", RUN_ID,
            "--type", "place_supplier_order",
            "--params", '{"ingredient_id": "ing_001", "qty": 5}',
            "--requested-by", "beth",
        ] + args,
    )
    assert result.exit_code == 0, result.output
    assert "cmd_000001" in result.output
    assert "status=pending" in result.output

    storage = _make_adapter("sqlite", tmp_path)
    try:
        (record,) = storage.list_commands(RUN_ID)
        assert record["status"] == "pending"
        assert record["params"] == {"ingredient_id": "ing_001", "qty": 5}
        assert record["requested_by"] == "beth"
    finally:
        storage.close()

    result = cli_runner.invoke(app, ["commands", "cafe", "--run-id", RUN_ID] + args)
    assert result.exit_code == 0, result.output
    assert "cmd_000001" in result.output
    assert "place_supplier_order" in result.output
    assert "pending" in result.output

    # A daemon (runner) over the same storage drains the command...
    storage = _make_adapter("sqlite", tmp_path)
    runner, _ = _cafe_runner(
        storage, RUN_ID, backend="sqlite", uri=str(tmp_path / "eden.db")
    )
    runner.run()

    # ...and the listing now shows it applied.
    result = cli_runner.invoke(app, ["commands", "cafe", "--run-id", RUN_ID] + args)
    assert result.exit_code == 0, result.output
    assert "applied" in result.output
    result = cli_runner.invoke(
        app, ["commands", "cafe", "--run-id", RUN_ID, "--status", "pending"] + args
    )
    assert result.exit_code == 0, result.output
    assert "No commands found." in result.output


def test_cli_command_rejected_reason_shows_in_listing(tmp_path):
    args = _cli_storage_args(tmp_path)
    result = cli_runner.invoke(
        app,
        [
            "command", "cafe", "--run-id", RUN_ID,
            "--type", "adjust_price",
            "--params", '{"item_id": "item_0001", "new_price": 9.0}',
        ] + args,
    )
    assert result.exit_code == 0, result.output
    storage = _make_adapter("sqlite", tmp_path)
    runner, _ = _cafe_runner(
        storage, RUN_ID, backend="sqlite", uri=str(tmp_path / "eden.db")
    )
    runner.run()
    result = cli_runner.invoke(app, ["commands", "cafe", "--run-id", RUN_ID] + args)
    assert result.exit_code == 0, result.output
    assert "rejected" in result.output
    assert "reason=requires_approval" in result.output


def test_cli_command_bad_json_params(tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "command", "cafe", "--run-id", RUN_ID,
            "--type", "place_supplier_order", "--params", "{not json",
        ] + _cli_storage_args(tmp_path),
    )
    assert result.exit_code != 0
    assert "not valid JSON" in result.output


def test_cli_command_non_object_params(tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "command", "cafe", "--run-id", RUN_ID,
            "--type", "place_supplier_order", "--params", "[1, 2]",
        ] + _cli_storage_args(tmp_path),
    )
    assert result.exit_code != 0
    assert "must be a JSON object" in result.output


def test_cli_command_unknown_business(tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "command", "bakery", "--run-id", RUN_ID,
            "--type", "place_supplier_order", "--params", "{}",
        ] + _cli_storage_args(tmp_path),
    )
    assert result.exit_code != 0
    assert "Unknown business_type" in result.output
