"""Tests for the weighted event catalog."""

import random

from eden_business_simulator.framework.catalog import WeightedEventCatalog


def _make_catalog(seed: int = 42):
    rng = random.Random(seed)
    return WeightedEventCatalog(rng)


def test_catalog_basic_weighted_choice():
    cat = _make_catalog()
    cat.register("a", 10.0)
    cat.register("b", 1.0)

    results = [cat.choose(hour=0, context=None) for _ in range(100)]
    assert "a" in results
    assert "b" in results
    assert results.count("a") > results.count("b")


def test_catalog_guard_filters_events():
    cat = _make_catalog()
    context = {"allow_b": False}
    cat.register("a", 10.0)
    cat.register(
        "b",
        10.0,
        guard=lambda ctx: ctx["allow_b"],
    )

    for _ in range(50):
        assert cat.choose(hour=0, context=context) == "a"

    context["allow_b"] = True
    results = [cat.choose(hour=0, context=context) for _ in range(100)]
    assert "b" in results


def test_catalog_time_modifier():
    cat = _make_catalog()
    cat.register("a", 1.0, time_modifier=lambda hour: 100.0 if hour == 12 else 0.1)
    cat.register("b", 1.0)

    results_noon = [cat.choose(hour=12, context=None) for _ in range(200)]
    assert results_noon.count("a") > results_noon.count("b") * 40

    results_other = [cat.choose(hour=11, context=None) for _ in range(200)]
    assert results_other.count("b") > results_other.count("a")


def test_catalog_eligible_types():
    cat = _make_catalog()
    cat.register("a", 5.0)
    cat.register("b", 0.0)
    cat.register("c", 5.0, guard=lambda ctx: False)

    eligible = cat.eligible_types(hour=0, context=None)
    assert eligible == {"a": 5.0}


def test_catalog_negative_weight_raises():
    cat = _make_catalog()
    try:
        cat.register("a", -1.0)
        assert False, "expected ValueError"
    except ValueError:
        pass


def test_catalog_no_eligible_returns_none():
    cat = _make_catalog()
    cat.register("a", 1.0, guard=lambda ctx: False)
    assert cat.choose(hour=0, context=None) is None
