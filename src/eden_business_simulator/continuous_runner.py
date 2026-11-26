"""Long-running continuous event generation with durable persistence."""

from __future__ import annotations

import logging
import queue
import signal
import threading
import time
import warnings
from datetime import datetime
from typing import Any

from eden_business_simulator.businesses.base import (
    ActionRequest,
    ActionResult,
    BusinessSimulator,
    action_audit_event,
)
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.framework.kpi import PeriodTracker, tracker_for
from eden_business_simulator.framework.state_codec import (
    SNAPSHOT_FORMAT_VERSION,
)
from eden_business_simulator.models import Clock, EventEnvelope
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.storage.base import StorageAdapter

logger = logging.getLogger(__name__)


class ContinuousRunner:
    """Runs a simulator indefinitely, persisting every event and checkpointing.

    The runner paces event generation against wall-clock time by default.  On
    SIGTERM/SIGINT it writes a final checkpoint, flushes the storage adapter,
    and exits cleanly.  On startup it reads the latest checkpoint and simulator
    snapshot for its ``stream_id``; if a snapshot exists it calls the
    simulator's ``restore`` hook so stateful domains can resume correctly.
    """

    SNAPSHOT_KEY = "latest"
    POSITION_KEY = "position"

    def __init__(
        self,
        config: SimulatorConfig,
        simulator: BusinessSimulator,
        storage: StorageAdapter,
        output: OutputAdapter | None = None,
        realtime: bool = True,
        actions: list[tuple[int, ActionRequest]] | None = None,
        command_polling: bool = True,
    ) -> None:
        self.config = config
        self.simulator = simulator
        self.storage = storage
        self.output = output
        self.realtime = realtime
        # Phase 5: poll the storage command table between ticks and
        # apply pending commands through the same path as submit_action.
        # The run id is the stream id; polling is a no-op when the
        # storage holds no commands, and can be disabled outright for
        # runners that should never consume external commands.
        self.command_polling = command_polling
        self.run_id = config.stream_id or storage.stream_id
        # Restorable state (S1): loud warnings raised while loading
        # resume state (e.g. a legacy summary-only snapshot, R2) are
        # collected here as well as warned/logged, so callers (the
        # daemon CLI) can surface them to the operator.
        self.resume_warnings: list[str] = []
        self.clock = Clock(tick_seconds=1.0 / config.events_per_second)
        self._shutdown_event = threading.Event()
        self._events_emitted = 0
        self._next_sequence = 0
        self._last_checkpoint_time = 0.0
        self._max_events = getattr(config, "max_events", 0)
        self._wall_start: float | None = None
        self._original_sigterm: Any = None
        self._original_sigint: Any = None
        self._tracker: PeriodTracker | None = tracker_for(config, simulator)
        # Phase 3 actions: scripted (sequence, request) pairs for
        # deterministic runs, plus a thread-safe live queue fed by
        # submit_action(). Both drain between ticks (D6).
        self._scripted_actions: list[tuple[int, ActionRequest]] = list(
            actions or []
        )
        self._action_queue: queue.Queue[ActionRequest] = queue.Queue()

    def submit_action(self, request: ActionRequest) -> None:
        """Queue an action from any thread; applied before the next
        business event the runner generates, in FIFO order."""
        self._action_queue.put(request)

    def _emit_action_event(
        self, event: dict[str, Any], *, observe: bool
    ) -> None:
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
        if observe and self._tracker is not None:
            self._tracker.observe(
                event["event_type"],
                event.get("payload", {}),
                sequence=envelope.sequence,
                timestamp=self.clock.now,
            )
        self._next_sequence += 1

    def _process_action(self, request: ActionRequest) -> ActionResult:
        """Apply one action; emit effect event(s), then the audit event.

        Action events get normal sequences and are persisted/output
        like any event, but never count toward ``max_events``. Only
        effect events whose type is a catalogued business event type
        are observed by the KPI tracker (see Runner for the same rule).
        """
        result = self.simulator.apply_action(request, self.clock)
        catalogued = set(self.simulator.available_event_types())
        for event in result.events:
            self._emit_action_event(
                event, observe=event["event_type"] in catalogued
            )
        self._emit_action_event(
            action_audit_event(request, result), observe=False
        )
        return result

    # ------------------------------------------------------------------
    # Command polling (Phase 5)
    # ------------------------------------------------------------------

    def _seed_applied_action_ids(self) -> None:
        """Seed the simulator's applied-id set from storage so action
        idempotency survives a daemon restart (Phase 5)."""
        if not self.command_polling:
            return
        try:
            applied = self.storage.applied_action_ids(self.run_id)
        except NotImplementedError:
            return
        if applied:
            self.simulator.seed_applied_action_ids(applied)

    def _process_command(self, record: dict[str, Any]) -> None:
        """Apply one stored command and record its outcome.

        Goes through the exact ``_process_action`` path as scripted and
        live actions, so effect + audit events land in the stream and
        outputs as usual. The command record is then resolved:
        ``applied`` with the effect event types, or ``rejected`` with
        the pipeline's reason (a duplicate surfaces as
        ``duplicate_action_id``).
        """
        request = ActionRequest(
            action_id=record["action_id"],
            action_type=record["action_type"],
            actor=record.get("requested_by") or "command",
            params=dict(record.get("params") or {}),
            approved_by=record.get("approved_by"),
        )
        result = self._process_action(request)
        if result.status == "applied":
            status = "applied"
            payload: dict[str, Any] = {
                "status": result.status,
                "effect_event_types": [
                    event["event_type"] for event in result.events
                ],
            }
        else:
            status = "rejected"
            payload = {
                "status": result.status,
                "reason": result.reason,
                "effect_event_types": [],
            }
        self.storage.record_command_result(
            record["command_id"],
            status,
            payload,
            resolved_at=self.clock.now.isoformat(),
            run_id=self.run_id,
        )

    def _drain_commands(self) -> None:
        """Apply every pending stored command, in submission order."""
        if not self.command_polling:
            return
        try:
            pending = self.storage.pending_commands(self.run_id)
        except NotImplementedError:
            self.command_polling = False
            return
        for record in pending:
            self._process_command(record)

    def _drain_actions(self) -> None:
        upcoming = self._next_sequence
        due = [item for item in self._scripted_actions if item[0] <= upcoming]
        self._scripted_actions = [
            item for item in self._scripted_actions if item[0] > upcoming
        ]
        for _, request in due:
            self._process_action(request)
        while True:
            try:
                request = self._action_queue.get_nowait()
            except queue.Empty:
                break
            self._process_action(request)

    def _drain_scenario_events(self) -> None:
        """Emit scenario_started/scenario_ended markers (Phase 6).

        Same treatment as period summaries: each marker gets a normal
        sequence and is persisted/output, but does not count toward
        ``max_events`` and is not observed by the KPI tracker.
        """
        transitions_fn = getattr(self.simulator, "scenario_transitions", None)
        if not callable(transitions_fn):
            return
        for event in transitions_fn(self.clock):
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
            self._next_sequence += 1

    def _emit_period_summary(self, payload: dict) -> None:
        """Persist/emit a period_summary event (Phase 2).

        Gets a normal sequence but does not count toward max_events,
        following the Phase 1 snapshot-event pattern.
        """
        envelope = EventEnvelope.from_event(
            business_type=self.config.business_type,
            event_type="period_summary",
            payload=payload,
            timestamp=self.clock.now,
            stream_id=self.config.stream_id,
            sequence=self._next_sequence,
        )
        self.storage.append(envelope)
        if self.output is not None:
            self.output.write(envelope)
        self._next_sequence += 1

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

    def _warn_resume(self, message: str) -> None:
        """Surface a resume warning loudly: list, log, and warning."""
        self.resume_warnings.append(message)
        logger.warning(message)
        warnings.warn(message, UserWarning, stacklevel=2)

    def _restore_clock(self, snapshot: dict[str, Any]) -> None:
        """Rebuild the simulated clock from the snapshot envelope.

        Without this, a resumed daemon's sim time restarts at wall-clock
        now and dayparts, period boundaries and scenario offsets all
        restart with it (restorable-state scope §3.5).
        """
        clock_data = snapshot.get("clock")
        if not clock_data:
            return
        start_time = datetime.fromisoformat(clock_data["start_time"])
        clock = Clock(
            start_time=start_time,
            tick_seconds=float(clock_data["tick_seconds"]),
        )
        clock.now = datetime.fromisoformat(clock_data["now"])
        self.clock = clock

    def _restore_simulator(self, data: dict[str, Any]) -> None:
        restore_snapshot = getattr(self.simulator, "restore_snapshot", None)
        if callable(restore_snapshot):
            restore_snapshot(data)
        else:
            # Duck-typed simulators (test doubles) predate the
            # full-state entry point; their restore() is the contract.
            self.simulator.restore(data)

    def _load_resume_state(self) -> None:
        checkpoint = self.storage.read_checkpoint()
        if checkpoint is None:
            self._next_sequence = 0
            return
        self._next_sequence = checkpoint["last_sequence"] + 1
        snapshot = self.storage.read_snapshot(self.SNAPSHOT_KEY)
        if snapshot is None:
            return
        snapshot_format = snapshot.get("snapshot_format")
        if snapshot_format is None:
            # R2 legacy fallback: a pre-restorable-state summary
            # snapshot. Resume exactly as before (sequence resumes,
            # business state restarts) — but say so, loudly, instead of
            # letting the operator believe the business continued.
            self._warn_resume(
                f"Resuming stream '{self.run_id}' from a legacy "
                "summary-only snapshot (no full-state format): the "
                "stream sequence resumes, but business state restarts "
                "from its initial state and the simulated clock "
                "restarts at wall-clock now. The next checkpoint "
                "rewrites the snapshot in the current format."
            )
            self._restore_simulator(snapshot.get("data", {}))
            return
        if snapshot_format != SNAPSHOT_FORMAT_VERSION:
            raise ValueError(
                f"Snapshot for stream '{self.run_id}' has "
                f"snapshot_format {snapshot_format!r}; this build "
                f"reads format {SNAPSHOT_FORMAT_VERSION}. Refusing to "
                "resume from a snapshot it cannot interpret."
            )
        # Restore order (S1, extended by S4): clock first (scenario
        # offsets and period boundaries are computed from it), then
        # the framework sections — KPI tracker, scenario engine —
        # then the simulator's own state, inside which RNG/Faker
        # state is restored last of all (see restore_full_state).
        self._restore_clock(snapshot)
        tracker_state = snapshot.get("tracker")
        if tracker_state is not None and self._tracker is not None:
            self._tracker.restore_state(tracker_state)
        scenario_state = snapshot.get("scenario")
        if scenario_state is not None:
            restore_scenario = getattr(
                self.simulator, "restore_scenario_state", None
            )
            if callable(restore_scenario):
                restore_scenario(scenario_state)
        self._restore_simulator(snapshot.get("data", {}))

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
        data = dict(self.simulator.state_snapshot())
        # Restorable state (S1): businesses that implement
        # serialize_state() get a versioned full_state section inside
        # the snapshot data, next to the summary keys. Duck-typed
        # simulators without the method keep summary-only snapshots.
        full_state_fn = getattr(self.simulator, "full_state_snapshot", None)
        if callable(full_state_fn):
            full_state = full_state_fn()
            if full_state is not None:
                data["full_state"] = full_state
        snapshot_data = {
            "seed": self.config.seed,
            "business_type": self.config.business_type,
            "snapshot_format": SNAPSHOT_FORMAT_VERSION,
            # The simulated clock rides in the envelope (sibling of
            # "data", per the scope) so resume can continue sim time
            # instead of restarting at wall-clock now.
            "clock": {
                "start_time": self.clock.start_time.isoformat(),
                "now": self.clock.now.isoformat(),
                "tick_seconds": self.clock.tick_seconds,
            },
            "data": data,
        }
        # Restorable state (S4): the framework state the *runner*
        # owns rides in the envelope as siblings of "data", never
        # inside the simulator's own section. The KPI tracker's open
        # period (R3) continues across a restart from here; the
        # scenario engine's pending/active state (R4) is read back
        # off the simulator, which owns the engine. Sections are
        # simply absent when the corresponding piece is off (no
        # tracker, no schedule), keeping those runs byte-identical
        # to their pre-S4 checkpoints.
        if self._tracker is not None:
            snapshot_data["tracker"] = self._tracker.serialize_state()
        scenario_state_fn = getattr(
            self.simulator, "scenario_state", None
        )
        if callable(scenario_state_fn):
            scenario_state = scenario_state_fn()
            if scenario_state is not None:
                snapshot_data["scenario"] = scenario_state
        self.storage.write_snapshot(self.SNAPSHOT_KEY, snapshot_data)
        # Phase 1: the position read API lives under its own key so the
        # "latest" snapshot/checkpoint/restore semantics are untouched.
        position_fn = getattr(self.simulator, "position_snapshot", None)
        if callable(position_fn):
            position_data = {
                **position_fn(),
                "stream_id": self.config.stream_id,
                "sequence": last_sequence,
            }
            self.storage.write_snapshot(self.POSITION_KEY, position_data)
        self.storage.write_checkpoint(last_sequence)
        self._last_checkpoint_time = time.monotonic()

    def _emit_position_snapshot_event(self) -> None:
        """Emit the opt-in in-stream position snapshot event (Phase 1)."""
        position_fn = getattr(self.simulator, "position_snapshot", None)
        if not callable(position_fn):
            return
        envelope = EventEnvelope.from_event(
            business_type=self.config.business_type,
            event_type="state_snapshot_recorded",
            payload=position_fn(),
            timestamp=self.clock.now,
            stream_id=self.config.stream_id,
            sequence=self._next_sequence,
        )
        self.storage.append(envelope)
        if self.output is not None:
            self.output.write(envelope)
        self._next_sequence += 1

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
        self._seed_applied_action_ids()
        self._wall_start = time.monotonic()
        self._last_checkpoint_time = self._wall_start

        try:
            while not self._shutdown_event.is_set():
                if self._max_events and self._events_emitted >= self._max_events:
                    break
                if (
                    self.config.duration_seconds
                    and self.clock.elapsed_seconds() >= self.config.duration_seconds
                ):
                    break
                # Phase 2: close the previous simulated day before the
                # first event of a new day is generated.
                if self._tracker is not None:
                    closed = self._tracker.close_if_crossed(self.clock.now)
                    if closed is not None:
                        self._emit_period_summary(closed)
                # Phase 6: scenario starts/ends crossed by the clock
                # land between ticks, before the next business event.
                self._drain_scenario_events()
                # Phase 3: actions land between ticks, after any period
                # close and before the next business event.
                self._drain_actions()
                # Phase 5: stored commands land between ticks too (D6),
                # drained after scripted/live actions each iteration.
                self._drain_commands()
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

                if self._tracker is not None:
                    self._tracker.observe(
                        event["event_type"],
                        event.get("payload", {}),
                        sequence=envelope.sequence,
                        timestamp=self.clock.now,
                    )
                self._events_emitted += 1
                self._next_sequence += 1

                every = getattr(
                    self.config, "position_snapshot_every_events", 0
                )
                if every and self._events_emitted % every == 0:
                    self._emit_position_snapshot_event()

                if self._should_checkpoint():
                    self._write_checkpoint()

                self.clock.advance()
                self._pace()
        finally:
            if self._events_emitted > 0:
                # Restorable state (S4, R3): no final partial period
                # summary on this path. The continuous runner is the
                # resumable one — its open KPI period is captured
                # in-progress by the checkpoint (see _write_checkpoint)
                # and continues across a restart, closing later as one
                # full-day summary. Flushing a partial at every process
                # end would permanently split that day's KPIs, which
                # is the defect R3 removes. Finite runs that are over
                # for good use the batch Runner, which still emits a
                # closing partial via PeriodTracker.close_final().
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
