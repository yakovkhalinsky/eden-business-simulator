"""Abstract base class for business simulators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock


class BusinessSimulator(ABC):
    """One business domain that can produce a realistic event stream."""

    business_type: str = "abstract"

    def configure(self, config: SimulatorConfig) -> None:
        """Apply runtime configuration before initialization."""
        self.config = config

    @abstractmethod
    def initialize(self, seed: int) -> None:
        """Seed all randomness and create the initial deterministic state."""
        ...

    @abstractmethod
    def available_event_types(self) -> list[str]:
        """Return the event types this simulator may emit."""
        ...

    @abstractmethod
    def next_event(self, clock: Clock) -> dict[str, Any]:
        """Generate the next business event for the current clock time.

        Returns a dict with at least ``event_type`` and ``payload`` keys.
        """
        ...

    @abstractmethod
    def state_snapshot(self) -> dict[str, Any]:
        """Return a serializable view of the simulator's current state."""
        ...

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore simulator state from a durable snapshot.

        Simulators that maintain internal entity state can override this hook
        to resume continuous generation from a checkpoint.  The default is a
        no-op so that stateless simulators can still be resumed deterministically
        from the original seed and the last checkpoint sequence.
        """
        pass
