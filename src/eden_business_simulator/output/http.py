"""HTTP POST output adapter."""

from __future__ import annotations

import httpx

from eden_business_simulator.models import EventEnvelope
from eden_business_simulator.output.base import OutputAdapter


class HttpOutputAdapter(OutputAdapter):
    """POST each event envelope as JSON to a webhook URL.

    For high-throughput scenarios this adapter can later be extended with
    buffering/batching; the current implementation keeps the contract simple and
    deterministic for evaluation purposes.
    """

    def __init__(
        self,
        url: str,
        client: httpx.Client | None = None,
        timeout: float = 5.0,
    ) -> None:
        if not url:
            raise ValueError("webhook URL is required")
        self.url = url
        self._owned_client = client is None
        self.client = client or httpx.Client(timeout=timeout)

    def write(self, envelope: EventEnvelope) -> None:
        body = envelope.model_dump(mode="json", exclude_none=True)
        response = self.client.post(self.url, json=body)
        response.raise_for_status()

    def close(self) -> None:
        if self._owned_client:
            self.client.close()
