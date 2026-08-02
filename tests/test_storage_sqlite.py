"""Tests for SQLite storage adapter."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import pytest

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter


@pytest.fixture
def tmp_sqlite(tmp_path: Path):
    uri = tmp_path / "test.db"
    adapter = SqliteStorageAdapter(str(uri), "test_stream")
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


def test_append_assigns_sequence(tmp_sqlite: SqliteStorageAdapter):
    envelope = make_envelope("order_placed")
    record = tmp_sqlite.append(envelope)
    assert record.sequence == 0
    assert record.stream_id == "test_stream"
    assert envelope.sequence == 0


def test_latest_offset_and_sequence(tmp_sqlite: SqliteStorageAdapter):
    assert tmp_sqlite.latest_offset() == -1
    assert tmp_sqlite.latest_sequence() == -1
    for i in range(3):
        tmp_sqlite.append(make_envelope("tick", seq=i))
    assert tmp_sqlite.latest_offset() >= 2
    assert tmp_sqlite.latest_sequence() == 2


def test_read_from_offset(tmp_sqlite: SqliteStorageAdapter):
    for i in range(5):
        tmp_sqlite.append(make_envelope("tick", seq=i))
    # SQLite offsets are 1-based (AUTOINCREMENT).
    records = list(tmp_sqlite.read_from(offset=3))
    assert len(records) == 3
    assert records[0].sequence == 2


def test_read_from_limit(tmp_sqlite: SqliteStorageAdapter):
    for i in range(5):
        tmp_sqlite.append(make_envelope("tick", seq=i))
    records = list(tmp_sqlite.read_from(offset=0, limit=2))
    assert len(records) == 2


def test_snapshot_round_trip(tmp_sqlite: SqliteStorageAdapter):
    tmp_sqlite.write_snapshot("latest", {"seed": 42, "count": 7})
    loaded = tmp_sqlite.read_snapshot("latest")
    assert loaded == {"seed": 42, "count": 7}


def test_checkpoint_round_trip(tmp_sqlite: SqliteStorageAdapter):
    tmp_sqlite.write_checkpoint(99)
    checkpoint = tmp_sqlite.read_checkpoint()
    assert checkpoint is not None
    assert checkpoint["last_sequence"] == 99


def test_stream_status(tmp_sqlite: SqliteStorageAdapter):
    for i in range(4):
        tmp_sqlite.append(make_envelope("tick", seq=i))
    tmp_sqlite.write_checkpoint(3)
    status = tmp_sqlite.stream_status()
    assert status["stream_id"] == "test_stream"
    assert status["event_count"] == 4
    assert status["latest_sequence"] == 3
    assert status["checkpoint"]["last_sequence"] == 3


def test_multiple_streams_are_isolated(tmp_sqlite: SqliteStorageAdapter):
    tmp_sqlite.append(make_envelope("a", seq=0))
    other = SqliteStorageAdapter(tmp_sqlite.uri, "other_stream")
    try:
        other.append(make_envelope("b", seq=0))
        assert other.latest_sequence() == 0
        assert tmp_sqlite.latest_sequence() == 0
        assert len(list(tmp_sqlite.read_from(offset=0))) == 1
        assert len(list(other.read_from(offset=0))) == 1
    finally:
        other.close()
