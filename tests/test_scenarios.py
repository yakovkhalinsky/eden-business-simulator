"""Phase 6: scenario pack tests.

Scenario packs are configuration (``configs/scenarios.json``) that
multiply catalogue selection weights while active. The properties that
matter:

* golden safety — no schedule (or no active scenario) leaves selection
  byte-identical; the whole pre-existing suite passing unmodified is
  the main proof, and the hook tests here pin the mechanism (a
  multiplier of exactly 1.0, eligible weights unchanged);
* effect — with a fixed seed, each pack moves its target event counts
  in the expected direction versus the same-seed baseline;
* lifecycle — ``scenario_started``/``scenario_ended`` markers bracket
  the active window, consume no ``max_events`` slot, and are invisible
  to the KPI tracker;
* determinism — same seed + schedule reproduces the stream exactly,
  and scenario randomness (D10) lives on its own derived stream.
"""

from __future__ import annotations

import random
from collections import Counter
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from typer.testing import CliRunner

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.cli import app
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.definition import load_definition
from eden_business_simulator.framework.scenario import (
    ScenarioEngine,
    ScenarioScheduleEntry,
    load_scenarios,
    scenario_names,
)
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner
from eden_business_simulator.storage.memory import MemoryStorageAdapter

ALL_BUSINESSES = [
    "cafe", "clinic", "ecommerce", "electrician", "field_service",
    "gym", "logistics", "plumber", "saas",
]
START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
NEAR_MIDNIGHT = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)
WALL_CLOCK_KEYS = {"registered_at", "signup_at"}

cli_runner = CliRunner()


class ListAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)

    def close(self) -> None:
        pass


def _batch_run(
    business_type: str,
    schedule: list | None = None,
    *,
    seed: int = 42,
    count: int = 2000,
    **config_kwargs,
):
    """Run the batch Runner with a pinned clock (1 simulated minute
    per event) and return the emitted envelopes."""
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=10**9,
        max_events=count,
        events_per_second=1 / 60,
        seed=seed,
        scenario_schedule=schedule or [],
        period_summaries_enabled=False,
        **config_kwargs,
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    adapter = ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False)
    runner.clock = Clock(start_time=START, tick_seconds=60.0)
    runner.run()
    return adapter.events


def _counts(events) -> Counter:
    return Counter(e.event_type for e in events)


def _sim(business_type: str, schedule: list | None = None, seed: int = 42):
    config = SimulatorConfig(
        business_type=business_type,
        duration_seconds=10**9,
        seed=seed,
        scenario_schedule=schedule or [],
    )
    sim = load_simulator(business_type)
    sim.configure(config)
    sim.initialize(seed)
    return sim


# ----------------------------------------------------------------------
# Pack registry / configuration
# ----------------------------------------------------------------------


def test_all_five_packs_load():
    packs = load_scenarios()
    assert scenario_names() == [
        "churn_wave",
        "demand_spike",
        "payment_failure_wave",
        "staff_sick_day",
        "supplier_failure",
    ]
    for pack in packs.values():
        assert pack.duration_minutes > 0
        assert pack.effects, pack.name
        for effect in pack.effects:
            assert effect.multiplier >= 0


def test_pack_targets_exist_in_some_catalogue():
    known: set[str] = set()
    for business_type in ALL_BUSINESSES:
        known.update(load_definition(business_type).event_types())
    for pack in load_scenarios().values():
        for effect in pack.effects:
            assert effect.event_type in known, (
                f"{pack.name} targets unknown event type "
                f"{effect.event_type!r}"
            )


def test_unknown_scenario_fails_fast_in_config():
    with pytest.raises(ValueError, match="unknown scenario"):
        SimulatorConfig(
            business_type="cafe",
            scenario_schedule=[{"name": "no_such_pack"}],
        )


def test_unknown_scenario_fails_fast_in_engine():
    with pytest.raises(ValueError, match="unknown scenario"):
        ScenarioEngine([ScenarioScheduleEntry(name="nope")], seed=42)


def test_schedule_entry_defaults_and_pack_duration():
    entry = ScenarioScheduleEntry(name="churn_wave")
    assert entry.start_offset_minutes == 0
    assert entry.duration_minutes is None
    engine = ScenarioEngine([entry], seed=42)
    clock_events = engine.transitions(START, 0.0)
    assert clock_events[0]["payload"]["duration_minutes"] == (
        load_scenarios()["churn_wave"].duration_minutes
    )


# ----------------------------------------------------------------------
# Engine / selection hook
# ----------------------------------------------------------------------


def test_no_schedule_means_no_scenario_effects():
    sim = _sim("saas")
    assert sim.weight_multiplier("churned") == 1.0
    assert sim.scenario_transitions(
        Clock(start_time=START, tick_seconds=60.0)
    ) == []
    assert sim.active_scenarios() == []


def test_activation_and_expiry_via_hook():
    sim = _sim(
        "cafe",
        [{"name": "staff_sick_day", "duration_minutes": 120}],
    )
    clock = Clock(start_time=START, tick_seconds=60.0)
    started = sim.scenario_transitions(clock)
    assert [e["event_type"] for e in started] == ["scenario_started"]
    payload = started[0]["payload"]
    assert payload["scenario"] == "staff_sick_day"
    assert payload["duration_minutes"] == 120
    started_at = datetime.fromisoformat(payload["started_at"])
    ends_at = datetime.fromisoformat(payload["ends_at"])
    assert ends_at - started_at == timedelta(minutes=120)
    assert payload["effects"] == [
        {"event_type": "staff_clocked_in", "multiplier": 0.25}
    ]
    assert sim.weight_multiplier("staff_clocked_in") == 0.25
    assert sim.weight_multiplier("order_taken") == 1.0
    assert len(sim.active_scenarios()) == 1

    clock.advance(119 * 60)
    assert sim.scenario_transitions(clock) == []
    assert sim.weight_multiplier("staff_clocked_in") == 0.25

    clock.advance(2 * 60)
    ended = sim.scenario_transitions(clock)
    assert [e["event_type"] for e in ended] == ["scenario_ended"]
    assert ended[0]["payload"]["scenario"] == "staff_sick_day"
    assert ended[0]["payload"]["duration_minutes"] == 120
    # After expiry, selection returns to normal.
    assert sim.weight_multiplier("staff_clocked_in") == 1.0
    assert sim.active_scenarios() == []


def test_delayed_start_offset():
    engine = ScenarioEngine(
        [ScenarioScheduleEntry(name="churn_wave", start_offset_minutes=60)],
        seed=42,
    )
    assert engine.transitions(START, 0.0) == []
    assert engine.transitions(START, 59.0) == []
    started = engine.transitions(START + timedelta(minutes=60), 60.0)
    assert [e["event_type"] for e in started] == ["scenario_started"]


def test_overlapping_scenarios_stack_multiplicatively():
    sim = _sim(
        "saas",
        [{"name": "churn_wave"}, {"name": "churn_wave"},
         {"name": "payment_failure_wave"}],
    )
    clock = Clock(start_time=START, tick_seconds=60.0)
    started = sim.scenario_transitions(clock)
    assert len(started) == 3
    assert sim.weight_multiplier("churned") == 16.0  # 4.0 x 4.0
    assert sim.weight_multiplier("payment_failed") == 4.0
    assert sim.weight_multiplier("feature_used") == 1.0


def test_scenario_rng_is_derived_and_deterministic():
    # D10: the scenario RNG stream is seeded from (run seed, scenario
    # name) — reproducible across engines, distinct per scenario, and
    # separate from any business RNG.
    def activated_rng(seed, name):
        engine = ScenarioEngine(
            [ScenarioScheduleEntry(name=name)], seed=seed
        )
        engine.transitions(START, 0.0)
        return engine.scenario_rng(name)

    rng_a = activated_rng(42, "churn_wave")
    rng_b = activated_rng(42, "churn_wave")
    assert [rng_a.random() for _ in range(3)] == [
        rng_b.random() for _ in range(3)
    ]
    rng_c = activated_rng(42, "demand_spike")
    rng_d = activated_rng(43, "churn_wave")
    assert rng_c.random() != rng_a.random()
    assert rng_d.random() != rng_a.random()


def test_catalog_hook_applies_and_zero_suppresses():
    class StubContext:
        def weight_multiplier(self, event_type: str) -> float:
            return {"boosted": 3.0, "muted": 0.0}.get(event_type, 1.0)

    catalog = WeightedEventCatalog(random.Random(42))
    catalog.register("plain", base_weight=2.0)
    catalog.register("boosted", base_weight=2.0)
    catalog.register("muted", base_weight=2.0)

    plain = catalog.eligible_types(hour=8, context=None)
    assert plain == {"plain": 2.0, "boosted": 2.0, "muted": 2.0}

    hooked = catalog.eligible_types(hour=8, context=StubContext())
    assert hooked == {"plain": 2.0, "boosted": 6.0}  # muted suppressed

    class NeutralContext:
        def weight_multiplier(self, event_type: str) -> float:
            return 1.0

    neutral = catalog.eligible_types(hour=8, context=NeutralContext())
    assert neutral == plain  # exactly 1.0 leaves weights byte-identical


def test_cafe_eligible_weight_reflects_sick_day():
    # End-to-end through the real chain (config -> engine -> simulator
    # hook -> catalogue): with staff_sick_day active, the eligible
    # weight of staff_clocked_in drops to exactly a quarter.
    def eligible_clock_in_weight(schedule):
        sim = _sim("cafe", schedule)
        clock = Clock(start_time=START, tick_seconds=60.0)
        sim.scenario_transitions(clock)  # activate scheduled packs
        sim.next_event(clock)  # shift_opened: hires + clocks in the opener
        # The clock-in guard needs a hired staff member who has not
        # clocked in; hire one directly (the stream hires on demand
        # through the setup queue, so this state is set up by hand).
        sim.staff.hire(role="barista", name="Extra Hands")
        return sim.catalog.eligible_types(hour=8, context=sim)[
            "staff_clocked_in"
        ]

    baseline = eligible_clock_in_weight(None)
    sick = eligible_clock_in_weight([{"name": "staff_sick_day"}])
    assert baseline > 0
    assert sick == pytest.approx(baseline * 0.25)


# ----------------------------------------------------------------------
# Stream-level pack effects (fixed seed, pinned clock)
# ----------------------------------------------------------------------

# (business, pack, target event type, minimum scenario/baseline ratio)
DIRECTION_CASES = [
    ("cafe", "supplier_failure", "stock_count_recorded", 1.8),
    ("cafe", "demand_spike", "order_taken", 1.25),
    ("ecommerce", "demand_spike", "order_placed", 1.2),
    ("logistics", "demand_spike", "shipment_created", 1.3),
    ("gym", "demand_spike", "check_in_recorded", 1.4),
    ("saas", "churn_wave", "churned", 2.5),
    ("gym", "churn_wave", "membership_cancelled", 2.5),
    ("saas", "payment_failure_wave", "payment_failed", 1.8),
    ("gym", "payment_failure_wave", "payment_failed", 2.0),
]


@pytest.mark.parametrize(
    "business,pack,target,min_ratio", DIRECTION_CASES
)
def test_pack_moves_target_counts(business, pack, target, min_ratio):
    baseline = _counts(_batch_run(business))
    scenario = _counts(_batch_run(business, [{"name": pack}]))
    assert baseline[target] > 0
    assert scenario[target] >= baseline[target] * min_ratio, (
        f"{pack} on {business}: {target} moved "
        f"{baseline[target]} -> {scenario[target]} "
        f"(wanted >= x{min_ratio})"
    )


def test_supplier_failure_deliveries_come_from_setup_queue():
    # Honest-scope pin: the cafe emits its opening deliveries through
    # a deterministic setup queue (one per ingredient at shift open),
    # which weight multipliers do not govern — so the delivery count
    # itself does not move in v1, while the pack's in-service effect
    # (stock counting, asserted above) does. Documented in
    # docs/scenarios.md.
    baseline = _counts(_batch_run("cafe"))
    scenario = _counts(_batch_run("cafe", [{"name": "supplier_failure"}]))
    assert scenario["supplier_delivery_received"] == (
        baseline["supplier_delivery_received"]
    )


def test_markers_bracket_the_active_window():
    events = _batch_run(
        "saas", [{"name": "churn_wave", "duration_minutes": 120}], count=600
    )
    types = [e.event_type for e in events]
    assert types[0] == "scenario_started"
    started = events[0]
    assert started.payload["scenario"] == "churn_wave"
    started_at = datetime.fromisoformat(started.payload["started_at"])
    ends_at = datetime.fromisoformat(started.payload["ends_at"])
    assert ends_at - started_at == timedelta(minutes=120)

    end_index = types.index("scenario_ended")
    assert events[end_index].payload["scenario"] == "churn_wave"
    # Markers never consume a max_events slot: exactly 600 business
    # events were emitted alongside the two markers.
    business = [t for t in types if not t.startswith("scenario_")]
    assert len(business) == 600


def test_frequency_returns_to_baseline_after_expiry():
    events = _batch_run(
        "saas", [{"name": "churn_wave", "duration_minutes": 120}], count=600
    )
    types = [e.event_type for e in events]
    end_index = types.index("scenario_ended")
    before = [t for t in types[:end_index] if not t.startswith("scenario_")]
    after = [t for t in types[end_index:] if not t.startswith("scenario_")]
    rate_before = Counter(before)["churned"] / len(before)
    rate_after = Counter(after)["churned"] / len(after)
    assert rate_before > rate_after * 1.8


def test_determinism_with_scenario():
    def normalised(events):
        return [
            (
                e.event_type,
                {k: v for k, v in e.payload.items()
                 if k not in WALL_CLOCK_KEYS},
            )
            for e in events
        ]

    schedule = [{"name": "churn_wave"}]
    run_a = normalised(_batch_run("saas", schedule, count=500))
    run_b = normalised(_batch_run("saas", schedule, count=500))
    assert run_a == run_b

    baseline = _batch_run("saas", count=500)
    assert [t for t, _ in run_a] != [e.event_type for e in baseline]


# ----------------------------------------------------------------------
# Continuous runner: markers, sequences, KPI safety
# ----------------------------------------------------------------------


def test_continuous_markers_sequences_and_kpi_reconciliation():
    storage = MemoryStorageAdapter("memory://", "scenario_kpi_saas")
    config = SimulatorConfig(
        business_type="saas",
        duration_seconds=10**9,
        events_per_second=1 / 60,
        seed=42,
        max_events=1500,
        stream_id=storage.stream_id,
        storage_backend="memory",
        checkpoint_interval_events=1000,
        checkpoint_interval_seconds=3600.0,
        scenario_schedule=[{"name": "churn_wave"}],
    )
    sim = load_simulator("saas")
    sim.configure(config)
    sim.initialize(42)
    runner = ContinuousRunner(config, sim, storage, realtime=False)
    runner.clock = Clock(start_time=NEAR_MIDNIGHT, tick_seconds=60.0)
    runner.run()
    records = list(storage.read_from())
    envelopes = [r.envelope for r in records]

    business_types = set(sim.available_event_types())
    business = [e for e in envelopes if e.event_type in business_types]
    markers = [
        e for e in envelopes if e.event_type.startswith("scenario_")
    ]
    # Markers consume sequences but not max_events slots: business
    # events hit the cap exactly, and sequences stay contiguous.
    assert len(business) == 1500
    assert [e.sequence for e in envelopes] == list(range(len(envelopes)))
    assert markers and markers[0].event_type == "scenario_started"
    assert markers[0].sequence == 0

    # KPI safety: every period_summary reconciles with an independent
    # recount of the raw business events in its period, and scenario
    # markers never leak into the counts.
    summaries = [e for e in envelopes if e.event_type == "period_summary"]
    assert summaries
    definition = load_definition("saas")
    count_kpis = [k for k in definition.kpis if k.kind == "count"]
    for summary in summaries:
        payload = summary.payload
        assert "scenario_started" not in payload["event_counts"]
        assert "scenario_ended" not in payload["event_counts"]
        period_events = [
            e for e in business
            if e.timestamp.date().isoformat() == payload["period_start"]
        ]
        assert payload["event_counts"] == dict(
            Counter(e.event_type for e in period_events)
        )
        for kpi in count_kpis:
            assert payload["kpis"][kpi.name] == payload["event_counts"].get(
                kpi.event_type, 0
            )


# ----------------------------------------------------------------------
# CLI
# ----------------------------------------------------------------------


def test_cli_run_with_scenario():
    result = cli_runner.invoke(
        app,
        [
            "run", "cafe",
            "--scenario", "demand_spike",
            "--no-realtime",
            "--max-events", "40",
            "--duration", "100000",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "scenario_started" in result.output
    assert "demand_spike" in result.output


def test_cli_run_scenario_duration_override():
    result = cli_runner.invoke(
        app,
        [
            "run", "saas",
            "--scenario", "churn_wave",
            "--scenario-duration", "30",
            "--no-realtime",
            "--max-events", "100",
            "--duration", "100000",
            "--rate", "0.01",
        ],
    )
    assert result.exit_code == 0, result.output
    assert "scenario_started" in result.output
    assert "scenario_ended" in result.output
    assert '"duration_minutes":30' in result.output


def test_cli_run_unknown_scenario_is_a_clean_error():
    result = cli_runner.invoke(
        app,
        ["run", "cafe", "--scenario", "no_such_pack", "--no-realtime"],
    )
    assert result.exit_code != 0
    assert "unknown scenario" in result.output.lower()
    assert "scenario_started" not in result.output


def test_cli_daemon_with_scenario(tmp_path):
    result = cli_runner.invoke(
        app,
        [
            "daemon", "cafe",
            "--scenario", "demand_spike",
            "--storage", "sqlite",
            "--storage-uri", str(tmp_path / "daemon.db"),
            "--stream-id", "scenario_daemon_test",
            "--max-events", "20",
            "--no-realtime",
            "--output", "none",
        ],
    )
    assert result.exit_code == 0, result.output
    # The daemon's NDJSON output is off; verify via storage instead.
    from eden_business_simulator.storage import load_storage_adapter

    adapter = load_storage_adapter(
        "sqlite", str(tmp_path / "daemon.db"), "scenario_daemon_test"
    )
    try:
        types = [r.envelope.event_type for r in adapter.read_from()]
    finally:
        adapter.close()
    assert types[0] == "scenario_started"
