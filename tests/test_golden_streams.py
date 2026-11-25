"""Golden-stream determinism tests (Phase 0).

With a fixed simulated start time and a fixed seed, two runs of the same
business must produce identical event streams. Ecommerce/SaaS stamp two
payload fields from wall-clock time (customer ``registered_at`` / account
``signup_at`` -- pre-existing behaviour), so those keys are normalised out
for those two businesses only.
"""

from datetime import datetime, timezone

import pytest

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock

WALL_CLOCK_KEYS = {"registered_at", "signup_at"}
CATALOGUED = [
    "cafe", "clinic", "electrician", "field_service", "gym", "logistics",
    "plumber",
]
ALL = CATALOGUED + ["ecommerce", "saas"]


def _run(business_type: str, seed: int = 42, count: int = 200):
    config = SimulatorConfig(business_type=business_type, duration_seconds=60)
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    clock = Clock(
        start_time=datetime(2026, 1, 1, tzinfo=timezone.utc), tick_seconds=0.5
    )
    events = []
    for _ in range(count):
        events.append(sim.next_event(clock))
        clock.advance()
    return events


def _normalise(events, business_type):
    if business_type not in ("ecommerce", "saas"):
        return events
    cleaned = []
    for event in events:
        payload = {
            k: v for k, v in event["payload"].items() if k not in WALL_CLOCK_KEYS
        }
        cleaned.append({"event_type": event["event_type"], "payload": payload})
    return cleaned


@pytest.mark.parametrize("business_type", ALL)
def test_same_seed_streams_are_identical(business_type):
    run_a = _normalise(_run(business_type), business_type)
    run_b = _normalise(_run(business_type), business_type)
    assert run_a == run_b


@pytest.mark.parametrize("business_type", ALL)
def test_all_event_types_reachable(business_type):
    events = _run(business_type, count=600)
    seen = {e["event_type"] for e in events}
    expected = set(load_simulator(business_type).available_event_types())
    if business_type == "cafe":
        expected.discard("shift_closed")  # needs the closing phase near duration end
    assert expected <= seen
