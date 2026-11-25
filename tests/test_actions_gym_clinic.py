"""Action catalogue tests (Phase 4a, gym + clinic).

Follows the Phase 3 patterns in ``test_actions.py``: happy path per
action (applied result, effect event, internal state and
``position_snapshot`` reflecting it), the main rejection paths, the
rejection rule (no RNG consumed — business stream identical to a
no-action run apart from audit events), determinism with actions,
and exact ``available_actions()`` sets.
"""

from datetime import datetime, timedelta, timezone
from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.base import ActionRequest
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner

START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)


def _sim(business_type: str, seed: int = 42):
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        period_summaries_enabled=False,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    clock = Clock(start_time=START, tick_seconds=1.0)
    return sim, clock


def _req(action_type, params, action_id="a1", approved_by=None):
    return ActionRequest(
        action_id=action_id,
        action_type=action_type,
        actor="manager_agent",
        params=params,
        approved_by=approved_by,
    )


# ----------------------------------------------------------------------
# Gym — direct apply_action
# ----------------------------------------------------------------------


def test_gym_available_actions_exact():
    sim, _ = _sim("gym")
    assert [a.action_type for a in sim.available_actions()] == [
        "freeze_membership",
        "unfreeze_membership",
        "schedule_class",
        "cancel_class",
        "send_retention_outreach",
        "adjust_plan_price",
    ]
    by_name = {a.action_type: a for a in sim.available_actions()}
    assert by_name["adjust_plan_price"].requires_approval is True


def test_gym_freeze_and_unfreeze_membership():
    sim, clock = _sim("gym")
    member_id = sim._active_member_ids()[0]
    before = sim.position_snapshot()["position"]

    result = sim.apply_action(
        _req("freeze_membership", {"member_id": member_id}), clock
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "membership_frozen"
    assert event["payload"]["reason"] == "manager_action"
    expected_resume = (START + timedelta(days=30)).date().isoformat()
    assert event["payload"]["resume_date"] == expected_resume
    assert sim.memberships[member_id]["status"] == "frozen"
    assert member_id in sim._frozen_members
    after = sim.position_snapshot()["position"]
    assert after["frozen_count"] == before["frozen_count"] + 1
    assert after["active_count"] == before["active_count"] - 1

    again = sim.apply_action(
        _req("freeze_membership", {"member_id": member_id}, action_id="a2"),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"membership_not_active:{member_id}"

    back = sim.apply_action(
        _req("unfreeze_membership", {"member_id": member_id}, action_id="a3"),
        clock,
    )
    assert back.status == "applied"
    assert back.events[0]["event_type"] == "membership_unfrozen"
    assert sim.memberships[member_id]["status"] == "active"
    assert member_id not in sim._frozen_members
    assert sim.position_snapshot()["position"]["frozen_count"] == 0


def test_gym_freeze_unknown_member_and_bad_resume_days():
    sim, clock = _sim("gym")
    result = sim.apply_action(
        _req("freeze_membership", {"member_id": "member_9999"}), clock
    )
    assert result.status == "rejected"
    assert result.reason == "unknown_member:member_9999"

    member_id = sim._active_member_ids()[0]
    bad = sim.apply_action(
        _req(
            "freeze_membership",
            {"member_id": member_id, "resume_days": 0},
            action_id="a2",
        ),
        clock,
    )
    assert bad.status == "rejected"
    assert bad.reason == "param_out_of_range:resume_days"


def test_gym_freeze_with_reason_and_resume_days():
    sim, clock = _sim("gym")
    member_id = sim._active_member_ids()[0]
    result = sim.apply_action(
        _req(
            "freeze_membership",
            {"member_id": member_id, "resume_days": 14, "reason": "travel"},
        ),
        clock,
    )
    assert result.status == "applied"
    assert result.events[0]["payload"]["reason"] == "travel"
    assert result.events[0]["payload"]["resume_date"] == (
        START + timedelta(days=14)
    ).date().isoformat()


def test_gym_schedule_class_default_and_named_instructor():
    sim, clock = _sim("gym")
    first_instructor = next(
        s["staff_id"] for s in sim.staff.all() if s["role"] == "instructor"
    )
    before = sim.position_snapshot()["position"]["class_count"]
    result = sim.apply_action(
        _req(
            "schedule_class",
            {"class_type": "yoga", "capacity": 20, "duration_minutes": 45},
        ),
        clock,
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "class_scheduled"
    assert payload["instructor_id"] == first_instructor
    assert payload["capacity"] == 20
    assert sim.position_snapshot()["position"]["class_count"] == before + 1

    trainer = next(
        s["staff_id"] for s in sim.staff.all() if s["role"] == "trainer"
    )
    named = sim.apply_action(
        _req(
            "schedule_class",
            {
                "class_type": "spin",
                "capacity": 10,
                "duration_minutes": 30,
                "instructor_id": trainer,
            },
            action_id="a2",
        ),
        clock,
    )
    assert named.status == "applied"
    assert named.events[0]["payload"]["instructor_id"] == trainer

    unknown = sim.apply_action(
        _req(
            "schedule_class",
            {
                "class_type": "spin",
                "capacity": 10,
                "duration_minutes": 30,
                "instructor_id": "staff_9999",
            },
            action_id="a3",
        ),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_instructor:staff_9999"

    bad_capacity = sim.apply_action(
        _req(
            "schedule_class",
            {"class_type": "spin", "capacity": 3, "duration_minutes": 30},
            action_id="a4",
        ),
        clock,
    )
    assert bad_capacity.status == "rejected"
    assert bad_capacity.reason == "param_out_of_range:capacity"


def test_gym_cancel_class_and_booking_exclusion():
    sim, clock = _sim("gym")
    class_ids = [c["class_id"] for c in sim.classes]
    result = sim.apply_action(
        _req("cancel_class", {"class_id": class_ids[0]}), clock
    )
    assert result.status == "applied"
    assert result.events[0]["event_type"] == "class_session_cancelled"
    assert result.events[0]["payload"]["reason"] == "manager_action"
    cls = next(c for c in sim.classes if c["class_id"] == class_ids[0])
    assert cls["status"] == "cancelled"

    again = sim.apply_action(
        _req("cancel_class", {"class_id": class_ids[0]}, action_id="a2"),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"class_already_cancelled:{class_ids[0]}"

    unknown = sim.apply_action(
        _req("cancel_class", {"class_id": "cls_9999"}, action_id="a3"), clock
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_class:cls_9999"

    # Cancel the rest: booking must fall back rather than book a
    # cancelled class.
    for i, class_id in enumerate(class_ids[1:], start=4):
        applied = sim.apply_action(
            _req("cancel_class", {"class_id": class_id}, action_id=f"a{i}"),
            clock,
        )
        assert applied.status == "applied"
    fallback = sim._emit_class_booked(clock)
    assert fallback["event_type"] == "check_in_recorded"


def test_gym_retention_outreach():
    sim, clock = _sim("gym")
    member_id = sim._active_member_ids()[0]
    result = sim.apply_action(
        _req("send_retention_outreach", {"member_id": member_id}), clock
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "retention_outreach_sent"
    assert payload["channel"] == "email"
    assert payload["message_type"] == "payment_retry"
    assert payload["member_id"] == member_id

    unknown = sim.apply_action(
        _req(
            "send_retention_outreach",
            {"member_id": "member_9999"},
            action_id="a2",
        ),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_member:member_9999"

    sim.memberships[member_id]["status"] = "cancelled"
    sim._cancelled_members.append(member_id)
    cancelled = sim.apply_action(
        _req(
            "send_retention_outreach",
            {"member_id": member_id},
            action_id="a3",
        ),
        clock,
    )
    assert cancelled.status == "rejected"
    assert cancelled.reason == f"member_cancelled:{member_id}"


def test_gym_adjust_plan_price_approval_and_effect():
    sim, clock = _sim("gym")
    tier = "standard"
    on_tier = [m for m in sim.memberships.values() if m["tier"] == tier]
    old_fee = sim._PLANS[tier]["monthly_fee"]

    unapproved = sim.apply_action(
        _req("adjust_plan_price", {"tier": tier, "new_monthly_fee": 55.0}),
        clock,
    )
    assert unapproved.status == "rejected"
    assert unapproved.reason == "requires_approval"
    assert sim._PLANS[tier]["monthly_fee"] == old_fee

    applied = sim.apply_action(
        _req(
            "adjust_plan_price",
            {"tier": tier, "new_monthly_fee": 55.0},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert applied.status == "applied"
    payload = applied.events[0]["payload"]
    assert applied.events[0]["event_type"] == "plan_price_adjusted"
    assert payload["old_price"] == old_fee
    assert payload["new_price"] == 55.0
    assert payload["memberships_updated"] == len(on_tier)
    assert sim._PLANS[tier]["monthly_fee"] == 55.0
    for membership in on_tier:
        assert membership["monthly_fee"] == 55.0

    out_of_range = sim.apply_action(
        _req(
            "adjust_plan_price",
            {"tier": tier, "new_monthly_fee": 0},
            action_id="a3",
            approved_by="yakov",
        ),
        clock,
    )
    assert out_of_range.status == "rejected"
    assert out_of_range.reason == "param_out_of_range:new_monthly_fee"


# ----------------------------------------------------------------------
# Clinic — direct apply_action
# ----------------------------------------------------------------------


def test_clinic_available_actions_exact():
    sim, _ = _sim("clinic")
    assert [a.action_type for a in sim.available_actions()] == [
        "schedule_appointment",
        "cancel_appointment",
        "reschedule_appointment",
        "add_provider_shift",
        "chase_claim",
    ]


def _clinic_ids(sim):
    patient_id = sim.patients.all()[0].actor_id
    provider_id = sim.providers.all()[0].actor_id
    return patient_id, provider_id


def _schedule_via_action(sim, clock, action_id="s1", **overrides):
    patient_id, provider_id = _clinic_ids(sim)
    params = {
        "patient_id": patient_id,
        "provider_id": provider_id,
        "appointment_type": "follow_up",
        "starts_in_hours": 24,
    }
    params.update(overrides)
    return sim.apply_action(
        _req("schedule_appointment", params, action_id=action_id), clock
    )


def test_clinic_schedule_appointment_happy_and_rejections():
    sim, clock = _sim("clinic")
    result = _schedule_via_action(sim, clock)
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "appointment_scheduled"
    assert payload["reason"] == "manager_action"
    assert payload["channel"] == "manager"
    assert payload["appointment_time"] == (
        START + timedelta(hours=24)
    ).isoformat()
    position = sim.position_snapshot()["position"]
    assert position["appointments_by_status"] == {"scheduled": 1}

    patient_id, provider_id = _clinic_ids(sim)
    unknown_patient = sim.apply_action(
        _req(
            "schedule_appointment",
            {
                "patient_id": "patient_9999",
                "provider_id": provider_id,
                "appointment_type": "follow_up",
                "starts_in_hours": 24,
            },
            action_id="s2",
        ),
        clock,
    )
    assert unknown_patient.status == "rejected"
    assert unknown_patient.reason == "unknown_patient:patient_9999"

    unknown_provider = sim.apply_action(
        _req(
            "schedule_appointment",
            {
                "patient_id": patient_id,
                "provider_id": "provider_9999",
                "appointment_type": "follow_up",
                "starts_in_hours": 24,
            },
            action_id="s3",
        ),
        clock,
    )
    assert unknown_provider.status == "rejected"
    assert unknown_provider.reason == "unknown_provider:provider_9999"

    too_soon = _schedule_via_action(
        sim, clock, action_id="s4", starts_in_hours=0.1
    )
    assert too_soon.status == "rejected"
    assert too_soon.reason == "param_out_of_range:starts_in_hours"


def test_clinic_cancel_and_reschedule_appointment():
    sim, clock = _sim("clinic")
    scheduled = _schedule_via_action(sim, clock)
    appt_id = scheduled.events[0]["payload"]["appointment_id"]

    moved = sim.apply_action(
        _req(
            "reschedule_appointment",
            {"appointment_id": appt_id, "starts_in_hours": 48},
            action_id="r1",
        ),
        clock,
    )
    assert moved.status == "applied"
    assert moved.events[0]["event_type"] == "appointment_rescheduled"
    assert moved.events[0]["payload"]["new_appointment_time"] == (
        START + timedelta(hours=48)
    ).isoformat()
    appt = sim.appointments[0]
    assert appt["scheduled_time"] == START + timedelta(hours=48)

    cancelled = sim.apply_action(
        _req("cancel_appointment", {"appointment_id": appt_id}, action_id="c1"),
        clock,
    )
    assert cancelled.status == "applied"
    assert cancelled.events[0]["event_type"] == "appointment_cancelled"
    assert cancelled.events[0]["payload"]["reason"] == "patient_request"
    assert appt["status"] == "cancelled"
    position = sim.position_snapshot()["position"]
    assert position["appointments_by_status"] == {"cancelled": 1}

    again = sim.apply_action(
        _req("cancel_appointment", {"appointment_id": appt_id}, action_id="c2"),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"appointment_not_scheduled:{appt_id}"

    unknown = sim.apply_action(
        _req("cancel_appointment", {"appointment_id": "appt_9999"},
             action_id="c3"),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_appointment:appt_9999"


def test_clinic_add_provider_shift():
    sim, clock = _sim("clinic")
    _, provider_id = _clinic_ids(sim)
    result = sim.apply_action(
        _req("add_provider_shift", {"provider_id": provider_id}), clock
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "provider_shift_added"
    assert payload["shift_hours"] == 8
    assert payload["shift_date"] == START.date().isoformat()
    assert sim.provider_shifts == [
        {"provider_id": provider_id, "date": START.date(), "hours": 8}
    ]

    unknown = sim.apply_action(
        _req("add_provider_shift", {"provider_id": "provider_9999"},
             action_id="a2"),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_provider:provider_9999"

    too_long = sim.apply_action(
        _req(
            "add_provider_shift",
            {"provider_id": provider_id, "shift_hours": 24},
            action_id="a3",
        ),
        clock,
    )
    assert too_long.status == "rejected"
    assert too_long.reason == "param_out_of_range:shift_hours"


def test_clinic_chase_claim():
    sim, clock = _sim("clinic")
    for _ in range(3000):
        if sim.claims:
            break
        sim.next_event(clock)
        clock.advance()
    assert sim.claims, "seed 42 should produce a claim"
    claim = sim.claims[0]

    result = sim.apply_action(
        _req("chase_claim", {"claim_id": claim["claim_id"]}), clock
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "claim_chased"
    assert payload["channel"] == "email"
    assert payload["chase_count"] == 1
    assert payload["patient_id"] == claim["patient_id"]
    assert claim["chase_count"] == 1
    assert claim["last_chased_at"] == payload["chased_at"]

    second = sim.apply_action(
        _req(
            "chase_claim",
            {"claim_id": claim["claim_id"], "channel": "portal"},
            action_id="a2",
        ),
        clock,
    )
    assert second.status == "applied"
    assert second.events[0]["payload"]["chase_count"] == 2

    unknown = sim.apply_action(
        _req("chase_claim", {"claim_id": "clm_9999"}, action_id="a3"), clock
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_claim:clm_9999"


# ----------------------------------------------------------------------
# Runner integration — rejection RNG rule + determinism (both)
# ----------------------------------------------------------------------


class _ListAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)


def _prepared(business_type: str):
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        events_per_second=1.0,
        seed=42,
        period_summaries_enabled=False,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(42)
    return config, sim


def _run_batch(config, sim, actions=None, max_events=40):
    config.max_events = max_events
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False, actions=actions)
    runner.clock = Clock(start_time=START, tick_seconds=1.0)
    runner.run()
    return [(e.event_type, e.payload) for e in adapter.events]


def test_gym_rejected_action_does_not_perturb_stream():
    config, sim = _prepared("gym")
    plain = _run_batch(config, sim)
    config2, sim2 = _prepared("gym")
    acted = _run_batch(
        config2,
        sim2,
        actions=[
            (10, _req("freeze_membership", {"member_id": "member_9999"}))
        ],
    )
    business = [p for p in acted if p[0] != "action_rejected"]
    assert business == plain
    rejected = [p for p in acted if p[0] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0][1]["reason"] == "unknown_member:member_9999"


def test_gym_determinism_with_actions():
    def script(ids):
        return [
            (5, _req("freeze_membership", {"member_id": ids["member"]})),
            (
                8,
                _req(
                    "schedule_class",
                    {
                        "class_type": "hiit",
                        "capacity": 15,
                        "duration_minutes": 60,
                    },
                    action_id="a2",
                ),
            ),
        ]

    config, sim = _prepared("gym")
    ids = {"member": sim._active_member_ids()[0]}
    run_a = _run_batch(config, sim, actions=script(ids))
    config2, sim2 = _prepared("gym")
    run_b = _run_batch(config2, sim2, actions=script(ids))
    assert run_a == run_b
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
    assert "membership_frozen" in types


def test_clinic_rejected_action_does_not_perturb_stream():
    config, sim = _prepared("clinic")
    plain = _run_batch(config, sim)
    config2, sim2 = _prepared("clinic")
    acted = _run_batch(
        config2,
        sim2,
        actions=[(10, _req("chase_claim", {"claim_id": "clm_9999"}))],
    )
    business = [p for p in acted if p[0] != "action_rejected"]
    assert business == plain
    rejected = [p for p in acted if p[0] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0][1]["reason"] == "unknown_claim:clm_9999"


def test_clinic_determinism_with_actions():
    def script(ids):
        return [
            (
                3,
                _req(
                    "schedule_appointment",
                    {
                        "patient_id": ids["patient"],
                        "provider_id": ids["provider"],
                        "appointment_type": "sick_visit",
                        "starts_in_hours": 12,
                    },
                ),
            ),
            (
                6,
                _req(
                    "add_provider_shift",
                    {"provider_id": ids["provider"], "shift_hours": 6},
                    action_id="a2",
                ),
            ),
        ]

    config, sim = _prepared("clinic")
    patient_id, provider_id = _clinic_ids(sim)
    ids = {"patient": patient_id, "provider": provider_id}
    run_a = _run_batch(config, sim, actions=script(ids))
    config2, sim2 = _prepared("clinic")
    run_b = _run_batch(config2, sim2, actions=script(ids))
    assert run_a == run_b
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
    assert "provider_shift_added" in types
