"""Output adapter contract."""

from __future__ import annotations

from abc import ABC, abstractmethod

from eden_business_simulator.models import EventEnvelope


class OutputAdapter(ABC):
    """Destination for the event stream."""

    @abstractmethod
    def write(self, envelope: EventEnvelope) -> None:
        """Emit a single event envelope."""
        ...

    def close(self) -> None:
        """Release any resources held by the adapter."""
        pass
