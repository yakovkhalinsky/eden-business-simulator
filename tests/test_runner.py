"""Tests for the simulator runner."""

from datetime import datetime, timezone
from typing import Any

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner


class FakeSimulator(BusinessSimulator):
    business_type = "fake"

    def __init__(self) -> None:
        self.config = None
        self.count = 0

    def configure(self, config: SimulatorConfig) -> None:
        self.config = config

    def initialize(self, seed: int) -> None:
        pass

    def available_event_types(self) -> list[str]:
        return ["tick"]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        self.count += 1
        return {
            "event_type": "tick",
            "payload": {"n": self.count, "ts": clock.now.isoformat()},
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {"count": self.count}


class ListAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)


def test_runner_respects_max_events():
    config = SimulatorConfig(
        business_type="fake",
        duration_seconds=1_000_000,
        events_per_second=1_000_000.0,
        max_events=5,
    )
    sim = FakeSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    adapter = ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    count = runner.run()
    assert count == 5
    assert len(adapter.events) == 5
    assert adapter.events[0].event_type == "tick"
    assert adapter.events[0].business_type == "fake"


def test_runner_respects_duration():
    config = SimulatorConfig(
        business_type="fake",
        duration_seconds=0.25,
        events_per_second=10.0,
        max_events=0,
    )
    sim = FakeSimulator()
    sim.configure(config)
    sim.initialize(config.seed)
    adapter = ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    count = runner.run()
    assert count == 3  # events at elapsed 0.0, 0.1, 0.2 before exceeding 0.25s
