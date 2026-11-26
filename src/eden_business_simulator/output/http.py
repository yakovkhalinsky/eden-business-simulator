"""HTTP POST output adapter (single-event webhook)."""

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


class HttpOutputAdapter(OutputAdapter):
    """POST each event envelope as JSON to a webhook URL.

    Delivery goes through :class:`HttpDelivery` (Phase 7): bearer
    auth from ``EDEN_WEBHOOK_TOKEN`` when set, bounded retry on
    network errors / 5xx, and dead-lettering instead of raising when
    the consumer stays down — a dead webhook never crashes a run.
    """

    def __init__(
        self,
        url: str,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
        *,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        dead_letter_path: Union[str, Path] = DEFAULT_DEAD_LETTER_PATH,
        token: Optional[str] = None,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if not url:
            raise ValueError("webhook URL is required")
        self.url = url
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

    def write(self, envelope: EventEnvelope) -> None:
        body = envelope.model_dump(mode="json", exclude_none=True)
        self.delivery.deliver(body, event_count=1)

    def close(self) -> None:
        if self._owned_client:
            self.client.close()
