"""Restorable state S1: framework machinery for full-state snapshots.

Covers the RNG/Faker codec (bit-exact continuation), the datetime
codec, the versioned ``full_state`` envelope through all three storage
backends, the simulated-clock resume, the R2 legacy fallback warning,
and the memory backend's snapshot deep copy. Businesses' own
serialize/restore arrive in S2/S3; here a test double proves the
machinery end to end: a split run (checkpoint -> fresh instance ->
resume) produces exactly the events of an uninterrupted run.
"""

from __future__ import annotations

import json
import random
import warnings
from datetime import date, datetime, timezone
from typing import Any

import pytest
from faker import Faker

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.state_codec import (
    FullStateVersionError,
    decode_datetimes,
    decode_random_state,
    encode_datetimes,
    encode_random_state,
    faker_random,
)
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.memory import MemoryStorageAdapter
from eden_business_simulator.storage.ndjson import NdjsonStorageAdapter
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

PINNED_START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Codec unit tests
# ----------------------------------------------------------------------


def _json_round_trip(value: Any) -> Any:
    return json.loads(json.dumps(value))


def test_random_state_codec_is_bit_exact_over_json():
    rng = random.Random(1234)
    [rng.random() for _ in range(50)]  # advance well into the stream
    encoded = _json_round_trip(encode_random_state(rng.getstate()))

    twin = random.Random(999)  # different seed: only the state matters
    twin.setstate(decode_random_state(encoded))

    expected = [rng.random() for _ in range(100)]
    actual = [twin.random() for _ in range(100)]
    assert actual == expected
    # Also the gauss channel: draws after restoring must match too.
    assert [twin.gauss(0, 1) for _ in range(5)] == [
        rng.gauss(0, 1) for _ in range(5)
    ]


def test_faker_state_codec_is_bit_exact_over_json():
    faker = Faker()
    faker.seed_instance(7)
    [faker.name() for _ in range(10)]
    encoded = _json_round_trip(
        encode_random_state(faker_random(faker).getstate())
    )

    twin = Faker()
    twin.seed_instance(31337)
    faker_random(twin).setstate(decode_random_state(encoded))

    assert [twin.name() for _ in range(25)] == [
        faker.name() for _ in range(25)
    ]


def test_datetime_codec_round_trips_real_datetimes():
    state = {
        "when": datetime(2026, 3, 4, 9, 30, tzinfo=timezone.utc),
        "day": date(2026, 3, 4),
        "nested": [
            {"at": datetime(2026, 3, 4, 10, 0, tzinfo=timezone.utc)},
            "plain",
            42,
            None,
        ],
        "plain_dict": {"__eden_datetime__": "not a tag", "other": 1},
    }
    decoded = decode_datetimes(_json_round_trip(encode_datetimes(state)))
    assert decoded == state
    assert isinstance(decoded["when"], datetime)
    assert isinstance(decoded["day"], date)
    assert not isinstance(decoded["day"], datetime)
    assert isinstance(decoded["nested"][0]["at"], datetime)
    # A dict that merely contains the tag key among others is data,
    # not an encoded datetime, and must survive untouched.
    assert decoded["plain_dict"] == {"__eden_datetime__": "not a tag", "other": 1}


# ----------------------------------------------------------------------
# A full-state test double
# ----------------------------------------------------------------------


class FullStateSim(BusinessSimulator):
    """Double opting into full-state snapshots.

    Every event draws from the simulator RNG, Faker and the IdGenerator
    and appends an entity carrying a real datetime, so exact split-run
    continuation exercises every piece of S1 machinery at once.
    """

    business_type = "fullstate"

    def __init__(self) -> None:
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.count = 0
        self.entities: list[dict[str, Any]] = []

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.id_gen = IdGenerator(self.rng)
        self.count = 0
        self.entities = []

    def available_event_types(self) -> list[str]:
        return ["tick"]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        self.count += 1
        entity = {
            "id": self.id_gen.next("ent"),
            "created_at": clock.now,
            "on_day": clock.now.date(),
            "draw": self.rng.random(),
            "name": self.faker.name(),
        }
        self.entities.append(entity)
        return {
            "event_type": "tick",
            "payload": {
                "n": self.count,
                "entity_id": entity["id"],
                "created_at": entity["created_at"].isoformat(),
                "draw": entity["draw"],
                "name": entity["name"],
            },
        }

    def state_snapshot(self) -> dict[str, Any]:
        return {"count": self.count}

    def serialize_state(self) -> dict[str, Any]:
        return {"count": self.count, "entities": self.entities}

    def restore_state(self, state: dict[str, Any]) -> None:
        self.count = state["count"]
        self.entities = state["entities"]


def _make_sim() -> FullStateSim:
    sim = FullStateSim()
    config = SimulatorConfig(
        business_type="fullstate",
        duration_seconds=0.0,
        events_per_second=2.0,
        seed=42,
        stream_id="fs",
        storage_backend="memory",
    )
    sim.configure(config)
    sim.initialize(config.seed)
    return sim


def test_full_state_snapshot_is_json_safe_and_complete():
    sim = _make_sim()
    clock = Clock(start_time=PINNED_START, tick_seconds=0.5)
    for _ in range(5):
        sim.next_event(clock)
        clock.advance()
    full = sim.full_state_snapshot()
    assert full is not None
    assert full["format_version"] == 2
    # The whole section must survive a strict JSON round-trip (no
    # default=str crutches) with datetimes encoded, not stringified.
    _json_round_trip(full)
    entity = full["state"]["entities"][0]
    assert isinstance(entity["created_at"], dict)  # tagged, not a str
    assert full["id_gen_counters"] == {"ent": 5}
    assert "rng_state" in full and "faker_state" in full


def test_restore_full_state_rejects_unknown_format_version():
    sim = _make_sim()
    with pytest.raises(FullStateVersionError, match="999"):
        sim.restore_snapshot(
            {"full_state": {"format_version": 999, "state": {}}}
        )


# ----------------------------------------------------------------------
# Storage round-trips (all three backends)
# ----------------------------------------------------------------------


def _adapters(tmp_path):
    return {
        "memory": MemoryStorageAdapter("memory://", "fs_stream"),
        "sqlite": SqliteStorageAdapter(str(tmp_path / "fs.db"), "fs_stream"),
        "ndjson": NdjsonStorageAdapter(
            str(tmp_path / "fs.jsonl"), "fs_stream"
        ),
    }


@pytest.mark.parametrize("backend", ["memory", "sqlite", "ndjson"])
def test_full_state_round_trips_through_storage(tmp_path, backend):
    sim = _make_sim()
    clock = Clock(start_time=PINNED_START, tick_seconds=0.5)
    for _ in range(6):
        sim.next_event(clock)
        clock.advance()
    envelope = {
        "seed": 42,
        "business_type": "fullstate",
        "snapshot_format": 2,
        "data": {**sim.state_snapshot(), "full_state": sim.full_state_snapshot()},
    }
    expected_next = [sim.next_event(clock)["payload"] for _ in range(3)]

    adapter = _adapters(tmp_path)[backend]
    try:
        adapter.write_snapshot("latest", envelope)
        loaded = adapter.read_snapshot("latest")
    finally:
        adapter.close()
    assert loaded is not None

    resumed = _make_sim()  # fresh initialise, exactly like the daemon
    resumed.restore_snapshot(loaded["data"])
    # Datetimes came back as datetimes, not strings.
    assert isinstance(resumed.entities[0]["created_at"], datetime)
    assert isinstance(resumed.entities[0]["on_day"], date)
    assert resumed.count == 6
    # Mirror the original clock's position (six ticks in) so the
    # clock-derived payload fields line up; the runner-level clock
    # resume that makes this automatic is proven separately below.
    clock2 = Clock(start_time=PINNED_START, tick_seconds=0.5)
    for _ in range(6):
        clock2.advance()
    actual_next = [resumed.next_event(clock2)["payload"] for _ in range(3)]
    assert actual_next == expected_next


def test_memory_snapshot_is_deep_copied_on_write_and_read():
    adapter = MemoryStorageAdapter("memory://", "deep")
    original = {"data": {"entities": [{"n": 1}], "rng_state": [3, [1, 2], None]}}
    adapter.write_snapshot("latest", original)
    original["data"]["entities"].append({"n": 2})
    original["data"]["rng_state"][1].append(3)
    loaded = adapter.read_snapshot("latest")
    assert loaded == {"data": {"entities": [{"n": 1}], "rng_state": [3, [1, 2], None]}}
    # Mutating the read copy must not corrupt the stored snapshot.
    loaded["data"]["entities"].append({"n": 99})
    assert adapter.read_snapshot("latest")["data"]["entities"] == [{"n": 1}]


# ----------------------------------------------------------------------
# Runner-level resume: clock, legacy fallback, split-run equality
# ----------------------------------------------------------------------


def _runner_config(stream_id: str, max_events: int) -> SimulatorConfig:
    return SimulatorConfig(
        business_type="fullstate",
        duration_seconds=0.0,
        events_per_second=2.0,
        seed=42,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="memory",
        checkpoint_interval_events=3,
        checkpoint_interval_seconds=3600.0,
    )


def _run_split(storage, stream_id: str, first: int, second: int):
    """Run ``first`` events, then resume with a fresh sim for ``second``."""
    config = _runner_config(stream_id, first)
    sim = FullStateSim()
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.clock = Clock(start_time=PINNED_START, tick_seconds=0.5)
    runner.run()

    config2 = _runner_config(stream_id, second)
    sim2 = FullStateSim()
    sim2.configure(config2)
    sim2.initialize(config2.seed)
    runner2 = ContinuousRunner(config2, sim2, storage, realtime=False)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        runner2.run()
    return sim2, runner2, caught


def _stored_events(storage):
    return [
        (r.envelope.event_type, r.envelope.payload, r.envelope.timestamp)
        for r in storage.read_from()
    ]


def test_split_run_matches_uninterrupted_run():
    whole_storage = MemoryStorageAdapter("memory://", "whole")
    config = _runner_config("whole", 10)
    sim = FullStateSim()
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(config, sim, storage=whole_storage, realtime=False)
    runner.clock = Clock(start_time=PINNED_START, tick_seconds=0.5)
    runner.run()
    whole_events = _stored_events(whole_storage)
    whole_position = sim.count, sim.entities

    split_storage = MemoryStorageAdapter("memory://", "split")
    sim2, runner2, caught = _run_split(split_storage, "split", 6, 4)
    split_events = _stored_events(split_storage)

    # No legacy warning on a current-format resume.
    assert runner2.resume_warnings == []
    assert not [w for w in caught if issubclass(w.category, UserWarning)]
    # Stream (types, payloads, timestamps) and final state continue
    # exactly across the checkpoint boundary.
    assert split_events == whole_events
    assert (sim2.count, sim2.entities) == whole_position
    # The clock itself continued: the resumed run's first event is
    # stamped where the uninterrupted run's seventh event is.
    assert split_events[6][2] == whole_events[6][2]


def test_clock_resumes_from_checkpoint():
    storage = MemoryStorageAdapter("memory://", "clocked")
    _, runner2, _ = _run_split(storage, "clocked", 6, 1)
    # Six ticks of 0.5s elapsed in the first process; the resumed
    # runner's clock starts from the checkpoint, not wall-clock now.
    assert runner2.clock.start_time == PINNED_START
    assert runner2.clock.now >= PINNED_START
    assert (runner2.clock.now - PINNED_START).total_seconds() >= 3.0


class _LegacyDouble(BusinessSimulator):
    """Summary-only double with a real legacy restore() hook."""

    business_type = "legacy"

    def __init__(self) -> None:
        self.count = 0

    def initialize(self, seed: int) -> None:
        self.count = 0

    def available_event_types(self) -> list[str]:
        return ["tick"]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        self.count += 1
        return {"event_type": "tick", "payload": {"n": self.count}}

    def state_snapshot(self) -> dict[str, Any]:
        return {"count": self.count}

    def restore(self, snapshot: dict[str, Any]) -> None:
        self.count = snapshot.get("count", 0)


def _legacy_runner(storage) -> ContinuousRunner:
    config = SimulatorConfig(
        business_type="legacy",
        duration_seconds=0.0,
        events_per_second=1000.0,
        seed=1,
        stream_id=storage.stream_id,
        storage_backend="memory",
    )
    sim = _LegacyDouble()
    sim.configure(config)
    sim.initialize(config.seed)
    return ContinuousRunner(config, sim, storage, realtime=False)


def test_legacy_snapshot_resumes_with_loud_warning():
    storage = MemoryStorageAdapter("memory://", "legacy_stream")
    # A pre-S1 snapshot: no snapshot_format marker, summary data only.
    storage.write_snapshot(
        "latest", {"seed": 1, "business_type": "legacy", "data": {"count": 5}}
    )
    storage.write_checkpoint(4)
    runner = _legacy_runner(storage)
    with pytest.warns(UserWarning, match="legacy summary-only snapshot"):
        runner._load_resume_state()
    assert runner.resume_warnings  # collected for the CLI surface
    assert "legacy" in runner.resume_warnings[0]
    # Behaviour is exactly the old behaviour: sequence and the legacy
    # restore() hook both applied.
    assert runner._next_sequence == 5
    assert runner.simulator.count == 5


def test_future_snapshot_format_is_a_clear_error():
    storage = MemoryStorageAdapter("memory://", "future_stream")
    storage.write_snapshot(
        "latest",
        {
            "seed": 1,
            "business_type": "legacy",
            "snapshot_format": 999,
            "data": {"count": 5},
        },
    )
    storage.write_checkpoint(4)
    runner = _legacy_runner(storage)
    with pytest.raises(ValueError, match="snapshot_format 999"):
        runner._load_resume_state()
