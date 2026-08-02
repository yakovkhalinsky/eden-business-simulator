"""Tests for the logistics simulator."""

from __future__ import annotations

from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.logistics import LogisticsSimulator
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


def test_logistics_registered():
    sim = load_simulator("logistics")
    assert isinstance(sim, LogisticsSimulator)


def test_logistics_emits_expected_event_types():
    config = SimulatorConfig(
        business_type="logistics",
        duration_seconds=5.0,
        events_per_second=50.0,
        seed=11,
    )
    sim = LogisticsSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    events = []
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    for _ in range(250):
        events.append(sim.next_event(clock))
        clock.advance()
    emitted_types = {e["event_type"] for e in events}
    for expected in LogisticsSimulator().available_event_types():
        assert expected in emitted_types, f"missing {expected}"


def test_logistics_determinism():
    config = SimulatorConfig(
        business_type="logistics",
        duration_seconds=1.0,
        events_per_second=20.0,
        seed=55,
    )
    run_a = _collect_event_types(config)
    run_b = _collect_event_types(config)
    assert run_a == run_b


def test_logistics_state_snapshot():
    config = SimulatorConfig(
        business_type="logistics",
        duration_seconds=0.5,
        events_per_second=10.0,
        seed=1,
    )
    sim = LogisticsSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=0.1)
    for _ in range(5):
        sim.next_event(clock)
        clock.advance()
    snapshot = sim.state_snapshot()
    assert "driver_count" in snapshot
    assert "shipment_count" in snapshot


def test_logistics_runner_smoke():
    config = SimulatorConfig(
        business_type="logistics",
        duration_seconds=0.3,
        events_per_second=10.0,
        seed=1,
    )
    sim = load_simulator("logistics")
    sim.configure(config)
    sim.initialize(config.seed)
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    count = runner.run()
    assert count >= 2
    assert all(e.business_type == "logistics" for e in adapter.events)


def _collect_event_types(config: SimulatorConfig) -> list[str]:
    sim = LogisticsSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    clock = Clock(tick_seconds=1.0 / config.events_per_second)
    types: list[str] = []
    for _ in range(40):
        types.append(sim.next_event(clock)["event_type"])
        clock.advance()
    return types
