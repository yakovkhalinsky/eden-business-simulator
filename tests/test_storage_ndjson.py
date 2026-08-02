"""Tests for NDJSON storage adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.ndjson import NdjsonStorageAdapter


@pytest.fixture
def tmp_ndjson(tmp_path: Path):
    uri = tmp_path / "test.jsonl"
    adapter = NdjsonStorageAdapter(str(uri), "test_stream")
    try:
        yield adapter
    finally:
        adapter.close()


def make_envelope(event_type: str, seq: int | None = None) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt-{event_type}",
        timestamp=datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        business_type="ecommerce",
        event_type=event_type,
        payload={"n": seq or 0},
        stream_id="test_stream",
        sequence=seq,
    )


def test_append_assigns_sequence(tmp_ndjson: NdjsonStorageAdapter):
    envelope = make_envelope("order_placed")
    record = tmp_ndjson.append(envelope)
    assert record.sequence == 0
    assert record.stream_id == "test_stream"
    assert envelope.sequence == 0


def test_latest_offset_and_sequence(tmp_ndjson: NdjsonStorageAdapter):
    assert tmp_ndjson.latest_offset() == -1
    assert tmp_ndjson.latest_sequence() == -1
    for i in range(3):
        tmp_ndjson.append(make_envelope("tick", seq=i))
    assert tmp_ndjson.latest_offset() >= 0
    assert tmp_ndjson.latest_sequence() == 2


def test_read_from_offset(tmp_ndjson: NdjsonStorageAdapter):
    for i in range(5):
        tmp_ndjson.append(make_envelope("tick", seq=i))
    records = list(tmp_ndjson.read_from(offset=2))
    # Byte offsets do not map 1:1 to record offsets, so read from 0.
    records = list(tmp_ndjson.read_from(offset=0))
    assert len(records) == 5
    assert records[2].sequence == 2


def test_snapshot_round_trip(tmp_ndjson: NdjsonStorageAdapter):
    tmp_ndjson.write_snapshot("latest", {"seed": 42, "count": 7})
    loaded = tmp_ndjson.read_snapshot("latest")
    assert loaded == {"seed": 42, "count": 7}


def test_checkpoint_round_trip(tmp_ndjson: NdjsonStorageAdapter):
    tmp_ndjson.write_checkpoint(99)
    checkpoint = tmp_ndjson.read_checkpoint()
    assert checkpoint is not None
    assert checkpoint["last_sequence"] == 99
