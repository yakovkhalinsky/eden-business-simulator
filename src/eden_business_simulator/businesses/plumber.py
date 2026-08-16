"""Plumber field-service simulator.

Models dispatched plumbing work: leak repairs, drain cleaning, pipe installs,
and emergency calls. Uses the same lifecycle as the generic field-service
simulator but focuses on plumbing categories and parts.
"""

from __future__ import annotations

from eden_business_simulator.businesses.field_service import FieldServiceSimulator


class PlumberSimulator(FieldServiceSimulator):
    """Simulates dispatched plumber field-service operations."""

    business_type = "plumber"

    _CATEGORIES = ("leak_repair", "drain_cleaning", "pipe_install", "emergency")
    _PARTS = (
        ("prt_p001", "pvc_pipe_10ft", 8.0),
        ("prt_p002", "faucet_cartridge", 22.0),
        ("prt_p003", "p_trap_kit", 12.5),
        ("prt_p004", "water_heater_element", 35.0),
    )
