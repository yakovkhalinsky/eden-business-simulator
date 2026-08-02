"""Tests for SimulatorConfig."""

import pytest

from eden_business_simulator.config import SimulatorConfig


def test_default_config():
    cfg = SimulatorConfig(business_type="ecommerce")
    assert cfg.duration_seconds == 60.0
    assert cfg.events_per_second == 2.0
    assert cfg.output_mode == "ndjson"
    assert cfg.seed == 42


def test_http_requires_webhook():
    with pytest.raises(ValueError):
        SimulatorConfig(business_type="ecommerce", output_mode="http")


def test_negative_duration_rejected():
    with pytest.raises(ValueError):
        SimulatorConfig(business_type="ecommerce", duration_seconds=-1)


def test_zero_rate_rejected():
    with pytest.raises(ValueError):
        SimulatorConfig(business_type="ecommerce", events_per_second=0)


def test_state_overrides_preserved():
    cfg = SimulatorConfig(
        business_type="ecommerce",
        initial_state_overrides={"initial_customers": 10},
    )
    assert cfg.initial_state_overrides["initial_customers"] == 10
