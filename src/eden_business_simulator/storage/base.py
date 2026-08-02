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
    ) -> Iterator[StoredRecord]:
        """Read stored records starting at ``offset`` (inclusive)."""
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

    def close(self) -> None:
        """Release any resources held by the adapter."""
        pass

    def _now(self) -> datetime:
        return datetime.now(timezone.utc)
