"""Run verifier (Phase 8) — independent recomputation over a stored run.

The verifier is the external check Beth/Aleph rely on: it loads a stored
run's *raw* event stream and recomputes everything that is derivable
from the stream alone, comparing against what the run stored about
itself. It never trusts stored aggregates:

* ``period_summary`` KPIs are recomputed from the raw business events
  in each summary's own sequence range, using the business definition's
  ``KpiDef`` specs (the same vocabulary as :mod:`framework.kpi`, but an
  independent aggregation — the tracker's accumulators are not reused).
* Position figures are recomputed with per-business reconciliation
  rules over the event history (money totals from payment events, open
  invoice sets by id, status tallies replayed per entity id). Position
  fields the stream does not determine — setup state created at
  ``initialize()`` (seed members, initial subscriptions, opening stock)
  and tallies whose transitions are not all named in the stream — are
  reported as *unverifiable by design*, never silently passed.
* Action audit events are checked against the Phase 3 semantics:
  applied ids unique, the audit immediately follows its declared
  effect events, every audit names a declared action type.
* Optionally, determinism: the run is re-executed from its recorded
  seed and the streams diffed. The original ``SimulatorConfig`` is not
  stored, so this only applies to runs whose shape is recoverable —
  runs containing action audits, scenario markers or a cafe
  ``shift_closed`` are SKIPped with the reason named, because their
  inputs are not recoverable from stored metadata.
* Resume integrity (restorable state S5): the run's *final* stored
  checkpoint is restored into a fresh simulator — in the runner's own
  restore order (clock, KPI tracker, scenario engine, then business
  state with RNG/Faker last) — and the restored simulator must
  re-serialise to the identical full state, reproduce the stored
  final position exactly, and generate continuation events without
  error. Storage retains only the latest snapshot, so this proves the
  final checkpoint is a genuine continuation point; it does **not**
  prove anything about earlier, overwritten checkpoints, nor that
  the stored stream was in fact produced by resuming (the stream
  checks above cover the stream's own consistency).

Every check returns a :class:`CheckResult` (``PASS`` / ``FAIL`` /
``SKIP``) whose detail names the period, field or sequence involved in
a failure. The CLI verb ``eden verify`` is a thin shell over
:func:`verify_run`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Iterable

from eden_business_simulator.framework.definition import (
    BusinessDefinition,
    load_definition,
)
from eden_business_simulator.storage import load_storage_adapter
from eden_business_simulator.storage.base import StoredRecord

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

# Framework event types that are never business events: they consume a
# sequence but are not observed by the KPI tracker and do not count
# toward a run's max_events.
FRAMEWORK_TYPES = {
    "period_summary",
    "state_snapshot_recorded",
    "scenario_started",
    "scenario_ended",
    "action_applied",
    "action_rejected",
}

# Payload keys stamped from wall-clock time by ecommerce/saas setup
# events (pre-existing behaviour, normalised out of determinism diffs —
# the same normalisation the golden-stream tests use).
WALL_CLOCK_KEYS = {"registered_at", "signup_at"}


@dataclass
class CheckResult:
    """One verifier check outcome."""

    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


@dataclass
class VerifyReport:
    """Structured result of verifying one stored run."""

    business_type: str | None
    stream_id: str
    checks: list[CheckResult] = field(default_factory=list)
    unverifiable_fields: list[str] = field(default_factory=list)
    unverifiable_kpis: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """True iff no check FAILED (SKIP is not a failure)."""
        return all(c.status != FAIL for c in self.checks)

    def check(self, name: str) -> CheckResult:
        for result in self.checks:
            if result.name == name:
                return result
        raise KeyError(name)


# ----------------------------------------------------------------------
# Event access helpers
# ----------------------------------------------------------------------


def event_dicts(records: Iterable[Any]) -> list[dict[str, Any]]:
    """Normalise stored records (or plain dicts) to plain event dicts."""
    events: list[dict[str, Any]] = []
    for record in records:
        if isinstance(record, dict):
            events.append(record)
            continue
        envelope = record.envelope
        events.append(
            {
                "sequence": record.sequence,
                "event_id": envelope.event_id,
                "event_type": envelope.event_type,
                "business_type": envelope.business_type,
                "timestamp": envelope.timestamp,
                "payload": envelope.payload,
            }
        )
    events.sort(key=lambda e: e["sequence"])
    return events


# ----------------------------------------------------------------------
# Check 1: sequence integrity
# ----------------------------------------------------------------------


def check_sequence_integrity(events: list[dict[str, Any]]) -> CheckResult:
    name = "sequence_integrity"
    if not events:
        return CheckResult(name, SKIP, "no events stored for this run")
    sequences = [e["sequence"] for e in events]
    first = sequences[0]
    for index, sequence in enumerate(sequences):
        expected = first + index
        if sequence != expected:
            return CheckResult(
                name,
                FAIL,
                f"sequence gap/duplicate at stream position {index}: "
                f"expected {expected}, found {sequence}",
            )
    seen: dict[str, int] = {}
    for e in events:
        event_id = e.get("event_id")
        if event_id is None:
            continue
        if event_id in seen:
            return CheckResult(
                name,
                FAIL,
                f"duplicate event_id {event_id!r} at sequences "
                f"{seen[event_id]} and {e['sequence']}",
            )
        seen[event_id] = e["sequence"]
    return CheckResult(
        name,
        PASS,
        f"{len(events)} events, sequences {first}..{sequences[-1]} contiguous, "
        "event_ids unique",
    )


# ----------------------------------------------------------------------
# Check 2: period summaries recomputed from the raw stream
# ----------------------------------------------------------------------


def _aggregate_kpis(
    definition: BusinessDefinition, events: list[dict[str, Any]]
) -> dict[str, Any]:
    """Independently recompute the stream-derivable KPIs for one period.

    Mirrors the ``KpiDef`` vocabulary of the tracker, but aggregates
    from the raw event list in one pass per KPI — deliberately not the
    tracker's incremental code path. ``snapshot``/``computed`` KPIs are
    omitted: they read live position at period close and are not
    stream-recomputable (see the module docstring in framework.kpi).
    """
    counts = Counter(e["event_type"] for e in events)
    by_name = {k.name: k for k in definition.kpis}
    resolved: dict[str, Any] = {}

    def resolve(kpi) -> Any:
        if kpi.name in resolved:
            return resolved[kpi.name]
        if kpi.kind == "count":
            value: Any = counts.get(kpi.event_type, 0)
        elif kpi.kind == "count_where":
            value = sum(
                1
                for e in events
                if e["event_type"] == kpi.event_type
                and e["payload"].get(kpi.filter_field) == kpi.filter_value
            )
        elif kpi.kind in ("sum", "sum_where"):
            value = 0.0
            for e in events:
                if e["event_type"] != kpi.event_type:
                    continue
                if (
                    kpi.kind == "sum_where"
                    and e["payload"].get(kpi.filter_field) != kpi.filter_value
                ):
                    continue
                raw = e["payload"].get(kpi.field)
                if isinstance(raw, (int, float)) and not isinstance(raw, bool):
                    value += float(raw)
        elif kpi.kind == "ratio":
            denominator = resolve(by_name[kpi.denominator])
            value = (
                0.0
                if not denominator
                else float(resolve(by_name[kpi.numerator])) / float(denominator)
            )
        else:  # snapshot / computed — not stream-recomputable
            return None
        resolved[kpi.name] = value
        return value

    for kpi in definition.kpis:
        resolve(kpi)
    return resolved


def check_period_summaries(
    events: list[dict[str, Any]],
    definition: BusinessDefinition,
    report: VerifyReport,
) -> CheckResult:
    name = "period_summaries"
    summaries = [e for e in events if e["event_type"] == "period_summary"]
    if not summaries:
        return CheckResult(name, SKIP, "no period_summary events in stream")
    catalogued = {e.event_type for e in definition.events}
    observed = [e for e in events if e["event_type"] in catalogued]
    unverifiable = {
        k.name for k in definition.kpis if k.kind in ("snapshot", "computed")
    }
    if unverifiable:
        report.unverifiable_kpis = sorted(unverifiable)

    for summary in summaries:
        payload = summary["payload"]
        where = (
            f"period_summary at sequence {summary['sequence']} "
            f"(period_start={payload.get('period_start')})"
        )
        # Note: a stored stream may span several daemon processes
        # (resume), each ending with its own partial summary — so a
        # non-final partial summary is legitimate here. Each summary
        # is verified against its own declared sequence range instead.
        start, end = payload.get("sequence_start"), payload.get("sequence_end")
        period_events = [
            e for e in observed if start <= e["sequence"] <= end
        ]
        expected_counts = dict(Counter(e["event_type"] for e in period_events))
        if payload.get("event_counts") != expected_counts:
            for event_type in sorted(
                set(expected_counts) | set(payload.get("event_counts") or {})
            ):
                stored_n = (payload.get("event_counts") or {}).get(event_type)
                if stored_n != expected_counts.get(event_type):
                    return CheckResult(
                        name,
                        FAIL,
                        f"{where}: event_counts[{event_type!r}] stored="
                        f"{stored_n} recomputed={expected_counts.get(event_type)}",
                    )
        expected_kpis = _aggregate_kpis(definition, period_events)
        stored_kpis = payload.get("kpis") or {}
        for kpi in definition.kpis:
            if kpi.kind in ("snapshot", "computed"):
                continue
            if stored_kpis.get(kpi.name) != expected_kpis.get(kpi.name):
                return CheckResult(
                    name,
                    FAIL,
                    f"{where}: kpi {kpi.name!r} stored="
                    f"{stored_kpis.get(kpi.name)!r} recomputed="
                    f"{expected_kpis.get(kpi.name)!r}",
                )
    note = (
        f"{len(summaries)} period summaries recomputed from raw events"
    )
    if unverifiable:
        note += (
            f"; snapshot/computed KPIs not stream-recomputable: "
            f"{', '.join(sorted(unverifiable))}"
        )
    return CheckResult(name, PASS, note)


# ----------------------------------------------------------------------
# Check 3: position recomputed from the event history
# ----------------------------------------------------------------------


def _round_money(value: float) -> float:
    return round(float(value), 2)


def _tally(events, id_field, mapping):
    """Replay per-entity status transitions; return a status Counter dict."""
    state: dict[Any, str] = {}
    for e in events:
        status = mapping.get(e["event_type"])
        entity = e["payload"].get(id_field)
        if status is not None and entity is not None:
            state[entity] = status
    return dict(Counter(state.values()))


def _by_type(events) -> dict[str, list[dict[str, Any]]]:
    grouped: dict[str, list[dict[str, Any]]] = {}
    for e in events:
        grouped.setdefault(e["event_type"], []).append(e["payload"])
    return grouped


def _recompute_cafe(events):
    by = _by_type(events)
    cash = sum(p["total"] for p in by.get("order_paid", []) if p["method"] == "cash")
    # The till accumulates every non-cash method ("card", "mobile")
    # into card_sales.
    card = sum(p["total"] for p in by.get("order_paid", []) if p["method"] != "cash")
    out = {
        "cash_sales": _round_money(cash),
        "card_sales": _round_money(card),
        "total_sales": _round_money(cash + card),
        "waste_total": _round_money(
            sum(p["estimated_cost"] for p in by.get("wastage_logged", []))
        ),
    }
    if by.get("shift_opened"):
        out["opening_float"] = by["shift_opened"][0]["opening_float"]
    return out


def _recompute_clinic(events):
    by = _by_type(events)
    adjudicated = {p["claim_id"] for p in by.get("claim_adjudicated", [])}
    open_claims = [
        p for p in by.get("claim_submitted", []) if p["claim_id"] not in adjudicated
    ]
    resulted = {p["lab_order_id"] for p in by.get("lab_result_received", [])}
    return {
        "appointments_by_status": _tally(
            events,
            "appointment_id",
            {
                "appointment_scheduled": "scheduled",
                "patient_checked_in": "checked_in",
                "encounter_started": "in_encounter",
                "appointment_cancelled": "cancelled",
                "no_show_recorded": "no_show",
            },
        ),
        "encounters_by_status": _tally(
            events,
            "encounter_id",
            {"encounter_started": "started", "diagnosis_recorded": "diagnosed"},
        ),
        "open_claims_count": len(open_claims),
        "open_claims_amount": _round_money(
            sum(p["billed_amount"] for p in open_claims)
        ),
        "lab_orders_pending": sum(
            1
            for p in by.get("lab_order_placed", [])
            if p["lab_order_id"] not in resulted
        ),
    }


def _recompute_ecommerce(events):
    status: dict[str, str] = {}
    totals: dict[str, float] = {}
    carts: dict[str, list] = {}
    for e in events:
        payload, event_type = e["payload"], e["event_type"]
        if event_type == "order_placed":
            status[payload["order_id"]] = "placed"
            totals[payload["order_id"]] = payload["total"]
            carts[payload["customer_id"]] = []
        elif event_type == "payment_processed":
            status[payload["order_id"]] = (
                "paid" if payload["status"] == "approved" else "payment_failed"
            )
        elif event_type == "order_shipped":
            status[payload["order_id"]] = "shipped"
        elif event_type == "refund_issued":
            status[payload["order_id"]] = "refunded"
        elif event_type == "cart_updated":
            carts[payload["customer_id"]] = payload["items"]
    by = _by_type(events)
    active_carts = {c: items for c, items in carts.items() if items}
    return {
        "revenue_total": _round_money(
            sum(
                totals[order_id]
                for order_id, state in status.items()
                if state in ("paid", "shipped", "refunded")
            )
        ),
        "orders_by_status": dict(Counter(status.values())),
        "refunds_total": _round_money(
            sum(p["amount"] for p in by.get("refund_issued", []))
        ),
        "active_carts_count": len(active_carts),
        "active_cart_items": sum(
            item["quantity"] for items in active_carts.values() for item in items
        ),
    }


def _recompute_gym(events):
    by = _by_type(events)
    # members_by_status / active_count / frozen_count / cancelled_count
    # are deliberately NOT recomputed here (restorable state S5): the
    # gym seeds its member pool at initialize() with no stream events,
    # so a stream tally only coincides with the position when every
    # seeded member happens to be touched by a status event. Those
    # fields are classified unverifiable-by-design (see
    # _UNVERIFIABLE_REASONS), like class_count/booking_count.
    return {
        "failed_payments_count": len(by.get("payment_failed", [])),
        "failed_payments_amount": _round_money(
            sum(p["amount"] for p in by.get("payment_failed", []))
        ),
    }


def _recompute_logistics(events):
    by = _by_type(events)
    attempts = by.get("delivery_attempted", [])
    return {
        "routes_by_status": _tally(
            events,
            "route_id",
            {
                "route_planned": "planned",
                "vehicle_departed": "started",
                "route_completed": "completed",
            },
        ),
        "deliveries_completed": sum(
            1 for p in attempts if p["outcome"] == "delivered"
        ),
        "exceptions_count": sum(
            1 for p in attempts if p["outcome"] != "delivered"
        ),
        "returns_count": len(by.get("return_initiated", [])),
        "fuel_cost_total": _round_money(
            sum(p["cost"] for p in by.get("fuel_stop_logged", []))
        ),
    }


def _recompute_saas(events):
    by = _by_type(events)
    paid_ids = {p["invoice_id"] for p in by.get("payment_succeeded", [])}
    open_invoices = [
        p for p in by.get("invoice_generated", []) if p["invoice_id"] not in paid_ids
    ]
    resolved = {p["ticket_id"] for p in by.get("ticket_resolved", [])}
    return {
        "open_invoices_count": len(open_invoices),
        "open_invoices_amount": _round_money(
            sum(p["total"] for p in open_invoices)
        ),
        "paid_revenue_total": _round_money(
            sum(p["amount"] for p in by.get("payment_succeeded", []))
        ),
        "open_tickets_count": sum(
            1
            for p in by.get("support_ticket_opened", [])
            if p["ticket_id"] not in resolved
        ),
        "churned_count": len(by.get("churned", [])),
    }


def _recompute_trades(events):
    by = _by_type(events)
    paid_ids = {p["invoice_id"] for p in by.get("payment_received", [])}
    open_invoices = [
        p for p in by.get("invoice_generated", []) if p["invoice_id"] not in paid_ids
    ]
    return {
        "tickets_by_status": _tally(
            events,
            "ticket_id",
            {
                "service_ticket_created": "open",
                "technician_assigned": "assigned",
                "technician_dispatched": "dispatched",
                "technician_arrived": "arrived",
                "diagnosis_recorded": "diagnosed",
                "work_completed": "completed",
                "invoice_generated": "invoiced",
            },
        ),
        "open_invoices_count": len(open_invoices),
        "open_invoices_amount": _round_money(
            sum(p["total"] for p in by.get("invoice_generated", []))
            - sum(p["amount"] for p in by.get("payment_received", []))
        ),
        "paid_total": _round_money(
            sum(p["amount"] for p in by.get("payment_received", []))
        ),
        "parts_used_total": sum(p["qty"] for p in by.get("parts_used", [])),
    }


# Per-business stream-recomputation rules. A field absent from a
# business's rule output is not derivable from the stream alone; the
# reasons below (for the fields we know about) say why. Any other
# stored field is reported with the generic reason.
_POSITION_RULES = {
    "cafe": _recompute_cafe,
    "clinic": _recompute_clinic,
    "ecommerce": _recompute_ecommerce,
    "gym": _recompute_gym,
    "logistics": _recompute_logistics,
    "saas": _recompute_saas,
    "field_service": _recompute_trades,
    "electrician": _recompute_trades,
    "plumber": _recompute_trades,
}

_UNVERIFIABLE_REASONS = {
    ("cafe", "stock"): "ingredient consumption is recipe-mediated; no per-consumption event names it",
    ("cafe", "orders_by_status"): "order status follows multi-item progress rules whose transitions are not all named in the stream",
    ("cafe", "tickets_by_status"): "tickets are created implicitly per order item; creation is not an event",
    ("cafe", "tables_occupied"): "tables are setup state; order payments free tables without a naming event",
    ("cafe", "tables_total"): "setup state from initialize(), not an event",
    ("clinic", "patient_count"): "patient pool is created at initialize(), not by events",
    ("clinic", "provider_count"): "provider pool is created at initialize(), not by events",
    ("ecommerce", "inventory"): "initial stock is assigned at initialize(); only adjustments carry absolute levels",
    ("gym", "members_by_status"): "member pool seeded at initialize before the stream starts",
    ("gym", "active_count"): "member pool seeded at initialize before the stream starts",
    ("gym", "frozen_count"): "member pool seeded at initialize before the stream starts",
    ("gym", "cancelled_count"): "member pool seeded at initialize before the stream starts",
    ("gym", "class_count"): "classes are seeded at initialize() as well as scheduled by events",
    ("gym", "booking_count"): "bookings are seeded at initialize() as well as made by events",
    ("gym", "at_risk_count"): "derived from internal churn-risk scoring, not from events",
    ("logistics", "shipments_by_status"): "the position retains only a filtered shipment set whose retention rule is not named in the stream",
    ("saas", "account_count"): "accounts are seeded at initialize() as well as signed up by events",
    ("saas", "active_subscriptions"): "subscriptions are seeded at initialize(); account-level churn does not name the subscription it cancels",
    ("saas", "mrr"): "depends on the seeded subscriptions and unnamed account-level churn (see active_subscriptions)",
    ("field_service", "tech_count"): "technician pool is created at initialize(), not by events",
    ("electrician", "tech_count"): "technician pool is created at initialize(), not by events",
    ("plumber", "tech_count"): "technician pool is created at initialize(), not by events",
}


def check_position(
    events: list[dict[str, Any]],
    business_type: str | None,
    stored_position: dict[str, Any] | None,
    report: VerifyReport,
) -> CheckResult:
    name = "position"
    if stored_position is None:
        return CheckResult(
            name,
            SKIP,
            "no stored position for this run (no checkpoint 'position' "
            "snapshot and no state_snapshot_recorded event)",
        )
    position = stored_position.get("position") or {}
    as_of = stored_position.get("sequence")
    rules = _POSITION_RULES.get(business_type or "")
    if rules is None:
        report.unverifiable_fields = sorted(position)
        return CheckResult(
            name, SKIP, f"no recomputation rules for business {business_type!r}"
        )
    relevant = [
        e for e in events if as_of is None or e["sequence"] <= as_of
    ]
    expected = rules(relevant)
    report.unverifiable_fields = sorted(set(position) - set(expected))
    for field_name in sorted(expected):
        if position.get(field_name) != expected[field_name]:
            return CheckResult(
                name,
                FAIL,
                f"position field {field_name!r} (as of sequence {as_of}): "
                f"stored={position.get(field_name)!r} "
                f"recomputed={expected[field_name]!r}",
            )
    detail = (
        f"{len(expected)} position fields recomputed from the stream "
        f"match the stored position (as of sequence {as_of})"
    )
    if report.unverifiable_fields:
        detail += (
            f"; {len(report.unverifiable_fields)} field(s) not "
            "stream-derivable (see unverifiable_fields)"
        )
    return CheckResult(name, PASS, detail)


def unverifiable_reason(business_type: str | None, field_name: str) -> str:
    """Why a position field cannot be recomputed from the stream."""
    return _UNVERIFIABLE_REASONS.get(
        (business_type or "", field_name),
        "not derivable from the event stream alone",
    )


# ----------------------------------------------------------------------
# Check 4: action audit consistency (Phase 3 semantics)
# ----------------------------------------------------------------------


def check_action_audit(
    events: list[dict[str, Any]], definition: BusinessDefinition
) -> CheckResult:
    name = "action_audit"
    audits = [
        e
        for e in events
        if e["event_type"] in ("action_applied", "action_rejected")
    ]
    if not audits:
        return CheckResult(name, SKIP, "no action audit events in stream")
    declared = {a.action_type: a for a in definition.actions}
    applied_ids: dict[str, int] = {}
    by_sequence = {e["sequence"]: index for index, e in enumerate(events)}
    for audit in audits:
        payload = audit["payload"]
        action_id = payload.get("action_id")
        action_type = payload.get("action_type")
        where = f"audit at sequence {audit['sequence']} (action_id={action_id!r})"
        if action_type not in declared:
            return CheckResult(
                name,
                FAIL,
                f"{where}: action_type {action_type!r} is not declared "
                f"for business {definition.business_type!r}",
            )
        if audit["event_type"] == "action_rejected":
            if not payload.get("reason"):
                return CheckResult(
                    name, FAIL, f"{where}: action_rejected without a reason"
                )
            continue
        # action_applied
        if action_id in applied_ids:
            return CheckResult(
                name,
                FAIL,
                f"{where}: action_id applied twice (first at sequence "
                f"{applied_ids[action_id]})",
            )
        applied_ids[action_id] = audit["sequence"]
        effect_types = payload.get("effect_event_types") or []
        declared_effect = declared[action_type].effect_event_type
        if declared_effect not in effect_types:
            return CheckResult(
                name,
                FAIL,
                f"{where}: declared effect {declared_effect!r} not in "
                f"audited effect_event_types {effect_types!r}",
            )
        index = by_sequence[audit["sequence"]]
        preceding = events[index - len(effect_types) : index]
        if [e["event_type"] for e in preceding] != list(effect_types):
            return CheckResult(
                name,
                FAIL,
                f"{where}: effect events {effect_types!r} do not "
                "immediately precede the audit event",
            )
    return CheckResult(
        name,
        PASS,
        f"{len(audits)} audit events consistent "
        f"({len(applied_ids)} applied, {len(audits) - len(applied_ids)} rejected)",
    )


# ----------------------------------------------------------------------
# Check 5 (optional): determinism re-run
# ----------------------------------------------------------------------


def _normalised_stream(events: list[dict[str, Any]]) -> list[tuple[str, dict]]:
    normalised = []
    for e in events:
        payload = {
            k: v for k, v in e["payload"].items() if k not in WALL_CLOCK_KEYS
        }
        normalised.append((e["event_type"], payload))
    return normalised


def check_determinism(
    events: list[dict[str, Any]],
    business_type: str | None,
    seed: int | None,
) -> CheckResult:
    name = "determinism"
    if business_type is None:
        return CheckResult(name, SKIP, "business type unknown")
    if any(e["event_type"] in ("action_applied", "action_rejected") for e in events):
        return CheckResult(
            name,
            SKIP,
            "run contains action audits; the action inputs are not "
            "recoverable from stored metadata",
        )
    if any(
        e["event_type"] in ("scenario_started", "scenario_ended") for e in events
    ):
        return CheckResult(
            name,
            SKIP,
            "run contains scenario markers; the scenario schedule is "
            "not recoverable from stored metadata",
        )
    if any(e["event_type"] == "shift_closed" for e in events):
        return CheckResult(
            name,
            SKIP,
            "run contains shift_closed; the original duration_seconds "
            "(which drives the cafe closing phase) is not stored",
        )
    if seed is None:
        return CheckResult(
            name,
            SKIP,
            "no seed recorded for this run (no checkpoint snapshot); "
            "cannot re-run",
        )

    # Recover the run shape from the stream itself.
    snapshot_events = [
        e for e in events if e["event_type"] == "state_snapshot_recorded"
    ]
    business_count = sum(
        1 for e in events if e["event_type"] not in FRAMEWORK_TYPES
    )
    snapshot_every = 0
    if snapshot_events:
        first_snapshot = snapshot_events[0]["sequence"]
        snapshot_every = sum(
            1
            for e in events
            if e["event_type"] not in FRAMEWORK_TYPES
            and e["sequence"] < first_snapshot
        )
    summaries_present = any(
        e["event_type"] == "period_summary" for e in events
    )
    timestamps = [e["timestamp"] for e in events if e.get("timestamp")]
    tick = 1.0
    if len(timestamps) >= 2:
        deltas = [
            (b - a).total_seconds()
            for a, b in zip(timestamps, timestamps[1:])
            if (b - a).total_seconds() > 0
        ]
        if deltas:
            tick = min(deltas)

    # Deferred imports: re-running needs the full simulator stack.
    from eden_business_simulator.businesses import load_simulator
    from eden_business_simulator.config import SimulatorConfig
    from eden_business_simulator.continuous_runner import ContinuousRunner
    from eden_business_simulator.models import Clock
    from eden_business_simulator.storage.memory import MemoryStorageAdapter

    stream_id = "verify_rerun"
    config = SimulatorConfig(
        business_type=business_type,
        # A duration no run reaches: the cafe closing phase keys off
        # duration_seconds, and shift_closed runs are SKIPped above.
        duration_seconds=1e12,
        events_per_second=1.0,
        max_events=business_count,
        seed=seed,
        stream_id=stream_id,
        storage_backend="memory",
        checkpoint_interval_events=10**9,
        checkpoint_interval_seconds=1e12,
        position_snapshot_every_events=snapshot_every,
        period_summaries_enabled=summaries_present,
    )
    simulator = load_simulator(business_type)
    simulator.configure(config)
    simulator.initialize(seed)
    storage = MemoryStorageAdapter("memory://", stream_id)
    runner = ContinuousRunner(
        config, simulator, storage, realtime=False, command_polling=False
    )
    if timestamps:
        runner.clock = Clock(start_time=timestamps[0], tick_seconds=tick)
    runner.run()
    rerun_events = event_dicts(storage.read_from())

    original = _normalised_stream(events)
    rerun = _normalised_stream(rerun_events)
    if len(original) != len(rerun):
        return CheckResult(
            name,
            FAIL,
            f"re-run produced {len(rerun)} events, stored run has "
            f"{len(original)} (run shape may not be fully recoverable "
            "from stored metadata)",
        )
    for index, (expected, actual) in enumerate(zip(original, rerun)):
        if expected != actual:
            detail = (
                f"streams diverge at stream position {index} "
                f"(stored sequence {events[index]['sequence']}): "
                f"stored {expected[0]!r} vs re-run {actual[0]!r}"
            )
            if expected[0] == actual[0]:
                differing = [
                    key
                    for key in set(expected[1]) | set(actual[1])
                    if expected[1].get(key) != actual[1].get(key)
                ]
                detail += f"; differing payload keys: {sorted(differing)}"
            return CheckResult(name, FAIL, detail)
    return CheckResult(
        name,
        PASS,
        f"re-run with seed {seed} reproduces all {len(original)} events "
        f"(business events: {business_count})",
    )


# ----------------------------------------------------------------------
# Check 6: resume integrity — the final checkpoint as a continuation
# point (restorable state S5)
# ----------------------------------------------------------------------

#: How many events the restored simulator must generate, without
#: error, as a continuation smoke test. This proves the restored
#: state is *live* (generators run against it), not that the
#: continuation equals any particular stream — the stored run's own
#: continuation past its final checkpoint does not exist to diff
#: against.
RESUME_CONTINUATION_EVENTS = 3


def _first_difference(left: Any, right: Any, path: str = "") -> str | None:
    """Describe the first difference between two plain-data trees."""
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right), key=str):
            where = f"{path}.{key}" if path else str(key)
            if key not in left:
                return f"{where}: only in restored"
            if key not in right:
                return f"{where}: only in stored"
            found = _first_difference(left[key], right[key], where)
            if found is not None:
                return found
        return None
    if left != right:
        return f"{path or '<root>'}: stored={left!r} restored={right!r}"
    return None


def check_resume_integrity(
    events: list[dict[str, Any]],
    business_type: str | None,
    snapshot: dict[str, Any] | None,
    checkpoint: dict[str, Any] | None,
    stored_position: dict[str, Any] | None,
) -> CheckResult:
    """Restore the final stored checkpoint and prove it reproduces.

    Storage keeps only the *latest* snapshot per stream, so the only
    checkpoint whose integrity can be proven after the fact is the
    final one. This check restores it into a fresh simulator in the
    runner's restore order and requires: the full state to
    re-serialise identically (restore is lossless), the KPI tracker
    and scenario sections (when present) to round-trip, the restored
    position to equal the position stored at the same checkpoint,
    and a short continuation to generate events without error.

    What this does NOT prove, by construction: that earlier
    (overwritten) checkpoints were valid continuation points, and
    that the stored stream was actually produced by resuming from
    this checkpoint — the stream-oriented checks cover the stream
    itself; this check covers the checkpoint artifact.
    """
    name = "resume_integrity"
    if snapshot is None:
        return CheckResult(
            name,
            SKIP,
            "no checkpoint snapshot stored for this run; there is no "
            "checkpoint artifact to restore",
        )
    from eden_business_simulator.framework.state_codec import (
        SNAPSHOT_FORMAT_VERSION,
    )

    snapshot_format = snapshot.get("snapshot_format")
    if snapshot_format is None:
        return CheckResult(
            name,
            SKIP,
            "stored snapshot is a legacy summary-only snapshot "
            "(pre-restorable-state, R2): it carries no full state, so "
            "resume integrity cannot be proven from stored artifacts",
        )
    if snapshot_format != SNAPSHOT_FORMAT_VERSION:
        return CheckResult(
            name,
            FAIL,
            f"stored snapshot_format {snapshot_format!r} is not "
            f"readable by this build (reads {SNAPSHOT_FORMAT_VERSION}); "
            "the runner would refuse to resume from this checkpoint",
        )
    data = snapshot.get("data") or {}
    full_state = data.get("full_state")
    if not isinstance(full_state, dict):
        return CheckResult(
            name,
            SKIP,
            "snapshot carries no full_state section (the business did "
            "not opt into full-state snapshots); resume integrity "
            "cannot be proven from stored artifacts",
        )
    if stored_position is None:
        return CheckResult(
            name,
            SKIP,
            "no stored position snapshot to compare the restored "
            "position against",
        )
    checkpoint_sequence = (checkpoint or {}).get("last_sequence")
    position_sequence = stored_position.get("sequence")
    if checkpoint_sequence is None or position_sequence != checkpoint_sequence:
        return CheckResult(
            name,
            SKIP,
            f"stored position is as of sequence {position_sequence} "
            f"but the final checkpoint is at sequence "
            f"{checkpoint_sequence}; the two were not written at the "
            "same checkpoint, so a restored position cannot be "
            "compared like-for-like",
        )

    # Deferred imports: restoring needs the full simulator stack.
    from eden_business_simulator.businesses import load_simulator
    from eden_business_simulator.config import SimulatorConfig
    from eden_business_simulator.framework.kpi import tracker_for
    from eden_business_simulator.framework.scenario import (
        ScenarioScheduleEntry,
    )
    from eden_business_simulator.models import Clock

    seed = snapshot.get("seed")
    if not isinstance(seed, int) or isinstance(seed, bool):
        seed = 0  # restore overwrites RNG state wholesale regardless

    # A simulator only builds a scenario engine when its config has a
    # schedule; rebuild one from the names the checkpoint itself
    # records (restore_state replaces pending/active wholesale, so
    # the constructed engine's initial pending list is immaterial).
    scenario_section = snapshot.get("scenario")
    schedule: list[ScenarioScheduleEntry] = []
    if isinstance(scenario_section, dict):
        for entry in scenario_section.get("pending") or []:
            schedule.append(
                ScenarioScheduleEntry(
                    name=entry["name"],
                    start_offset_minutes=entry["start_offset_minutes"],
                    duration_minutes=entry["duration_minutes"],
                )
            )
        for entry in scenario_section.get("active") or []:
            schedule.append(
                ScenarioScheduleEntry(
                    name=entry["name"],
                    start_offset_minutes=0,
                    duration_minutes=entry["duration_minutes"],
                )
            )

    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=1e12,
        seed=seed,
        stream_id="verify_resume",
        storage_backend="memory",
        scenario_schedule=schedule,
    )
    simulator = load_simulator(business_type)
    simulator.configure(config)
    simulator.initialize(seed)

    clock_section = snapshot.get("clock") or {}
    if clock_section:
        clock = Clock(
            start_time=datetime.fromisoformat(clock_section["start_time"]),
            tick_seconds=float(clock_section["tick_seconds"]),
        )
        clock.now = datetime.fromisoformat(clock_section["now"])
    elif events:
        clock = Clock(start_time=events[-1]["timestamp"], tick_seconds=60.0)
    else:
        return CheckResult(name, SKIP, "no events and no clock state stored")

    try:
        # The runner's restore order: tracker, scenario, then the
        # business state (inside which RNG/Faker restore last).
        tracker_section = snapshot.get("tracker")
        tracker = None
        if tracker_section is not None:
            tracker = tracker_for(config, simulator)
            if tracker is not None:
                tracker.restore_state(tracker_section)
        if scenario_section and schedule:
            simulator.restore_scenario_state(scenario_section)
        simulator.restore_snapshot(data)
    except Exception as exc:  # a checkpoint that cannot be restored
        return CheckResult(
            name,
            FAIL,
            f"restoring the final checkpoint (sequence "
            f"{checkpoint_sequence}) raised {type(exc).__name__}: {exc}",
        )

    if tracker is not None and tracker.serialize_state() != tracker_section:
        difference = _first_difference(
            tracker_section, tracker.serialize_state(), "tracker"
        )
        return CheckResult(
            name,
            FAIL,
            f"KPI tracker state did not round-trip through restore: "
            f"{difference}",
        )
    if scenario_section and schedule:
        restored_scenario = simulator.scenario_state()
        if restored_scenario != scenario_section:
            difference = _first_difference(
                scenario_section, restored_scenario, "scenario"
            )
            return CheckResult(
                name,
                FAIL,
                f"scenario state did not round-trip through restore: "
                f"{difference}",
            )
    reserialised = simulator.full_state_snapshot()
    if reserialised != full_state:
        difference = _first_difference(full_state, reserialised, "full_state")
        return CheckResult(
            name,
            FAIL,
            f"restored full state does not re-serialise to the stored "
            f"full_state: {difference}",
        )
    restored_position = (simulator.position_snapshot() or {}).get("position")
    expected_position = stored_position.get("position")
    if restored_position != expected_position:
        difference = _first_difference(
            expected_position, restored_position, "position"
        )
        return CheckResult(
            name,
            FAIL,
            f"position restored from the final checkpoint differs "
            f"from the position stored at sequence "
            f"{checkpoint_sequence}: {difference}",
        )
    try:
        for _ in range(RESUME_CONTINUATION_EVENTS):
            simulator.scenario_transitions(clock)
            event = simulator.next_event(clock)
            if not isinstance(event, dict) or "event_type" not in event:
                return CheckResult(
                    name,
                    FAIL,
                    "continuation from the restored checkpoint "
                    f"produced a malformed event: {event!r}",
                )
            clock.advance()
    except Exception as exc:
        return CheckResult(
            name,
            FAIL,
            f"continuation from the restored checkpoint raised "
            f"{type(exc).__name__}: {exc}",
        )
    return CheckResult(
        name,
        PASS,
        f"final checkpoint (sequence {checkpoint_sequence}) restored "
        "into a fresh simulator: full state re-serialises identically, "
        "position matches the stored final position, and "
        f"{RESUME_CONTINUATION_EVENTS} continuation events generated "
        "without error. Proves the final checkpoint is a genuine "
        "continuation point; earlier checkpoints are overwritten in "
        "storage and cannot be proven after the fact",
    )


# ----------------------------------------------------------------------
# Loading + top-level entry points
# ----------------------------------------------------------------------


def stored_position_for(
    adapter, events: list[dict[str, Any]]
) -> dict[str, Any] | None:
    """Find the run's final stored position, if any.

    Preference order: the checkpoint ``position`` snapshot (Phase 1 —
    carries the sequence it was taken at), else the payload of the
    last ``state_snapshot_recorded`` event.
    """
    stored = adapter.read_snapshot("position")
    if stored is not None and "position" in stored:
        return stored
    snapshots = [
        e for e in events if e["event_type"] == "state_snapshot_recorded"
    ]
    if snapshots:
        last = snapshots[-1]
        return {**last["payload"], "sequence": last["sequence"]}
    return None


def verify_events(
    events: list[dict[str, Any]],
    definition: BusinessDefinition,
    *,
    stored_position: dict[str, Any] | None = None,
    seed: int | None = None,
    check_determinism_flag: bool = False,
    stream_id: str = "",
    snapshot: dict[str, Any] | None = None,
    checkpoint: dict[str, Any] | None = None,
) -> VerifyReport:
    """Run the stream checks over already-loaded event dicts."""
    report = VerifyReport(
        business_type=definition.business_type, stream_id=stream_id
    )
    report.checks.append(check_sequence_integrity(events))
    report.checks.append(check_period_summaries(events, definition, report))
    report.checks.append(
        check_position(events, definition.business_type, stored_position, report)
    )
    report.checks.append(check_action_audit(events, definition))
    report.checks.append(
        check_resume_integrity(
            events,
            definition.business_type,
            snapshot,
            checkpoint,
            stored_position,
        )
    )
    if check_determinism_flag:
        report.checks.append(
            check_determinism(events, definition.business_type, seed)
        )
    return report


def verify_run(
    storage_backend: str,
    storage_uri: str,
    stream_id: str,
    *,
    check_determinism_flag: bool = False,
) -> VerifyReport:
    """Load a stored run from a durable backend and verify it.

    Supports the ``sqlite`` and ``ndjson`` backends; the memory
    backend is per-process and its runs are not addressable after the
    fact.
    """
    if storage_backend == "memory":
        raise ValueError(
            "memory storage is per-process; only sqlite and ndjson "
            "runs can be verified after the fact"
        )
    adapter = load_storage_adapter(storage_backend, storage_uri, stream_id)
    try:
        events = event_dicts(adapter.read_from())
        business_type = events[0]["business_type"] if events else None
        if business_type is None:
            report = VerifyReport(business_type=None, stream_id=stream_id)
            report.checks.append(
                CheckResult(
                    "sequence_integrity", FAIL,
                    f"no events stored for stream {stream_id!r}",
                )
            )
            return report
        definition = load_definition(business_type)
        stored_position = stored_position_for(adapter, events)
        seed = None
        latest = adapter.read_snapshot("latest")
        if latest is not None:
            seed = latest.get("seed")
        return verify_events(
            events,
            definition,
            stored_position=stored_position,
            seed=seed,
            check_determinism_flag=check_determinism_flag,
            stream_id=stream_id,
            snapshot=latest,
            checkpoint=adapter.read_checkpoint(),
        )
    finally:
        adapter.close()
