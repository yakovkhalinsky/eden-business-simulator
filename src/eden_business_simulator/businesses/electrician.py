"""Electrician field-service simulator.

Models dispatched electrical work: residential, commercial, industrial, and
emergency calls. Uses the same lifecycle as the generic field-service simulator
but focuses on electrical categories and parts.
"""

from __future__ import annotations

from eden_business_simulator.businesses.field_service import FieldServiceSimulator


class ElectricianSimulator(FieldServiceSimulator):
    """Simulates dispatched electrician field-service operations."""

    business_type = "electrician"

    _CATEGORIES = ("residential", "commercial", "industrial", "emergency")
    _PARTS = (
        ("prt_e001", "circuit_breaker_20a", 12.0),
        ("prt_e002", "wire_12awg_25ft", 18.5),
        ("prt_e003", "outlet_gfci", 15.0),
        ("prt_e004", "led_panel_2x4", 45.0),
    )
