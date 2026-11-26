"""Phase 2: KPI period-summary tests.

The per-business recomputation tests are the heart of this file: they run a
real simulator across a simulated midnight, then independently aggregate
the raw business events from storage and assert the ``period_summary``
KPIs match -- the property an external verifier (Beth/Aleph) relies on.
"""

from __future__ import annotations

from collections import Counter
from datetime import datetime, timezone
from typing import Any

import pytest

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.definition import (
    BusinessDefinition,
    load_definition,
)
from eden_business_simulator.framework.kpi import PeriodTracker
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner
from eden_business_simulator.storage.memory import MemoryStorageAdapter

ALL = [
    "cafe", "clinic", "ecommerce", "electrician", "field_service",
    "gym", "logistics", "plumber", "saas",
]

EXPECTED_KPIS = {
    "cafe": {"revenue", "orders", "orders_taken", "avg_order_value",
             "waste_cost", "stock_counts"},
    "clinic": {"revenue", "appointments", "no_shows", "no_show_rate",
               "cancellations", "claims_submitted", "claims_denied"},
    "ecommerce": {"revenue", "orders", "refunds", "refund_count", "views"},
    "gym": {"new_members", "cancellations", "frozen", "check_ins",
            "failed_payments", "retail_revenue"},
    "logistics": {"deliveries", "attempts", "delivered_attempts",
                  "success_rate", "exceptions", "returns", "fuel_cost"},
    "saas": {"new_accounts", "churned", "invoices_paid", "invoices_failed",
             "revenue", "mrr_end", "tickets_opened"},
}
for _t in ("field_service", "electrician", "plumber"):
    EXPECTED_KPIS[_t] = {"revenue", "jobs_completed", "invoices",
                         "avg_job_value", "parts_cost"}

NEAR_MIDNIGHT = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)


# ----------------------------------------------------------------------
# Definition-level validation
# ----------------------------------------------------------------------


def _definition(kpis, events=("sale", "refund")):
    return BusinessDefinition.model_validate({
        "business_type": "demo",
        "events": [{"event_type": e, "base_weight": 1.0} for e in events],
        "kpis": kpis,
    })


def test_kpi_unknown_event_fails():
    with pytest.raises(ValueError, match="unknown event"):
        _definition([{"name": "x", "kind": "count", "event_type": "nope"}])


def test_kpi_duplicate_name_fails():
    with pytest.raises(ValueError, match="duplicate KPI"):
        _definition([
            {"name": "x", "kind": "count", "event_type": "sale"},
            {"name": "x", "kind": "count", "event_type": "refund"},
        ])


def test_kpi_ratio_unknown_reference_fails():
    with pytest.raises(ValueError, match="unknown"):
        _definition([
            {"name": "x", "kind": "ratio", "numerator": "ghost",
             "denominator": "x"},
        ])


def test_kpi_ratio_cycle_fails():
    with pytest.raises(ValueError, match="cyclic"):
        _definition([
            {"name": "a", "kind": "ratio", "numerator": "b",
             "denominator": "b"},
            {"name": "b", "kind": "ratio", "numerator": "a",
             "denominator": "a"},
        ])


def test_kpi_missing_required_field_fails():
    with pytest.raises(ValueError, match="missing fields"):
        _definition([{"name": "x", "kind": "sum", "event_type": "sale"}])


def test_kpi_unknown_computed_handler_fails_at_tracker_build():
    definition = _definition([
        {"name": "x", "kind": "computed", "handler": "no_such_handler"},
    ])
    with pytest.raises(ValueError, match="unknown computed handler"):
        PeriodTracker(definition)


@pytest.mark.parametrize("business_type", ALL)
def test_all_definitions_declare_expected_kpis(business_type):
    definition = load_definition(business_type)
    assert {k.name for k in definition.kpis} == EXPECTED_KPIS[business_type]


# ----------------------------------------------------------------------
# Tracker unit tests
# ----------------------------------------------------------------------


def _tracker(position=None):
    definition = _definition([
        {"name": "sales", "kind": "count", "event_type": "sale"},
        {"name": "revenue", "kind": "sum", "event_type": "sale",
         "field": "amount"},
        {"name": "web_sales", "kind": "count_where", "event_type": "sale",
         "filter_field": "channel", "filter_value": "web"},
        {"name": "web_revenue", "kind": "sum_where", "event_type": "sale",
         "field": "amount", "filter_field": "channel",
         "filter_value": "web"},
        {"name": "avg_sale", "kind": "ratio", "numerator": "revenue",
         "denominator": "sales"},
        {"name": "mrr", "kind": "snapshot", "field": "position.mrr"},
        {"name": "total_events", "kind": "computed",
         "handler": "total_events"},
    ])
    return PeriodTracker(
        definition,
        position_provider=lambda: position or {"position": {"mrr": 99.0}},
        computed_handlers={
            "total_events": lambda counts, pos: sum(counts.values())
        },
    )


def _at(day, hour=12):
    return datetime(2026, 1, day, hour, tzinfo=timezone.utc)


def test_tracker_vocabulary_and_boundary():
    tracker = _tracker()
    sales = [
        ({"amount": 10.0, "channel": "web"}, 0),
        ({"amount": 20.0, "channel": "shop"}, 1),
        ({"amount": 30.0, "channel": "web"}, 2),
    ]
    for payload, seq in sales:
        tracker.observe("sale", payload, sequence=seq, timestamp=_at(1))
    tracker.observe("refund", {"amount": 5.0}, sequence=3, timestamp=_at(1))

    # No summary while still on the same simulated day.
    assert tracker.close_if_crossed(_at(1, hour=23)) is None

    summary = tracker.close_if_crossed(_at(2, hour=0))
    assert summary is not None
    assert summary["partial"] is False
    assert summary["period_start"] == "2026-01-01"
    assert summary["period_end"] == "2026-01-02"
    assert summary["sequence_start"] == 0
    assert summary["sequence_end"] == 3
    assert summary["event_counts"] == {"sale": 3, "refund": 1}
    kpis = summary["kpis"]
    assert kpis["sales"] == 3
    assert kpis["revenue"] == 60.0
    assert kpis["web_sales"] == 2
    assert kpis["web_revenue"] == 40.0
    assert kpis["avg_sale"] == 20.0
    assert kpis["mrr"] == 99.0
    assert kpis["total_events"] == 4


def test_tracker_ratio_divide_by_zero_is_zero():
    tracker = _tracker()
    tracker.observe("refund", {"amount": 5.0}, sequence=0, timestamp=_at(1))
    summary = tracker.close_final()
    assert summary["partial"] is True
    assert summary["kpis"]["sales"] == 0
    assert summary["kpis"]["avg_sale"] == 0.0


def test_tracker_snapshot_missing_field_is_none():
    tracker = _tracker(position={"position": {}})
    tracker.observe("sale", {"amount": 1.0}, sequence=0, timestamp=_at(1))
    assert tracker.close_final()["kpis"]["mrr"] is None


def test_tracker_empty_periods_produce_no_summary():
    tracker = _tracker()
    assert tracker.close_if_crossed(_at(2)) is None
    assert tracker.close_final() is None


# ----------------------------------------------------------------------
# Runner integration: independent recomputation per business
# ----------------------------------------------------------------------


def _continuous_run(business_type, max_events=150, seed=42, **config_kwargs):
    storage = MemoryStorageAdapter("memory://", f"kpi_{business_type}")
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        events_per_second=1.0,
        seed=seed,
        max_events=max_events,
        stream_id=storage.stream_id,
        storage_backend="memory",
        checkpoint_interval_events=1000,
        checkpoint_interval_seconds=3600.0,
        **config_kwargs,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    # Pin the simulated clock just before midnight so the run crosses
    # exactly one calendar-day boundary (60 events on day 1 at 1s ticks).
    runner.clock = Clock(start_time=NEAR_MIDNIGHT, tick_seconds=1.0)
    runner.run()
    records = list(storage.read_from())
    return sim, records


def _aggregate(definition, events):
    """Independently recompute KPIs from raw events (verifier's view)."""
    counts = Counter(e.event_type for e in events)
    resolved: dict[str, Any] = {}

    def resolve(kpi):
        if kpi.name in resolved:
            return resolved[kpi.name]
        if kpi.kind == "count":
            value = counts.get(kpi.event_type, 0)
        elif kpi.kind == "count_where":
            value = sum(
                1 for e in events
                if e.event_type == kpi.event_type
                and e.payload.get(kpi.filter_field) == kpi.filter_value
            )
        elif kpi.kind in ("sum", "sum_where"):
            value = 0.0
            for e in events:
                if e.event_type != kpi.event_type:
                    continue
                if (
                    kpi.kind == "sum_where"
                    and e.payload.get(kpi.filter_field) != kpi.filter_value
                ):
                    continue
                v = e.payload.get(kpi.field)
                if isinstance(v, (int, float)) and not isinstance(v, bool):
                    value += float(v)
        elif kpi.kind == "ratio":
            by_name = {k.name: k for k in definition.kpis}
            denominator = resolve(by_name[kpi.denominator])
            value = (
                0.0 if not denominator
                else float(resolve(by_name[kpi.numerator])) / float(denominator)
            )
        else:
            return None  # snapshot/computed: not stream-recomputable
        resolved[kpi.name] = value
        return value

    for kpi in definition.kpis:
        resolve(kpi)
    return resolved


@pytest.mark.parametrize("business_type", ALL)
def test_period_summary_recomputation(business_type):
    definition = load_definition(business_type)
    sim, records = _continuous_run(business_type)
    summaries = [r for r in records if r.envelope.event_type == "period_summary"]
    business = [
        r for r in records
        if r.envelope.event_type not in ("period_summary",
                                         "state_snapshot_recorded")
    ]

    # Summaries never consume a max_events slot; sequences stay contiguous.
    assert len(business) == 150
    assert [r.sequence for r in records] == list(range(len(records)))

    # Exactly one summary: day 1 closed on crossing. Day 2 is still
    # open at run end — since restorable-state S4 (R3) the continuous
    # runner checkpoints its open period in-progress instead of
    # flushing a final partial at every process end, so a resumed
    # run closes the day as one summary. The final-partial behaviour
    # survives on the batch Runner (test_batch_runner_* below) and in
    # the PeriodTracker unit tests.
    assert len(summaries) == 1
    closed = summaries[0]
    assert closed.envelope.payload["period_start"] == "2026-01-01"
    assert closed.envelope.payload["partial"] is False
    assert closed.sequence == 60  # right after day 1's 60 business events

    for record in summaries:
        payload = record.envelope.payload
        assert payload["schema_version"] == 1
        assert payload["business_type"] == business_type
        assert payload["definition_hash"] == definition.definition_hash()
        period_events = [
            r.envelope for r in business
            if payload["sequence_start"] <= r.sequence <= payload["sequence_end"]
        ]
        assert payload["event_counts"] == dict(
            Counter(e.event_type for e in period_events)
        )
        expected = _aggregate(definition, period_events)
        for kpi in definition.kpis:
            if kpi.kind in ("snapshot", "computed"):
                continue
            assert payload["kpis"][kpi.name] == expected[kpi.name], (
                business_type, payload["period_start"], kpi.name,
            )


def test_saas_mrr_end_snapshot_kpi():
    sim, records = _continuous_run("saas")
    summaries = [
        r.envelope.payload for r in records
        if r.envelope.event_type == "period_summary"
    ]
    # Since S4 (R3) the continuous run emits only the closed period's
    # summary; the open day's run-end partial is batch-runner /
    # PeriodTracker.close_final behaviour (unit-tested above).
    assert len(summaries) == 1
    closed = summaries[0]
    # Closed period: MRR as of the close, i.e. after day 1's 60 events --
    # replay those events directly and compare positions.
    config = SimulatorConfig(business_type="saas", duration_seconds=3600)
    replay = load_simulator("saas")
    replay.configure(config)
    replay.initialize(42)
    clock = Clock(start_time=NEAR_MIDNIGHT, tick_seconds=1.0)
    for _ in range(60):
        replay.next_event(clock)
        clock.advance()
    assert closed["kpis"]["mrr_end"] == replay.position_snapshot()["position"]["mrr"]


@pytest.mark.parametrize("business_type", ALL)
def test_period_summaries_deterministic(business_type):
    _, records_a = _continuous_run(business_type, max_events=80)
    _, records_b = _continuous_run(business_type, max_events=80)
    summaries_a = [
        r.envelope.payload for r in records_a
        if r.envelope.event_type == "period_summary"
    ]
    summaries_b = [
        r.envelope.payload for r in records_b
        if r.envelope.event_type == "period_summary"
    ]
    assert summaries_a == summaries_b
    assert summaries_a  # the run did produce summaries


def test_period_summaries_disabled():
    _, records = _continuous_run(
        "cafe", max_events=80, period_summaries_enabled=False
    )
    assert not any(
        r.envelope.event_type == "period_summary" for r in records
    )
    assert len(records) == 80


# ----------------------------------------------------------------------
# Batch Runner integration
# ----------------------------------------------------------------------


class _ListAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)


def _batch_run(business_type="cafe", max_events=120, start=NEAR_MIDNIGHT,
               **config_kwargs):
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=3600,
        events_per_second=1.0,
        seed=42,
        max_events=max_events,
        **config_kwargs,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(42)
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    runner.clock = Clock(start_time=start, tick_seconds=1.0)
    runner.run()
    return adapter.events


def test_batch_runner_crossing_and_final_partial():
    events = _batch_run()
    summaries = [e for e in events if e.event_type == "period_summary"]
    business = [e for e in events if e.event_type != "period_summary"]
    assert len(business) == 120
    assert len(summaries) == 2
    assert summaries[0].payload["partial"] is False
    assert summaries[0].payload["period_start"] == "2026-01-01"
    assert summaries[0].payload["sequence_start"] == 0
    assert summaries[0].payload["sequence_end"] == 59
    assert summaries[1].payload["partial"] is True
    assert summaries[1].payload["period_start"] == "2026-01-02"
    # The closed summary is emitted before day 2's first business event.
    idx = events.index(summaries[0])
    assert events[idx + 1].event_type != "period_summary"


def test_batch_runner_short_run_gets_final_partial_only():
    events = _batch_run(
        max_events=10, start=datetime(2026, 1, 1, 12, tzinfo=timezone.utc)
    )
    summaries = [e for e in events if e.event_type == "period_summary"]
    assert len(summaries) == 1
    assert summaries[0].payload["partial"] is True
    assert summaries[0].payload["period_start"] == "2026-01-01"


def test_batch_business_events_identical_with_summaries_disabled():
    enabled = _batch_run()
    disabled = _batch_run(period_summaries_enabled=False)
    enabled_business = [
        (e.event_type, e.payload) for e in enabled
        if e.event_type != "period_summary"
    ]
    disabled_business = [(e.event_type, e.payload) for e in disabled]
    assert enabled_business == disabled_business
    assert not any(e.event_type == "period_summary" for e in disabled)
