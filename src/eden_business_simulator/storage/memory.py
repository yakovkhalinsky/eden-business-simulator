"""In-memory storage adapter for tests and ephemeral streams."""

from __future__ import annotations

from typing import Any, Iterator

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import StorageAdapter, StoredRecord


class MemoryStorageAdapter(StorageAdapter):
    """Volatile in-memory storage, useful mainly for unit tests."""

    def __init__(self, uri: str, stream_id: str) -> None:
        super().__init__(uri or "memory://", stream_id)
        self._records: list[StoredRecord] = []
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._checkpoint: dict[str, Any] | None = None

    def append(self, envelope: EventEnvelope) -> StoredRecord:
        sequence = len(self._records)
        envelope.sequence = sequence
        envelope.stream_id = self.stream_id
        record = StoredRecord(
            offset=sequence,
            sequence=sequence,
            stream_id=self.stream_id,
            stored_at=self._now(),
            envelope=envelope,
        )
        self._records.append(record)
        return record

    def read_from(
        self,
        offset: int = 0,
        limit: int | None = None,
    ) -> Iterator[StoredRecord]:
        records = self._records[offset:]
        if limit is not None:
            records = records[:limit]
        yield from records

    def latest_offset(self) -> int:
        return len(self._records) - 1

    def latest_sequence(self) -> int:
        return len(self._records) - 1

    def write_snapshot(self, key: str, data: dict[str, Any]) -> None:
        self._snapshots[key] = dict(data)

    def read_snapshot(self, key: str) -> dict[str, Any] | None:
        return self._snapshots.get(key)

    def write_checkpoint(self, last_sequence: int) -> None:
        self._checkpoint = {
            "last_sequence": last_sequence,
            "saved_at": self._now().isoformat(),
        }

    def read_checkpoint(self) -> dict[str, Any] | None:
        return self._checkpoint
