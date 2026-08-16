"""Tests for the field service simulator."""

from __future__ import annotations

from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.field_service import FieldServiceSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock
from eden_business_simulator.runner import Runner


class _ListAdapter:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)

    def close(self) -> None:
        pass


def test_field_service_registered():
    sim = load_simulator("field_service")
    assert isinstance(sim, FieldServiceSimulator)


def test_field_service_emits_expected_event_types():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=13,
    )
    sim = FieldServiceSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    events = []
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    for _ in range(250):
        events.append(sim.next_event(clock))
        clock.advance()
    emitted_types = {e["event_type"] for e in events}
    for expected in FieldServiceSimulator().available_event_types():
        assert expected in emitted_types, f"missing {expected}"


def test_field_service_determinism():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=1.0,
        events_per_second=20.0,
        seed=77,
    )
    run_a = _collect_event_types(config)
    run_b = _collect_event_types(config)
    assert run_a == run_b


def test_field_service_state_snapshot():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=0.5,
        events_per_second=10.0,
        seed=1,
    )
    sim = FieldServiceSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=0.1)
    for _ in range(5):
        sim.next_event(clock)
        clock.advance()
    snapshot = sim.state_snapshot()
    assert "customer_count" in snapshot
    assert "ticket_count" in snapshot


def test_field_service_runner_smoke():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=0.3,
        events_per_second=10.0,
        seed=1,
    )
    sim = load_simulator("field_service")
    sim.configure(config)
    sim.initialize(config.seed)
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    count = runner.run()
    assert count >= 2
    assert all(e.business_type == "field_service" for e in adapter.events)


def test_payment_invoice_id_links_to_prior_invoice():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=202,
    )
    sim = FieldServiceSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    invoices: set[str] = set()
    payments: list[dict[str, Any]] = []
    for _ in range(300):
        event = sim.next_event(clock)
        if event["event_type"] == "invoice_generated":
            invoices.add(event["payload"]["invoice_id"])
        elif event["event_type"] == "payment_received":
            payments.append(event["payload"])
        clock.advance()
    if payments:
        for payment in payments:
            assert payment["invoice_id"] in invoices, "payment_received must reference a prior invoice_generated"


def test_invoice_and_payment_include_tax():
    config = SimulatorConfig(
        business_type="field_service",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=303,
    )
    sim = FieldServiceSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    invoice_event = None
    payment_event = None
    for _ in range(300):
        event = sim.next_event(clock)
        if event["event_type"] == "invoice_generated" and invoice_event is None:
            invoice_event = event
        elif event["event_type"] == "payment_received" and payment_event is None:
            payment_event = event
        if invoice_event and payment_event:
            break
        clock.advance()

    assert invoice_event is not None
    assert invoice_event["payload"]["tax_type"] == "GST"
    assert invoice_event["payload"]["tax_amount"] > 0
    assert payment_event is not None
    assert payment_event["payload"]["tax_type"] == "GST"
    assert payment_event["payload"]["tax_amount"] == invoice_event["payload"]["tax_amount"]


def _collect_event_types(config: SimulatorConfig) -> list[str]:
    sim = FieldServiceSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    types: list[str] = []
    for _ in range(40):
        types.append(sim.next_event(clock)["event_type"])
        clock.advance()
    return types
