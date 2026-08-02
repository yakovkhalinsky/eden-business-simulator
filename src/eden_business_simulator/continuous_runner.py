"""Long-running continuous event generation with durable persistence."""

from __future__ import annotations

import signal
import threading
import time
from typing import Any

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock, EventEnvelope
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.storage.base import StorageAdapter


class ContinuousRunner:
    """Runs a simulator indefinitely, persisting every event and checkpointing.

    The runner paces event generation against wall-clock time by default.  On
    SIGTERM/SIGINT it writes a final checkpoint, flushes the storage adapter,
    and exits cleanly.  On startup it reads the latest checkpoint and simulator
    snapshot for its ``stream_id``; if a snapshot exists it calls the
    simulator's ``restore`` hook so stateful domains can resume correctly.
    """

    SNAPSHOT_KEY = "latest"

    def __init__(
        self,
        config: SimulatorConfig,
        simulator: BusinessSimulator,
        storage: StorageAdapter,
        output: OutputAdapter | None = None,
        realtime: bool = True,
    ) -> None:
        self.config = config
        self.simulator = simulator
        self.storage = storage
        self.output = output
        self.realtime = realtime
        self.clock = Clock(tick_seconds=1.0 / config.events_per_second)
        self._shutdown_event = threading.Event()
        self._events_emitted = 0
        self._next_sequence = 0
        self._last_checkpoint_time = 0.0
        self._max_events = getattr(config, "max_events", 0)
        self._wall_start: float | None = None
        self._original_sigterm: Any = None
        self._original_sigint: Any = None

    def _request_shutdown(self, signum: int, frame: Any) -> None:
        self._shutdown_event.set()

    def _install_signal_handlers(self) -> None:
        try:
            self._original_sigterm = signal.getsignal(signal.SIGTERM)
            self._original_sigint = signal.getsignal(signal.SIGINT)
            signal.signal(signal.SIGTERM, self._request_shutdown)
            signal.signal(signal.SIGINT, self._request_shutdown)
        except (ValueError, OSError):
            # May not be available on all platforms (e.g., Windows).
            pass

    def _restore_signal_handlers(self) -> None:
        try:
            if self._original_sigterm is not None:
                signal.signal(signal.SIGTERM, self._original_sigterm)
            if self._original_sigint is not None:
                signal.signal(signal.SIGINT, self._original_sigint)
        except (ValueError, OSError):
            pass

    def _load_resume_state(self) -> None:
        checkpoint = self.storage.read_checkpoint()
        if checkpoint is not None:
            self._next_sequence = checkpoint["last_sequence"] + 1
            snapshot = self.storage.read_snapshot(self.SNAPSHOT_KEY)
            if snapshot is not None:
                self.simulator.restore(snapshot.get("data", {}))
        else:
            self._next_sequence = 0

    def _should_checkpoint(self) -> bool:
        if self._events_emitted == 0:
            return False
        if self._events_emitted % self.config.checkpoint_interval_events == 0:
            return True
        elapsed = time.monotonic() - self._last_checkpoint_time
        if elapsed >= self.config.checkpoint_interval_seconds:
            return True
        return False

    def _write_checkpoint(self) -> None:
        last_sequence = self._next_sequence - 1
        snapshot_data = {
            "seed": self.config.seed,
            "business_type": self.config.business_type,
            "data": self.simulator.state_snapshot(),
        }
        self.storage.write_snapshot(self.SNAPSHOT_KEY, snapshot_data)
        self.storage.write_checkpoint(last_sequence)
        self._last_checkpoint_time = time.monotonic()

    def _pace(self) -> None:
        if not self.realtime or self._wall_start is None:
            return
        expected_wall = (
            self._wall_start
            + (self.clock.now - self.clock.start_time).total_seconds()
        )
        sleep_for = expected_wall - time.monotonic()
        if sleep_for > 0:
            time.sleep(sleep_for)

    def run(self) -> int:
        """Run the continuous loop until shutdown or error."""
        self._install_signal_handlers()
        self._load_resume_state()
        self._wall_start = time.monotonic()
        self._last_checkpoint_time = self._wall_start

        try:
            while not self._shutdown_event.is_set():
                if self._max_events and self._events_emitted >= self._max_events:
                    break
                event = self.simulator.next_event(self.clock)
                envelope = EventEnvelope.from_event(
                    business_type=self.config.business_type,
                    event_type=event["event_type"],
                    payload=event.get("payload", {}),
                    timestamp=self.clock.now,
                    stream_id=self.config.stream_id,
                    sequence=self._next_sequence,
                )
                self.storage.append(envelope)
                if self.output is not None:
                    self.output.write(envelope)

                self._events_emitted += 1
                self._next_sequence += 1

                if self._should_checkpoint():
                    self._write_checkpoint()

                self.clock.advance()
                self._pace()
        finally:
            if self._events_emitted > 0:
                self._write_checkpoint()
            self._restore_signal_handlers()
            if self.output is not None:
                try:
                    self.output.close()
                except Exception:
                    pass
            self.storage.close()
        return self._events_emitted

    def request_shutdown(self) -> None:
        """Request a clean shutdown (useful in tests)."""
        self._shutdown_event.set()
