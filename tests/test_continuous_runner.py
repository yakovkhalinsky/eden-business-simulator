"""Tests for the continuous runner."""

from __future__ import annotations

import threading
from typing import Any

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.models import Clock, EventEnvelope
from eden_business_simulator.storage.memory import MemoryStorageAdapter


class TickSimulator(BusinessSimulator):
    business_type = "tick"

    def __init__(self) -> None:
        self.config = None
        self.count = 0

    def configure(self, config: SimulatorConfig) -> None:
        self.config = config

    def initialize(self, seed: int) -> None:
        self.count = 0

    def available_event_types(self) -> list[str]:
        return ["tick"]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        self.count += 1
        return {
            "event_type": "tick",
            "payload": {"n": self.count},
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {"count": self.count}

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.count = snapshot.get("count", 0)


def make_config(stream_id: str) -> SimulatorConfig:
    return SimulatorConfig(
        business_type="tick",
        duration_seconds=0.0,
        events_per_second=1000.0,
        seed=1,
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
        stream_id=stream_id,
        storage_backend="memory",
    )


def test_continuous_runner_emits_events_and_checkpoints():
    sim = TickSimulator()
    storage = MemoryStorageAdapter("memory://", "tick_stream")
    config = make_config(storage.stream_id)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    timer = threading.Timer(0.02, runner.request_shutdown)
    timer.start()
    count = runner.run()
    timer.join()
    assert count >= 1
    assert storage.latest_sequence() == count - 1
    checkpoint = storage.read_checkpoint()
    assert checkpoint is not None
    assert checkpoint["last_sequence"] == count - 1


def test_continuous_runner_resumes_from_checkpoint():
    sim = TickSimulator()
    storage = MemoryStorageAdapter("memory://", "tick_stream_resume")
    # Pre-seed storage with 5 events, a checkpoint at sequence 4, and a snapshot.
    for i in range(5):
        envelope = EventEnvelope(
            timestamp=Clock().now,
            business_type="tick",
            event_type="tick",
            payload={"n": i + 1},
            stream_id=storage.stream_id,
            sequence=i,
        )
        storage.append(envelope)
    storage.write_checkpoint(4)
    storage.write_snapshot(
        "latest", {"seed": 1, "business_type": "tick", "data": {"count": 5}}
    )

    config = make_config(storage.stream_id)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    timer = threading.Timer(0.02, runner.request_shutdown)
    timer.start()
    count = runner.run()
    timer.join()
    # Restore set count to 5 and then the runner emitted more events.
    assert sim.count >= 5
    assert storage.latest_sequence() == 4 + count
