"""Action catalogue tests (Phase 4b, trades).

One implementation on ``FieldServiceSimulator`` serves all three
trades businesses; they declare the same six actions in their
definitions and differ only by configured categories and parts
catalogues. Follows the Phase 3/4a patterns: happy path per action,
the main rejection paths, the rejection rule (no RNG consumed —
business stream identical to a no-action run apart from audit
events), determinism with actions, and exact ``available_actions()``
sets. The config-proof tests show a part valid for one trade is
rejected as unknown by another trade whose catalogue lacks it.
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

EXPECTED_ACTIONS = [
    "assign_technician",
    "dispatch_technician",
    "order_parts",
    "approve_estimate",
    "chase_invoice",
    "schedule_follow_up",
]

TRADES = ["field_service", "electrician", "plumber"]


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


def _drive_until(sim, clock, predicate, limit=20000):
    for _ in range(limit):
        if predicate():
            return
        sim.next_event(clock)
        clock.advance()
    assert predicate(), "drive limit reached without reaching the state"


def _open_ticket(sim, clock):
    _drive_until(
        sim, clock, lambda: any(t["status"] == "open" for t in sim.tickets)
    )
    return next(t for t in sim.tickets if t["status"] == "open")


def _tech_id(sim):
    return sim.techs.all()[0].actor_id


# ----------------------------------------------------------------------
# Availability — exact, identical for all three trades
# ----------------------------------------------------------------------


def test_trades_available_actions_exact():
    for business_type in TRADES:
        sim, _ = _sim(business_type)
        actions = sim.available_actions()
        assert [a.action_type for a in actions] == EXPECTED_ACTIONS
        # No trades action requires approval (routine operations).
        assert all(not a.requires_approval for a in actions)


# ----------------------------------------------------------------------
# field_service — direct apply_action
# ----------------------------------------------------------------------


def test_assign_and_dispatch_technician():
    sim, clock = _sim("field_service")
    ticket = _open_ticket(sim, clock)
    tech_id = _tech_id(sim)
    assigned_before = sim.position_snapshot()["position"][
        "tickets_by_status"
    ].get("assigned", 0)

    result = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": ticket["ticket_id"], "technician_id": tech_id},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "technician_assigned"
    payload = event["payload"]
    assert payload["technician_id"] == tech_id
    assert payload["vehicle_id"] == sim.vehicles[0]["vehicle_id"]
    assert payload["estimated_arrival"] == (
        clock.now + timedelta(minutes=60)
    ).isoformat()
    assert ticket["status"] == "assigned"
    assert ticket["technician_id"] == tech_id
    assigned_after = sim.position_snapshot()["position"][
        "tickets_by_status"
    ].get("assigned", 0)
    assert assigned_after == assigned_before + 1

    dispatched = sim.apply_action(
        _req(
            "dispatch_technician",
            {"ticket_id": ticket["ticket_id"]},
            action_id="a2",
        ),
        clock,
    )
    assert dispatched.status == "applied"
    payload = dispatched.events[0]["payload"]
    assert dispatched.events[0]["event_type"] == "technician_dispatched"
    assert payload["location"] == {"lat": -33.8688, "lon": 151.2093}
    assert ticket["status"] == "dispatched"


def test_assign_technician_rejections():
    sim, clock = _sim("field_service")
    ticket = _open_ticket(sim, clock)
    tech_id = _tech_id(sim)

    unknown_ticket = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": "tkt_9999", "technician_id": tech_id},
        ),
        clock,
    )
    assert unknown_ticket.status == "rejected"
    assert unknown_ticket.reason == "unknown_ticket:tkt_9999"

    unknown_tech = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": ticket["ticket_id"], "technician_id": "tech_9999"},
            action_id="a2",
        ),
        clock,
    )
    assert unknown_tech.status == "rejected"
    assert unknown_tech.reason == "unknown_technician:tech_9999"

    bad_eta = sim.apply_action(
        _req(
            "assign_technician",
            {
                "ticket_id": ticket["ticket_id"],
                "technician_id": tech_id,
                "eta_minutes": 0,
            },
            action_id="a3",
        ),
        clock,
    )
    assert bad_eta.status == "rejected"
    assert bad_eta.reason == "param_out_of_range:eta_minutes"
    assert ticket["status"] == "open"

    applied = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": ticket["ticket_id"], "technician_id": tech_id},
            action_id="a4",
        ),
        clock,
    )
    assert applied.status == "applied"
    again = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": ticket["ticket_id"], "technician_id": tech_id},
            action_id="a5",
        ),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"ticket_not_open:{ticket['ticket_id']}"


def test_dispatch_technician_wrong_status():
    sim, clock = _sim("field_service")
    ticket = _open_ticket(sim, clock)
    result = sim.apply_action(
        _req("dispatch_technician", {"ticket_id": ticket["ticket_id"]}),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == f"ticket_not_assigned:{ticket['ticket_id']}"
    assert ticket["status"] == "open"


def test_order_parts_happy_and_rejections():
    sim, clock = _sim("field_service")
    result = sim.apply_action(
        _req("order_parts", {"part_id": "prt_002", "qty": 5}), clock
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "parts_reorder_placed"
    assert event["payload"]["parts"] == [{"part_id": "prt_002", "qty": 5}]
    assert sim.parts_orders == [
        {
            "reorder_id": event["payload"]["reorder_id"],
            "part_id": "prt_002",
            "qty": 5,
            "ordered_at": event["payload"]["placed_at"],
        }
    ]

    unknown = sim.apply_action(
        _req("order_parts", {"part_id": "prt_999", "qty": 1}, action_id="a2"),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_part:prt_999"
    assert len(sim.parts_orders) == 1

    for qty, action_id in [(0, "a3"), (101, "a4")]:
        bad = sim.apply_action(
            _req(
                "order_parts",
                {"part_id": "prt_002", "qty": qty},
                action_id=action_id,
            ),
            clock,
        )
        assert bad.status == "rejected"
        assert bad.reason == "param_out_of_range:qty"


def test_approve_estimate_happy_and_rejections():
    sim, clock = _sim("field_service")
    ticket = _open_ticket(sim, clock)
    result = sim.apply_action(
        _req(
            "approve_estimate",
            {
                "ticket_id": ticket["ticket_id"],
                "labor_estimate": 150,
                "parts_estimate": 50,
            },
        ),
        clock,
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "estimate_approved"
    assert payload["total_estimate"] == 200.0
    assert payload["tax_amount"] == 20.0
    assert payload["tax_type"] == "GST"
    assert ticket["estimate_approved"] is True
    assert ticket["labor_estimate"] == 150.0
    assert ticket["parts_estimate"] == 50.0

    again = sim.apply_action(
        _req(
            "approve_estimate",
            {
                "ticket_id": ticket["ticket_id"],
                "labor_estimate": 100,
                "parts_estimate": 10,
            },
            action_id="a2",
        ),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"estimate_already_approved:{ticket['ticket_id']}"

    too_cheap = sim.apply_action(
        _req(
            "approve_estimate",
            {
                "ticket_id": ticket["ticket_id"],
                "labor_estimate": 5,
                "parts_estimate": 10,
            },
            action_id="a3",
        ),
        clock,
    )
    assert too_cheap.status == "rejected"
    assert too_cheap.reason == "param_out_of_range:labor_estimate"


def test_approve_estimate_wrong_status():
    sim, clock = _sim("field_service")
    ticket = _open_ticket(sim, clock)
    assigned = sim.apply_action(
        _req(
            "assign_technician",
            {"ticket_id": ticket["ticket_id"], "technician_id": _tech_id(sim)},
        ),
        clock,
    )
    assert assigned.status == "applied"
    result = sim.apply_action(
        _req(
            "approve_estimate",
            {
                "ticket_id": ticket["ticket_id"],
                "labor_estimate": 100,
                "parts_estimate": 10,
            },
            action_id="a2",
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == f"ticket_not_open:{ticket['ticket_id']}"


def _open_invoice(sim, clock):
    _drive_until(
        sim,
        clock,
        lambda: any(i["status"] == "open" for i in sim.invoices),
    )
    return next(i for i in sim.invoices if i["status"] == "open")


def test_chase_invoice_happy_and_rejections():
    sim, clock = _sim("field_service")
    invoice = _open_invoice(sim, clock)

    result = sim.apply_action(
        _req("chase_invoice", {"invoice_id": invoice["invoice_id"]}), clock
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "invoice_chased"
    assert payload["channel"] == "email"
    assert payload["amount"] == invoice["total"]
    assert payload["ticket_id"] == invoice["ticket_id"]
    assert payload["chase_count"] == 1
    assert invoice["chase_count"] == 1
    assert invoice["last_chased_at"] == payload["chased_at"]

    second = sim.apply_action(
        _req(
            "chase_invoice",
            {"invoice_id": invoice["invoice_id"], "channel": "sms"},
            action_id="a2",
        ),
        clock,
    )
    assert second.status == "applied"
    assert second.events[0]["payload"]["chase_count"] == 2

    unknown = sim.apply_action(
        _req("chase_invoice", {"invoice_id": "inv_9999"}, action_id="a3"),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_invoice:inv_9999"

    invoice["status"] = "paid"
    paid = sim.apply_action(
        _req(
            "chase_invoice",
            {"invoice_id": invoice["invoice_id"]},
            action_id="a4",
        ),
        clock,
    )
    assert paid.status == "rejected"
    assert paid.reason == f"invoice_not_open:{invoice['invoice_id']}"
    assert invoice["chase_count"] == 2


def test_schedule_follow_up_happy_and_wrong_status():
    sim, clock = _sim("field_service")
    open_ticket = _open_ticket(sim, clock)
    wrong = sim.apply_action(
        _req("schedule_follow_up", {"ticket_id": open_ticket["ticket_id"]}),
        clock,
    )
    assert wrong.status == "rejected"
    assert wrong.reason == f"ticket_not_completed:{open_ticket['ticket_id']}"

    _drive_until(
        sim,
        clock,
        lambda: any(
            t["status"] in ("completed", "invoiced") for t in sim.tickets
        ),
    )
    ticket = next(
        t for t in sim.tickets if t["status"] in ("completed", "invoiced")
    )
    result = sim.apply_action(
        _req(
            "schedule_follow_up",
            {"ticket_id": ticket["ticket_id"]},
            action_id="a2",
        ),
        clock,
    )
    assert result.status == "applied"
    payload = result.events[0]["payload"]
    assert result.events[0]["event_type"] == "follow_up_scheduled"
    assert payload["follow_up_type"] == "maintenance_check"
    assert payload["scheduled_date"] == (
        clock.now + timedelta(days=30)
    ).date().isoformat()

    custom = sim.apply_action(
        _req(
            "schedule_follow_up",
            {
                "ticket_id": ticket["ticket_id"],
                "days_ahead": 7,
                "follow_up_type": "warranty_check",
            },
            action_id="a3",
        ),
        clock,
    )
    assert custom.status == "applied"
    assert custom.events[0]["payload"]["scheduled_date"] == (
        clock.now + timedelta(days=7)
    ).date().isoformat()

    too_far = sim.apply_action(
        _req(
            "schedule_follow_up",
            {"ticket_id": ticket["ticket_id"], "days_ahead": 181},
            action_id="a4",
        ),
        clock,
    )
    assert too_far.status == "rejected"
    assert too_far.reason == "param_out_of_range:days_ahead"


# ----------------------------------------------------------------------
# electrician + plumber — own catalogues, cross-trade config proof
# ----------------------------------------------------------------------


def test_electrician_and_plumber_order_parts_own_catalogue():
    for business_type, part_id in [
        ("electrician", "prt_e001"),
        ("plumber", "prt_p001"),
    ]:
        sim, clock = _sim(business_type)
        result = sim.apply_action(
            _req("order_parts", {"part_id": part_id, "qty": 2}), clock
        )
        assert result.status == "applied"
        assert result.events[0]["payload"]["parts"] == [
            {"part_id": part_id, "qty": 2}
        ]
        assert [o["part_id"] for o in sim.parts_orders] == [part_id]


def test_parts_catalogues_are_business_specific():
    # prt_e001 (electrician) is unknown to plumber and field_service;
    # prt_p001 (plumber) is unknown to electrician; prt_001
    # (field_service) is unknown to electrician.
    cases = [
        ("electrician", "prt_p001"),
        ("electrician", "prt_001"),
        ("plumber", "prt_e001"),
        ("field_service", "prt_e001"),
        ("field_service", "prt_p001"),
    ]
    for business_type, part_id in cases:
        sim, clock = _sim(business_type)
        result = sim.apply_action(
            _req("order_parts", {"part_id": part_id, "qty": 1}), clock
        )
        assert result.status == "rejected", (business_type, part_id)
        assert result.reason == f"unknown_part:{part_id}"
        assert sim.parts_orders == []


# ----------------------------------------------------------------------
# Runner integration — rejection RNG rule + determinism
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


def test_field_service_rejected_action_does_not_perturb_stream():
    config, sim = _prepared("field_service")
    plain = _run_batch(config, sim)
    config2, sim2 = _prepared("field_service")
    acted = _run_batch(
        config2,
        sim2,
        actions=[
            (10, _req("order_parts", {"part_id": "prt_999", "qty": 1}))
        ],
    )
    business = [p for p in acted if p[0] != "action_rejected"]
    assert business == plain
    rejected = [p for p in acted if p[0] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0][1]["reason"] == "unknown_part:prt_999"


def test_field_service_determinism_with_actions():
    def script():
        return [
            (5, _req("order_parts", {"part_id": "prt_001", "qty": 3})),
            (
                8,
                _req(
                    "order_parts",
                    {"part_id": "prt_003", "qty": 10},
                    action_id="a2",
                ),
            ),
        ]

    config, sim = _prepared("field_service")
    run_a = _run_batch(config, sim, actions=script())
    config2, sim2 = _prepared("field_service")
    run_b = _run_batch(config2, sim2, actions=script())
    assert run_a == run_b
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
    assert types.count("parts_reorder_placed") >= 2


def test_field_service_direct_determinism_with_assign():
    outcomes = []
    for _ in range(2):
        sim, clock = _sim("field_service")
        ticket = _open_ticket(sim, clock)
        assigned = sim.apply_action(
            _req(
                "assign_technician",
                {
                    "ticket_id": ticket["ticket_id"],
                    "technician_id": _tech_id(sim),
                    "eta_minutes": 45,
                },
            ),
            clock,
        )
        subsequent = []
        for _ in range(20):
            subsequent.append(sim.next_event(clock))
            clock.advance()
        outcomes.append(
            (assigned, subsequent, sim.position_snapshot())
        )
    assert outcomes[0] == outcomes[1]


def test_plumber_determinism_with_actions():
    def script():
        return [
            (5, _req("order_parts", {"part_id": "prt_p002", "qty": 4})),
            (
                9,
                _req(
                    "order_parts",
                    {"part_id": "prt_p004", "qty": 1},
                    action_id="a2",
                ),
            ),
        ]

    config, sim = _prepared("plumber")
    run_a = _run_batch(config, sim, actions=script())
    config2, sim2 = _prepared("plumber")
    run_b = _run_batch(config2, sim2, actions=script())
    assert run_a == run_b
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
