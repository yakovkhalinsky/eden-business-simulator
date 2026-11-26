"""Shared HTTP delivery: bearer auth, bounded retry, dead-letter (Phase 7).

Both HTTP output adapters (the single-event webhook and the batched
adapter) deliver through :class:`HttpDelivery` so the policy lives in
exactly one place:

- **Auth (D11)** — when the ``EDEN_WEBHOOK_TOKEN`` environment variable
  is set, every request carries ``Authorization: Bearer <token>``. The
  token is read from the environment at adapter construction, never
  from CLI arguments or config files, and never logged.
- **Retry** — network errors and HTTP 5xx are retried up to
  ``max_retries`` total attempts with exponential backoff
  (``backoff_base_seconds`` × ``backoff_factor`` ** (attempt - 1)
  between attempts). HTTP 4xx is not retryable and fails immediately.
- **Dead-letter** — after the final failed attempt the payload is
  appended to a dead-letter NDJSON file and delivery reports failure
  *without raising*: a dead consumer must never crash or hang a run.
  Each dead-lettered event is written as its own line: the event
  envelope plus a ``_dead_letter`` object with ``failed_at``,
  ``attempts``, ``last_status`` (or ``error``) and ``url``.

Delivery semantics, stated precisely: a payload is either delivered
(a 2xx response), or captured in the dead-letter file. There is no
automatic redelivery of dead-lettered payloads and no deduplication —
a consumer that also reads storage should dedupe by ``event_id`` /
sequence.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Optional, Union

import httpx

TOKEN_ENV_VAR = "EDEN_WEBHOOK_TOKEN"
DEFAULT_DEAD_LETTER_PATH = "eden-dead-letter.ndjson"
DEFAULT_MAX_RETRIES = 3
DEFAULT_BACKOFF_BASE_SECONDS = 0.5
DEFAULT_BACKOFF_FACTOR = 2.0


def token_from_env() -> Optional[str]:
    """Return the configured webhook bearer token, if any (D11)."""
    token = os.environ.get(TOKEN_ENV_VAR)
    return token or None


class HttpDelivery:
    """POST JSON payloads with auth, bounded retry and dead-lettering."""

    def __init__(
        self,
        client: httpx.Client,
        url: str,
        *,
        token: Optional[str] = None,
        max_retries: int = DEFAULT_MAX_RETRIES,
        backoff_base_seconds: float = DEFAULT_BACKOFF_BASE_SECONDS,
        backoff_factor: float = DEFAULT_BACKOFF_FACTOR,
        dead_letter_path: Union[str, Path] = DEFAULT_DEAD_LETTER_PATH,
        sleep: Callable[[float], None] = time.sleep,
        now: Callable[[], datetime] = lambda: datetime.now(timezone.utc),
    ) -> None:
        if max_retries < 1:
            raise ValueError("max_retries must be at least 1")
        if backoff_base_seconds < 0:
            raise ValueError("backoff_base_seconds must be non-negative")
        self.client = client
        self.url = url
        self.token = token_from_env() if token is None else token
        self.max_retries = max_retries
        self.backoff_base_seconds = backoff_base_seconds
        self.backoff_factor = backoff_factor
        self.dead_letter_path = Path(dead_letter_path)
        self._sleep = sleep
        self._now = now

        # Counters (events, not requests — except ``attempts``).
        self.attempts = 0
        self.delivered_events = 0
        self.failed_events = 0
        self.dead_lettered_events = 0

    @property
    def headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        return headers

    def deliver(
        self,
        payload: Union[Mapping[str, Any], list[Mapping[str, Any]]],
        event_count: int,
    ) -> bool:
        """Deliver one payload (single event object or a batch array).

        Returns True on a 2xx response. On final failure the events
        are dead-lettered and False is returned. Never raises for
        delivery failures.
        """
        last_status: Optional[int] = None
        last_error: Optional[str] = None
        attempts_used = 0
        for attempt in range(1, self.max_retries + 1):
            attempts_used = attempt
            self.attempts += 1
            try:
                response = self.client.post(
                    self.url, json=payload, headers=self.headers
                )
            except httpx.HTTPError as exc:
                last_status, last_error = None, f"{type(exc).__name__}: {exc}"
            else:
                if 200 <= response.status_code < 300:
                    self.delivered_events += event_count
                    return True
                last_status, last_error = response.status_code, None
                if 400 <= response.status_code < 500:
                    break  # 4xx is not retryable.
            if attempt < self.max_retries:
                self._sleep(
                    self.backoff_base_seconds
                    * (self.backoff_factor ** (attempt - 1))
                )
        self.failed_events += event_count
        self._dead_letter(payload, attempts_used, last_status, last_error)
        return False

    def _dead_letter(
        self,
        payload: Union[Mapping[str, Any], list[Mapping[str, Any]]],
        attempts_used: int,
        last_status: Optional[int],
        last_error: Optional[str],
    ) -> None:
        events = payload if isinstance(payload, list) else [payload]
        meta: dict[str, Any] = {
            "failed_at": self._now().isoformat(),
            "attempts": attempts_used,
            "url": self.url,
        }
        if last_status is not None:
            meta["last_status"] = last_status
        if last_error is not None:
            meta["error"] = last_error
        try:
            with self.dead_letter_path.open("a", encoding="utf-8") as handle:
                for event in events:
                    line = dict(event)
                    line["_dead_letter"] = meta
                    handle.write(json.dumps(line) + "\n")
            self.dead_lettered_events += len(events)
        except OSError:
            # Even a dead-letter failure must not crash the run; the
            # events are counted in failed_events either way.
            pass
