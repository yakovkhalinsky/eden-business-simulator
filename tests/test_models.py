"""Tests for shared models."""

from datetime import datetime, timedelta, timezone

from eden_business_simulator.models import Actor, Clock, EventEnvelope


def test_actor():
    actor = Actor(
        actor_id="actor-1",
        actor_type="customer",
        created_at=datetime.now(timezone.utc),
        attributes={"region": "ap-southeast-2"},
    )
    assert actor.actor_type == "customer"
    assert actor.attributes["region"] == "ap-southeast-2"


def test_clock_advances():
    start = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)
    clock = Clock(start_time=start, tick_seconds=0.5)
    assert clock.now == start
    clock.advance()
    assert clock.now == start + timedelta(seconds=0.5)
    clock.advance(2.0)
    assert clock.elapsed_seconds() == 2.5


def test_event_envelope_serializes_payload():
    envelope = EventEnvelope(
        event_id="evt-123",
        timestamp=datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        business_type="ecommerce",
        event_type="order_placed",
        payload={"order_id": "ord-1", "total": 99.95},
    )
    line = envelope.to_json_line()
    assert '"order_placed"' in line
    assert '"ord-1"' in line
    assert envelope.version == "1.0"


def test_event_envelope_from_event():
    now = datetime.now(timezone.utc)
    envelope = EventEnvelope.from_event(
        business_type="saas",
        event_type="feature_used",
        payload={"feature_key": "api_call"},
        timestamp=now,
    )
    assert envelope.business_type == "saas"
    assert envelope.event_type == "feature_used"
    assert envelope.timestamp == now
