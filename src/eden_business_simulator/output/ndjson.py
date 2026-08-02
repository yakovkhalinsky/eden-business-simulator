"""Stdout NDJSON output adapter."""

from __future__ import annotations

import sys
from typing import TextIO

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.output.base import OutputAdapter


class NDJsonOutputAdapter(OutputAdapter):
    """Writes one JSON event per line to a text stream (stdout by default)."""

    def __init__(self, stream: TextIO | None = None) -> None:
        self.stream = sys.stdout if stream is None else stream

    def write(self, envelope: EventEnvelope) -> None:
        self.stream.write(envelope.to_json_line() + "\n")
        self.stream.flush()

    def close(self) -> None:
        try:
            self.stream.flush()
        except Exception:
            pass
