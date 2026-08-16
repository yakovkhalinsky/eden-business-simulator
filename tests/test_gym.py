"""Tests for the gym simulator."""

from __future__ import annotations

from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.gym import GymSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock
from eden_business_simulator.runner import Runner


def test_gym_registered():
    sim = load_simulator("gym")
    assert isinstance(sim, GymSimulator)


def test_gym_emits_expected_event_types():
    config = SimulatorConfig(
        business_type="gym",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=7,
    )
    sim = GymSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    events = []
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    for _ in range(120):
        events.append(sim.next_event(clock))
        clock.advance()
    emitted_types = {e["event_type"] for e in events}
    for expected in GymSimulator().available_event_types():
        assert expected in emitted_types, f"missing {expected}"


def test_gym_determinism():
    config = SimulatorConfig(
        business_type="gym",
        duration_seconds=1.0,
        events_per_second=20.0,
        seed=123,
    )
    run_a = _collect_event_types(config)
    run_b = _collect_event_types(config)
    assert run_a == run_b


def test_gym_state_snapshot():
    config = SimulatorConfig(business_type="gym", duration_seconds=0.5, events_per_second=10.0, seed=1)
    sim = GymSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=0.1)
    for _ in range(5):
        sim.next_event(clock)
        clock.advance()
    snapshot = sim.state_snapshot()
    assert "member_count" in snapshot
    assert "active_membership_count" in snapshot


def test_gym_runner_smoke():
    config = SimulatorConfig(
        business_type="gym",
        duration_seconds=0.3,
        events_per_second=10.0,
        seed=1,
    )
    sim = load_simulator("gym")
    sim.configure(config)
    sim.initialize(config.seed)
    runner = Runner(config, sim, output_adapter := _ListAdapter(), realtime=False)
    count = runner.run()
    assert count >= 2
    assert all(e.business_type == "gym" for e in output_adapter.events)


class _ListAdapter:
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)

    def close(self) -> None:
        pass


def test_retail_purchase_includes_tax():
    config = SimulatorConfig(business_type="gym", duration_seconds=5.0, events_per_second=50.0, seed=17)
    sim = GymSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    purchase = None
    for _ in range(120):
        event = sim.next_event(clock)
        if event["event_type"] == "retail_purchase_made":
            purchase = event
            break
        clock.advance()
    assert purchase is not None
    payload = purchase["payload"]
    assert "tax_amount" in payload
    assert payload["tax_type"] == "GST"
    assert payload["total"] == round(payload["subtotal"] + payload["tax_amount"], 2)


def _collect_event_types(config: SimulatorConfig) -> list[str]:
    sim = GymSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    types: list[str] = []
    for _ in range(40):
        types.append(sim.next_event(clock)["event_type"])
        clock.advance()
    return types
