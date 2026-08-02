"""Vary event weights by simulated clock hour."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass
class Daypart:
    """A named slice of the simulated day with optional event-weight modifiers."""

    name: str
    start_hour: int
    end_hour: int  # exclusive
    weight_modifiers: dict[str, float] | None = None
    default_modifier: float = 1.0

    def contains(self, hour: int) -> bool:
        if self.end_hour <= self.start_hour:
            return hour >= self.start_hour or hour < self.end_hour
        return self.start_hour <= hour < self.end_hour


class DaypartScheduler:
    """Applies time-of-day modifiers to a base event-weight map."""

    def __init__(
        self,
        dayparts: list[Daypart] | None = None,
        default_modifier: float = 1.0,
    ) -> None:
        self.dayparts = list(dayparts or [])
        self.default_modifier = default_modifier

    def current_phase(self, hour: int) -> Daypart | None:
        for daypart in self.dayparts:
            if daypart.contains(hour):
                return daypart
        return None

    def modifier_for(self, event_type: str, hour: int) -> float:
        phase = self.current_phase(hour)
        if phase is None:
            return self.default_modifier
        return (phase.weight_modifiers or {}).get(
            event_type, phase.default_modifier
        )

    def modify_weights(
        self,
        base_weights: dict[str, float],
        hour: int,
    ) -> dict[str, float]:
        """Return a new weight map with time-of-day modifiers applied."""
        return {
            event_type: round(
                weight * self.modifier_for(event_type, hour), 6
            )
            for event_type, weight in base_weights.items()
        }

    @classmethod
    def default_cafe_schedule(cls) -> "DaypartScheduler":
        return cls(
            dayparts=[
                Daypart(
                    name="open",
                    start_hour=6,
                    end_hour=8,
                    weight_modifiers={
                        "staff_clocked_in": 2.0,
                        "supplier_delivery_received": 1.5,
                        "table_occupied": 0.5,
                        "order_taken": 0.3,
                    },
                    default_modifier=0.2,
                ),
                Daypart(
                    name="breakfast_rush",
                    start_hour=8,
                    end_hour=11,
                    weight_modifiers={
                        "table_occupied": 2.5,
                        "order_taken": 3.0,
                        "item_fired_to_kitchen": 2.0,
                        "item_prepared": 2.0,
                        "order_paid": 2.0,
                    },
                    default_modifier=1.0,
                ),
                Daypart(
                    name="lunch_rush",
                    start_hour=11,
                    end_hour=14,
                    weight_modifiers={
                        "table_occupied": 2.5,
                        "order_taken": 3.0,
                        "item_fired_to_kitchen": 2.0,
                        "item_prepared": 2.0,
                        "order_paid": 2.0,
                    },
                    default_modifier=1.0,
                ),
                Daypart(
                    name="afternoon",
                    start_hour=14,
                    end_hour=17,
                    weight_modifiers={
                        "order_taken": 0.7,
                        "wastage_logged": 1.5,
                        "stock_count_recorded": 1.0,
                    },
                    default_modifier=0.6,
                ),
                Daypart(
                    name="close",
                    start_hour=17,
                    end_hour=6,
                    weight_modifiers={
                        "order_taken": 0.1,
                        "stock_count_recorded": 2.0,
                        "wastage_logged": 1.5,
                        "shift_closed": 5.0,
                    },
                    default_modifier=0.3,
                ),
            ]
        )
