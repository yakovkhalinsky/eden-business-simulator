"""Output adapters for the event stream."""

from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.output.http import HttpOutputAdapter
from eden_business_simulator.output.ndjson import NDJsonOutputAdapter

__all__ = [
    "OutputAdapter",
    "NDJsonOutputAdapter",
    "HttpOutputAdapter",
]
