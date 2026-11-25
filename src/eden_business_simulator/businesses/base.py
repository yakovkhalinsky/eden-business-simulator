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
        try:
            from eden_business_simulator.framework.definition import load_definition

            definition = load_definition(self.business_type)
        except FileNotFoundError:
            return
        definition.validate_overrides(config.initial_state_overrides)
        self.definition = definition
        self.definition_hash = definition.definition_hash()

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
        """
        pass
