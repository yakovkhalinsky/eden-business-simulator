"""Batched HTTP POST output adapter (Phase 7)."""

from __future__ import annotations

import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Optional, Union

import httpx

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.output.delivery import (
    DEFAULT_BACKOFF_BASE_SECONDS,
    DEFAULT_BACKOFF_FACTOR,
    DEFAULT_DEAD_LETTER_PATH,
    DEFAULT_MAX_RETRIES,
    HttpDelivery,
)

DEFAULT_BATCH_SIZE = 50
DEFAULT_FLUSH_INTERVAL_SECONDS = 5.0


class HttpBatchOutputAdapter(OutputAdapter):
    """Buffer envelopes and POST them as JSON arrays.

    Flush triggers (no background threads — all checks happen on
    ``write``/``flush``/``close`` calls):

    - the buffer reaches ``batch_size`` events;
    - ``flush_interval_seconds`` have elapsed since the first event
      of the current buffer was written (checked on each ``write``);
    - an explicit ``flush()`` or ``close()`` — a partial final batch
      is delivered at run end.

    Event order is preserved within and across batches. Delivery
    policy (auth, retry, dead-letter, never-raise) is the shared
    :class:`HttpDelivery` behaviour.
    """

    def __init__(
        self,
        url: str,
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval_seconds: float = DEFAULT_FLUSH_INTERVAL_SECONDS,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        dead_letter_path: Union[str, Path] = DEFAULT_DEAD_LETTER_PATH,
        token: Optional[str] = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not url:
            raise ValueError("webhook URL is required")
        if batch_size < 1:
            raise ValueError("batch_size must be at least 1")
        if flush_interval_seconds < 0:
            raise ValueError("flush_interval_seconds must be non-negative")
        self.url = url
        self.batch_size = batch_size
        self.flush_interval_seconds = flush_interval_seconds
        self._owned_client = client is None
        self.client = client or httpx.Client(timeout=timeout)
        self.delivery = HttpDelivery(
            self.client,
            url,
            token=token,
            max_retries=max_retries,
            backoff_base_seconds=backoff_base_seconds,
            backoff_factor=backoff_factor,
            dead_letter_path=dead_letter_path,
            sleep=sleep,
            now=now,
        )
        self._clock = clock
        self._buffer: list[dict] = []
        self._first_buffered_at: Optional[float] = None

    # Delivery counters, delegated to the shared helper.
    @property
    def attempts(self) -> int:
        return self.delivery.attempts

    @property
    def delivered_events(self) -> int:
        return self.delivery.delivered_events

    @property
    def failed_events(self) -> int:
        return self.delivery.failed_events

    @property
    def dead_lettered_events(self) -> int:
        return self.delivery.dead_lettered_events

    @property
    def buffered_events(self) -> int:
        return len(self._buffer)

    def write(self, envelope: EventEnvelope) -> None:
        if (
            self._buffer
            and self._first_buffered_at is not None
            and self._clock() - self._first_buffered_at
            >= self.flush_interval_seconds
        ):
            self.flush()
        if not self._buffer:
            self._first_buffered_at = self._clock()
        self._buffer.append(envelope.model_dump(mode="json", exclude_none=True))
        if len(self._buffer) >= self.batch_size:
            self.flush()

    def flush(self) -> None:
        """Deliver the current buffer (if any) as one JSON array."""
        if not self._buffer:
            return
        batch = self._buffer
        self._buffer = []
        self._first_buffered_at = None
        self.delivery.deliver(batch, event_count=len(batch))

    def close(self) -> None:
        try:
            self.flush()
        finally:
            if self._owned_client:
                self.client.close()
