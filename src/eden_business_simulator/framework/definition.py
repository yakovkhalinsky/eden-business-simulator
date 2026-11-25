"""Versioned, configuration-driven business definitions (Phase 0).

A ``BusinessDefinition`` is the declarative part of a simulator: event
catalogue (weights + guard *names*), dayparts, domain data, and the declared
initial-state keys/defaults. Guard and emitter logic stays in code; a
definition only refers to guards by name, and ``build_weighted_catalog``
resolves those names against the guard callables a simulator supplies --
an unknown name is a hard failure, never a silent skip.
"""

from __future__ import annotations

import hashlib
import json
import random
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.scheduler import Daypart, DaypartScheduler

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"

# Global guard-name registry: names a definition may legally reference when
# no per-simulator guard map is supplied. Simulators normally pass their own
# guard map to ``build_weighted_catalog``; registering a name here documents
# it as part of the shared vocabulary.
GUARD_REGISTRY: dict[str, Callable[[Any], bool]] = {}


def register_guard(name: str, fn: Callable[[Any], bool]) -> Callable[[Any], bool]:
    GUARD_REGISTRY[name] = fn
    return fn


class EventDef(BaseModel):
    event_type: str
    base_weight: float = Field(ge=0)
    guard: str | None = None
    in_catalog: bool = True

    @field_validator("guard")
    @classmethod
    def guard_must_be_identifier(cls, value: str | None) -> str | None:
        if value is not None and not value.isidentifier():
            raise ValueError(f"guard name is not an identifier: {value!r}")
        return value


class DaypartDef(BaseModel):
    name: str
    start_hour: int = Field(ge=0, le=23)
    end_hour: int = Field(ge=0, le=24)
    weight_modifiers: dict[str, float] = Field(default_factory=dict)
    default_modifier: float = 1.0


class InitialStateDef(BaseModel):
    defaults: dict[str, Any] = Field(default_factory=dict)
    types: dict[str, str] = Field(default_factory=dict)

    @field_validator("types")
    @classmethod
    def types_must_be_known(cls, value: dict[str, str]) -> dict[str, str]:
        allowed = {"int", "float", "str", "bool"}
        bad = {k: v for k, v in value.items() if v not in allowed}
        if bad:
            raise ValueError(f"unknown initial-state types: {bad}")
        return value


class BusinessDefinition(BaseModel):
    schema_version: Literal[1] = 1
    business_type: str
    version: str = "1.0"
    events: list[EventDef]
    dayparts: list[DaypartDef] = Field(default_factory=list)
    domain: dict[str, Any] = Field(default_factory=dict)
    initial_state: InitialStateDef = Field(default_factory=InitialStateDef)

    @model_validator(mode="after")
    def events_must_be_complete_and_unique(self) -> "BusinessDefinition":
        if not self.events:
            raise ValueError("definition must declare at least one event")
        names = [e.event_type for e in self.events]
        if len(names) != len(set(names)):
            raise ValueError("duplicate event_type in definition")
        known = set(names)
        for daypart in self.dayparts:
            unknown = set(daypart.weight_modifiers) - known
            if unknown:
                raise ValueError(
                    f"daypart {daypart.name!r} modifies unknown events: {sorted(unknown)}"
                )
        return self

    def event_types(self) -> list[str]:
        return [e.event_type for e in self.events]

    def definition_hash(self) -> str:
        canonical = json.dumps(
            self.model_dump(mode="json"), sort_keys=True, separators=(",", ":")
        )
        return hashlib.sha256(canonical.encode()).hexdigest()

    def scheduler(self) -> DaypartScheduler | None:
        if not self.dayparts:
            return None
        return DaypartScheduler(
            dayparts=[
                Daypart(
                    name=d.name,
                    start_hour=d.start_hour,
                    end_hour=d.end_hour,
                    weight_modifiers=dict(d.weight_modifiers),
                    default_modifier=d.default_modifier,
                )
                for d in self.dayparts
            ]
        )

    def validate_overrides(self, overrides: dict[str, Any]) -> dict[str, Any]:
        """Merge overrides over declared defaults; unknown keys/types fail."""
        unknown = set(overrides) - set(self.initial_state.defaults)
        if unknown:
            raise ValueError(
                f"unknown initial_state_overrides for {self.business_type}: "
                f"{sorted(unknown)}"
            )
        for key, value in overrides.items():
            declared = self.initial_state.types.get(key)
            if declared == "int" and (isinstance(value, bool) or not isinstance(value, int)):
                raise ValueError(f"initial state {key!r} must be int")
            if declared == "float" and (
                isinstance(value, bool) or not isinstance(value, (int, float))
            ):
                raise ValueError(f"initial state {key!r} must be float")
            if declared == "str" and not isinstance(value, str):
                raise ValueError(f"initial state {key!r} must be str")
            if declared == "bool" and not isinstance(value, bool):
                raise ValueError(f"initial state {key!r} must be bool")
        merged = dict(self.initial_state.defaults)
        merged.update(overrides)
        return merged


@lru_cache(maxsize=None)
def load_definition(business_type: str) -> BusinessDefinition:
    path = CONFIGS_DIR / f"{business_type}.json"
    if not path.exists():
        raise FileNotFoundError(f"no business definition for {business_type!r}: {path}")
    return BusinessDefinition.model_validate_json(path.read_text())


def build_weighted_catalog(
    definition: BusinessDefinition,
    rng: random.Random,
    guards: dict[str, Callable[[Any], bool]],
    scheduler: DaypartScheduler | None = None,
) -> WeightedEventCatalog:
    """Build a catalog from a definition, resolving guard names to code."""
    catalog = WeightedEventCatalog(rng, scheduler)
    for event in definition.events:
        if not event.in_catalog:
            continue
        guard_fn = None
        if event.guard is not None:
            if event.guard not in guards:
                raise ValueError(
                    f"unknown guard {event.guard!r} for event {event.event_type!r} "
                    f"in definition {definition.business_type!r}"
                )
            guard_fn = guards[event.guard]
        catalog.register(event.event_type, base_weight=event.base_weight, guard=guard_fn)
    return catalog
