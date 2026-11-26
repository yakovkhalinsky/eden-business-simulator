"""In-memory storage adapter for tests and ephemeral streams."""

from __future__ import annotations

import copy
from typing import Any, Iterator

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import (
    StorageAdapter,
    StoredRecord,
    build_command_record,
    format_command_id,
)


class MemoryStorageAdapter(StorageAdapter):
    """Volatile in-memory storage, useful mainly for unit tests."""

    def __init__(self, uri: str, stream_id: str) -> None:
        super().__init__(uri or "memory://", stream_id)
        self._records: list[StoredRecord] = []
        self._snapshots: dict[str, dict[str, Any]] = {}
        self._checkpoint: dict[str, Any] | None = None
        self._commands: dict[str, list[dict[str, Any]]] = {}

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
        from_sequence: int | None = None,
    ) -> Iterator[StoredRecord]:
        records = self._records[offset:]
        if from_sequence is not None:
            records = [r for r in records if r.sequence >= from_sequence]
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

    # ------------------------------------------------------------------
    # Command input (Phase 5): plain dicts keyed by run.
    # ------------------------------------------------------------------

    def submit_command(
        self,
        run_id: str,
        action_type: str,
        params: dict[str, Any],
        requested_by: str | None = None,
        approved_by: str | None = None,
        action_id: str | None = None,
        submitted_at: str | None = None,
    ) -> str:
        commands = self._commands.setdefault(run_id, [])
        command_id = format_command_id(len(commands) + 1)
        commands.append(
            build_command_record(
                command_id=command_id,
                run_id=run_id,
                action_type=action_type,
                params=params,
                requested_by=requested_by,
                approved_by=approved_by,
                action_id=action_id,
                submitted_at=submitted_at or self._now().isoformat(),
            )
        )
        return command_id

    def pending_commands(self, run_id: str) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(record)
            for record in self._commands.get(run_id, [])
            if record["status"] == "pending"
        ]

    def record_command_result(
        self,
        command_id: str,
        status: str,
        result_payload: dict[str, Any],
        resolved_at: str,
        run_id: str | None = None,
    ) -> None:
        runs = [run_id] if run_id is not None else list(self._commands)
        for run in runs:
            for record in self._commands.get(run, []):
                if record["command_id"] == command_id:
                    record["status"] = status
                    record["result"] = copy.deepcopy(result_payload)
                    record["resolved_at"] = resolved_at
                    return
        raise KeyError(f"Unknown command_id: {command_id}")

    def list_commands(
        self, run_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        return [
            copy.deepcopy(record)
            for record in self._commands.get(run_id, [])
            if status is None or record["status"] == status
        ]

    def applied_action_ids(self, run_id: str) -> set[str]:
        ids = {
            record["action_id"]
            for record in self._commands.get(run_id, [])
            if record["status"] == "applied"
        }
        for stored in self._records:
            if stored.envelope.event_type == "action_applied":
                action_id = stored.envelope.payload.get("action_id")
                if action_id:
                    ids.add(action_id)
        return ids
