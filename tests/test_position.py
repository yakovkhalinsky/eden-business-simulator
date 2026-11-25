"""Phase 1: position_snapshot() state read API tests."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest
from typer.testing import CliRunner

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.cli import app
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.memory import MemoryStorageAdapter
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

ALL = [
    "cafe", "clinic", "ecommerce", "electrician", "field_service",
    "gym", "logistics", "plumber", "saas",
]
TRADES = ["electrician", "field_service", "plumber"]

REQUIRED_KEYS = {
    "cafe": {"cash_sales", "card_sales", "total_sales", "opening_float",
             "stock", "orders_by_status", "tickets_by_status",
             "tables_occupied", "tables_total", "waste_total"},
    "clinic": {"appointments_by_status", "encounters_by_status",
               "open_claims_count", "open_claims_amount",
               "lab_orders_pending", "patient_count", "provider_count"},
    "ecommerce": {"revenue_total", "orders_by_status", "inventory",
                  "active_carts_count", "active_cart_items", "refunds_total"},
    "gym": {"members_by_status", "active_count", "frozen_count",
            "cancelled_count", "at_risk_count", "class_count",
            "booking_count", "failed_payments_count",
            "failed_payments_amount"},
    "logistics": {"shipments_by_status", "routes_by_status",
                  "deliveries_completed", "exceptions_count",
                  "returns_count", "fuel_cost_total"},
    "saas": {"active_subscriptions", "mrr", "account_count",
             "open_invoices_count", "open_invoices_amount",
             "paid_revenue_total", "open_tickets_count", "churned_count"},
}
for _trade in TRADES:
    REQUIRED_KEYS[_trade] = {
        "tickets_by_status", "open_invoices_count", "open_invoices_amount",
        "paid_total", "tech_count", "parts_used_total",
    }


def _run(business_type, seed=42, count=200, **config_kwargs):
    config = SimulatorConfig(
        business_type=business_type, duration_seconds=3600, **config_kwargs
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    clock = Clock(
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        tick_seconds=0.5,
    )
    events = []
    for _ in range(count):
        events.append(sim.next_event(clock))
        clock.advance()
    return sim, events


@pytest.mark.parametrize("business_type", ALL)
def test_position_envelope_shape(business_type):
    sim, _ = _run(business_type, count=50)
    envelope = sim.position_snapshot()
    assert envelope["schema_version"] == 1
    assert envelope["business_type"] == business_type
    assert REQUIRED_KEYS[business_type] <= set(envelope["position"])


@pytest.mark.parametrize("business_type", ALL)
def test_position_deterministic_at_same_sequence(business_type):
    sim_a, _ = _run(business_type, count=120)
    sim_b, _ = _run(business_type, count=120)
    assert sim_a.position_snapshot() == sim_b.position_snapshot()


def test_cafe_position_reconciles_with_stream():
    sim, events = _run("cafe")
    position = sim.position_snapshot()["position"]
    stream_total = round(
        sum(
            e["payload"]["total"]
            for e in events
            if e["event_type"] == "order_paid"
        ),
        2,
    )
    assert position["total_sales"] == stream_total
    assert (
        position["cash_sales"] + position["card_sales"]
        == pytest.approx(position["total_sales"])
    )


def test_saas_open_invoices_reconcile_with_stream():
    sim, events = _run("saas")
    position = sim.position_snapshot()["position"]
    generated = sum(
        1 for e in events if e["event_type"] == "invoice_generated"
    )
    paid = sum(
        1 for e in events if e["event_type"] == "payment_succeeded"
    )
    assert position["open_invoices_count"] == generated - paid


def test_ecommerce_revenue_reconciles_with_stream():
    sim, events = _run("ecommerce")
    position = sim.position_snapshot()["position"]
    approved = round(
        sum(
            e["payload"]["amount"]
            for e in events
            if e["event_type"] == "payment_processed"
            and e["payload"]["status"] == "approved"
        ),
        2,
    )
    assert position["revenue_total"] == approved


@pytest.mark.parametrize("business_type", TRADES)
def test_trades_outstanding_reconciles_with_stream(business_type):
    sim, events = _run(business_type)
    position = sim.position_snapshot()["position"]
    invoiced = round(
        sum(
            e["payload"]["total"]
            for e in events
            if e["event_type"] == "invoice_generated"
        ),
        2,
    )
    paid = round(
        sum(
            e["payload"]["amount"]
            for e in events
            if e["event_type"] == "payment_received"
        ),
        2,
    )
    assert position["open_invoices_amount"] == pytest.approx(invoiced - paid)
    assert position["paid_total"] == paid


def _continuous(business_type, storage, stream_id, max_events, every=0):
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        events_per_second=1000.0,
        seed=42,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="memory",
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
        position_snapshot_every_events=every,
        # Phase 2: this helper isolates Phase 1 snapshot behaviour.
        period_summaries_enabled=False,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.run()
    return sim


def test_checkpoint_stores_position_under_separate_key():
    storage = MemoryStorageAdapter("memory://", "pos_stream")
    sim = _continuous("cafe", storage, "pos_stream", max_events=20)
    stored = storage.read_snapshot(ContinuousRunner.POSITION_KEY)
    assert stored is not None
    assert stored["business_type"] == "cafe"
    assert stored["sequence"] == 19
    assert stored["position"] == sim.position_snapshot()["position"]
    # The existing "latest" snapshot semantics are untouched.
    assert storage.read_snapshot(ContinuousRunner.SNAPSHOT_KEY) is not None


def test_opt_in_snapshot_events_only_when_enabled():
    storage = MemoryStorageAdapter("memory://", "pos_events")
    _continuous("cafe", storage, "pos_events", max_events=30, every=10)
    records = list(storage.read_from())
    snapshots = [
        r for r in records if r.envelope.event_type == "state_snapshot_recorded"
    ]
    assert [r.sequence for r in snapshots] == [10, 21, 32]
    assert snapshots[0].envelope.payload["schema_version"] == 1
    assert len(records) == 33

    storage_off = MemoryStorageAdapter("memory://", "pos_events_off")
    _continuous("cafe", storage_off, "pos_events_off", max_events=30)
    records_off = list(storage_off.read_from())
    assert len(records_off) == 30
    assert not any(
        r.envelope.event_type == "state_snapshot_recorded"
        for r in records_off
    )


class _CountingSim:
    """Minimal restorable double: position is its restored counter."""

    business_type = "tick"

    def __init__(self):
        self.count = 0
        self.config = None

    def configure(self, config):
        self.config = config

    def initialize(self, seed):
        self.count = 0

    def available_event_types(self):
        return ["tick"]

    def next_event(self, clock):
        self.count += 1
        return {"event_type": "tick", "payload": {"n": self.count}}

    def state_snapshot(self):
        return {"count": self.count}

    def position_snapshot(self):
        return {
            "schema_version": 1,
            "business_type": "tick",
            "position": {"count": self.count},
        }

    def restore(self, snapshot):
        self.count = snapshot.get("count", 0)


def _run_double(storage, stream_id, max_events):
    config = SimulatorConfig(
        business_type="tick",
        duration_seconds=0.0,
        events_per_second=1000.0,
        seed=1,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="memory",
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
    )
    sim = _CountingSim()
    sim.configure(config)
    sim.initialize(config.seed)
    ContinuousRunner(config, sim, storage, realtime=False).run()
    return storage.read_snapshot(ContinuousRunner.POSITION_KEY)


def test_resumed_position_matches_uninterrupted():
    whole_storage = MemoryStorageAdapter("memory://", "whole")
    whole = _run_double(whole_storage, "whole", max_events=15)

    split_storage = MemoryStorageAdapter("memory://", "split")
    _run_double(split_storage, "split", max_events=10)
    split = _run_double(split_storage, "split", max_events=5)

    assert split["position"] == whole["position"] == {"count": 15}
    assert split["sequence"] == whole["sequence"] == 14


def test_position_config_validation():
    with pytest.raises(Exception):
        SimulatorConfig(
            business_type="cafe", position_snapshot_every_events=-1
        )


def test_cli_position_reads_stored_stream(tmp_path):
    db_path = str(tmp_path / "pos.db")
    storage = SqliteStorageAdapter(db_path, "cli_stream")
    _continuous("gym", storage, "cli_stream", max_events=15)
    # _continuous's runner closes the adapter; reopen for the CLI read.

    result = CliRunner().invoke(
        app,
        [
            "position",
            "--storage", "sqlite",
            "--storage-uri", db_path,
            "--stream-id", "cli_stream",
        ],
    )
    assert result.exit_code == 0, result.output
    payload = json.loads(result.output)
    assert payload["business_type"] == "gym"
    assert "members_by_status" in payload["position"]


def test_cli_position_missing_stream_errors(tmp_path):
    db_path = str(tmp_path / "empty.db")
    SqliteStorageAdapter(db_path, "seed_stream").close()
    result = CliRunner().invoke(
        app,
        [
            "position",
            "--storage", "sqlite",
            "--storage-uri", db_path,
            "--stream-id", "nope",
        ],
    )
    assert result.exit_code == 1
    assert "No position stored" in result.output
