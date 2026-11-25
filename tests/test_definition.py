"""Tests for the Phase 0 business-definition system."""

import random

import pytest

from eden_business_simulator.framework.definition import (
    BusinessDefinition,
    build_weighted_catalog,
    load_definition,
)

ALL_TYPES = [
    "cafe", "clinic", "ecommerce", "electrician", "field_service",
    "gym", "logistics", "plumber", "saas",
]


def _minimal(**overrides):
    data = {
        "business_type": "demo",
        "events": [{"event_type": "thing_happened", "base_weight": 1.0}],
    }
    data.update(overrides)
    return data


def test_negative_weight_fails():
    with pytest.raises(ValueError):
        BusinessDefinition.model_validate(
            _minimal(events=[{"event_type": "x", "base_weight": -1.0}])
        )


def test_empty_events_fail():
    with pytest.raises(ValueError):
        BusinessDefinition.model_validate(_minimal(events=[]))


def test_duplicate_events_fail():
    with pytest.raises(ValueError):
        BusinessDefinition.model_validate(
            _minimal(events=[
                {"event_type": "x", "base_weight": 1.0},
                {"event_type": "x", "base_weight": 2.0},
            ])
        )


def test_daypart_unknown_event_fails():
    with pytest.raises(ValueError):
        BusinessDefinition.model_validate(
            _minimal(dayparts=[{
                "name": "peak", "start_hour": 8, "end_hour": 10,
                "weight_modifiers": {"nope": 2.0},
            }])
        )


def test_unknown_guard_fails_at_catalog_build():
    definition = BusinessDefinition.model_validate(
        _minimal(events=[{
            "event_type": "thing_happened", "base_weight": 1.0,
            "guard": "no_such_guard",
        }])
    )
    with pytest.raises(ValueError, match="unknown guard"):
        build_weighted_catalog(definition, random.Random(1), guards={})


def test_known_guard_builds_catalog():
    definition = BusinessDefinition.model_validate(
        _minimal(events=[{
            "event_type": "thing_happened", "base_weight": 1.0,
            "guard": "always",
        }])
    )
    catalog = build_weighted_catalog(
        definition, random.Random(1), guards={"always": lambda ctx: True}
    )
    assert catalog.choose(hour=10, context=None) == "thing_happened"


def test_hash_is_stable_and_sensitive():
    a = BusinessDefinition.model_validate(_minimal())
    b = BusinessDefinition.model_validate(_minimal())
    assert a.definition_hash() == b.definition_hash()
    changed = BusinessDefinition.model_validate(
        _minimal(events=[{"event_type": "thing_happened", "base_weight": 2.0}])
    )
    assert changed.definition_hash() != a.definition_hash()


def test_validate_overrides_unknown_key_and_type():
    definition = BusinessDefinition.model_validate(
        _minimal(initial_state={
            "defaults": {"initial_members": 5},
            "types": {"initial_members": "int"},
        })
    )
    assert definition.validate_overrides({"initial_members": 9}) == {"initial_members": 9}
    assert definition.validate_overrides({}) == {"initial_members": 5}
    with pytest.raises(ValueError, match="unknown initial_state_overrides"):
        definition.validate_overrides({"bogus": 1})
    with pytest.raises(ValueError, match="must be int"):
        definition.validate_overrides({"initial_members": "nine"})


@pytest.mark.parametrize("business_type", ALL_TYPES)
def test_all_definitions_load(business_type):
    definition = load_definition(business_type)
    assert definition.business_type == business_type
    assert definition.schema_version == 1
    assert len(definition.definition_hash()) == 64


def test_configure_validates_overrides_and_records_hash():
    from eden_business_simulator.businesses import load_simulator
    from eden_business_simulator.config import SimulatorConfig

    sim = load_simulator("cafe")
    sim.configure(
        SimulatorConfig(
            business_type="cafe", initial_state_overrides={"initial_members": 2}
        )
    )
    assert sim.definition_hash == load_definition("cafe").definition_hash()

    bad = load_simulator("cafe")
    with pytest.raises(ValueError, match="unknown initial_state_overrides"):
        bad.configure(
            SimulatorConfig(
                business_type="cafe", initial_state_overrides={"bogus_key": 1}
            )
        )


@pytest.mark.parametrize("business_type", ALL_TYPES)
def test_definition_events_match_available_event_types(business_type):
    from eden_business_simulator.businesses import load_simulator

    definition = load_definition(business_type)
    assert definition.event_types() == load_simulator(business_type).available_event_types()
