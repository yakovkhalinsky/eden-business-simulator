"""Abstract base class for business simulators."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.models import Clock

if TYPE_CHECKING:
    from eden_business_simulator.framework.definition import (
        ActionDef,
        ActionParamDef,
    )


DEFAULT_TAX_RATE = 0.10
DEFAULT_TAX_TYPE = "GST"


def compute_tax(amount: float, tax_rate: float = DEFAULT_TAX_RATE) -> float:
    """Return the tax component for a monetary amount using the given rate."""
    return round(float(amount) * tax_rate, 2)


@dataclass
class ActionRequest:
    """One management action submitted to a simulator (Phase 3).

    ``action_id`` is the caller's idempotency key: applying the same id
    twice has exactly one effect. ``approved_by`` carries the Beth-side
    approval stamp (D9); Eden only checks its presence for actions whose
    definition sets ``requires_approval``.
    """

    action_id: str
    action_type: str
    actor: str
    params: dict[str, Any] = field(default_factory=dict)
    approved_by: str | None = None


@dataclass
class ActionResult:
    """Outcome of ``apply_action``.

    ``events`` holds domain effect event dicts (same shape as
    ``next_event`` returns) for the runner to envelope and emit; it is
    empty for rejections and duplicates.
    """

    status: str  # "applied" | "rejected" | "already_applied"
    action_id: str
    action_type: str
    reason: str | None = None
    events: list[dict[str, Any]] = field(default_factory=list)


class ActionRejected(Exception):
    """Raised by an effect handler to reject an action with a reason.

    Handlers must validate *before* mutating and must not consume RNG on
    a path that can end in rejection, so a rejected action leaves both
    state and the deterministic stream untouched.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def _validate_action_params(
    action_def: "ActionDef", params: dict[str, Any]
) -> str | None:
    """Return a rejection reason naming the offending param, or None."""
    declared: dict[str, "ActionParamDef"] = {p.name: p for p in action_def.params}
    for name in params:
        if name not in declared:
            return f"unknown_param:{name}"
    for name, param in declared.items():
        if name not in params:
            if param.required:
                return f"missing_param:{name}"
            continue
        value = params[name]
        if param.type == "string" and not isinstance(value, str):
            return f"invalid_param_type:{name}"
        if param.type == "boolean" and not isinstance(value, bool):
            return f"invalid_param_type:{name}"
        if param.type == "integer" and (
            isinstance(value, bool) or not isinstance(value, int)
        ):
            return f"invalid_param_type:{name}"
        if param.type == "number" and (
            isinstance(value, bool) or not isinstance(value, (int, float))
        ):
            return f"invalid_param_type:{name}"
        if param.enum is not None and value not in param.enum:
            return f"param_not_in_enum:{name}"
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if param.min is not None and value < param.min:
                return f"param_out_of_range:{name}"
            if param.max is not None and value > param.max:
                return f"param_out_of_range:{name}"
    return None


def action_audit_event(
    request: ActionRequest, result: ActionResult
) -> dict[str, Any]:
    """Build the audit event dict for an action outcome (D5).

    Runners emit the domain effect event(s) first, then this audit
    event, so the audit reads as a receipt naming the effects it
    produced. Duplicates are audited as ``action_rejected`` with reason
    ``duplicate_action_id`` and their ``already_applied`` status noted.
    """
    if result.status == "applied":
        return {
            "event_type": "action_applied",
            "payload": {
                "action_id": request.action_id,
                "action_type": request.action_type,
                "actor": request.actor,
                "approved_by": request.approved_by,
                "params": dict(request.params),
                "effect_event_types": [e["event_type"] for e in result.events],
            },
        }
    return {
        "event_type": "action_rejected",
        "payload": {
            "action_id": request.action_id,
            "action_type": request.action_type,
            "actor": request.actor,
            "params": dict(request.params),
            "reason": result.reason,
            "status": result.status,
        },
    }


class BusinessSimulator(ABC):
    """One business domain that can produce a realistic event stream."""

    business_type: str = "abstract"

    def configure(self, config: SimulatorConfig) -> None:
        """Apply runtime configuration before initialization.

        Phase 0: load and validate this business's definition (if one
        exists), validate ``initial_state_overrides`` against its declared
        keys, and record the definition hash so a run is reproducible
        against the exact configuration that produced it. Simulators with
        no definition file (e.g. test doubles) keep the old behaviour.
        """
        self.config = config
        self.definition = None
        self.definition_hash = None
        self._setup_scenarios(config)
        try:
            from eden_business_simulator.framework.definition import load_definition

            definition = load_definition(self.business_type)
        except FileNotFoundError:
            return
        definition.validate_overrides(config.initial_state_overrides)
        self.definition = definition
        self.definition_hash = definition.definition_hash()

    # ------------------------------------------------------------------
    # Scenario packs (Phase 6)
    # ------------------------------------------------------------------

    def _setup_scenarios(self, config: SimulatorConfig) -> None:
        """Build the scenario engine from the run's schedule, if any.

        Construction resolves pack names, so an unknown name raises a
        clear configuration error here — before any event exists. An
        empty schedule leaves no engine, and every scenario hook below
        degrades to a no-op (multiplier exactly 1.0, no transitions),
        which is what keeps unscheduled runs byte-identical.
        """
        from eden_business_simulator.framework.scenario import ScenarioEngine

        schedule = getattr(config, "scenario_schedule", None) or []
        self._scenario_engine = (
            ScenarioEngine(schedule, seed=config.seed) if schedule else None
        )

    def weight_multiplier(self, event_type: str) -> float:
        """Scenario multiplier for one event type (the catalogue hook).

        ``WeightedEventCatalog`` consults this on the selection context
        (the simulator itself) for every candidate type. Returns
        exactly 1.0 when no scenario targets the type, so weights —
        and therefore the deterministic stream — are unchanged.
        """
        engine = getattr(self, "_scenario_engine", None)
        if engine is None:
            return 1.0
        return engine.weight_multiplier(event_type)

    def scenario_transitions(self, clock: Clock) -> list[dict[str, Any]]:
        """Marker events for scenario starts/ends crossed by ``clock``.

        Runners call this between ticks (like the period-summary
        close) and emit the returned ``scenario_started`` /
        ``scenario_ended`` dicts as framework events.
        """
        engine = getattr(self, "_scenario_engine", None)
        if engine is None:
            return []
        return engine.transitions(
            clock.now, clock.elapsed_seconds() / 60.0
        )

    def active_scenarios(self) -> list[dict[str, Any]]:
        """Currently active scenarios (name + started/ends timestamps)."""
        engine = getattr(self, "_scenario_engine", None)
        if engine is None:
            return []
        return engine.active_scenarios()

    def scenario_state(self) -> dict[str, Any] | None:
        """Scenario engine state for a checkpoint (restorable state S4).

        Framework-owned, like the RNG/Faker sections: the runner
        stores this as a sibling of the business's own state in the
        snapshot envelope. Returns ``None`` when the run has no
        schedule (no engine), leaving unscheduled runs untouched.
        """
        engine = getattr(self, "_scenario_engine", None)
        if engine is None:
            return None
        return engine.serialize_state()

    def restore_scenario_state(self, state: dict[str, Any]) -> None:
        """Restore scenario engine state from :meth:`scenario_state`.

        Called by the runner *before* the business's own state (and
        well before RNG state) — see the restore ordering documented
        in ``ContinuousRunner._load_resume_state``. A checkpoint that
        carries scenario state for a run whose current configuration
        builds no engine is a configuration mismatch: fail loudly
        rather than silently dropping an in-flight scenario.
        """
        engine = getattr(self, "_scenario_engine", None)
        if engine is None:
            raise ValueError(
                f"Checkpoint for {self.business_type!r} carries "
                "scenario state, but this run's configuration has no "
                "scenario schedule to restore it into."
            )
        engine.restore_state(state)

    @abstractmethod
    def initialize(self, seed: int) -> None:
        """Seed all randomness and create the initial deterministic state."""
        ...

    @abstractmethod
    def available_event_types(self) -> list[str]:
        """Return the event types this simulator may emit."""
        ...

    @abstractmethod
    def next_event(self, clock: Clock) -> dict[str, Any]:
        """Generate the next business event for the current clock time.

        Returns a dict with at least ``event_type`` and ``payload`` keys.
        """
        ...

    @abstractmethod
    def state_snapshot(self) -> dict[str, Any]:
        """Return a serializable view of the simulator's current state."""
        ...

    # ------------------------------------------------------------------
    # Full-state snapshots (restorable state, S1)
    # ------------------------------------------------------------------

    def serialize_state(self) -> dict[str, Any] | None:
        """Return this business's complete state, or ``None``.

        The opt-in half of genuine restore: businesses that implement
        this (S2/S3) get their state persisted inside the checkpoint
        snapshot's ``full_state`` section, alongside the framework-owned
        RNG/Faker/IdGenerator state, and handed back to
        :meth:`restore_state` on resume. The returned dict must contain
        only plain data (dicts, lists, strings, numbers, booleans,
        ``None``, and ``datetime``/``date`` values, which the framework
        encodes explicitly). The default returns ``None`` — the
        business keeps today's summary-only snapshot behaviour.
        """
        return None

    def restore_state(self, state: dict[str, Any]) -> None:
        """Replace this business's state from :meth:`serialize_state`.

        Must *replace*, not merge: the daemon path initialises a fresh
        simulator first, so anything not overwritten here would leak
        initial state into the resumed run. Called by
        :meth:`restore_full_state` before RNG/Faker state is restored
        (RNG state goes back last, per the scope's ordering rule).
        """
        pass

    def full_state_snapshot(self) -> dict[str, Any] | None:
        """Assemble the versioned ``full_state`` section, if opted in.

        Returns ``None`` when the business does not implement
        :meth:`serialize_state`. Otherwise the section carries the
        business's own state (datetimes explicitly encoded) plus the
        framework-owned state every simulator shares: RNG state,
        Faker state and IdGenerator counters — all JSON-safe (R6: raw
        JSON, no pickle).
        """
        state = self.serialize_state()
        if state is None:
            return None
        from eden_business_simulator.framework.state_codec import (
            FULL_STATE_FORMAT_VERSION,
            encode_datetimes,
            encode_random_state,
            faker_random,
        )

        full: dict[str, Any] = {
            "format_version": FULL_STATE_FORMAT_VERSION,
            "business_type": self.business_type,
            "state": encode_datetimes(state),
        }
        rng = getattr(self, "rng", None)
        if rng is not None:
            full["rng_state"] = encode_random_state(rng.getstate())
        faker = getattr(self, "faker", None)
        if faker is not None:
            full["faker_state"] = encode_random_state(
                faker_random(faker).getstate()
            )
        id_gen = getattr(self, "id_gen", None)
        if id_gen is not None:
            full["id_gen_counters"] = id_gen.counters
        return full

    def restore_full_state(self, full: dict[str, Any]) -> None:
        """Restore from a ``full_state`` section, validating its version.

        An unrecognised ``format_version`` raises
        :class:`FullStateVersionError` — a clear failure at resume,
        never a silent misread. Ordering (per the scope): the
        business's own state first, then IdGenerator counters, and
        RNG/Faker state last, after any construction that might draw.
        """
        from eden_business_simulator.framework.state_codec import (
            FULL_STATE_FORMAT_VERSION,
            FullStateVersionError,
            decode_datetimes,
            decode_random_state,
            faker_random,
        )

        version = full.get("format_version")
        if version != FULL_STATE_FORMAT_VERSION:
            raise FullStateVersionError(
                f"Unsupported full_state format_version {version!r} for "
                f"business {self.business_type!r}: this build reads "
                f"version {FULL_STATE_FORMAT_VERSION}. Refusing to "
                "resume from state it cannot interpret."
            )
        self.restore_state(decode_datetimes(full.get("state") or {}))
        id_gen = getattr(self, "id_gen", None)
        if id_gen is not None and "id_gen_counters" in full:
            id_gen.restore_counters(full["id_gen_counters"])
        rng = getattr(self, "rng", None)
        if rng is not None and "rng_state" in full:
            rng.setstate(decode_random_state(full["rng_state"]))
        faker = getattr(self, "faker", None)
        if faker is not None and "faker_state" in full:
            faker_random(faker).setstate(
                decode_random_state(full["faker_state"])
            )

    def restore_snapshot(self, data: dict[str, Any]) -> None:
        """Runner entry point for restoring checkpoint ``data``.

        Runs the legacy :meth:`restore` hook first (summary snapshots
        behave exactly as they always have), then applies the
        ``full_state`` section when one is present — so RNG/Faker state
        is restored last, after everything that might draw from it.
        Runners call this rather than :meth:`restore` directly.
        """
        self.restore(data)
        if isinstance(data, dict):
            full = data.get("full_state")
            if full is not None:
                self.restore_full_state(full)

    def position_snapshot(self) -> dict[str, Any]:
        """Return the current business position as a versioned envelope.

        Separate from ``state_snapshot()`` (which is checkpoint/resume
        state): this is the read API for external consumers — where the
        business stands right now (cash, stock, open work). Runners/CLI
        may add stream/sequence/timestamp around this envelope.

        The default derives a minimal position from ``state_snapshot()``;
        every real business overrides it with domain sections.
        """
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "position": {"state": self.state_snapshot()},
        }

    # ------------------------------------------------------------------
    # Action channel (Phase 3)
    # ------------------------------------------------------------------

    def available_actions(self) -> list["ActionDef"]:
        """Return the actions this simulator accepts, from its definition."""
        definition = getattr(self, "definition", None)
        if definition is None:
            return []
        return list(definition.actions)

    def _applied_action_ids(self) -> set[str]:
        ids = getattr(self, "_applied_ids", None)
        if ids is None:
            ids = set()
            self._applied_ids = ids
        return ids

    def seed_applied_action_ids(self, action_ids: Any) -> None:
        """Merge externally-known applied action ids into this instance.

        Phase 5: the applied-id set is otherwise per-instance and
        resets on ``initialize()``. A runner that resumes a stored run
        seeds the set from storage (applied commands + stored
        ``action_applied`` events) so an action applied by an earlier
        process is still recognised as a duplicate after a restart.
        ``apply_action`` semantics are unchanged.
        """
        self._applied_action_ids().update(action_ids)

    def apply_action(
        self, request: ActionRequest, clock: Clock
    ) -> ActionResult:
        """Apply one management action through the shared pipeline.

        Order: unknown action -> duplicate id -> approval gate (D9) ->
        parameter validation -> effect handler. A rejected action
        consumes no RNG and mutates no state; only a successful effect
        records the ``action_id`` as applied. Handlers are resolved by
        the ``_apply_<action_type>`` naming convention, must validate
        before mutating, and signal domain rejections by raising
        ``ActionRejected`` (or returning one / a rejected ActionResult).
        """
        defs = {a.action_type: a for a in self.available_actions()}
        action_def = defs.get(request.action_type)
        if action_def is None:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason="unknown_action",
            )
        if request.action_id in self._applied_action_ids():
            return ActionResult(
                "already_applied", request.action_id, request.action_type,
                reason="duplicate_action_id",
            )
        if action_def.requires_approval and not request.approved_by:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason="requires_approval",
            )
        reason = _validate_action_params(action_def, request.params)
        if reason is not None:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason=reason,
            )
        handler = getattr(self, f"_apply_{request.action_type}", None)
        if handler is None:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason="handler_missing",
            )
        try:
            outcome = handler(request.params, clock)
        except ActionRejected as exc:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason=exc.reason,
            )
        if isinstance(outcome, ActionResult):
            if outcome.status != "applied":
                return outcome
            events = outcome.events
        elif isinstance(outcome, ActionRejected):
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason=outcome.reason,
            )
        elif isinstance(outcome, list):
            events = outcome
        elif isinstance(outcome, dict):
            events = [outcome]
        else:
            return ActionResult(
                "rejected", request.action_id, request.action_type,
                reason="invalid_handler_result",
            )
        self._applied_action_ids().add(request.action_id)
        return ActionResult(
            "applied", request.action_id, request.action_type, events=events
        )

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore simulator state from a durable snapshot.

        Simulators that maintain internal entity state can override this hook
        to resume continuous generation from a checkpoint.  The default is a
        no-op so that stateless simulators can still be resumed deterministically
        from the original seed and the last checkpoint sequence.

        Note: runners call :meth:`restore_snapshot`, which invokes this
        hook and then applies the ``full_state`` section (see
        :meth:`serialize_state`) when one is present. Overrides therefore
        do not need to call ``super()`` to get full-state handling — but
        businesses implementing genuine restore should implement
        :meth:`restore_state` instead of overriding this hook.
        """
        pass
