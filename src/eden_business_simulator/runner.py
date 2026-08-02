"""Runner that drives a single simulator and emits events through adapters."""

from __future__ import annotations

import time
from typing import Any

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock, EventEnvelope
from eden_business_simulator.output.base import OutputAdapter


class Runner:
    """Advances one simulator, wraps events in envelopes, and writes them out."""

    def __init__(
        self,
        config: SimulatorConfig,
        simulator: BusinessSimulator,
        adapter: OutputAdapter,
        realtime: bool = True,
    ) -> None:
        self.config = config
        self.simulator = simulator
        self.adapter = adapter
        self.realtime = realtime
        self.clock = Clock(tick_seconds=1.0 / config.events_per_second)
        self.events_emitted = 0
        self._wall_start: float | None = None

    def should_continue(self) -> bool:
        if self.config.max_events and self.events_emitted >= self.config.max_events:
            return False
        if self.clock.elapsed_seconds() >= self.config.duration_seconds:
            return False
        return True

    def run(self) -> int:
        """Run the simulator loop and return the number of emitted events."""
        self._wall_start = time.monotonic()
        try:
            while self.should_continue():
                event = self.simulator.next_event(self.clock)
                envelope = EventEnvelope.from_event(
                    business_type=self.config.business_type,
                    event_type=event["event_type"],
                    payload=event.get("payload", {}),
                    timestamp=self.clock.now,
                )
                self.adapter.write(envelope)
                self.events_emitted += 1
                self.clock.advance()
                if self.realtime:
                    self._pace()
        finally:
            self.adapter.close()
        return self.events_emitted

    def _pace(self) -> None:
        if self._wall_start is None:
            return
        expected_wall = (
            self._wall_start + (self.clock.now - self.clock.start_time).total_seconds()
        )
        sleep_for = expected_wall - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)
