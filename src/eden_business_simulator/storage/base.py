"""Abstract storage adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterator

from eden_business_simulator.models import EventEnvelope


@dataclass
class StoredRecord:
    """An event envelope plus persistence metadata."""

    offset: int
    sequence: int
    stream_id: str
    stored_at: datetime
    envelope: EventEnvelope


# ----------------------------------------------------------------------
# Command records (Phase 5 — daemon command input)
# ----------------------------------------------------------------------
#
# A command is an operator's request for one management action on one
# run (stream). Commands are plain dicts with this shape::
#
#     {
#         "command_id":   "cmd_000001",   # per-run counter, see below
#         "run_id":       <stream id>,
#         "action_id":    <idempotency key; defaults to command_id>,
#         "action_type":  e.g. "place_supplier_order",
#         "params":       {...},
#         "requested_by": str | None,     # who asked (e.g. an Aleph run)
#         "approved_by":  str | None,     # Beth approval stamp (D9)
#         "status":       "pending" | "applied" | "rejected",
#         "submitted_at": <ISO string, supplied by the caller>,
#         "result":       dict | None,    # outcome payload once resolved
#         "resolved_at":  <ISO string> | None,
#     }
#
# Storage stays clock-free for commands: callers (CLI, runner) pass the
# timestamps. Command ids are a per-run counter derived from the stored
# rows (``cmd_`` + zero-padded count), never wall-clock or random, so
# replaying the stored command log re-derives the same ids.


def format_command_id(index: int) -> str:
    """Return the command id for the 1-based per-run submission index."""
    return f"cmd_{index:06d}"


def build_command_record(
    *,
    command_id: str,
    run_id: str,
    action_type: str,
    params: dict[str, Any],
    requested_by: str | None,
    approved_by: str | None,
    action_id: str | None,
    submitted_at: str,
) -> dict[str, Any]:
    """Assemble a fresh ``pending`` command record dict."""
    return {
        "command_id": command_id,
        "run_id": run_id,
        "action_id": action_id if action_id is not None else command_id,
        "action_type": action_type,
        "params": dict(params),
        "requested_by": requested_by,
        "approved_by": approved_by,
        "status": "pending",
        "submitted_at": submitted_at,
        "result": None,
        "resolved_at": None,
    }


class StorageAdapter(ABC):
    """Durable destination for an event stream with replay support."""

    def __init__(self, uri: str, stream_id: str) -> None:
        self.uri = uri
        self.stream_id = stream_id

    @abstractmethod
    def append(self, envelope: EventEnvelope) -> StoredRecord:
        """Persist an event and return its stored record.

        The adapter is responsible for assigning the monotonic ``sequence``
        before persistence so that replay is ordered and idempotent.
        """
        ...

    @abstractmethod
    def read_from(
        self,
        offset: int = 0,
        limit: int | None = None,
        from_sequence: int | None = None,
    ) -> Iterator[StoredRecord]:
        """Read stored records starting at ``offset`` (inclusive).

        If ``from_sequence`` is provided, only records whose ``sequence`` is
        greater than or equal to that value are yielded.  ``offset`` is the
        storage-level offset (rowid for SQLite, byte offset for NDJSON) and
        should not be confused with the logical sequence number.
        """
        ...

    @abstractmethod
    def latest_offset(self) -> int:
        """Return the highest persisted offset, or -1 if empty."""
        ...

    @abstractmethod
    def latest_sequence(self) -> int:
        """Return the highest persisted sequence, or -1 if empty."""
        ...

    @abstractmethod
    def write_snapshot(self, key: str, data: dict[str, Any]) -> None:
        """Persist a named snapshot document for this stream."""
        ...

    @abstractmethod
    def read_snapshot(self, key: str) -> dict[str, Any] | None:
        """Retrieve a named snapshot document, or ``None`` if absent."""
        ...

    @abstractmethod
    def write_checkpoint(self, last_sequence: int) -> None:
        """Persist the resume offset for this stream."""
        ...

    @abstractmethod
    def read_checkpoint(self) -> dict[str, Any] | None:
        """Retrieve the latest checkpoint for this stream, if any."""
        ...

    # ------------------------------------------------------------------
    # Command input (Phase 5)
    # ------------------------------------------------------------------
    #
    # ``run_id`` is the stream the commands belong to; adapters are
    # constructed per stream, so in practice ``run_id`` equals the
    # adapter's ``stream_id``. Each backend implements the same
    # semantics over its native store.

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
        """Store a new ``pending`` command and return its command id.

        ``submitted_at`` is an ISO timestamp supplied by the caller so
        storage stays clock-free; backends fall back to their own clock
        only when it is omitted.
        """
        raise NotImplementedError

    def pending_commands(self, run_id: str) -> list[dict[str, Any]]:
        """Return pending command records for a run, in submission order."""
        raise NotImplementedError

    def record_command_result(
        self,
        command_id: str,
        status: str,
        result_payload: dict[str, Any],
        resolved_at: str,
        run_id: str | None = None,
    ) -> None:
        """Resolve a command: set its status, result and resolved_at."""
        raise NotImplementedError

    def list_commands(
        self, run_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        """Return command records for a run in submission order,
        optionally filtered to one status."""
        raise NotImplementedError

    def applied_action_ids(self, run_id: str) -> set[str]:
        """Return action ids that must count as already applied for a run.

        The union of (a) action ids of commands resolved ``applied``
        and (b) action ids carried by stored ``action_applied`` events
        for the run — the latter covers actions applied by scripted or
        live runners in earlier processes, so idempotency survives a
        daemon restart.
        """
        raise NotImplementedError

    def stream_ids(self) -> list[str]:
        """Return the stream identifiers present in this storage.

        Adapters that do not support enumeration (e.g. in-memory) may return
        ``[self.stream_id]``.
        """
        return [self.stream_id]

    def close(self) -> None:
        """Release any resources held by the adapter."""
        pass

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
