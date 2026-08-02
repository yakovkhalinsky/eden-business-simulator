"""Tests for the replay command and replay output."""

from __future__ import annotations

from io import StringIO
from typing import Any

import pytest

from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.models import Clock, EventEnvelope
from eden_business_simulator.output.ndjson import NDJsonOutputAdapter
from eden_business_simulator.storage.memory import MemoryStorageAdapter


class ReplaySimulator:
    """Tiny fake simulator for replay tests."""

    business_type = "replay"

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
        return {"event_type": "tick", "payload": {"n": self.count}}

    def state_snapshot(self) -> dict[str, Any]:
        return {"count": self.count}


def test_replay_emits_events_to_output_adapter():
    storage = MemoryStorageAdapter("memory://", "replay_stream")
    for i in range(5):
        storage.append(
            EventEnvelope(
                timestamp=Clock().now,
                business_type="replay",
                event_type="tick",
                payload={"n": i + 1},
                stream_id=storage.stream_id,
                sequence=i,
            )
        )

    stream = StringIO()
    output = NDJsonOutputAdapter(stream)
    for record in storage.read_from(offset=0):
        output.write(record.envelope)
    output.close()

    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == 5
    assert '"sequence":4' in lines[-1]


def test_replay_from_sequence_offset():
    storage = MemoryStorageAdapter("memory://", "replay_offset_stream")
    for i in range(10):
        storage.append(
            EventEnvelope(
                timestamp=Clock().now,
                business_type="replay",
                event_type="tick",
                payload={"n": i + 1},
                stream_id=storage.stream_id,
                sequence=i,
            )
        )

    records = list(storage.read_from(offset=7))
    assert len(records) == 3
    assert records[0].sequence == 7


def test_continuous_runner_replay_via_output():
    sim = ReplaySimulator()
    storage = MemoryStorageAdapter("memory://", "runner_replay_stream")
    config = SimulatorConfig(
        business_type="replay",
        duration_seconds=0.0,
        events_per_second=1000.0,
        seed=1,
        checkpoint_interval_events=100,
        checkpoint_interval_seconds=3600.0,
        stream_id=storage.stream_id,
        storage_backend="memory",
    )
    sim.configure(config)
    sim.initialize(config.seed)
    stream = StringIO()
    output = NDJsonOutputAdapter(stream)
    runner = ContinuousRunner(config, sim, storage, output=output, realtime=False)
    import threading

    timer = threading.Timer(0.02, runner.request_shutdown)
    timer.start()
    count = runner.run()
    timer.join()

    assert count >= 1
    lines = stream.getvalue().strip().splitlines()
    assert len(lines) == count
    assert lines[0].startswith('{"event_id"')
