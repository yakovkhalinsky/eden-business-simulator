"""Tests for the reusable transition-model state machine."""

from eden_business_simulator.framework.state_machine import TransitionModel


def test_register_and_get():
    machine = TransitionModel({"new": ("active",), "active": ("closed",)})
    machine.register("ord_0001", "new")
    assert machine.get("ord_0001") == "new"


def test_allowed_transition():
    machine = TransitionModel({"new": ("active",), "active": ("closed",)})
    machine.register("ord_0001", "new")
    assert machine.transition("ord_0001", "active")
    assert machine.get("ord_0001") == "active"


def test_disallowed_transition():
    machine = TransitionModel({"new": ("active",), "active": ("closed",)})
    machine.register("ord_0001", "new")
    assert not machine.transition("ord_0001", "closed")
    assert machine.get("ord_0001") == "new"


def test_entities_in_state():
    machine = TransitionModel({"new": ("paid",), "paid": ()})
    for i in range(3):
        machine.register(f"ord_{i:04d}", "new")
    machine.transition("ord_0001", "paid")
    assert machine.entities_in_state("new") == ["ord_0000", "ord_0002"]


def test_unknown_entity_cannot_transition():
    machine = TransitionModel({"new": ("active",)})
    assert not machine.can_transition("missing", "active")
    assert not machine.transition("missing", "active")
