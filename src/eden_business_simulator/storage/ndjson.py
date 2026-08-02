"""Append-only NDJSON storage adapter."""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Iterator

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import StorageAdapter, StoredRecord


class NdjsonStorageAdapter(StorageAdapter):
    """Lightweight append-only storage to a newline-delimited JSON file.

    Each line is a JSON object containing persistence metadata and the
    embedded envelope.  This format is human-readable and easy to tail, but it
    does not support random access or compaction.  Snapshots and checkpoints
    are stored in a companion ``.meta.json`` file next to the log.
    """

    def __init__(self, uri: str, stream_id: str) -> None:
        super().__init__(uri, stream_id)
        self._lock = threading.RLock()
        self._path = Path(uri)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._meta_path = self._path.with_suffix(".meta.json")
        self._meta = self._load_meta()
        self._next_sequence = self.latest_sequence() + 1

    def _load_meta(self) -> dict[str, Any]:
        if not self._meta_path.exists():
            return {"stream_id": self.stream_id, "snapshots": {}, "checkpoints": {}}
        try:
            return json.loads(self._meta_path.read_text())
        except (json.JSONDecodeError, OSError):
            return {"stream_id": self.stream_id, "snapshots": {}, "checkpoints": {}}

    def _save_meta(self) -> None:
        self._meta_path.write_text(json.dumps(self._meta, default=str))

    def _read_line(self, line: str) -> StoredRecord | None:
        try:
            data = json.loads(line)
            return StoredRecord(
                offset=data["offset"],
                sequence=data["sequence"],
                stream_id=data["stream_id"],
                stored_at=data["stored_at"],
                envelope=EventEnvelope.model_validate(data["envelope"]),
            )
        except (json.JSONDecodeError, KeyError):
            return None

    def append(self, envelope: EventEnvelope) -> StoredRecord:
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            envelope.sequence = sequence
            envelope.stream_id = self.stream_id
            stored_at = self._now().isoformat()
            offset = self._path.stat().st_size if self._path.exists() else 0
            record = StoredRecord(
                offset=offset,
                sequence=sequence,
                stream_id=self.stream_id,
                stored_at=stored_at,
                envelope=envelope,
            )
            line = json.dumps(
                {
                    "offset": offset,
                    "sequence": sequence,
                    "stream_id": self.stream_id,
                    "stored_at": stored_at,
                    "envelope": envelope.model_dump(mode="json", exclude_none=True),
                },
                default=str,
            )
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")
                handle.flush()
            return record

    def read_from(
        self,
        offset: int = 0,
        limit: int | None = None,
        from_sequence: int | None = None,
    ) -> Iterator[StoredRecord]:
        with self._lock:
            if not self._path.exists():
                return
            emitted = 0
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = self._read_line(line)
                    if record is None:
                        continue
                    if record.offset < offset:
                        continue
                    if from_sequence is not None and record.sequence < from_sequence:
                        continue
                    yield record
                    emitted += 1
                    if limit is not None and emitted >= limit:
                        return

    def latest_offset(self) -> int:
        with self._lock:
            if not self._path.exists():
                return -1
            # The byte offset of the next appended line is the latest offset.
            return self._path.stat().st_size

    def latest_sequence(self) -> int:
        with self._lock:
            checkpoint = self.read_checkpoint()
            if checkpoint:
                return checkpoint["last_sequence"]
            if not self._path.exists():
                return -1
            last_sequence = -1
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = self._read_line(line)
                    if record is not None:
                        last_sequence = max(last_sequence, record.sequence)
            return last_sequence

    def write_snapshot(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._meta["snapshots"][key] = {
                "saved_at": self._now().isoformat(),
                "data": data,
            }
            self._save_meta()

    def read_snapshot(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            snapshot = self._meta["snapshots"].get(key)
            if snapshot is None:
                return None
            return snapshot.get("data")

    def write_checkpoint(self, last_sequence: int) -> None:
        with self._lock:
            self._meta["checkpoints"][self.stream_id] = {
                "last_sequence": last_sequence,
                "saved_at": self._now().isoformat(),
            }
            self._save_meta()

    def read_checkpoint(self) -> dict[str, Any] | None:
        with self._lock:
            return self._meta["checkpoints"].get(self.stream_id)

    def stream_ids(self) -> list[str]:
        with self._lock:
            ids: set[str] = set()
            if not self._path.exists():
                return []
            with self._path.open("r", encoding="utf-8") as handle:
                for line in handle:
                    if not line.strip():
                        continue
                    record = self._read_line(line)
                    if record is not None:
                        ids.add(record.stream_id)
            return sorted(ids)
