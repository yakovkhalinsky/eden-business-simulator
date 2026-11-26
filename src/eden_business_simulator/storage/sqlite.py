"""SQLite storage adapter with WAL mode."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterator

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.storage.base import (
    StorageAdapter,
    StoredRecord,
    build_command_record,
    format_command_id,
)


class SqliteStorageAdapter(StorageAdapter):
    """Default durable storage using a local SQLite database.

    The schema is intentionally simple: one append-only ``event_log`` table,
    one ``snapshots`` table for named state blobs, and one ``checkpoints``
    table per stream for resume offsets.  WAL mode is enabled so that readers
    do not block writers and SIGTERM can leave the journal in a recoverable
    state.
    """

    def __init__(self, uri: str, stream_id: str) -> None:
        super().__init__(uri, stream_id)
        self._lock = threading.RLock()
        self._connection = sqlite3.connect(str(uri), check_same_thread=False)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA synchronous=NORMAL")
        self._ensure_schema()
        self._next_sequence = self.latest_sequence() + 1

    def _ensure_schema(self) -> None:
        with self._lock:
            self._connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS event_log(
                    offset INTEGER PRIMARY KEY AUTOINCREMENT,
                    sequence INTEGER NOT NULL,
                    stream_id TEXT NOT NULL,
                    stored_at TEXT NOT NULL,
                    envelope_json TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_event_log_stream_sequence
                    ON event_log(stream_id, sequence);

                CREATE TABLE IF NOT EXISTS snapshots(
                    stream_id TEXT NOT NULL,
                    key TEXT NOT NULL,
                    saved_at TEXT NOT NULL,
                    data_json TEXT NOT NULL,
                    PRIMARY KEY(stream_id, key)
                );

                CREATE TABLE IF NOT EXISTS checkpoints(
                    stream_id TEXT PRIMARY KEY,
                    last_sequence INTEGER NOT NULL,
                    saved_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS commands(
                    command_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    action_id TEXT NOT NULL,
                    action_type TEXT NOT NULL,
                    params_json TEXT NOT NULL,
                    requested_by TEXT,
                    approved_by TEXT,
                    status TEXT NOT NULL DEFAULT 'pending',
                    submitted_at TEXT NOT NULL,
                    result_json TEXT,
                    resolved_at TEXT,
                    PRIMARY KEY(run_id, command_id)
                );
                CREATE INDEX IF NOT EXISTS idx_commands_run_status
                    ON commands(run_id, status);
                """
            )
            self._connection.commit()

    def _record_from_row(self, row: sqlite3.Row) -> StoredRecord:
        envelope = EventEnvelope.model_validate_json(row["envelope_json"])
        return StoredRecord(
            offset=row["offset"],
            sequence=row["sequence"],
            stream_id=row["stream_id"],
            stored_at=row["stored_at"],
            envelope=envelope,
        )

    def append(self, envelope: EventEnvelope) -> StoredRecord:
        with self._lock:
            sequence = self._next_sequence
            self._next_sequence += 1
            envelope.sequence = sequence
            envelope.stream_id = self.stream_id
            stored_at = self._now().isoformat()
            cursor = self._connection.execute(
                """
                INSERT INTO event_log(sequence, stream_id, stored_at, envelope_json)
                VALUES (?, ?, ?, ?)
                """,
                (
                    sequence,
                    self.stream_id,
                    stored_at,
                    envelope.model_dump_json(exclude_none=True),
                ),
            )
            self._connection.commit()
            return StoredRecord(
                offset=cursor.lastrowid or sequence,
                sequence=sequence,
                stream_id=self.stream_id,
                stored_at=stored_at,
                envelope=envelope,
            )

    def read_from(
        self,
        offset: int = 0,
        limit: int | None = None,
        from_sequence: int | None = None,
    ) -> Iterator[StoredRecord]:
        with self._lock:
            sql = """
                SELECT offset, sequence, stream_id, stored_at, envelope_json
                FROM event_log
                WHERE stream_id = ? AND offset >= ?
            """
            params: list[Any] = [self.stream_id, offset]
            if from_sequence is not None:
                sql += " AND sequence >= ?"
                params.append(from_sequence)
            sql += " ORDER BY sequence"
            if limit is not None:
                sql += " LIMIT ?"
                params.append(limit)
            cursor = self._connection.execute(sql, params)
            for row in cursor:
                yield self._record_from_row(row)

    def latest_offset(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(offset) AS max_offset FROM event_log WHERE stream_id = ?",
                (self.stream_id,),
            ).fetchone()
            return row[0] if row and row[0] is not None else -1

    def latest_sequence(self) -> int:
        with self._lock:
            row = self._connection.execute(
                "SELECT MAX(sequence) AS max_sequence FROM event_log WHERE stream_id = ?",
                (self.stream_id,),
            ).fetchone()
            return row[0] if row and row[0] is not None else -1

    def write_snapshot(self, key: str, data: dict[str, Any]) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO snapshots(stream_id, key, saved_at, data_json)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(stream_id, key) DO UPDATE SET
                    saved_at=excluded.saved_at,
                    data_json=excluded.data_json
                """,
                (
                    self.stream_id,
                    key,
                    self._now().isoformat(),
                    json.dumps(data, default=str),
                ),
            )
            self._connection.commit()

    def read_snapshot(self, key: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT data_json FROM snapshots WHERE stream_id = ? AND key = ?",
                (self.stream_id, key),
            ).fetchone()
            if row is None:
                return None
            return json.loads(row[0])

    def write_checkpoint(self, last_sequence: int) -> None:
        with self._lock:
            self._connection.execute(
                """
                INSERT INTO checkpoints(stream_id, last_sequence, saved_at)
                VALUES (?, ?, ?)
                ON CONFLICT(stream_id) DO UPDATE SET
                    last_sequence=excluded.last_sequence,
                    saved_at=excluded.saved_at
                """,
                (self.stream_id, last_sequence, self._now().isoformat()),
            )
            self._connection.commit()

    def read_checkpoint(self) -> dict[str, Any] | None:
        with self._lock:
            row = self._connection.execute(
                "SELECT last_sequence, saved_at FROM checkpoints WHERE stream_id = ?",
                (self.stream_id,),
            ).fetchone()
            if row is None:
                return None
            return {"last_sequence": row[0], "saved_at": row[1]}

    # ------------------------------------------------------------------
    # Command input (Phase 5): a `commands` table in the same database,
    # so a separate CLI process can submit while a daemon polls.
    # ------------------------------------------------------------------

    def _command_from_row(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "command_id": row["command_id"],
            "run_id": row["run_id"],
            "action_id": row["action_id"],
            "action_type": row["action_type"],
            "params": json.loads(row["params_json"]),
            "requested_by": row["requested_by"],
            "approved_by": row["approved_by"],
            "status": row["status"],
            "submitted_at": row["submitted_at"],
            "result": (
                json.loads(row["result_json"])
                if row["result_json"] is not None
                else None
            ),
            "resolved_at": row["resolved_at"],
        }

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
        with self._lock:
            count = self._connection.execute(
                "SELECT COUNT(*) FROM commands WHERE run_id = ?", (run_id,)
            ).fetchone()[0]
            record = build_command_record(
                command_id=format_command_id(count + 1),
                run_id=run_id,
                action_type=action_type,
                params=params,
                requested_by=requested_by,
                approved_by=approved_by,
                action_id=action_id,
                submitted_at=submitted_at or self._now().isoformat(),
            )
            self._connection.execute(
                """
                INSERT INTO commands(
                    command_id, run_id, action_id, action_type, params_json,
                    requested_by, approved_by, status, submitted_at,
                    result_json, resolved_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["command_id"],
                    record["run_id"],
                    record["action_id"],
                    record["action_type"],
                    json.dumps(record["params"], default=str),
                    record["requested_by"],
                    record["approved_by"],
                    record["status"],
                    record["submitted_at"],
                    None,
                    None,
                ),
            )
            self._connection.commit()
            return record["command_id"]

    def _select_commands(
        self, run_id: str, status: str | None
    ) -> list[dict[str, Any]]:
        sql = "SELECT * FROM commands WHERE run_id = ?"
        params: list[Any] = [run_id]
        if status is not None:
            sql += " AND status = ?"
            params.append(status)
        # Submission order is the per-run counter embedded in the id's
        # row insertion order; rowid is monotonic for inserts.
        sql += " ORDER BY rowid"
        with self._lock:
            cursor = self._connection.execute(sql, params)
            return [self._command_from_row(row) for row in cursor]

    def pending_commands(self, run_id: str) -> list[dict[str, Any]]:
        return self._select_commands(run_id, "pending")

    def list_commands(
        self, run_id: str, status: str | None = None
    ) -> list[dict[str, Any]]:
        return self._select_commands(run_id, status)

    def record_command_result(
        self,
        command_id: str,
        status: str,
        result_payload: dict[str, Any],
        resolved_at: str,
        run_id: str | None = None,
    ) -> None:
        with self._lock:
            sql = (
                "UPDATE commands SET status = ?, result_json = ?,"
                " resolved_at = ? WHERE command_id = ?"
            )
            params: list[Any] = [
                status,
                json.dumps(result_payload, default=str),
                resolved_at,
                command_id,
            ]
            if run_id is not None:
                sql += " AND run_id = ?"
                params.append(run_id)
            cursor = self._connection.execute(sql, params)
            self._connection.commit()
            if cursor.rowcount == 0:
                raise KeyError(f"Unknown command_id: {command_id}")

    def applied_action_ids(self, run_id: str) -> set[str]:
        with self._lock:
            ids = {
                row[0]
                for row in self._connection.execute(
                    "SELECT action_id FROM commands"
                    " WHERE run_id = ? AND status = 'applied'",
                    (run_id,),
                )
            }
            # Stored action_applied events cover actions applied by
            # scripted/live runners in earlier processes. The event type
            # lives inside the envelope JSON, so filter in Python.
            for row in self._connection.execute(
                "SELECT envelope_json FROM event_log WHERE stream_id = ?",
                (run_id,),
            ):
                envelope = json.loads(row[0])
                if envelope.get("event_type") == "action_applied":
                    action_id = envelope.get("payload", {}).get("action_id")
                    if action_id:
                        ids.add(action_id)
            return ids

    def stream_status(self) -> dict[str, Any]:
        with self._lock:
            total = self._connection.execute(
                "SELECT COUNT(*) FROM event_log WHERE stream_id = ?",
                (self.stream_id,),
            ).fetchone()[0]
            checkpoint = self.read_checkpoint()
            return {
                "stream_id": self.stream_id,
                "latest_offset": self.latest_offset(),
                "latest_sequence": self.latest_sequence(),
                "event_count": total,
                "checkpoint": checkpoint,
            }

    def stream_ids(self) -> list[str]:
        with self._lock:
            cursor = self._connection.execute(
                "SELECT DISTINCT stream_id FROM event_log ORDER BY stream_id"
            )
            return [row[0] for row in cursor]

    def close(self) -> None:
        with self._lock:
            try:
                self._connection.commit()
            finally:
                self._connection.close()
