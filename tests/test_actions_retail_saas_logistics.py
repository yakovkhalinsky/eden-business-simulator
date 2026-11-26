"""Action catalogue tests (Phase 4c — ecommerce, saas, logistics).

Phase 4c completes the Phase 4 catalogues: all nine businesses now
declare actions. Follows the Phase 3/4a/4b patterns: happy path per
action (effect event + payload, internal state, and
``position_snapshot()`` reflection where the position exposes the
figure), the main rejection paths, approval gating for the
money-impacting actions, the rejection rule (no RNG consumed —
business stream identical to a no-action run apart from audit
events), and determinism with actions.
"""

from datetime import datetime, timezone
from typing import Any

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.base import ActionRequest
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner

START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)

# Payload fields stamped from the wall clock (not the simulated
# clock) by the natural emitters; normalised out of stream
# comparisons, as in the golden-stream tests.
_WALL_CLOCK_KEYS = {"registered_at", "signup_at"}


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


def _approval_flags(sim):
    return {a.action_type: a.requires_approval for a in sim.available_actions()}


# ----------------------------------------------------------------------
# Ecommerce
# ----------------------------------------------------------------------

ECOMMERCE_ACTIONS = [
    "adjust_price",
    "restock_product",
    "issue_refund",
    "expedite_order",
]


def test_ecommerce_available_actions_exact():
    sim, _ = _sim("ecommerce")
    assert [a.action_type for a in sim.available_actions()] == ECOMMERCE_ACTIONS
    assert _approval_flags(sim) == {
        "adjust_price": True,
        "restock_product": False,
        "issue_refund": True,
        "expedite_order": False,
    }


def test_ecommerce_adjust_price_requires_approval_then_applies():
    sim, clock = _sim("ecommerce")
    product = sim.products[0]
    old_price = product["price"]

    denied = sim.apply_action(
        _req(
            "adjust_price",
            {"product_id": product["product_id"], "new_price": 19.99},
        ),
        clock,
    )
    assert denied.status == "rejected"
    assert denied.reason == "requires_approval"
    assert product["price"] == old_price

    result = sim.apply_action(
        _req(
            "adjust_price",
            {"product_id": product["product_id"], "new_price": 19.99},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "product_price_adjusted"
    assert event["payload"]["old_price"] == old_price
    assert event["payload"]["new_price"] == 19.99
    assert product["price"] == 19.99


def test_ecommerce_adjust_price_rejections():
    sim, clock = _sim("ecommerce")
    unknown = sim.apply_action(
        _req(
            "adjust_price",
            {"product_id": "prod_9999", "new_price": 10.0},
            approved_by="yakov",
        ),
        clock,
    )
    assert unknown.status == "rejected"
    assert unknown.reason == "unknown_product:prod_9999"

    product = sim.products[0]
    out_of_range = sim.apply_action(
        _req(
            "adjust_price",
            {"product_id": product["product_id"], "new_price": 0.1},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert out_of_range.status == "rejected"
    assert out_of_range.reason == "param_out_of_range:new_price"


def test_ecommerce_restock_product():
    sim, clock = _sim("ecommerce")
    product = sim.products[0]
    before = product["inventory"]
    result = sim.apply_action(
        _req(
            "restock_product",
            {"product_id": product["product_id"], "qty": 25},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "inventory_adjusted"
    assert event["payload"]["delta"] == 25
    assert event["payload"]["new_inventory"] == before + 25
    assert event["payload"]["reason"] == "restock"
    position = sim.position_snapshot()["position"]
    assert position["inventory"][product["product_id"]] == before + 25

    unknown = sim.apply_action(
        _req(
            "restock_product",
            {"product_id": "prod_9999", "qty": 1},
            action_id="a2",
        ),
        clock,
    )
    assert unknown.reason == "unknown_product:prod_9999"


def _order_with_status(sim, clock, status):
    _drive_until(
        sim, clock, lambda: any(o["status"] == status for o in sim.orders)
    )
    return next(o for o in sim.orders if o["status"] == status)


def test_ecommerce_issue_refund_requires_approval_then_applies():
    sim, clock = _sim("ecommerce")
    order = _order_with_status(sim, clock, "paid")

    denied = sim.apply_action(
        _req("issue_refund", {"order_id": order["order_id"]}), clock
    )
    assert denied.status == "rejected"
    assert denied.reason == "requires_approval"
    assert order["status"] == "paid"

    result = sim.apply_action(
        _req(
            "issue_refund",
            {"order_id": order["order_id"]},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "refund_issued"
    assert event["payload"]["amount"] == order["total"]
    assert event["payload"]["reason"] == "manager_goodwill"
    assert order["status"] == "refunded"
    position = sim.position_snapshot()["position"]
    assert position["orders_by_status"].get("refunded", 0) >= 1
    assert position["refunds_total"] >= order["total"]


def test_ecommerce_issue_refund_wrong_status_and_unknown():
    sim, clock = _sim("ecommerce")
    placed = _order_with_status(sim, clock, "placed")
    result = sim.apply_action(
        _req(
            "issue_refund",
            {"order_id": placed["order_id"]},
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == f"order_not_refundable:{placed['order_id']}"

    unknown = sim.apply_action(
        _req(
            "issue_refund",
            {"order_id": "ord_999999"},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert unknown.reason == "unknown_order:ord_999999"


def test_ecommerce_expedite_order():
    sim, clock = _sim("ecommerce")
    order = _order_with_status(sim, clock, "paid")
    result = sim.apply_action(
        _req(
            "expedite_order",
            {"order_id": order["order_id"], "carrier": "DHL"},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "order_expedited"
    assert event["payload"]["carrier"] == "DHL"
    assert order["expedited"] is True
    assert order["carrier"] == "DHL"

    placed = _order_with_status(sim, clock, "placed")
    rejected = sim.apply_action(
        _req(
            "expedite_order",
            {"order_id": placed["order_id"]},
            action_id="a2",
        ),
        clock,
    )
    assert rejected.status == "rejected"
    assert rejected.reason == f"order_not_paid:{placed['order_id']}"


# ----------------------------------------------------------------------
# SaaS
# ----------------------------------------------------------------------

SAAS_ACTIONS = [
    "chase_invoice",
    "change_plan_price",
    "freeze_account",
    "unfreeze_account",
    "offer_discount",
    "resolve_ticket",
]


def test_saas_available_actions_exact():
    sim, _ = _sim("saas")
    assert [a.action_type for a in sim.available_actions()] == SAAS_ACTIONS
    assert _approval_flags(sim) == {
        "chase_invoice": False,
        "change_plan_price": True,
        "freeze_account": False,
        "unfreeze_account": False,
        "offer_discount": True,
        "resolve_ticket": False,
    }


def _open_invoice(sim, clock):
    _drive_until(
        sim, clock, lambda: any(i["status"] == "open" for i in sim.invoices)
    )
    return next(i for i in sim.invoices if i["status"] == "open")


def _active_subscription(sim, clock):
    _drive_until(
        sim,
        clock,
        lambda: any(
            s["status"] == "active" for s in sim.subscriptions.values()
        ),
    )
    return next(
        s for s in sim.subscriptions.values() if s["status"] == "active"
    )


def test_saas_chase_invoice():
    sim, clock = _sim("saas")
    invoice = _open_invoice(sim, clock)
    result = sim.apply_action(
        _req("chase_invoice", {"invoice_id": invoice["invoice_id"]}), clock
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "invoice_chased"
    assert event["payload"]["channel"] == "email"
    assert event["payload"]["chase_count"] == 1
    assert event["payload"]["amount"] == invoice["amount"]
    assert invoice["chase_count"] == 1

    again = sim.apply_action(
        _req(
            "chase_invoice",
            {"invoice_id": invoice["invoice_id"], "channel": "phone"},
            action_id="a2",
        ),
        clock,
    )
    assert again.status == "applied"
    assert invoice["chase_count"] == 2

    unknown = sim.apply_action(
        _req(
            "chase_invoice",
            {"invoice_id": "inv_999999"},
            action_id="a3",
        ),
        clock,
    )
    assert unknown.reason == "unknown_invoice:inv_999999"


def test_saas_chase_invoice_not_open():
    sim, clock = _sim("saas")
    _drive_until(
        sim, clock, lambda: any(i["status"] == "paid" for i in sim.invoices)
    )
    paid = next(i for i in sim.invoices if i["status"] == "paid")
    result = sim.apply_action(
        _req("chase_invoice", {"invoice_id": paid["invoice_id"]}), clock
    )
    assert result.status == "rejected"
    assert result.reason == f"invoice_not_open:{paid['invoice_id']}"


def test_saas_change_plan_price_moves_mrr():
    sim, clock = _sim("saas")
    subscription = _active_subscription(sim, clock)
    plan_name = subscription["plan_name"]
    active_on_plan = sum(
        1
        for s in sim.subscriptions.values()
        if s["plan_name"] == plan_name and s["status"] == "active"
    )
    old_fee = subscription["monthly_fee"]
    mrr_before = sim.position_snapshot()["position"]["mrr"]

    denied = sim.apply_action(
        _req(
            "change_plan_price",
            {"plan_name": plan_name, "new_monthly_fee": old_fee + 100},
        ),
        clock,
    )
    assert denied.status == "rejected"
    assert denied.reason == "requires_approval"
    assert sim.position_snapshot()["position"]["mrr"] == mrr_before

    result = sim.apply_action(
        _req(
            "change_plan_price",
            {"plan_name": plan_name, "new_monthly_fee": old_fee + 100},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "plan_price_changed"
    assert event["payload"]["old_price"] == old_fee
    assert event["payload"]["new_price"] == old_fee + 100
    assert subscription["monthly_fee"] == old_fee + 100
    mrr_after = sim.position_snapshot()["position"]["mrr"]
    assert mrr_after == round(mrr_before + 100 * active_on_plan, 2)


def test_saas_freeze_and_unfreeze_account():
    sim, clock = _sim("saas")
    subscription = _active_subscription(sim, clock)
    account_id = subscription["account_id"]
    active_before = sim.position_snapshot()["position"][
        "active_subscriptions"
    ]

    frozen = sim.apply_action(
        _req("freeze_account", {"account_id": account_id}), clock
    )
    assert frozen.status == "applied"
    (event,) = frozen.events
    assert event["event_type"] == "account_frozen"
    assert event["payload"]["reason"] == "manager_action"
    assert subscription["status"] == "frozen"
    position = sim.position_snapshot()["position"]
    assert position["active_subscriptions"] == active_before - 1

    again = sim.apply_action(
        _req(
            "freeze_account",
            {"account_id": account_id},
            action_id="a2",
        ),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"subscription_not_active:{account_id}"

    unfrozen = sim.apply_action(
        _req(
            "unfreeze_account",
            {"account_id": account_id},
            action_id="a3",
        ),
        clock,
    )
    assert unfrozen.status == "applied"
    assert unfrozen.events[0]["event_type"] == "account_unfrozen"
    assert subscription["status"] == "active"
    position = sim.position_snapshot()["position"]
    assert position["active_subscriptions"] == active_before


def test_saas_freeze_rejections():
    sim, clock = _sim("saas")
    unknown = sim.apply_action(
        _req("freeze_account", {"account_id": "acc_9999"}), clock
    )
    assert unknown.reason == "unknown_account:acc_9999"

    # Initial accounts exist from initialize(); at least one has no
    # subscription before any events are driven.
    no_sub_account = next(
        a
        for a in sim.accounts
        if a["account_id"] not in sim.subscriptions
    )
    result = sim.apply_action(
        _req(
            "freeze_account",
            {"account_id": no_sub_account["account_id"]},
            action_id="a2",
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == (
        f"no_subscription:{no_sub_account['account_id']}"
    )


def test_saas_offer_discount():
    sim, clock = _sim("saas")
    subscription = _active_subscription(sim, clock)
    account_id = subscription["account_id"]

    denied = sim.apply_action(
        _req(
            "offer_discount",
            {"account_id": account_id, "percent": 25},
        ),
        clock,
    )
    assert denied.status == "rejected"
    assert denied.reason == "requires_approval"

    result = sim.apply_action(
        _req(
            "offer_discount",
            {"account_id": account_id, "percent": 25, "months": 3},
            action_id="a2",
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "discount_offered"
    assert event["payload"]["subscription_id"] == (
        subscription["subscription_id"]
    )
    assert event["payload"]["percent"] == 25
    assert event["payload"]["duration_months"] == 3
    assert subscription["discount"]["percent"] == 25

    out_of_range = sim.apply_action(
        _req(
            "offer_discount",
            {"account_id": account_id, "percent": 95},
            action_id="a3",
            approved_by="yakov",
        ),
        clock,
    )
    assert out_of_range.status == "rejected"
    assert out_of_range.reason == "param_out_of_range:percent"


def test_saas_offer_discount_without_subscription():
    sim, clock = _sim("saas")
    account = next(
        a
        for a in sim.accounts
        if a["account_id"] not in sim.subscriptions
    )
    result = sim.apply_action(
        _req(
            "offer_discount",
            {"account_id": account["account_id"], "percent": 10},
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["payload"]["subscription_id"] is None
    assert event["payload"]["duration_months"] == 1
    assert len(sim.discounts) == 1
    assert sim.discounts[0]["account_id"] == account["account_id"]


def test_saas_resolve_ticket():
    sim, clock = _sim("saas")
    _drive_until(
        sim, clock, lambda: any(t["status"] == "open" for t in sim.tickets)
    )
    ticket = next(t for t in sim.tickets if t["status"] == "open")
    result = sim.apply_action(
        _req("resolve_ticket", {"ticket_id": ticket["ticket_id"]}), clock
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "ticket_resolved"
    assert event["payload"]["resolution"] == "answered"
    assert ticket["status"] == "resolved"
    position = sim.position_snapshot()["position"]
    assert position["open_tickets_count"] == sum(
        1 for t in sim.tickets if t["status"] == "open"
    )

    again = sim.apply_action(
        _req(
            "resolve_ticket",
            {"ticket_id": ticket["ticket_id"]},
            action_id="a2",
        ),
        clock,
    )
    assert again.status == "rejected"
    assert again.reason == f"ticket_not_open:{ticket['ticket_id']}"

    unknown = sim.apply_action(
        _req(
            "resolve_ticket",
            {"ticket_id": "tkt_9999"},
            action_id="a3",
        ),
        clock,
    )
    assert unknown.reason == "unknown_ticket:tkt_9999"


# ----------------------------------------------------------------------
# Logistics
# ----------------------------------------------------------------------

LOGISTICS_ACTIONS = [
    "assign_driver",
    "replan_route",
    "expedite_shipment",
    "initiate_return",
]


def test_logistics_available_actions_exact():
    sim, _ = _sim("logistics")
    assert [a.action_type for a in sim.available_actions()] == LOGISTICS_ACTIONS
    assert all(not a.requires_approval for a in sim.available_actions())


def _planned_route(sim, clock):
    _drive_until(
        sim, clock, lambda: any(r["status"] == "planned" for r in sim.routes)
    )
    return next(r for r in sim.routes if r["status"] == "planned")


def _delivered_shipment(sim, clock):
    _drive_until(
        sim,
        clock,
        lambda: any(s["status"] == "delivered" for s in sim.shipments),
    )
    return next(s for s in sim.shipments if s["status"] == "delivered")


def test_logistics_assign_driver():
    sim, clock = _sim("logistics")
    route = _planned_route(sim, clock)
    other_driver = next(
        d.actor_id
        for d in sim.drivers.all()
        if d.actor_id != route["driver_id"]
    )
    result = sim.apply_action(
        _req(
            "assign_driver",
            {"route_id": route["route_id"], "driver_id": other_driver},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "driver_assigned"
    assert event["payload"]["driver_id"] == other_driver
    assert event["payload"]["vehicle_id"] == route["vehicle_id"]
    assert event["payload"]["driver_app_version"] == "3.2.1"
    assert route["driver_id"] == other_driver


def test_logistics_assign_driver_rejections():
    sim, clock = _sim("logistics")
    route = _planned_route(sim, clock)
    driver_id = sim.drivers.all()[0].actor_id

    unknown_route = sim.apply_action(
        _req(
            "assign_driver",
            {"route_id": "rte_9999", "driver_id": driver_id},
        ),
        clock,
    )
    assert unknown_route.reason == "unknown_route:rte_9999"

    unknown_driver = sim.apply_action(
        _req(
            "assign_driver",
            {"route_id": route["route_id"], "driver_id": "driver_9999"},
            action_id="a2",
        ),
        clock,
    )
    assert unknown_driver.reason == "unknown_driver:driver_9999"

    route["status"] = "started"
    not_planned = sim.apply_action(
        _req(
            "assign_driver",
            {"route_id": route["route_id"], "driver_id": driver_id},
            action_id="a3",
        ),
        clock,
    )
    assert not_planned.status == "rejected"
    assert not_planned.reason == f"route_not_planned:{route['route_id']}"


def test_logistics_replan_route_reverses_stops():
    sim, clock = _sim("logistics")
    _drive_until(
        sim,
        clock,
        lambda: any(
            r["status"] == "planned" and len(r["stops"]) >= 2
            for r in sim.routes
        ),
    )
    route = next(
        r
        for r in sim.routes
        if r["status"] == "planned" and len(r["stops"]) >= 2
    )
    before = list(route["stops"])
    result = sim.apply_action(
        _req("replan_route", {"route_id": route["route_id"]}), clock
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "route_replanned"
    expected = list(reversed(before))
    assert route["stops"] == expected
    assert [s["stop_id"] for s in event["payload"]["stops"]] == expected
    assert [s["sequence"] for s in event["payload"]["stops"]] == list(
        range(1, len(expected) + 1)
    )
    # The stop records themselves carry the new sequence numbers.
    stops_by_id = {s["stop_id"]: s for s in sim.stops}
    for idx, stop_id in enumerate(expected):
        assert stops_by_id[stop_id]["sequence"] == idx + 1


def test_logistics_replan_route_rejections():
    sim, clock = _sim("logistics")
    route = _planned_route(sim, clock)
    route["status"] = "completed"
    result = sim.apply_action(
        _req("replan_route", {"route_id": route["route_id"]}), clock
    )
    assert result.status == "rejected"
    assert result.reason == f"route_not_replannable:{route['route_id']}"

    unknown = sim.apply_action(
        _req("replan_route", {"route_id": "rte_9999"}, action_id="a2"),
        clock,
    )
    assert unknown.reason == "unknown_route:rte_9999"


def test_logistics_expedite_shipment():
    sim, clock = _sim("logistics")
    shipment = sim.shipments[0]
    old_level = shipment["service_level"]
    result = sim.apply_action(
        _req(
            "expedite_shipment",
            {"shipment_id": shipment["shipment_id"]},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "shipment_expedited"
    assert event["payload"]["old_service_level"] == old_level
    assert event["payload"]["new_service_level"] == "same_day"
    assert shipment["service_level"] == "same_day"
    assert shipment["expedited"] is True


def test_logistics_expedite_delivered_shipment_rejected():
    sim, clock = _sim("logistics")
    delivered = _delivered_shipment(sim, clock)
    result = sim.apply_action(
        _req(
            "expedite_shipment",
            {"shipment_id": delivered["shipment_id"]},
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == (
        f"shipment_already_delivered:{delivered['shipment_id']}"
    )

    unknown = sim.apply_action(
        _req(
            "expedite_shipment",
            {"shipment_id": "shp_9999"},
            action_id="a2",
        ),
        clock,
    )
    assert unknown.reason == "unknown_shipment:shp_9999"


def test_logistics_initiate_return():
    sim, clock = _sim("logistics")
    delivered = _delivered_shipment(sim, clock)
    returns_before = sim.position_snapshot()["position"]["returns_count"]
    result = sim.apply_action(
        _req(
            "initiate_return",
            {"shipment_id": delivered["shipment_id"]},
        ),
        clock,
    )
    assert result.status == "applied"
    (event,) = result.events
    assert event["event_type"] == "return_initiated"
    assert event["payload"]["reason"] == "wrong_item"
    assert event["payload"]["shipment_id"] == delivered["shipment_id"]
    position = sim.position_snapshot()["position"]
    assert position["returns_count"] == returns_before + 1


def test_logistics_initiate_return_not_delivered():
    sim, clock = _sim("logistics")
    created = next(s for s in sim.shipments if s["status"] == "created")
    result = sim.apply_action(
        _req(
            "initiate_return",
            {"shipment_id": created["shipment_id"]},
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == (
        f"shipment_not_delivered:{created['shipment_id']}"
    )


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


def _normalised(events):
    return [
        (
            event_type,
            {
                k: v
                for k, v in payload.items()
                if k not in _WALL_CLOCK_KEYS
            },
        )
        for event_type, payload in events
    ]


def _run_batch(config, sim, actions=None, max_events=40):
    config.max_events = max_events
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False, actions=actions)
    runner.clock = Clock(start_time=START, tick_seconds=1.0)
    runner.run()
    return [(e.event_type, e.payload) for e in adapter.events]


def test_ecommerce_rejected_action_does_not_perturb_stream():
    config, sim = _prepared("ecommerce")
    plain = _run_batch(config, sim)
    config2, sim2 = _prepared("ecommerce")
    acted = _run_batch(
        config2,
        sim2,
        actions=[
            (
                10,
                _req(
                    "restock_product",
                    {"product_id": "prod_9999", "qty": 5},
                ),
            )
        ],
    )
    business = [p for p in acted if p[0] != "action_rejected"]
    assert _normalised(business) == _normalised(plain)
    rejected = [p for p in acted if p[0] == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0][1]["reason"] == "unknown_product:prod_9999"


def test_saas_determinism_with_actions():
    def script():
        return [
            (
                5,
                _req(
                    "offer_discount",
                    {"account_id": "acc_0001", "percent": 10},
                    approved_by="yakov",
                ),
            ),
            (
                8,
                _req(
                    "change_plan_price",
                    {"plan_name": "starter", "new_monthly_fee": 39.0},
                    action_id="a2",
                    approved_by="yakov",
                ),
            ),
        ]

    config, sim = _prepared("saas")
    run_a = _run_batch(config, sim, actions=script())
    config2, sim2 = _prepared("saas")
    run_b = _run_batch(config2, sim2, actions=script())
    assert _normalised(run_a) == _normalised(run_b)
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
    assert types.count("discount_offered") == 1
    assert types.count("plan_price_changed") == 1


def test_logistics_determinism_with_actions():
    def script():
        return [
            (
                2,
                _req(
                    "expedite_shipment",
                    {"shipment_id": "shp_0001"},
                ),
            ),
            (
                3,
                _req(
                    "expedite_shipment",
                    {"shipment_id": "shp_0002"},
                    action_id="a2",
                ),
            ),
        ]

    config, sim = _prepared("logistics")
    run_a = _run_batch(config, sim, actions=script())
    config2, sim2 = _prepared("logistics")
    run_b = _run_batch(config2, sim2, actions=script())
    assert run_a == run_b
    types = [t for t, _ in run_a]
    assert types.count("action_applied") == 2
    assert types.count("shipment_expedited") == 2
