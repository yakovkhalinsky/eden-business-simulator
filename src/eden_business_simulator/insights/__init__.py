"""Public API for the insights workflow."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from eden_business_simulator.insights.analyzer import StreamAnalyzer
from eden_business_simulator.insights.artifacts import write_artifacts
from eden_business_simulator.insights.models import InsightsReport
from eden_business_simulator.storage.base import StorageAdapter


def analyze_stream(
    storage_adapter: StorageAdapter,
    *,
    window_minutes: int = 1,
    storage_backend: str = "unknown",
    storage_uri: str = "",
) -> InsightsReport:
    """Analyze a persisted stream and return a structured report.

    The caller is responsible for closing ``storage_adapter``.
    """
    records = storage_adapter.read_from()
    analyzer = StreamAnalyzer(records, window_minutes=window_minutes)
    return analyzer.analyze(storage_backend, storage_uri)


__all__ = [
    "analyze_stream",
    "write_artifacts",
    "InsightsReport",
]
