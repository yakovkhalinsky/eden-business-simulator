"""Tests for the SaaS simulator."""

from __future__ import annotations

from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.saas import SaaSSimulator
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


def test_saas_registered():
    sim = load_simulator("saas")
    assert isinstance(sim, SaaSSimulator)


def test_saas_emits_expected_event_types():
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=11,
    )
    sim = SaaSSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    events = []
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    for _ in range(120):
        events.append(sim.next_event(clock))
        clock.advance()
    emitted_types = {e["event_type"] for e in events}
    for expected in SaaSSimulator().available_event_types():
        assert expected in emitted_types, f"missing {expected}"


def test_saas_determinism():
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=1.0,
        events_per_second=20.0,
        seed=44,
    )
    run_a = _collect_event_types(config)
    run_b = _collect_event_types(config)
    assert run_a == run_b


def test_saas_state_snapshot():
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=0.5,
        events_per_second=10.0,
        seed=1,
    )
    sim = SaaSSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=0.1)
    for _ in range(5):
        sim.next_event(clock)
        clock.advance()
    snapshot = sim.state_snapshot()
    assert "account_count" in snapshot
    assert "subscription_count" in snapshot


def test_saas_runner_smoke():
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=0.3,
        events_per_second=10.0,
        seed=1,
    )
    sim = load_simulator("saas")
    sim.configure(config)
    sim.initialize(config.seed)
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    count = runner.run()
    assert count >= 2
    assert all(e.business_type == "saas" for e in adapter.events)


def test_saas_per_event_payloads():
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=33,
    )
    sim = SaaSSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    seen: dict[str, dict[str, Any]] = {}
    for _ in range(200):
        event = sim.next_event(clock)
        seen[event["event_type"]] = event["payload"]
        clock.advance()

    assert "plan_name" in seen["plan_subscribed"]
    assert "monthly_fee" in seen["plan_subscribed"]
    assert "tax_amount" in seen["plan_subscribed"]
    assert seen["plan_subscribed"]["tax_type"] == "GST"
    assert "invoice_id" in seen["invoice_generated"]
    assert "line_items" in seen["invoice_generated"]
    assert "tax_amount" in seen["invoice_generated"]
    assert seen["invoice_generated"]["tax_type"] == "GST"
    assert "invoice_id" in seen["payment_succeeded"]
    assert "payment_method" in seen["payment_succeeded"]
    assert "tax_amount" in seen["payment_succeeded"]
    assert seen["payment_succeeded"]["tax_type"] == "GST"
    assert "failure_reason" in seen["payment_failed"]
    assert "tax_amount" in seen["payment_failed"]
    assert seen["payment_failed"]["tax_type"] == "GST"
    assert "subject" in seen["support_ticket_opened"]
    assert "severity" in seen["support_ticket_opened"]
    assert "resolution" in seen["ticket_resolved"]
    assert "reason" in seen["churned"]


def _collect_event_types(config: SimulatorConfig) -> list[str]:
    sim = SaaSSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    types: list[str] = []
    for _ in range(40):
        types.append(sim.next_event(clock)["event_type"])
        clock.advance()
    return types
