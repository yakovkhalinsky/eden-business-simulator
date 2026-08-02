"""Tests for output adapters."""

import json
from datetime import datetime, timezone
from io import StringIO

import httpx
import pytest

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.output.http import HttpOutputAdapter
from eden_business_simulator.output.ndjson import NDJsonOutputAdapter


def make_envelope() -> EventEnvelope:
    return EventEnvelope(
        event_id="evt-1",
        timestamp=datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        business_type="ecommerce",
        event_type="order_placed",
        payload={"order_id": "ord-1", "total": 19.99},
    )


def test_ndjson_adapter_to_stream():
    stream = StringIO()
    adapter = NDJsonOutputAdapter(stream=stream)
    adapter.write(make_envelope())
    adapter.close()
    line = stream.getvalue().strip()
    data = json.loads(line)
    assert data["business_type"] == "ecommerce"


def test_http_adapter_posts_event():
    received: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        received.append(request)
        return httpx.Response(200)

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    adapter = HttpOutputAdapter("https://example.com/webhook", client=client)
    adapter.write(make_envelope())
    adapter.close()

    assert len(received) == 1
    body = json.loads(received[0].content)
    assert body["event_type"] == "order_placed"
    assert body["payload"]["order_id"] == "ord-1"


def test_http_adapter_requires_url():
    with pytest.raises(ValueError):
        HttpOutputAdapter("")
