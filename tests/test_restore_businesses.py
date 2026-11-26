"""Restorable state S2: full-state snapshot/restore for trades + cafe.

The flagship proof, per business: a split run — N events, checkpoint,
then a *fresh* simulator instance resumed from sqlite storage for M
more events — produces exactly the stream and final position of an
uninterrupted N+M run. Equality is exact on (event_type, payload,
envelope timestamp); the only thing not compared is ``event_id``, a
fresh uuid per envelope. No payload normalisation is needed: neither
the trades nor cafe stamp any wall-clock fields (the known cases are
ecommerce ``registered_at`` and saas ``signup_at``).

Period summaries are disabled in these configs: the KPI tracker's own
resume is S4's slice, and its per-process partial summaries would
otherwise (correctly, for S2) differ across the restart boundary.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.base import ActionRequest
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.models import Clock
from eden_business_simulator.storage.sqlite import SqliteStorageAdapter

PINNED_START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
TICK_SECONDS = 0.001  # events_per_second=1000

TRADES = ["field_service", "electrician", "plumber"]
# Each trade validates order_parts against its own configured parts
# catalogue, so the scripted order names that business's first part.
FIRST_PART = {
    "field_service": "prt_001",
    "electrician": "prt_e001",
    "plumber": "prt_p001",
}


def _config(
    business: str, stream_id: str, max_events: int
) -> SimulatorConfig:
    return SimulatorConfig(
        business_type=business,
        duration_seconds=3600,
        events_per_second=1000.0,
        seed=42,
        max_events=max_events,
        stream_id=stream_id,
        storage_backend="sqlite",
        checkpoint_interval_events=5,
        checkpoint_interval_seconds=3600.0,
        period_summaries_enabled=False,
    )


def _run_segment(
    db_path: str,
    business: str,
    stream_id: str,
    max_events: int,
    actions: list[tuple[int, ActionRequest]] | None = None,
    *,
    pin_clock: bool = False,
):
    """Run one runner segment over the db; return the simulator.

    The runner closes its storage adapter on exit, exactly as a daemon
    process would; each segment therefore opens a fresh adapter over
    the same file, and a resumed segment's simulator is a fresh
    instance that went through configure() + initialize(seed) first —
    the real daemon resume path.
    """
    storage = SqliteStorageAdapter(db_path, stream_id)
    config = _config(business, stream_id, max_events)
    sim = load_simulator(business)
    sim.configure(config)
    sim.initialize(config.seed)
    runner = ContinuousRunner(
        config, sim, storage, realtime=False, actions=actions
    )
    if pin_clock:
        runner.clock = Clock(
            start_time=PINNED_START, tick_seconds=TICK_SECONDS
        )
    runner.run()
    return sim


def _stored_events(db_path: str, stream_id: str) -> list[tuple[Any, ...]]:
    storage = SqliteStorageAdapter(db_path, stream_id)
    try:
        return [
            (
                record.envelope.event_type,
                record.envelope.payload,
                record.envelope.timestamp,
            )
            for record in storage.read_from()
        ]
    finally:
        storage.close()


def _split_run(
    tmp_path: Path,
    business: str,
    first: int,
    second: int,
    actions: list[tuple[int, ActionRequest]] | None = None,
):
    """Run whole and split variants; return (whole_sim, split_sim,
    whole_events, split_events)."""
    whole_db = str(tmp_path / "whole.db")
    split_db = str(tmp_path / "split.db")

    whole_sim = _run_segment(
        whole_db, business, "s", first + second, actions, pin_clock=True
    )
    whole_events = _stored_events(whole_db, "s")

    _run_segment(split_db, business, "s", first, actions, pin_clock=True)
    split_sim = _run_segment(split_db, business, "s", second)
    split_events = _stored_events(split_db, "s")
    return whole_sim, split_sim, whole_events, split_events


def _assert_exact_continuation(whole_sim, split_sim, whole_events, split_events):
    assert split_events == whole_events
    assert split_sim.position_snapshot() == whole_sim.position_snapshot()


# ----------------------------------------------------------------------
# Trades (field_service implementation; electrician/plumber inherit)
# ----------------------------------------------------------------------


@pytest.mark.parametrize("business", TRADES)
def test_trades_split_run_matches_uninterrupted(tmp_path, business):
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, business, first=40, second=60
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )


def _trades_actions(business: str) -> list[tuple[int, ActionRequest]]:
    """Assign + order parts, drained at sequence 1 — right after the
    first business event (deterministically ``service_ticket_created``
    for tkt_0001, the only guard-eligible type on an empty board)."""
    return [
        (
            1,
            ActionRequest(
                "op-assign",
                "assign_technician",
                "test",
                {
                    "ticket_id": "tkt_0001",
                    "technician_id": "tech_0001",
                },
            ),
        ),
        (
            1,
            ActionRequest(
                "op-parts",
                "order_parts",
                "test",
                {"part_id": FIRST_PART[business], "qty": 5},
            ),
        ),
    ]


@pytest.mark.parametrize("business", TRADES)
def test_trades_split_run_with_actions_before_checkpoint(
    tmp_path, business
):
    actions = _trades_actions(business)
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, business, first=40, second=60, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    # Both actions really applied (effect + audit events present),
    # in the whole run and across the restart.
    applied = [
        payload
        for event_type, payload, _ in split_events
        if event_type == "action_applied"
    ]
    assert {p["action_id"] for p in applied} == {"op-assign", "op-parts"}
    # The action-mutated state itself survived the restart: the ticket
    # carries its assigned technician and the parts order is on file.
    ticket = next(
        t for t in split_sim.tickets if t["ticket_id"] == "tkt_0001"
    )
    assert ticket["technician_id"] == "tech_0001"
    assert len(split_sim.parts_orders) == 1
    assert split_sim.parts_orders[0]["part_id"] == FIRST_PART[business]
    assert split_sim.parts_orders[0]["qty"] == 5


# ----------------------------------------------------------------------
# Cafe
# ----------------------------------------------------------------------


def test_cafe_split_run_matches_uninterrupted_mid_shift(tmp_path):
    # N=60 lands mid-operations: the 19-event setup queue has drained
    # and the cafe is in service, not at any day/shift boundary.
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "cafe", first=60, second=140
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )


def test_cafe_split_run_checkpoint_during_setup(tmp_path):
    # N=7 lands inside the deterministic setup queue (shift_opened,
    # staff clock-ins, menu items, deliveries still pending): the
    # queue itself is state and must resume where it left off.
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "cafe", first=7, second=80
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )


def test_cafe_split_run_with_price_change_before_checkpoint(tmp_path):
    # adjust_price mutates a menu item's price; at sequence 30 the
    # setup queue has drained and the menu exists. Approval-gated, so
    # the request carries the Beth-side stamp.
    actions = [
        (
            30,
            ActionRequest(
                "op-price",
                "adjust_price",
                "test",
                {"item_id": "item_0001", "new_price": 6.25},
                approved_by="yakov",
            ),
        )
    ]
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "cafe", first=60, second=140, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    applied = [
        payload
        for event_type, payload, _ in split_events
        if event_type == "action_applied"
    ]
    assert [p["action_id"] for p in applied] == ["op-price"]
    # The adjusted price — action-mutated state — survived the restart
    # and matches the uninterrupted run's menu.
    for sim in (whole_sim, split_sim):
        item = next(
            i for i in sim.menu.all() if i["item_id"] == "item_0001"
        )
        assert item["price"] == 6.25


def test_cafe_ticket_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "cafe", first=60, second=140
    )
    fired = [t for t in split_sim.tickets if "fired_at" in t]
    assert fired, "expected at least one fired ticket in 200 events"
    # The framework codec must hand back real datetimes, not strings:
    # ticket payload code calls .isoformat() on them after resume.
    assert all(isinstance(t["fired_at"], datetime) for t in fired)
    assert all(isinstance(t["due_at"], datetime) for t in fired)
