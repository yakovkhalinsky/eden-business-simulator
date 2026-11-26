"""Restorable state S3: full-state snapshot/restore for the remaining
five businesses (clinic, ecommerce, gym, logistics, saas).

Same flagship proof as S2 (see tests/test_restore_businesses.py): a
split run — N events, checkpoint, then a *fresh* simulator instance
resumed from sqlite storage for M more events — produces exactly the
stream and final position of an uninterrupted N+M run. Equality is
exact on (event_type, payload, envelope timestamp); ``event_id`` is
not compared (a fresh uuid per envelope).

One payload normalisation is needed here, and only here: ecommerce
``customer_created`` payloads carry ``registered_at`` and saas
``account_signed_up`` payloads carry ``signup_at``, both stamped from
``datetime.now(timezone.utc)`` at entity creation (the same
wall-clock fields framework/verify.py lists in ``WALL_CLOCK_KEYS``).
Two runs of the same seed therefore stamp different real times into
those two fields and nothing else — every other payload value is
simulated-clock or RNG derived. The normalisation below replaces
exactly those two keys' values, wherever they appear in a payload.

Period summaries are disabled in these configs: the KPI tracker's own
resume is S4's slice, and its per-process partial summaries would
otherwise (correctly, for S3) differ across the restart boundary.
"""

from __future__ import annotations

from datetime import date, datetime, timezone
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

FIVE = ["clinic", "ecommerce", "gym", "logistics", "saas"]

# The only wall-clock-derived payload fields in these businesses:
# ecommerce registered_at (customer_created) and saas signup_at
# (account_signed_up). See the module docstring.
WALL_CLOCK_PAYLOAD_KEYS = {"registered_at", "signup_at"}


def _normalise(value: Any) -> Any:
    if isinstance(value, dict):
        return {
            key: (
                "<wall-clock>"
                if key in WALL_CLOCK_PAYLOAD_KEYS
                else _normalise(item)
            )
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [_normalise(item) for item in value]
    return value


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

    Mirrors the S2 helper: each segment opens a fresh storage adapter
    over the same file, and a resumed segment's simulator is a fresh
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
                _normalise(record.envelope.payload),
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


def _applied_ids(events) -> set[str]:
    return {
        payload["action_id"]
        for event_type, payload, _ in events
        if event_type == "action_applied"
    }


# ----------------------------------------------------------------------
# Plain split-run proofs, all five businesses
# ----------------------------------------------------------------------


@pytest.mark.parametrize("business", FIVE)
def test_split_run_matches_uninterrupted(tmp_path, business):
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, business, first=50, second=90
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )


# ----------------------------------------------------------------------
# Clinic: schedule_appointment before the checkpoint
# ----------------------------------------------------------------------


def test_clinic_split_run_with_appointment_booked_before_checkpoint(
    tmp_path,
):
    # patient_0001 / provider_0001 exist from initialize(); the action
    # books a real appointment whose scheduled_time is a datetime.
    actions = [
        (
            5,
            ActionRequest(
                "op-appt",
                "schedule_appointment",
                "test",
                {
                    "patient_id": "patient_0001",
                    "provider_id": "provider_0001",
                    "appointment_type": "follow_up",
                    "starts_in_hours": 24,
                },
            ),
        )
    ]
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "clinic", first=40, second=80, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    assert _applied_ids(split_events) == {"op-appt"}
    # The manager-booked appointment is in the stream with its
    # manager_action marker, in both runs.
    booked = [
        payload
        for event_type, payload, _ in split_events
        if event_type == "appointment_scheduled"
        and payload.get("reason") == "manager_action"
    ]
    assert len(booked) == 1
    assert booked[0]["patient_id"] == "patient_0001"
    # The action-created entity survived the restart in state.
    assert any(
        a["patient_id"] == "patient_0001"
        and a["provider_id"] == "provider_0001"
        and a["appointment_type"] == "follow_up"
        for a in split_sim.appointments
    )


def test_clinic_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "clinic", first=50, second=90
    )
    assert split_sim.appointments, "expected appointments after 140 events"
    # The framework codec must hand back real datetimes, not strings:
    # the reschedule emitter does arithmetic on scheduled_time.
    assert all(
        isinstance(a["scheduled_time"], datetime)
        for a in split_sim.appointments
    )


# ----------------------------------------------------------------------
# Ecommerce: restock + approved refund before the checkpoint
# ----------------------------------------------------------------------


def test_ecommerce_split_run_with_restock_and_refund_before_checkpoint(
    tmp_path,
):
    # prod_0000 exists from initialize(); ord_000002 is deterministically
    # in "paid" status when the refund drains at sequence 40 (the
    # restock's effect + audit events consume two sequences, so the
    # refund sees the state after 38 business events).
    actions = [
        (
            10,
            ActionRequest(
                "op-restock",
                "restock_product",
                "test",
                {"product_id": "prod_0000", "qty": 500},
            ),
        ),
        (
            40,
            ActionRequest(
                "op-refund",
                "issue_refund",
                "test",
                {"order_id": "ord_000002"},
                approved_by="yakov",
            ),
        ),
    ]
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "ecommerce", first=60, second=80, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    assert _applied_ids(split_events) == {"op-restock", "op-refund"}
    # The restock landed as an inventory_adjusted effect event. The
    # natural emitter can also produce reason="restock" events, but
    # its deltas come from a small fixed set — delta 500 is uniquely
    # the action's.
    restocks = [
        payload
        for event_type, payload, _ in split_events
        if event_type == "inventory_adjusted"
        and payload.get("reason") == "restock"
        and payload.get("delta") == 500
    ]
    assert len(restocks) == 1
    assert restocks[0]["product_id"] == "prod_0000"
    # The refund's state mutation survived the restart: the order is
    # still refunded in the resumed simulator (refunded is terminal).
    for sim in (whole_sim, split_sim):
        order = next(
            o for o in sim.orders if o["order_id"] == "ord_000002"
        )
        assert order["status"] == "refunded"


def test_ecommerce_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "ecommerce", first=50, second=90
    )
    # registered_at (customers) and placed_at (orders) must come back
    # as real datetimes; order payloads call .isoformat() on placed_at.
    assert all(
        isinstance(c["registered_at"], datetime)
        for c in split_sim.customers
    )
    assert split_sim.orders, "expected orders after 140 events"
    assert all(
        isinstance(o["placed_at"], datetime) for o in split_sim.orders
    )


# ----------------------------------------------------------------------
# Gym: approved plan price change before the checkpoint
# ----------------------------------------------------------------------


def test_gym_split_run_with_plan_price_change_before_checkpoint(tmp_path):
    # adjust_plan_price mutates the _PLANS table itself, not just
    # memberships — the proof that _PLANS is captured as instance
    # state rather than rebuilt from the definition on resume.
    actions = [
        (
            10,
            ActionRequest(
                "op-plan",
                "adjust_plan_price",
                "test",
                {"tier": "basic", "new_monthly_fee": 35.0},
                approved_by="yakov",
            ),
        )
    ]
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "gym", first=50, second=90, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    assert _applied_ids(split_events) == {"op-plan"}
    # The adjusted plan price survived the restart in both runs.
    for sim in (whole_sim, split_sim):
        assert sim._PLANS["basic"]["monthly_fee"] == 35.0
        # Any member currently on basic pays the adjusted fee: the
        # action repriced existing memberships, and enrolments /
        # tier changes since then read the captured _PLANS.
        assert all(
            m["monthly_fee"] == 35.0
            for m in sim.memberships.values()
            if m["tier"] == "basic"
        )


def test_gym_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "gym", first=50, second=90
    )
    # booked_at / scheduled_at / logged_at / attempted_at are stored
    # as datetimes on bookings, sessions, workouts, failed payments.
    for rows, field in [
        (split_sim.bookings, "booked_at"),
        (split_sim.sessions, "scheduled_at"),
        (split_sim.workouts, "logged_at"),
        (split_sim.payments_failed, "attempted_at"),
    ]:
        assert all(isinstance(row[field], datetime) for row in rows)
    # The at-risk set round-trips as a set, not a list.
    assert isinstance(split_sim.at_risk_members, set)


# ----------------------------------------------------------------------
# Logistics: assign_driver before the checkpoint
# ----------------------------------------------------------------------


def test_logistics_split_run_with_driver_assigned_before_checkpoint(
    tmp_path,
):
    # rte_0002 is deterministically planned (driver_0003) when the
    # action drains at sequence 14; driver_0002 exists from
    # initialize(). Nothing else ever mutates a route's driver_id.
    actions = [
        (
            14,
            ActionRequest(
                "op-driver",
                "assign_driver",
                "test",
                {"route_id": "rte_0002", "driver_id": "driver_0002"},
            ),
        )
    ]
    whole_sim, split_sim, whole_events, split_events = _split_run(
        tmp_path, "logistics", first=50, second=90, actions=actions
    )
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    assert _applied_ids(split_events) == {"op-driver"}
    # The reassignment survived the restart.
    for sim in (whole_sim, split_sim):
        route = next(
            r for r in sim.routes if r["route_id"] == "rte_0002"
        )
        assert route["driver_id"] == "driver_0002"


def test_logistics_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "logistics", first=50, second=90
    )
    # Stop ETAs are datetimes the route planner computed; attempts
    # carry attempted_at datetimes.
    assert split_sim.stops, "expected stops after 140 events"
    assert all(isinstance(s["eta"], datetime) for s in split_sim.stops)
    assert all(
        isinstance(a["attempted_at"], datetime)
        for a in split_sim.attempts
    )


# ----------------------------------------------------------------------
# SaaS: freeze + approved plan price change before the checkpoint
# ----------------------------------------------------------------------


def _saas_actions() -> list[tuple[int, ActionRequest]]:
    # acc_0004 deterministically holds an active growth subscription
    # (sub_0002, 79.0) when the freeze drains at sequence 30.
    return [
        (
            30,
            ActionRequest(
                "op-freeze",
                "freeze_account",
                "test",
                {"account_id": "acc_0004"},
            ),
        ),
        (
            35,
            ActionRequest(
                "op-price",
                "change_plan_price",
                "test",
                {"plan_name": "growth", "new_monthly_fee": 99.0},
                approved_by="yakov",
            ),
        ),
    ]


def test_saas_split_run_with_freeze_and_price_change_before_checkpoint(
    tmp_path,
):
    actions = _saas_actions()
    whole_db = str(tmp_path / "whole.db")
    split_db = str(tmp_path / "split.db")

    whole_sim = _run_segment(
        whole_db, "saas", "s", 140, actions, pin_clock=True
    )
    whole_events = _stored_events(whole_db, "s")

    seg1_sim = _run_segment(
        split_db, "saas", "s", 50, actions, pin_clock=True
    )
    # At the checkpoint boundary the freeze and the repricing are
    # both in force: acc_0004 is frozen at the new growth fee, and
    # frozen subscriptions are excluded from MRR.
    boundary = seg1_sim.subscriptions["acc_0004"]
    assert boundary["status"] == "frozen"
    assert boundary["monthly_fee"] == 99.0
    assert seg1_sim._PLANS[1] == ("growth", 99.0, "month")
    mrr = seg1_sim.position_snapshot()["position"]["mrr"]
    active_fees = [
        s["monthly_fee"]
        for s in seg1_sim.subscriptions.values()
        if s["status"] == "active"
    ]
    assert mrr == round(sum(active_fees), 2)
    assert 99.0 not in active_fees

    split_sim = _run_segment(split_db, "saas", "s", 90)
    split_events = _stored_events(split_db, "s")
    _assert_exact_continuation(
        whole_sim, split_sim, whole_events, split_events
    )
    assert _applied_ids(split_events) == {"op-freeze", "op-price"}
    # The action-mutated plan table crossed the restart (in its
    # native tuple shape). acc_0004's own fee is asserted at the
    # boundary above: after it, a natural plan_subscribed event may
    # legitimately re-subscribe the account onto another plan in
    # both runs alike.
    for sim in (whole_sim, split_sim):
        assert sim._PLANS[1] == ("growth", 99.0, "month")


def test_saas_signup_datetimes_round_trip_through_resume(tmp_path):
    _, split_sim, _, _ = _split_run(
        tmp_path, "saas", first=50, second=90
    )
    # signup_at must come back as the original datetime value, not a
    # string and not a re-stamp: account payloads call .isoformat().
    assert split_sim.accounts, "expected accounts after 140 events"
    assert all(
        isinstance(a["signup_at"], datetime) for a in split_sim.accounts
    )
