"""Runner that drives a single simulator and emits events through adapters."""

from __future__ import annotations

import time
from typing import Any

from eden_business_simulator.businesses.base import (
    ActionRequest,
    BusinessSimulator,
    action_audit_event,
)
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.framework.kpi import PeriodTracker, tracker_for
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
        actions: list[tuple[int, ActionRequest]] | None = None,
    ) -> None:
        self.config = config
        self.simulator = simulator
        self.adapter = adapter
        self.realtime = realtime
        self.clock = Clock(tick_seconds=1.0 / config.events_per_second)
        self.events_emitted = 0
        self._wall_start: float | None = None
        self._tracker: PeriodTracker | None = tracker_for(config, simulator)
        # Phase 3 scripted actions: (business-event index, request) —
        # applied between ticks, before the business event with that
        # index is generated (D6). Batch envelopes carry no sequence
        # field, so the index counts business events emitted so far.
        self._scripted_actions: list[tuple[int, ActionRequest]] = list(
            actions or []
        )

    def _process_action(self, request: ActionRequest) -> None:
        """Apply one action and emit effect event(s), then the audit.

        Ordering (Phase 3 decision): domain effect events first, the
        ``action_applied``/``action_rejected`` audit event last. Action
        events never count toward ``max_events``; effect events whose
        type is a catalogued business event type are observed by the
        KPI tracker, audit events and new action-only types are not.
        """
        result = self.simulator.apply_action(request, self.clock)
        for event in result.events:
            self.adapter.write(
                EventEnvelope.from_event(
                    business_type=self.config.business_type,
                    event_type=event["event_type"],
                    payload=event.get("payload", {}),
                    timestamp=self.clock.now,
                )
            )
            if (
                self._tracker is not None
                and event["event_type"] in self.simulator.available_event_types()
            ):
                self._tracker.observe(
                    event["event_type"],
                    event.get("payload", {}),
                    sequence=self.events_emitted,
                    timestamp=self.clock.now,
                )
        audit = action_audit_event(request, result)
        self.adapter.write(
            EventEnvelope.from_event(
                business_type=self.config.business_type,
                event_type=audit["event_type"],
                payload=audit["payload"],
                timestamp=self.clock.now,
            )
        )

    def _drain_scripted_actions(self) -> None:
        upcoming = self.events_emitted
        due = [item for item in self._scripted_actions if item[0] <= upcoming]
        self._scripted_actions = [
            item for item in self._scripted_actions if item[0] > upcoming
        ]
        for _, request in due:
            self._process_action(request)

    def _write_period_summary(self, payload: dict) -> None:
        self.adapter.write(
            EventEnvelope.from_event(
                business_type=self.config.business_type,
                event_type="period_summary",
                payload=payload,
                timestamp=self.clock.now,
            )
        )

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
                # Phase 2: close the previous simulated day before the
                # first event of a new day is generated, so snapshot KPIs
                # read the position exactly as the closed period ended.
                if self._tracker is not None:
                    closed = self._tracker.close_if_crossed(self.clock.now)
                    if closed is not None:
                        self._write_period_summary(closed)
                # Phase 3: scripted actions land between ticks, after
                # any period close and before the next business event.
                self._drain_scripted_actions()
                event = self.simulator.next_event(self.clock)
                envelope = EventEnvelope.from_event(
                    business_type=self.config.business_type,
                    event_type=event["event_type"],
                    payload=event.get("payload", {}),
                    timestamp=self.clock.now,
                )
                self.adapter.write(envelope)
                if self._tracker is not None:
                    self._tracker.observe(
                        event["event_type"],
                        event.get("payload", {}),
                        sequence=self.events_emitted,
                        timestamp=self.clock.now,
                    )
                self.events_emitted += 1
                every = getattr(
                    self.config, "position_snapshot_every_events", 0
                )
                position_fn = getattr(
                    self.simulator, "position_snapshot", None
                )
                if (
                    every
                    and self.events_emitted % every == 0
                    and callable(position_fn)
                ):
                    self.adapter.write(
                        EventEnvelope.from_event(
                            business_type=self.config.business_type,
                            event_type="state_snapshot_recorded",
                            payload=position_fn(),
                            timestamp=self.clock.now,
                        )
                    )
                self.clock.advance()
                if self.realtime:
                    self._pace()
            # Final partial summary for the last open period.
            if self._tracker is not None:
                final = self._tracker.close_final()
                if final is not None:
                    self._write_period_summary(final)
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
