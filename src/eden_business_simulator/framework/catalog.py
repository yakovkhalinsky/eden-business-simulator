"""Declarative event-probability catalog with guards and time-of-day modifiers."""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any, Callable

from eden_business_simulator.framework.scheduler import DaypartScheduler


@dataclass
class CatalogEntry:
    """One weighted event type in the catalog."""

    base_weight: float
    guard: Callable[[Any], bool] | None = None
    time_modifier: Callable[[int], float] | None = None


class WeightedEventCatalog:
    """Chooses the next event type given base weights, guards, and time modifiers.

    ``choose`` evaluates every registered entry:

    * If a ``guard`` is supplied it is called with the provided ``context``.
      Entries whose guard returns ``False`` are skipped.
    * If a ``time_modifier`` is supplied it is called with the simulated clock
      hour and the resulting multiplier is applied to the base weight.
    * A shared ``DaypartScheduler`` can be supplied as a convenience; when an
      entry has no explicit ``time_modifier`` the scheduler's modifier for that
      event type and hour is used.
    * If the ``context`` exposes a callable ``weight_multiplier(event_type)``
      (simulators do — Phase 6 scenario packs), its result multiplies the
      weight as the final step.

    The catalog itself does not mutate simulator state; callers update their own
    state after ``choose`` returns.
    """

    def __init__(
        self,
        rng: random.Random,
        scheduler: DaypartScheduler | None = None,
    ) -> None:
        self.rng = rng
        self.scheduler = scheduler
        self._entries: dict[str, CatalogEntry] = {}

    def register(
        self,
        event_type: str,
        base_weight: float,
        guard: Callable[[Any], bool] | None = None,
        time_modifier: Callable[[int], float] | None = None,
    ) -> None:
        if base_weight < 0:
            raise ValueError("base_weight must be non-negative")
        self._entries[event_type] = CatalogEntry(
            base_weight=base_weight,
            guard=guard,
            time_modifier=time_modifier,
        )

    def effective_weight(
        self,
        event_type: str,
        entry: CatalogEntry,
        context: Any,
        hour: int,
    ) -> float | None:
        if entry.guard is not None and not entry.guard(context):
            return None

        modifier = 1.0
        if entry.time_modifier is not None:
            modifier = entry.time_modifier(hour)
        elif self.scheduler is not None:
            modifier = self.scheduler.modifier_for(event_type, hour)

        weight = entry.base_weight * modifier

        # Phase 6 scenario hook: simulators pass themselves as the
        # selection context, and BusinessSimulator exposes
        # ``weight_multiplier`` — the product of active scenario pack
        # multipliers for this type. Contexts without the hook (test
        # doubles, plain objects) are untouched, and a multiplier of
        # exactly 1.0 leaves the weight byte-identical.
        hook = getattr(context, "weight_multiplier", None)
        if callable(hook):
            weight = weight * hook(event_type)

        return weight

    def choose(self, hour: int, context: Any = None) -> str | None:
        """Return a weighted-random event type, or ``None`` if nothing is eligible."""
        types: list[str] = []
        weights: list[float] = []
        for event_type, entry in self._entries.items():
            weight = self.effective_weight(event_type, entry, context, hour)
            if weight is None or weight <= 0:
                continue
            types.append(event_type)
            weights.append(weight)

        if not types:
            return None
        return self.rng.choices(types, weights=weights, k=1)[0]

    def eligible_types(self, hour: int, context: Any = None) -> dict[str, float]:
        """Return the effective weight map for the current context and hour."""
        result: dict[str, float] = {}
        for event_type, entry in self._entries.items():
            weight = self.effective_weight(event_type, entry, context, hour)
            if weight is not None and weight > 0:
                result[event_type] = weight
        return result
