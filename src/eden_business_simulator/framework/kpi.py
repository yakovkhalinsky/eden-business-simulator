"""Period KPI summaries (Phase 2).

A ``PeriodTracker`` is fed every business event a runner emits and, at each
simulated calendar-day boundary (D4), produces a ``period_summary`` payload
whose KPIs are computed from the *config-declared* KPI definitions in the
business's ``BusinessDefinition`` (see ``framework.definition.KpiDef`` for
the vocabulary). An external verifier can recompute every KPI from the raw
stream: ``count``/``count_where``/``sum``/``sum_where`` are pure stream
aggregations, ``ratio`` divides two sibling KPIs (divide-by-zero -> 0.0),
and ``snapshot`` reads a dotted field from ``position_snapshot()`` taken at
period close -- the one KPI family that is not stream-recomputable, by
design (it records where the business *stood* when the period closed).

``computed`` KPIs are the escape hatch: the definition names a handler,
resolved from ``COMPUTED_REGISTRY`` (or a per-tracker override map). A
handler receives ``(event_counts, position)`` and returns a number.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Callable

from eden_business_simulator.framework.definition import BusinessDefinition, KpiDef

# Named handlers for `computed` KPIs: name -> fn(event_counts, position).
COMPUTED_REGISTRY: dict[str, Callable[[dict, dict | None], float]] = {}


def register_computed(
    name: str, fn: Callable[[dict, dict | None], float]
) -> Callable[[dict, dict | None], float]:
    COMPUTED_REGISTRY[name] = fn
    return fn


def tracker_for(config: Any, simulator: Any) -> "PeriodTracker | None":
    """Build a tracker for a runner, or ``None`` when summaries are off.

    Summaries are off when the run config disables them, or when the
    simulator carries no definition with KPI declarations (test doubles
    and definition-less simulators are therefore untouched by Phase 2).
    """
    if not getattr(config, "period_summaries_enabled", True):
        return None
    definition = getattr(simulator, "definition", None)
    if definition is None or not definition.kpis:
        return None
    position_provider = getattr(simulator, "position_snapshot", None)
    return PeriodTracker(
        definition,
        position_provider=position_provider
        if callable(position_provider)
        else None,
    )


class PeriodTracker:
    """Accumulates one simulated day of business events at a time."""

    def __init__(
        self,
        definition: BusinessDefinition,
        position_provider: Callable[[], dict[str, Any]] | None = None,
        computed_handlers: dict[str, Callable] | None = None,
    ) -> None:
        self.definition = definition
        self.business_type = definition.business_type
        self._position_provider = position_provider
        handlers = {**COMPUTED_REGISTRY, **(computed_handlers or {})}
        for kpi in definition.kpis:
            if kpi.kind == "computed" and kpi.handler not in handlers:
                raise ValueError(
                    f"KPI {kpi.name!r} references unknown computed handler "
                    f"{kpi.handler!r}"
                )
        self._handlers = handlers
        self._kpis = {k.name: k for k in definition.kpis}
        self._period_date: date | None = None
        self._reset_accumulators()

    # -- accumulation ----------------------------------------------------

    def _reset_accumulators(self) -> None:
        self._event_counts: dict[str, int] = {}
        self._filtered_counts: dict[str, int] = {}
        self._sums: dict[str, float] = {}
        self._sequence_start: int | None = None
        self._sequence_end: int | None = None
        self._event_total = 0

    @property
    def has_events(self) -> bool:
        return self._event_total > 0

    # -- checkpoint state (restorable state S4, R3) -----------------------

    def serialize_state(self) -> dict[str, Any]:
        """Capture the in-progress period for a checkpoint.

        Everything the tracker accumulates is plain data (the period
        date as ISO), so the section is JSON-safe as built. A resumed
        tracker restored from this continues the *same* period: the
        day that was open at checkpoint time closes once, as one
        summary, instead of being split by the restart.
        """
        return {
            "period_date": (
                self._period_date.isoformat() if self._period_date else None
            ),
            "event_counts": dict(self._event_counts),
            "filtered_counts": dict(self._filtered_counts),
            "sums": dict(self._sums),
            "sequence_start": self._sequence_start,
            "sequence_end": self._sequence_end,
            "event_total": self._event_total,
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Replace the in-progress period from :meth:`serialize_state`.

        Must *replace*, not merge: the runner builds a fresh tracker
        for the resumed process, so anything left over from
        construction would double-count the period.
        """
        period_date = state.get("period_date")
        self._period_date = (
            date.fromisoformat(period_date) if period_date else None
        )
        self._event_counts = {
            str(key): int(value)
            for key, value in (state.get("event_counts") or {}).items()
        }
        self._filtered_counts = {
            str(key): int(value)
            for key, value in (state.get("filtered_counts") or {}).items()
        }
        self._sums = {
            str(key): float(value)
            for key, value in (state.get("sums") or {}).items()
        }
        self._sequence_start = state.get("sequence_start")
        self._sequence_end = state.get("sequence_end")
        self._event_total = int(state.get("event_total") or 0)

    def observe(
        self,
        event_type: str,
        payload: dict[str, Any],
        *,
        sequence: int,
        timestamp: datetime,
    ) -> None:
        """Record one business event. Never call for summary/snapshot events."""
        day = timestamp.date()
        if self._period_date is None:
            self._period_date = day
        self._event_counts[event_type] = self._event_counts.get(event_type, 0) + 1
        for kpi in self.definition.kpis:
            if kpi.event_type != event_type:
                continue
            if kpi.kind == "count_where":
                if payload.get(kpi.filter_field) == kpi.filter_value:
                    self._filtered_counts[kpi.name] = (
                        self._filtered_counts.get(kpi.name, 0) + 1
                    )
            elif kpi.kind in ("sum", "sum_where"):
                if (
                    kpi.kind == "sum_where"
                    and payload.get(kpi.filter_field) != kpi.filter_value
                ):
                    continue
                value = payload.get(kpi.field)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    self._sums[kpi.name] = self._sums.get(kpi.name, 0.0) + float(value)
        if self._sequence_start is None:
            self._sequence_start = sequence
        self._sequence_end = sequence
        self._event_total += 1

    # -- period closing ----------------------------------------------------

    def close_if_crossed(self, now: datetime) -> dict[str, Any] | None:
        """Close the open period if ``now`` is on a later simulated date.

        Runners call this *before* generating the next event, so a
        ``snapshot`` KPI reads the position exactly as the period ended.
        """
        if self._period_date is None or not self.has_events:
            return None
        if now.date() <= self._period_date:
            return None
        summary = self._build_summary(partial=False)
        self._period_date = None
        self._reset_accumulators()
        return summary

    def close_final(self) -> dict[str, Any] | None:
        """Close the open period as a partial summary, if it has events."""
        if self._period_date is None or not self.has_events:
            return None
        summary = self._build_summary(partial=True)
        self._period_date = None
        self._reset_accumulators()
        return summary

    # -- KPI evaluation ----------------------------------------------------

    def _resolve_snapshot(self, dotted: str, position: dict | None) -> Any:
        node: Any = position
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return None
            node = node[part]
        return node

    def _evaluate(self, position: dict | None) -> dict[str, Any]:
        resolved: dict[str, Any] = {}

        def resolve(kpi: KpiDef) -> Any:
            if kpi.name in resolved:
                return resolved[kpi.name]
            if kpi.kind == "count":
                value: Any = self._event_counts.get(kpi.event_type, 0)
            elif kpi.kind == "count_where":
                value = self._filtered_counts.get(kpi.name, 0)
            elif kpi.kind in ("sum", "sum_where"):
                value = self._sums.get(kpi.name, 0.0)
            elif kpi.kind == "ratio":
                numerator = resolve(self._kpis[kpi.numerator])
                denominator = resolve(self._kpis[kpi.denominator])
                if not denominator:
                    value = 0.0  # divide-by-zero rule: 0.0, documented above
                else:
                    value = float(numerator) / float(denominator)
            elif kpi.kind == "snapshot":
                value = self._resolve_snapshot(kpi.field, position)
            elif kpi.kind == "computed":
                value = self._handlers[kpi.handler](self._event_counts, position)
            else:  # pragma: no cover - kinds are schema-validated
                raise ValueError(f"unknown KPI kind {kpi.kind!r}")
            resolved[kpi.name] = value
            return value

        for kpi in self.definition.kpis:
            resolve(kpi)
        return {k.name: resolved[k.name] for k in self.definition.kpis}

    def _build_summary(self, *, partial: bool) -> dict[str, Any]:
        position = None
        if self._position_provider is not None and any(
            k.kind in ("snapshot", "computed") for k in self.definition.kpis
        ):
            position = self._position_provider()
        period_start = self._period_date
        period_end = period_start + timedelta(days=1)
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "partial": partial,
            "sequence_start": self._sequence_start,
            "sequence_end": self._sequence_end,
            "event_counts": dict(self._event_counts),
            "kpis": self._evaluate(position),
            "definition_version": self.definition.version,
            "definition_hash": self.definition.definition_hash(),
        }
