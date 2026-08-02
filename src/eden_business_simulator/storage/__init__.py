"""Pluggable storage adapters for durable event streams."""

from __future__ import annotations

from eden_business_simulator.storage.base import StorageAdapter, StoredRecord
from eden_business_simulator.storage.memory import MemoryStorageAdapter
from eden_business_simulator.storage.ndjson import NdjsonStorageAdapter
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

__all__ = [
    "StorageAdapter",
    "StoredRecord",
    "MemoryStorageAdapter",
    "NdjsonStorageAdapter",
    "SqliteStorageAdapter",
    "load_storage_adapter",
]


def load_storage_adapter(backend: str, uri: str, stream_id: str) -> StorageAdapter:
    """Factory for supported storage backends."""
    if backend == "sqlite":
        return SqliteStorageAdapter(uri, stream_id)
    if backend == "ndjson":
        return NdjsonStorageAdapter(uri, stream_id)
    if backend == "memory":
        return MemoryStorageAdapter(stream_id)
    raise ValueError(f"Unsupported storage backend: {backend}")
