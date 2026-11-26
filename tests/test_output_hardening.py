"""Phase 7 tests: batched HTTP output, bearer auth, retry, dead-letter.

Uses a real local HTTP server (not a mock transport) so batching,
headers and status handling are exercised end to end.
"""

from __future__ import annotations

import json
import socket
import threading
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx
import pytest

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.output.http import HttpOutputAdapter
from eden_business_simulator.output.http_batch import HttpBatchOutputAdapter



def local_client() -> httpx.Client:
    """A real client that ignores this environment's proxy variables."""
    return httpx.Client(trust_env=False)


FAST = {"backoff_base_seconds": 0.001, "sleep": lambda _s: None}


def make_envelope(index: int) -> EventEnvelope:
    return EventEnvelope(
        event_id=f"evt-{index}",
        timestamp=datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc),
        business_type="ecommerce",
        event_type="order_placed",
        payload={"seq": index},
    )


class _RecordingHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:  # noqa: N802 - http.server API
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        self.server.records.append(  # type: ignore[attr-defined]
            {"headers": self.headers, "body": body}
        )
        if self.server.status_plan:  # type: ignore[attr-defined]
            status = self.server.status_plan.pop(0)  # type: ignore[attr-defined]
        else:
            status = self.server.default_status  # type: ignore[attr-defined]
        self.send_response(status)
        self.end_headers()

    def log_message(self, *_args) -> None:  # keep the suite quiet
        pass


@pytest.fixture()
def server():
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _RecordingHandler)
    httpd.records = []  # type: ignore[attr-defined]
    httpd.status_plan = []  # type: ignore[attr-defined]
    httpd.default_status = 200  # type: ignore[attr-defined]
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield httpd
    finally:
        httpd.shutdown()
        httpd.server_close()


def url_of(server) -> str:
    port = server.server_address[1]
    return f"http://127.0.0.1:{port}/events"


def bodies_of(server) -> list:
    return [json.loads(record["body"]) for record in server.records]


def dead_letter_lines(path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


# ---------------------------------------------------------------------------
# Batching
# ---------------------------------------------------------------------------


def test_batches_of_50_50_20_preserve_order(server, tmp_path):
    adapter = HttpBatchOutputAdapter(
        url_of(server),
        client=local_client(),
        batch_size=50,
        dead_letter_path=tmp_path / "dl.ndjson",
        **FAST,
    )
    for index in range(120):
        adapter.write(make_envelope(index))
    adapter.close()

    batches = bodies_of(server)
    assert [len(batch) for batch in batches] == [50, 50, 20]
    assert all(isinstance(batch, list) for batch in batches)
    flat_ids = [event["event_id"] for batch in batches for event in batch]
    assert flat_ids == [f"evt-{i}" for i in range(120)]
    content_type = server.records[0]["headers"].get("Content-Type")
    assert "application/json" in content_type
    assert adapter.delivered_events == 120
    assert adapter.attempts == 3


def test_partial_batch_flushed_on_close(server, tmp_path):
    adapter = HttpBatchOutputAdapter(
        url_of(server),
        client=local_client(),
        batch_size=50,
        dead_letter_path=tmp_path / "dl.ndjson",
        **FAST,
    )
    for index in range(3):
        adapter.write(make_envelope(index))
    assert server.records == []  # nothing sent before close
    adapter.close()
    batches = bodies_of(server)
    assert len(batches) == 1
    assert [event["event_id"] for event in batches[0]] == ["evt-0", "evt-1", "evt-2"]


def test_flush_interval_trigger_with_injected_clock(server, tmp_path):
    now = [100.0]
    adapter = HttpBatchOutputAdapter(
        url_of(server),
        client=local_client(),
        batch_size=100,
        flush_interval_seconds=5.0,
        clock=lambda: now[0],
        dead_letter_path=tmp_path / "dl.ndjson",
        **FAST,
    )
    adapter.write(make_envelope(0))
    now[0] = 104.0  # inside the interval: no flush
    adapter.write(make_envelope(1))
    assert server.records == []
    now[0] = 106.0  # past the interval since the first buffered event
    adapter.write(make_envelope(2))
    batches = bodies_of(server)
    assert len(batches) == 1
    assert [event["event_id"] for event in batches[0]] == ["evt-0", "evt-1"]
    adapter.close()
    batches = bodies_of(server)
    assert len(batches) == 2
    assert [event["event_id"] for event in batches[1]] == ["evt-2"]


# ---------------------------------------------------------------------------
# Auth (D11)
# ---------------------------------------------------------------------------


def test_bearer_token_sent_by_both_adapters(server, tmp_path, monkeypatch):
    monkeypatch.setenv("EDEN_WEBHOOK_TOKEN", "sekret-token")
    batch = HttpBatchOutputAdapter(
        url_of(server), client=local_client(), batch_size=2, dead_letter_path=tmp_path / "dl.ndjson", **FAST
    )
    batch.write(make_envelope(0))
    batch.write(make_envelope(1))
    batch.close()
    single = HttpOutputAdapter(
        url_of(server), client=local_client(), dead_letter_path=tmp_path / "dl.ndjson", **FAST
    )
    single.write(make_envelope(2))
    single.close()
    assert len(server.records) == 2
    for record in server.records:
        assert record["headers"].get("Authorization") == "Bearer sekret-token"


def test_no_authorization_header_without_token(server, tmp_path, monkeypatch):
    monkeypatch.delenv("EDEN_WEBHOOK_TOKEN", raising=False)
    adapter = HttpOutputAdapter(
        url_of(server), client=local_client(), dead_letter_path=tmp_path / "dl.ndjson", **FAST
    )
    adapter.write(make_envelope(0))
    adapter.close()
    assert server.records[0]["headers"].get("Authorization") is None


# ---------------------------------------------------------------------------
# Retry + dead-letter
# ---------------------------------------------------------------------------


def test_retry_on_500_then_success(server, tmp_path):
    server.status_plan = [500, 500]
    adapter = HttpBatchOutputAdapter(
        url_of(server),
        client=local_client(),
        batch_size=2,
        max_retries=3,
        dead_letter_path=tmp_path / "dl.ndjson",
        **FAST,
    )
    adapter.write(make_envelope(0))
    adapter.write(make_envelope(1))
    adapter.close()
    assert adapter.attempts == 3
    assert adapter.delivered_events == 2
    assert dead_letter_lines(tmp_path / "dl.ndjson") == []


def test_exhausted_retries_dead_letter_then_recovery(server, tmp_path):
    server.default_status = 500
    dl_path = tmp_path / "dl.ndjson"
    adapter = HttpBatchOutputAdapter(
        url_of(server),
        client=local_client(),
        batch_size=2,
        max_retries=2,
        dead_letter_path=dl_path,
        **FAST,
    )
    adapter.write(make_envelope(0))
    adapter.write(make_envelope(1))  # triggers a flush that will fail
    adapter.flush()  # must not raise even though the consumer is down

    lines = dead_letter_lines(dl_path)
    assert [line["event_id"] for line in lines] == ["evt-0", "evt-1"]
    for line in lines:
        meta = line["_dead_letter"]
        assert meta["attempts"] == 2
        assert meta["last_status"] == 500
        assert meta["url"] == url_of(server)
        assert meta["failed_at"]
    assert adapter.failed_events == 2
    assert adapter.dead_lettered_events == 2
    assert adapter.delivered_events == 0

    server.default_status = 200  # consumer recovers
    adapter.write(make_envelope(2))
    adapter.write(make_envelope(3))
    adapter.close()
    assert adapter.delivered_events == 2
    assert len(dead_letter_lines(dl_path)) == 2  # nothing new dead-lettered


def test_4xx_fails_immediately_and_dead_letters(server, tmp_path):
    server.default_status = 400
    dl_path = tmp_path / "dl.ndjson"
    adapter = HttpOutputAdapter(
        url_of(server), client=local_client(), max_retries=3, dead_letter_path=dl_path, **FAST
    )
    adapter.write(make_envelope(0))  # must not raise
    adapter.close()
    assert adapter.attempts == 1
    lines = dead_letter_lines(dl_path)
    assert len(lines) == 1
    assert lines[0]["_dead_letter"]["last_status"] == 400
    assert lines[0]["_dead_letter"]["attempts"] == 1


def test_network_error_dead_letters_with_error_detail(tmp_path):
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()  # nothing is listening on this port now
    dl_path = tmp_path / "dl.ndjson"
    adapter = HttpBatchOutputAdapter(
        f"http://127.0.0.1:{port}/events",
        client=local_client(),
        batch_size=1,
        max_retries=2,
        dead_letter_path=dl_path,
        **FAST,
    )
    adapter.write(make_envelope(0))  # connection refused; must not raise
    adapter.close()
    assert adapter.attempts == 2
    lines = dead_letter_lines(dl_path)
    assert len(lines) == 1
    meta = lines[0]["_dead_letter"]
    assert "error" in meta
    assert "last_status" not in meta


# ---------------------------------------------------------------------------
# Webhook adapter regression (single-event semantics + new policy)
# ---------------------------------------------------------------------------


def test_webhook_single_event_body_and_retry(server, tmp_path, monkeypatch):
    monkeypatch.setenv("EDEN_WEBHOOK_TOKEN", "tok-123")
    server.status_plan = [500]  # first attempt fails, retry succeeds
    adapter = HttpOutputAdapter(
        url_of(server), client=local_client(), max_retries=3, dead_letter_path=tmp_path / "dl.ndjson", **FAST
    )
    adapter.write(make_envelope(7))
    adapter.close()
    assert adapter.attempts == 2
    assert adapter.delivered_events == 1
    bodies = bodies_of(server)
    assert len(bodies) == 2
    assert isinstance(bodies[-1], dict)  # single event object, not an array
    assert bodies[-1]["event_id"] == "evt-7"
    assert server.records[-1]["headers"].get("Authorization") == "Bearer tok-123"
    assert dead_letter_lines(tmp_path / "dl.ndjson") == []
