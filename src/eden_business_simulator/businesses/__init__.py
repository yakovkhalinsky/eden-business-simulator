"""Business simulator registry."""

from __future__ import annotations

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.businesses.cafe import CafeSimulator
from eden_business_simulator.businesses.clinic import ClinicSimulator
from eden_business_simulator.businesses.ecommerce import EcommerceSimulator
from eden_business_simulator.businesses.field_service import FieldServiceSimulator
from eden_business_simulator.businesses.gym import GymSimulator
from eden_business_simulator.businesses.logistics import LogisticsSimulator
from eden_business_simulator.businesses.saas import SaaSSimulator

_REGISTRY: dict[str, type[BusinessSimulator]] = {
    CafeSimulator.business_type: CafeSimulator,
    ClinicSimulator.business_type: ClinicSimulator,
    EcommerceSimulator.business_type: EcommerceSimulator,
    FieldServiceSimulator.business_type: FieldServiceSimulator,
    GymSimulator.business_type: GymSimulator,
    LogisticsSimulator.business_type: LogisticsSimulator,
    SaaSSimulator.business_type: SaaSSimulator,
}


def list_business_types() -> list[str]:
    return sorted(_REGISTRY.keys())


def load_simulator(business_type: str) -> BusinessSimulator:
    """Instantiate the simulator for a registered business type."""
    try:
        simulator_class = _REGISTRY[business_type]
    except KeyError as exc:
        available = ", ".join(list_business_types())
        raise ValueError(
            f"Unknown business_type '{business_type}'. Available: {available}"
        ) from exc
    return simulator_class()
