"""Tests for the e-commerce simulator."""

from datetime import datetime, timezone

from eden_business_simulator.businesses.ecommerce import EcommerceSimulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock


def _make_sim(seed: int = 42, initial_customers: int = 5) -> EcommerceSimulator:
    sim = EcommerceSimulator()
    cfg = SimulatorConfig(
        business_type="ecommerce",
        seed=seed,
        initial_state_overrides={"initial_customers": initial_customers},
    )
    sim.configure(cfg)
    sim.initialize(seed)
    return sim


def test_initialization_creates_state():
    sim = _make_sim()
    assert len(sim.products) == 20
    assert len(sim.customers) == 5
    assert len(sim.carts) == 5
    assert sim.state_snapshot()["customer_count"] == 5


def test_seed_determinism():
    sim_a = _make_sim(seed=123)
    sim_b = _make_sim(seed=123)
    assert sim_a.state_snapshot() == sim_b.state_snapshot()
    assert sim_a.customers[0]["email"] == sim_b.customers[0]["email"]

    events_a = [sim_a.next_event(Clock())["event_type"] for _ in range(20)]
    events_b = [sim_b.next_event(Clock())["event_type"] for _ in range(20)]
    assert events_a == events_b


def test_available_event_types():
    sim = _make_sim()
    assert "customer_created" in sim.available_event_types()
    assert "order_placed" in sim.available_event_types()
    assert "refund_issued" in sim.available_event_types()


def test_event_payloads():
    sim = _make_sim()
    clock = Clock(start_time=datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc))

    for _ in range(50):
        event = sim.next_event(clock)
        assert "event_type" in event
        assert "payload" in event
        assert event["event_type"] in sim.available_event_types()
        assert isinstance(event["payload"], dict)


def test_order_lifecycle():
    sim = _make_sim(seed=7, initial_customers=1)
    clock = Clock()

    # Force a customer with a cart item so an order can be placed.
    sim._update_cart(sim.customers[0]["customer_id"], sim.products[0], 2)
    assert sim.carts[sim.customers[0]["customer_id"]]

    # Place the order manually.
    customer, order = sim._place_order(clock)
    assert order["status"] == "placed"
    assert order["total"] > 0
    assert not sim.carts[customer["customer_id"]]

    # Process payment.
    event = sim.next_event(clock)
    while event["event_type"] != "payment_processed":
        event = sim.next_event(clock)
    assert event["payload"]["status"] in ("approved", "declined")
