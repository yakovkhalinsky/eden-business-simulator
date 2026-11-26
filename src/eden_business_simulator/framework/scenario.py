"""Scenario packs (Phase 6).

A scenario pack is *configuration*: a named, time-boxed set of event
weight multipliers declared in ``configs/scenarios.json`` and validated
by :class:`ScenarioDef`. While a scenario is active, the weighted event
catalogue multiplies the selection weight of each targeted
``event_type`` by the pack's multiplier (``0`` suppresses the type
entirely). A business whose catalogue lacks a targeted type is simply
unaffected, so one pack can span several businesses.

Scenarios are *scheduled*, not actions: a run declares a schedule of
``{name, start_offset_minutes, duration_minutes?}`` entries (see
:class:`ScenarioScheduleEntry`, carried on ``SimulatorConfig``), and
the :class:`ScenarioEngine` — held by the simulator — activates and
expires them as the simulated clock crosses the boundaries. Runners
drain the engine's transitions between ticks and emit
``scenario_started`` / ``scenario_ended`` marker events; like
``period_summary`` events, markers consume a sequence (continuous
runner) but never a ``max_events`` slot, and are never observed by the
KPI tracker.

Determinism rules:

* With no scenario active, selection is byte-identical to a run with
  no schedule at all: the multiplier is exactly ``1.0`` and no extra
  RNG draws are made anywhere.
* D10: each activation derives a *separate* scenario RNG stream from
  ``(run seed, scenario name)`` — never from the business RNG — so
  scenario-internal randomness can never perturb the business stream.
  The v1 packs are pure multipliers and draw nothing from it; the
  stream exists so future state-mutating packs have somewhere
  deterministic to draw from.
* Resume (restorable-state S4, R4): the engine's pending/active
  state is checkpointed in the snapshot envelope (see
  ``serialize_state``), so a resumed daemon neither re-fires
  ``scenario_started`` for an in-flight pack nor loses its ending,
  and a pending pack still fires at its original offset — the
  runner restores the simulated clock alongside, which is what
  keeps the relative offsets meaningful.
"""

from __future__ import annotations

import hashlib
import json
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, model_validator

from eden_business_simulator.framework.state_codec import (
    decode_random_state,
    encode_random_state,
)

CONFIGS_DIR = Path(__file__).resolve().parent.parent / "configs"
SCENARIOS_PATH = CONFIGS_DIR / "scenarios.json"


class ScenarioEffectDef(BaseModel):
    """One weight multiplier on one event type."""

    event_type: str
    multiplier: float = Field(ge=0)


class ScenarioDef(BaseModel):
    """One scenario pack, as declared in ``configs/scenarios.json``."""

    name: str
    description: str
    duration_minutes: int = Field(gt=0)
    effects: list[ScenarioEffectDef] = Field(min_length=1)

    @model_validator(mode="after")
    def effects_must_target_unique_types(self) -> "ScenarioDef":
        names = [e.event_type for e in self.effects]
        if len(names) != len(set(names)):
            raise ValueError(
                f"scenario {self.name!r} targets an event type twice"
            )
        return self


class ScenarioScheduleEntry(BaseModel):
    """One scheduled activation of a pack within a run.

    ``start_offset_minutes`` is simulated minutes after run start;
    ``duration_minutes`` overrides the pack's default duration when
    given.
    """

    name: str
    start_offset_minutes: float = Field(default=0, ge=0)
    duration_minutes: int | None = Field(default=None, gt=0)


@lru_cache(maxsize=None)
def load_scenarios() -> dict[str, ScenarioDef]:
    """Load and validate every pack in ``configs/scenarios.json``."""
    raw = json.loads(SCENARIOS_PATH.read_text())
    scenarios: dict[str, ScenarioDef] = {}
    for entry in raw["scenarios"]:
        definition = ScenarioDef.model_validate(entry)
        if definition.name in scenarios:
            raise ValueError(f"duplicate scenario pack {definition.name!r}")
        scenarios[definition.name] = definition
    return scenarios


def scenario_names() -> list[str]:
    return sorted(load_scenarios())


def unknown_scenario_error(name: str) -> ValueError:
    return ValueError(
        f"unknown scenario {name!r}; available scenarios: "
        f"{', '.join(scenario_names())}"
    )


@dataclass
class _Scheduled:
    definition: ScenarioDef
    start_offset_minutes: float
    duration_minutes: int


@dataclass
class _Active:
    definition: ScenarioDef
    started_at: datetime
    ends_at: datetime
    duration_minutes: int
    rng: random.Random  # D10 derived stream; v1 packs never draw from it


@dataclass
class ScenarioEngine:
    """Runtime state for one run's scenario schedule.

    Constructing the engine resolves every scheduled name against the
    pack registry, so an unknown name fails fast — before any event is
    generated — with a clear configuration error.
    """

    schedule: list[ScenarioScheduleEntry] = field(default_factory=list)
    seed: int = 42

    def __post_init__(self) -> None:
        packs = load_scenarios()
        self._pending: list[_Scheduled] = []
        for entry in self.schedule:
            definition = packs.get(entry.name)
            if definition is None:
                raise unknown_scenario_error(entry.name)
            self._pending.append(
                _Scheduled(
                    definition=definition,
                    start_offset_minutes=entry.start_offset_minutes,
                    duration_minutes=(
                        entry.duration_minutes or definition.duration_minutes
                    ),
                )
            )
        self._pending.sort(key=lambda item: item.start_offset_minutes)
        self._active: list[_Active] = []

    # -- lifecycle -------------------------------------------------------

    def _derive_rng(self, name: str) -> random.Random:
        """D10: a scenario-private RNG stream seeded from (seed, name)."""
        digest = hashlib.sha256(
            f"eden-scenario:{self.seed}:{name}".encode()
        ).digest()
        return random.Random(int.from_bytes(digest[:8], "big"))

    def transitions(
        self, now: datetime, elapsed_minutes: float
    ) -> list[dict[str, Any]]:
        """Advance the schedule to ``now``; return marker event dicts.

        Expiries are processed before activations, so a pack that ends
        and another that starts at the same tick hand over cleanly.
        Returned dicts have the ``next_event`` shape
        (``event_type``/``payload``) for runners to envelope.
        """
        events: list[dict[str, Any]] = []
        still_active: list[_Active] = []
        for active in self._active:
            if now >= active.ends_at:
                events.append(
                    {
                        "event_type": "scenario_ended",
                        "payload": {
                            "scenario": active.definition.name,
                            "ended_at": now.isoformat(),
                            "duration_minutes": active.duration_minutes,
                        },
                    }
                )
            else:
                still_active.append(active)
        self._active = still_active

        still_pending: list[_Scheduled] = []
        for item in self._pending:
            if elapsed_minutes >= item.start_offset_minutes:
                ends_at = now + timedelta(minutes=item.duration_minutes)
                self._active.append(
                    _Active(
                        definition=item.definition,
                        started_at=now,
                        ends_at=ends_at,
                        duration_minutes=item.duration_minutes,
                        rng=self._derive_rng(item.definition.name),
                    )
                )
                events.append(
                    {
                        "event_type": "scenario_started",
                        "payload": {
                            "scenario": item.definition.name,
                            "description": item.definition.description,
                            "started_at": now.isoformat(),
                            "ends_at": ends_at.isoformat(),
                            "duration_minutes": item.duration_minutes,
                            "effects": [
                                {
                                    "event_type": e.event_type,
                                    "multiplier": e.multiplier,
                                }
                                for e in item.definition.effects
                            ],
                        },
                    }
                )
            else:
                still_pending.append(item)
        self._pending = still_pending
        return events

    # -- selection hook ----------------------------------------------------

    def weight_multiplier(self, event_type: str) -> float:
        """Product of active multipliers for ``event_type`` (1.0 if none).

        Overlapping scenarios targeting the same type stack
        multiplicatively. With nothing active this returns exactly
        ``1.0``, leaving catalogue weights byte-identical.
        """
        result = 1.0
        for active in self._active:
            for effect in active.definition.effects:
                if effect.event_type == event_type:
                    result *= effect.multiplier
        return result

    def active_scenarios(self) -> list[dict[str, Any]]:
        return [
            {
                "scenario": active.definition.name,
                "started_at": active.started_at.isoformat(),
                "ends_at": active.ends_at.isoformat(),
            }
            for active in self._active
        ]

    def scenario_rng(self, name: str) -> random.Random | None:
        """The D10 derived stream for an active scenario, if it is active."""
        for active in self._active:
            if active.definition.name == name:
                return active.rng
        return None

    # -- checkpoint state (restorable state S4, R4) -----------------------

    def serialize_state(self) -> dict[str, Any]:
        """Capture pending + active scenarios for a checkpoint.

        Pending entries keep their *relative* start offsets — the
        runner restores the simulated clock alongside this section,
        so elapsed time (and therefore the offsets) means the same
        thing after resume as before. Active entries carry absolute
        ``started_at``/``ends_at`` so an in-flight scenario ends at
        its original time, and their D10 RNG state rides along via
        the S1 codec. Ended scenarios appear in neither list: they
        are finished and must not re-fire. JSON-safe as built.
        """
        return {
            "pending": [
                {
                    "name": item.definition.name,
                    "start_offset_minutes": item.start_offset_minutes,
                    "duration_minutes": item.duration_minutes,
                }
                for item in self._pending
            ],
            "active": [
                {
                    "name": active.definition.name,
                    "started_at": active.started_at.isoformat(),
                    "ends_at": active.ends_at.isoformat(),
                    "duration_minutes": active.duration_minutes,
                    "rng_state": encode_random_state(active.rng.getstate()),
                }
                for active in self._active
            ],
        }

    def restore_state(self, state: dict[str, Any]) -> None:
        """Replace pending + active scenarios from
        :meth:`serialize_state`.

        Must *replace*, not merge: the engine a resumed process
        constructs from the run's schedule starts with every entry
        pending and none active, so restoring over it wholesale is
        what prevents a duplicate ``scenario_started`` for an
        already-started scenario (its activation is recorded here as
        active, not pending) and a lost ending for an in-flight one.
        Names are re-resolved against the pack registry, so an
        unknown name fails fast exactly as at construction.
        """
        packs = load_scenarios()
        pending: list[_Scheduled] = []
        for entry in state.get("pending") or []:
            definition = packs.get(entry["name"])
            if definition is None:
                raise unknown_scenario_error(entry["name"])
            pending.append(
                _Scheduled(
                    definition=definition,
                    start_offset_minutes=entry["start_offset_minutes"],
                    duration_minutes=entry["duration_minutes"],
                )
            )
        active: list[_Active] = []
        for entry in state.get("active") or []:
            definition = packs.get(entry["name"])
            if definition is None:
                raise unknown_scenario_error(entry["name"])
            rng = self._derive_rng(entry["name"])
            if entry.get("rng_state"):
                rng.setstate(decode_random_state(entry["rng_state"]))
            active.append(
                _Active(
                    definition=definition,
                    started_at=datetime.fromisoformat(entry["started_at"]),
                    ends_at=datetime.fromisoformat(entry["ends_at"]),
                    duration_minutes=entry["duration_minutes"],
                    rng=rng,
                )
            )
        self._pending = pending
        self._active = active
