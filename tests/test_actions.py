"""Action channel tests (Phase 3, cafe pilot).

Covers the definition schema for actions, the shared ``apply_action``
pipeline (approval gate D9, parameter validation, idempotency, the
rejection rule: no RNG consumed, no state mutated), the cafe pilot
effect handlers, and runner integration — scripted actions between
ticks (D6), effect-then-audit emission (D5), the ContinuousRunner
``submit_action`` queue, and KPI-tracker interaction.
"""

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from eden_business_simulator.businesses import load_simulator
from eden_business_simulator.businesses.base import ActionRequest
from eden_business_simulator.config import SimulatorConfig
from eden_business_simulator.continuous_runner import ContinuousRunner
from eden_business_simulator.framework.definition import BusinessDefinition
from eden_business_simulator.models import Clock
from eden_business_simulator.output.base import OutputAdapter
from eden_business_simulator.runner import Runner
from eden_business_simulator.storage.memory import MemoryStorageAdapter

START = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
NEAR_MIDNIGHT = datetime(2026, 1, 1, 23, 59, tzinfo=timezone.utc)
SETUP_EVENTS = 19  # shift_opened + 4 staff + 5 menu + 9 deliveries


def _cafe(seed: int = 42, **config_kwargs):
    config = SimulatorConfig(
        business_type="cafe",
        duration_seconds=3600,
        period_summaries_enabled=False,
        **config_kwargs,
    )
    sim = load_simulator("cafe")
    sim.configure(config)
    sim.initialize(seed)
    clock = Clock(start_time=START, tick_seconds=1.0)
    return sim, clock


def _cafe_in_service(seed: int = 42):
    """A cafe that has emitted its setup events (staff in, menu, stock)."""
    sim, clock = _cafe(seed)
    for _ in range(SETUP_EVENTS):
        sim.next_event(clock)
        clock.advance()
    return sim, clock


def _order(action_id="act_1", **params):
    merged = {"ingredient_id": "ing_001", "qty": 7.5}
    merged.update(params)
    return ActionRequest(
        action_id=action_id,
        action_type="place_supplier_order",
        actor="manager_agent",
        params=merged,
    )


# ----------------------------------------------------------------------
# Definition schema
# ----------------------------------------------------------------------


def _definition(actions):
    return BusinessDefinition.model_validate(
        {
            "schema_version": 1,
            "business_type": "toy",
            "events": [{"event_type": "thing_happened", "base_weight": 1}],
            "actions": actions,
        }
    )


def test_action_def_minimal_valid():
    definition = _definition(
        [
            {
                "action_type": "do_thing",
                "effect": "do_thing",
                "effect_event_type": "thing_happened",
                "params": [
                    {"name": "qty", "type": "number", "min": 1, "max": 5}
                ],
            }
        ]
    )
    assert definition.actions[0].requires_approval is False


def test_action_def_duplicate_action_type_fails():
    action = {
        "action_type": "do_thing",
        "effect": "do_thing",
        "effect_event_type": "thing_happened",
    }
    with pytest.raises(ValidationError):
        _definition([action, action])


def test_action_def_unknown_param_type_fails():
    with pytest.raises(ValidationError):
        _definition(
            [
                {
                    "action_type": "do_thing",
                    "effect": "do_thing",
                    "effect_event_type": "thing_happened",
                    "params": [{"name": "qty", "type": "text"}],
                }
            ]
        )


def test_action_def_min_above_max_fails():
    with pytest.raises(ValidationError):
        _definition(
            [
                {
                    "action_type": "do_thing",
                    "effect": "do_thing",
                    "effect_event_type": "thing_happened",
                    "params": [
                        {"name": "qty", "type": "number", "min": 9, "max": 5}
                    ],
                }
            ]
        )


def test_action_def_duplicate_param_name_fails():
    with pytest.raises(ValidationError):
        _definition(
            [
                {
                    "action_type": "do_thing",
                    "effect": "do_thing",
                    "effect_event_type": "thing_happened",
                    "params": [
                        {"name": "qty", "type": "number"},
                        {"name": "qty", "type": "string"},
                    ],
                }
            ]
        )


def test_available_actions_come_from_definition():
    sim, _ = _cafe()
    names = [a.action_type for a in sim.available_actions()]
    assert names == [
        "place_supplier_order",
        "adjust_price",
        "clock_staff_in",
        "clock_staff_out",
    ]
    by_name = {a.action_type: a for a in sim.available_actions()}
    # D9 pilot choice: price changes are the approval-gated cafe action.
    assert by_name["adjust_price"].requires_approval is True
    assert by_name["place_supplier_order"].requires_approval is False


def test_saas_actions_are_the_phase4c_catalogue():
    # Phase 3 asserted saas had no actions yet; Phase 4c supersedes
    # that — saas now declares its own catalogue (covered in full by
    # tests/test_actions_retail_saas_logistics.py).
    config = SimulatorConfig(business_type="saas", duration_seconds=60)
    sim = load_simulator("saas")
    sim.configure(config)
    sim.initialize(42)
    assert [a.action_type for a in sim.available_actions()] == [
        "chase_invoice",
        "change_plan_price",
        "freeze_account",
        "unfreeze_account",
        "offer_discount",
        "resolve_ticket",
    ]


# ----------------------------------------------------------------------
# apply_action pipeline + cafe handlers (direct)
# ----------------------------------------------------------------------


def test_place_supplier_order_happy_path():
    sim, clock = _cafe_in_service()
    before = sim.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    result = sim.apply_action(_order(), clock)
    assert result.status == "applied"
    assert result.reason is None
    (event,) = result.events
    assert event["event_type"] == "supplier_delivery_received"
    (item,) = event["payload"]["items"]
    assert item == {
        "ingredient_id": "ing_001",
        "name": "espresso",
        "qty": 7.5,
        "unit": "kg",
        "unit_cost": 18.0,
    }
    assert event["payload"]["supplier_id"] == "sup_01"
    after = sim.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    assert after == pytest.approx(before + 7.5)


def test_place_supplier_order_unknown_ingredient_rejected():
    sim, clock = _cafe_in_service()
    result = sim.apply_action(_order(ingredient_id="ing_999"), clock)
    assert result.status == "rejected"
    assert result.reason == "unknown_ingredient:ing_999"
    assert result.events == []


def test_adjust_price_requires_approval_then_applies():
    sim, clock = _cafe_in_service()
    request = ActionRequest(
        action_id="price_1",
        action_type="adjust_price",
        actor="manager_agent",
        params={"item_id": "item_0001", "new_price": 6.25},
    )
    rejected = sim.apply_action(request, clock)
    assert rejected.status == "rejected"
    assert rejected.reason == "requires_approval"
    item = next(i for i in sim.menu.all() if i["item_id"] == "item_0001")
    assert item["price"] == 5.50

    request.approved_by = "yakov"
    applied = sim.apply_action(request, clock)
    assert applied.status == "applied"
    (event,) = applied.events
    assert event["event_type"] == "menu_price_adjusted"
    assert event["payload"]["old_price"] == 5.50
    assert event["payload"]["new_price"] == 6.25
    assert item["price"] == 6.25


def test_adjust_price_unknown_item_rejected():
    sim, clock = _cafe_in_service()
    result = sim.apply_action(
        ActionRequest(
            action_id="price_2",
            action_type="adjust_price",
            actor="manager_agent",
            params={"item_id": "item_9999", "new_price": 6.0},
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == "unknown_item:item_9999"


def test_clock_staff_out_and_in():
    sim, clock = _cafe_in_service()
    staff = sim.staff.get("staff_0001")
    assert staff["clocked_in"] is True

    out = sim.apply_action(
        ActionRequest(
            action_id="out_1",
            action_type="clock_staff_out",
            actor="manager_agent",
            params={"staff_id": "staff_0001"},
        ),
        clock,
    )
    assert out.status == "applied"
    assert out.events[0]["event_type"] == "staff_clocked_out"
    assert out.events[0]["payload"]["staff_id"] == "staff_0001"
    assert staff["clocked_in"] is False

    out_again = sim.apply_action(
        ActionRequest(
            action_id="out_2",
            action_type="clock_staff_out",
            actor="manager_agent",
            params={"staff_id": "staff_0001"},
        ),
        clock,
    )
    assert out_again.status == "rejected"
    assert out_again.reason == "staff_not_clocked_in:staff_0001"

    back_in = sim.apply_action(
        ActionRequest(
            action_id="in_1",
            action_type="clock_staff_in",
            actor="manager_agent",
            params={"staff_id": "staff_0001"},
        ),
        clock,
    )
    assert back_in.status == "applied"
    assert back_in.events[0]["event_type"] == "staff_clocked_in"
    assert staff["clocked_in"] is True


def test_clock_staff_unknown_staff_rejected():
    sim, clock = _cafe_in_service()
    result = sim.apply_action(
        ActionRequest(
            action_id="out_x",
            action_type="clock_staff_out",
            actor="manager_agent",
            params={"staff_id": "staff_9999"},
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == "unknown_staff:staff_9999"


def test_unknown_action_rejected():
    sim, clock = _cafe_in_service()
    result = sim.apply_action(
        ActionRequest(
            action_id="x1", action_type="fire_everyone", actor="manager_agent"
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == "unknown_action"


@pytest.mark.parametrize(
    "params,reason",
    [
        ({"ingredient_id": "ing_001"}, "missing_param:qty"),
        (
            {"ingredient_id": "ing_001", "qty": "lots"},
            "invalid_param_type:qty",
        ),
        ({"ingredient_id": "ing_001", "qty": 0}, "param_out_of_range:qty"),
        (
            {"ingredient_id": "ing_001", "qty": 20000},
            "param_out_of_range:qty",
        ),
        (
            {"ingredient_id": "ing_001", "qty": 5, "surprise": 1},
            "unknown_param:surprise",
        ),
    ],
)
def test_param_validation_rejections(params, reason):
    sim, clock = _cafe_in_service()
    result = sim.apply_action(
        ActionRequest(
            action_id="p1",
            action_type="place_supplier_order",
            actor="manager_agent",
            params=params,
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == reason


def test_price_out_of_range_rejected():
    sim, clock = _cafe_in_service()
    result = sim.apply_action(
        ActionRequest(
            action_id="price_3",
            action_type="adjust_price",
            actor="manager_agent",
            params={"item_id": "item_0001", "new_price": 0.10},
            approved_by="yakov",
        ),
        clock,
    )
    assert result.status == "rejected"
    assert result.reason == "param_out_of_range:new_price"


def test_duplicate_action_id_is_idempotent():
    sim, clock = _cafe_in_service()
    before = sim.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    first = sim.apply_action(_order(), clock)
    second = sim.apply_action(_order(), clock)
    assert first.status == "applied"
    assert second.status == "already_applied"
    assert second.reason == "duplicate_action_id"
    assert second.events == []
    after = sim.position_snapshot()["position"]["stock"]["ing_001"]["qty"]
    assert after == pytest.approx(before + 7.5)


# ----------------------------------------------------------------------
# Batch Runner integration
# ----------------------------------------------------------------------


class _ListAdapter(OutputAdapter):
    def __init__(self) -> None:
        self.events: list[Any] = []

    def write(self, envelope) -> None:
        self.events.append(envelope)


def _batch(actions=None, max_events=30, start=START, **config_kwargs):
    config_kwargs.setdefault("period_summaries_enabled", False)
    config = SimulatorConfig(
        business_type="cafe",
        duration_seconds=3600,
        events_per_second=1.0,
        seed=42,
        max_events=max_events,
        **config_kwargs,
    )
    sim = load_simulator("cafe")
    sim.configure(config)
    sim.initialize(42)
    adapter = _ListAdapter()
    runner = Runner(config, sim, adapter, realtime=False, actions=actions)
    runner.clock = Clock(start_time=start, tick_seconds=1.0)
    runner.run()
    return sim, adapter.events


def _stream(events):
    return [(e.event_type, e.payload) for e in events]


def test_batch_scripted_action_effect_then_audit():
    _, events = _batch(actions=[(5, _order())], max_events=30)
    effects = [
        e
        for e in events
        if e.event_type == "supplier_delivery_received"
        and e.payload["items"][0]["qty"] == 7.5
    ]
    assert len(effects) == 1
    # Exactly max_events *generated* business events: the stream holds
    # those plus the action's effect event and its audit event.
    generated = [
        e
        for e in events
        if e.event_type not in ("action_applied", "action_rejected")
        and e is not effects[0]
    ]
    assert len(generated) == 30
    idx = events.index(effects[0])
    audit = events[idx + 1]
    assert audit.event_type == "action_applied"
    assert audit.payload["action_id"] == "act_1"
    assert audit.payload["actor"] == "manager_agent"
    assert audit.payload["effect_event_types"] == ["supplier_delivery_received"]


def test_batch_rejected_action_emits_audit_only():
    _, events = _batch(
        actions=[(5, _order(action_id="bad_1", ingredient_id="ing_999"))],
        max_events=30,
    )
    rejected = [e for e in events if e.event_type == "action_rejected"]
    assert len(rejected) == 1
    assert rejected[0].payload["reason"] == "unknown_ingredient:ing_999"
    assert rejected[0].payload["status"] == "rejected"
    assert not any(e.event_type == "action_applied" for e in events)


def test_rejected_actions_do_not_perturb_business_stream():
    _, plain = _batch(max_events=40)
    _, with_rejections = _batch(
        actions=[
            (10, _order(action_id="bad_1", ingredient_id="ing_999")),
            (
                20,
                ActionRequest(
                    action_id="bad_2",
                    action_type="adjust_price",
                    actor="manager_agent",
                    params={"item_id": "item_0001", "new_price": 9.0},
                ),
            ),
        ],
        max_events=40,
    )
    business = [
        pair
        for pair in _stream(with_rejections)
        if pair[0] != "action_rejected"
    ]
    assert business == _stream(plain)
    reasons = [
        e.payload["reason"]
        for e in with_rejections
        if e.event_type == "action_rejected"
    ]
    assert reasons == ["unknown_ingredient:ing_999", "requires_approval"]


def test_determinism_with_actions():
    script = [
        (5, _order()),
        (
            12,
            ActionRequest(
                action_id="price_1",
                action_type="adjust_price",
                actor="manager_agent",
                params={"item_id": "item_0001", "new_price": 6.25},
                approved_by="yakov",
            ),
        ),
    ]
    _, run_a = _batch(actions=script, max_events=40)
    _, run_b = _batch(actions=list(script), max_events=40)
    assert _stream(run_a) == _stream(run_b)
    assert any(e.event_type == "menu_price_adjusted" for e in run_a)


def test_empty_action_list_matches_no_actions():
    _, plain = _batch(max_events=30)
    _, empty = _batch(actions=[], max_events=30)
    assert _stream(plain) == _stream(empty)


# ----------------------------------------------------------------------
# ContinuousRunner integration
# ----------------------------------------------------------------------


def _continuous(actions=None, max_events=25, submit=None, **config_kwargs):
    storage = MemoryStorageAdapter("memory://", "actions_stream")
    config = SimulatorConfig(
        business_type="cafe",
        duration_seconds=3600,
        events_per_second=1000.0,
        seed=42,
        max_events=max_events,
        stream_id=storage.stream_id,
        storage_backend="memory",
        checkpoint_interval_events=1000,
        checkpoint_interval_seconds=3600.0,
        period_summaries_enabled=False,
        **config_kwargs,
    )
    sim = load_simulator("cafe")
    sim.configure(config)
    sim.initialize(42)
    runner = ContinuousRunner(
        config, sim, storage, realtime=False, actions=actions
    )
    for request in submit or []:
        runner.submit_action(request)
    runner.run()
    return sim, list(storage.read_from())


def test_continuous_scripted_action_sequences():
    _, records = _continuous(actions=[(3, _order())], max_events=25)
    assert [r.sequence for r in records] == list(range(len(records)))
    effects = [
        r
        for r in records
        if r.envelope.event_type == "supplier_delivery_received"
        and r.envelope.payload["items"][0]["qty"] == 7.5
    ]
    assert len(effects) == 1
    assert effects[0].sequence == 3
    audit = records[4]
    assert audit.envelope.event_type == "action_applied"
    assert audit.sequence == 4
    # Exactly max_events *generated* business events, plus effect+audit.
    generated = [
        r
        for r in records
        if r.envelope.event_type
        not in ("action_applied", "action_rejected")
        and r is not effects[0]
    ]
    assert len(generated) == 25


def test_continuous_submit_action_queue():
    _, records = _continuous(submit=[_order()], max_events=20)
    assert records[0].envelope.event_type == "supplier_delivery_received"
    assert records[1].envelope.event_type == "action_applied"
    assert records[2].envelope.event_type == "shift_opened"


def test_continuous_duplicate_action_audited_once():
    _, records = _continuous(
        actions=[(2, _order()), (4, _order())], max_events=25
    )
    effects = [
        r
        for r in records
        if r.envelope.event_type == "supplier_delivery_received"
        and r.envelope.payload["items"][0]["qty"] == 7.5
    ]
    assert len(effects) == 1
    rejected = [
        r for r in records if r.envelope.event_type == "action_rejected"
    ]
    assert len(rejected) == 1
    assert rejected[0].envelope.payload["reason"] == "duplicate_action_id"
    assert rejected[0].envelope.payload["status"] == "already_applied"


# ----------------------------------------------------------------------
# KPI tracker interaction
# ----------------------------------------------------------------------


def test_effect_events_counted_in_kpis_but_audit_and_new_types_not():
    def summaries(events):
        return [e for e in events if e.event_type == "period_summary"]

    _, plain = _batch(
        max_events=90, start=NEAR_MIDNIGHT, period_summaries_enabled=True
    )
    _, acted = _batch(
        actions=[
            (5, _order()),
            (
                6,
                ActionRequest(
                    action_id="price_1",
                    action_type="adjust_price",
                    actor="manager_agent",
                    params={"item_id": "item_0001", "new_price": 6.25},
                    approved_by="yakov",
                ),
            ),
        ],
        max_events=90,
        start=NEAR_MIDNIGHT,
        period_summaries_enabled=True,
    )
    plain_counts = summaries(plain)[0].payload["event_counts"]
    acted_counts = summaries(acted)[0].payload["event_counts"]
    # The action's supplier delivery is a real business event: +1.
    assert (
        acted_counts["supplier_delivery_received"]
        == plain_counts["supplier_delivery_received"] + 1
    )
    # Audit events and the new action-only type are not business events.
    assert "action_applied" not in acted_counts
    assert "menu_price_adjusted" not in acted_counts
