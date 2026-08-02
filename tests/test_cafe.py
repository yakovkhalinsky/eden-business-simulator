"""Tests for the cafe simulator."""

from datetime import datetime, timezone

from eden_business_simulator.businesses.cafe import CafeSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock


def _make_sim(
    seed: int = 42,
    duration: float = 60.0,
    opening_float: float = 150.0,
    initial_members: int = 5,
) -> CafeSimulator:
    sim = CafeSimulator()
    cfg = SimulatorConfig(
        business_type="cafe",
        seed=seed,
        duration_seconds=duration,
        initial_state_overrides={
            "opening_float": opening_float,
            "initial_members": initial_members,
        },
    )
    sim.configure(cfg)
    sim.initialize(seed)
    return sim


def _collect_event_types(
    sim: CafeSimulator,
    clock: Clock,
    target: set[str] | None = None,
    max_events: int = 1000,
) -> set[str]:
    seen: set[str] = set()
    for _ in range(max_events):
        event = sim.next_event(clock)
        seen.add(event["event_type"])
        clock.advance()
        if target is not None and target.issubset(seen):
            break
    return seen


def test_initialization_creates_state():
    sim = _make_sim(seed=42, duration=3600)
    snapshot = sim.state_snapshot()
    assert snapshot["shift_opened"] is False
    assert snapshot["staff_count"] == 0
    assert snapshot["menu_item_count"] == 0
    assert snapshot["ingredient_count"] == 9
    assert snapshot["member_count"] == 5


def test_seed_determinism():
    clock_a = Clock(start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc))
    clock_b = Clock(start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc))
    sim_a = _make_sim(seed=123)
    sim_b = _make_sim(seed=123)

    events_a = [sim_a.next_event(clock_a)["event_type"] for _ in range(30)]
    events_b = [sim_b.next_event(clock_b)["event_type"] for _ in range(30)]
    assert events_a == events_b

    payload_a = sim_a.next_event(clock_a)["payload"]
    payload_b = sim_b.next_event(clock_b)["payload"]
    assert list(payload_a.keys()) == list(payload_b.keys())


def test_available_event_types():
    sim = _make_sim()
    assert sim.available_event_types() == [
        "shift_opened",
        "staff_clocked_in",
        "menu_item_added",
        "supplier_delivery_received",
        "table_occupied",
        "order_taken",
        "item_fired_to_kitchen",
        "item_prepared",
        "order_paid",
        "loyalty_points_earned",
        "wastage_logged",
        "stock_count_recorded",
        "shift_closed",
    ]


def test_setup_events_all_appear():
    sim = _make_sim(seed=42, duration=3600)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    seen = _collect_event_types(
        sim,
        clock,
        target={
            "shift_opened",
            "staff_clocked_in",
            "menu_item_added",
            "supplier_delivery_received",
        },
        max_events=50,
    )
    assert "shift_opened" in seen
    assert "staff_clocked_in" in seen
    assert "menu_item_added" in seen
    assert "supplier_delivery_received" in seen
    assert len(sim.menu.all()) == len(CafeSimulator._MENU)
    assert len(sim.staff.all()) == len(CafeSimulator._STAFF)


def test_service_events_appear_before_close():
    sim = _make_sim(seed=42, duration=28_800)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 7, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    # Skip the setup queue deterministically.
    while sim._pending_setup:
        sim.next_event(clock)
        clock.advance()

    target = {
        "table_occupied",
        "order_taken",
        "item_fired_to_kitchen",
        "item_prepared",
        "order_paid",
        "loyalty_points_earned",
        "wastage_logged",
        "stock_count_recorded",
    }
    seen = _collect_event_types(sim, clock, target=target, max_events=400)
    assert target.issubset(seen), f"Missing service events: {target - seen}"


def test_shift_closed_appears_at_end():
    sim = _make_sim(seed=42, duration=3_600)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    seen = _collect_event_types(
        sim, clock, target={"shift_closed"}, max_events=200
    )
    assert "shift_closed" in seen
    assert sim._shift_closed is True
    assert sim.state_snapshot()["shift_closed"] is True


def test_order_lifecycle():
    sim = _make_sim(seed=7, duration=3_600)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    # Run through setup.
    while sim._pending_setup:
        sim.next_event(clock)
        clock.advance()

    taken = sim._emit_order_taken(clock)
    order_id = taken["payload"]["order_id"]
    assert sim.order_machine.get(order_id) == "new"

    tickets = sim._tickets_for_order(order_id)
    assert tickets
    for _ in tickets:
        sim._emit_item_fired_to_kitchen(clock)
    assert sim.order_machine.get(order_id) == "fired"

    for _ in tickets:
        sim._emit_item_prepared(clock)
    assert sim.order_machine.get(order_id) == "ready"

    paid = sim._emit_order_paid(clock)
    assert sim.order_machine.get(order_id) == "paid"
    assert paid["payload"]["amount"] == taken["payload"]["subtotal"]
    assert paid["payload"]["payment_id"].startswith("pay_")

    loyalty = sim._emit_loyalty_points_earned(clock)
    assert loyalty["payload"]["order_id"] == order_id
    assert loyalty["payload"]["member_id"].startswith("member_")


def test_shift_closed_payload():
    sim = _make_sim(seed=42, duration=3_600)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    # Advance until close event is emitted.
    seen = _collect_event_types(
        sim, clock, target={"shift_closed"}, max_events=200
    )
    assert "shift_closed" in seen
    # Find the close event payload from state for simplicity.
    close_event = sim._emit_shift_closed(clock)
    payload = close_event["payload"]
    assert payload["register_id"].startswith("reg_")
    assert payload["opening_float"] == 150.0
    assert "closing_float" in payload
    assert "cash_sales" in payload
    assert "card_sales" in payload
    assert "discrepancy" in payload
    assert "waste_total" in payload


def test_event_payload_has_required_keys():
    sim = _make_sim(seed=42, duration=3_600)
    clock = Clock(
        start_time=datetime(2026, 8, 2, 6, 0, 0, tzinfo=timezone.utc),
        tick_seconds=60.0,
    )
    for _ in range(50):
        event = sim.next_event(clock)
        assert "event_type" in event
        assert "payload" in event
        assert isinstance(event["payload"], dict)
        assert event["event_type"] in sim.available_event_types()
        clock.advance()
