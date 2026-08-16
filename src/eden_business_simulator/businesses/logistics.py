"""Last-mile delivery / logistics simulator."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    BusinessSimulator,
    DEFAULT_TAX_TYPE,
    compute_tax,
)
from eden_business_simulator.framework.actors import ActorPool
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.scheduler import Daypart, DaypartScheduler
from eden_business_simulator.models import Clock


class LogisticsSimulator(BusinessSimulator):
    """Simulates a last-mile delivery operation."""

    business_type = "logistics"

    _SERVICE_LEVELS = ("same_day", "next_day", "standard")
    _EXCEPTION_REASONS = (
        "customer_not_home",
        "address_not_found",
        "access_restricted",
        "package_damaged",
        "refused_by_customer",
    )

    def __init__(self) -> None:
        self.config = None
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.drivers = ActorPool(
            self.id_gen,
            "driver",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.vehicles: list[dict[str, Any]] = []
        self.shipments: list[dict[str, Any]] = []
        self.routes: list[dict[str, Any]] = []
        self.stops: list[dict[str, Any]] = []
        self.attempts: list[dict[str, Any]] = []
        self.feedback: list[dict[str, Any]] = []
        self.returns: list[dict[str, Any]] = []
        self.catalog = WeightedEventCatalog(self.rng)
        self.hub_id = "hub_east"

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)

        self.id_gen = IdGenerator(self.rng)
        self.drivers = ActorPool(
            self.id_gen,
            "driver",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.vehicles = []
        self.shipments = []
        self.routes = []
        self.stops = []
        self.attempts = []
        self.feedback = []
        self.returns = []
        self.catalog = WeightedEventCatalog(self.rng, self._build_schedule())

        initial_drivers = self.config.initial_state_overrides.get("initial_drivers", 4)
        for _ in range(initial_drivers):
            self.drivers.create()

        initial_vehicles = self.config.initial_state_overrides.get(
            "initial_vehicles", 3
        )
        for _ in range(initial_vehicles):
            self.vehicles.append(self._create_vehicle())

        initial_shipments = self.config.initial_state_overrides.get(
            "initial_shipments", 8
        )
        for _ in range(initial_shipments):
            self.shipments.append(self._create_shipment())

        self._build_catalog()

    def available_event_types(self) -> list[str]:
        return [
            "shipment_created",
            "route_planned",
            "driver_assigned",
            "vehicle_departed",
            "stop_arrived",
            "delivery_attempted",
            "delivery_delivered",
            "proof_of_delivery_captured",
            "delivery_exception_recorded",
            "customer_feedback_received",
            "return_initiated",
            "vehicle_location_update",
            "route_completed",
            "fuel_stop_logged",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "vehicle_location_update"
        return self._emit(event_type, clock)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "driver_count": len(self.drivers.all()),
            "vehicle_count": len(self.vehicles),
            "shipment_count": len(self.shipments),
            "route_count": len(self.routes),
            "stop_count": len(self.stops),
            "attempt_count": len(self.attempts),
            "feedback_count": len(self.feedback),
            "return_count": len(self.returns),
            "shipments_by_status": self._shipments_by_status(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_schedule(self) -> DaypartScheduler:
        return DaypartScheduler(
            dayparts=[
                Daypart(
                    name="early",
                    start_hour=4,
                    end_hour=8,
                    weight_modifiers={
                        "shipment_created": 1.5,
                        "route_planned": 2.0,
                        "driver_assigned": 2.0,
                        "vehicle_departed": 1.5,
                    },
                    default_modifier=0.5,
                ),
                Daypart(
                    name="morning",
                    start_hour=8,
                    end_hour=12,
                    weight_modifiers={
                        "vehicle_departed": 3.0,
                        "stop_arrived": 3.0,
                        "delivery_attempted": 3.0,
                        "vehicle_location_update": 2.0,
                    },
                    default_modifier=1.0,
                ),
                Daypart(
                    name="afternoon",
                    start_hour=12,
                    end_hour=17,
                    weight_modifiers={
                        "stop_arrived": 3.0,
                        "delivery_attempted": 3.0,
                        "proof_of_delivery_captured": 2.5,
                        "vehicle_location_update": 1.5,
                    },
                    default_modifier=1.0,
                ),
                Daypart(
                    name="evening",
                    start_hour=17,
                    end_hour=21,
                    weight_modifiers={
                        "route_completed": 3.0,
                        "customer_feedback_received": 2.0,
                        "fuel_stop_logged": 2.0,
                    },
                    default_modifier=0.6,
                ),
                Daypart(
                    name="night",
                    start_hour=21,
                    end_hour=4,
                    weight_modifiers={
                        "return_initiated": 2.0,
                        "shipment_created": 1.0,
                    },
                    default_modifier=0.2,
                ),
            ]
        )

    def _build_catalog(self) -> None:
        def has_shipments(ctx: "LogisticsSimulator") -> bool:
            return len(ctx.shipments) > 0

        def has_unrouted(ctx: "LogisticsSimulator") -> bool:
            return any(s["status"] == "created" for s in ctx.shipments)

        def has_drivers(ctx: "LogisticsSimulator") -> bool:
            return len(ctx.drivers.all()) > 0 and len(ctx.vehicles) > 0

        def has_routes(ctx: "LogisticsSimulator") -> bool:
            return any(r["status"] == "planned" for r in ctx.routes)

        def has_active_routes(ctx: "LogisticsSimulator") -> bool:
            return any(r["status"] == "started" for r in ctx.routes)

        def has_unattempted_stops(ctx: "LogisticsSimulator") -> bool:
            return any(s["status"] == "arrived" for s in ctx.stops)

        def has_delivered(ctx: "LogisticsSimulator") -> bool:
            return any(
                a["outcome"] == "delivered" for a in ctx.attempts
            )

        def has_failed_attempts(ctx: "LogisticsSimulator") -> bool:
            return any(
                a["outcome"] != "delivered" for a in ctx.attempts
            )

        self.catalog.register("shipment_created", base_weight=6.0)
        self.catalog.register(
            "route_planned",
            base_weight=4.0,
            guard=lambda ctx: has_drivers(ctx) and has_unrouted(ctx),
        )
        self.catalog.register(
            "driver_assigned",
            base_weight=4.0,
            guard=has_routes,
        )
        self.catalog.register(
            "vehicle_departed",
            base_weight=4.0,
            guard=has_routes,
        )
        self.catalog.register(
            "stop_arrived",
            base_weight=8.0,
            guard=has_active_routes,
        )
        self.catalog.register(
            "delivery_attempted",
            base_weight=8.0,
            guard=has_unattempted_stops,
        )
        self.catalog.register(
            "delivery_delivered",
            base_weight=6.0,
            guard=has_delivered,
        )
        self.catalog.register(
            "proof_of_delivery_captured",
            base_weight=5.0,
            guard=has_delivered,
        )
        self.catalog.register(
            "delivery_exception_recorded",
            base_weight=3.0,
            guard=has_failed_attempts,
        )
        self.catalog.register(
            "customer_feedback_received",
            base_weight=3.0,
            guard=has_delivered,
        )
        self.catalog.register(
            "return_initiated",
            base_weight=2.0,
            guard=has_delivered,
        )
        self.catalog.register(
            "vehicle_location_update",
            base_weight=5.0,
            guard=has_active_routes,
        )
        self.catalog.register(
            "route_completed",
            base_weight=3.0,
            guard=has_active_routes,
        )
        self.catalog.register(
            "fuel_stop_logged",
            base_weight=1.5,
            guard=has_active_routes,
        )

    def _create_vehicle(self) -> dict[str, Any]:
        vehicle_id = self.id_gen.next("vh")
        return {
            "vehicle_id": vehicle_id,
            "type": self.rng.choice(["van", "truck", "ute"]),
            "capacity_kg": round(self.rng.uniform(500.0, 2500.0), 1),
            "odometer_km": self.rng.randint(20000, 120000),
        }

    def _create_shipment(self) -> dict[str, Any]:
        shipment_id = self.id_gen.next("shp")
        lat = round(self.rng.uniform(-38.0, -33.0), 4)
        lon = round(self.rng.uniform(140.0, 153.0), 4)
        return {
            "shipment_id": shipment_id,
            "order_id": self.id_gen.next("ord"),
            "destination": {"lat": lat, "lon": lon},
            "service_level": self.rng.choice(self._SERVICE_LEVELS),
            "weight_kg": round(self.rng.uniform(0.5, 15.0), 2),
            "status": "created",
        }

    def _shipments_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for shipment in self.shipments:
            counts[shipment["status"]] = counts.get(shipment["status"], 0) + 1
        return counts

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _emit_shipment_created(self, clock: Clock) -> dict[str, Any]:
        shipment = self._create_shipment()
        return {
            "event_type": "shipment_created",
            "payload": {
                "shipment_id": shipment["shipment_id"],
                "order_id": shipment["order_id"],
                "destination": shipment["destination"],
                "service_level": shipment["service_level"],
                "weight_kg": shipment["weight_kg"],
                "hub_id": self.hub_id,
                "created_at": clock.now.isoformat(),
            },
        }

    def _emit_route_planned(self, clock: Clock) -> dict[str, Any]:
        unrouted = [s for s in self.shipments if s["status"] == "created"]
        if not self.drivers.all() or not self.vehicles or not unrouted:
            return self._emit_shipment_created(clock)
        stop_count = min(len(unrouted), self.rng.randint(3, 8))
        selected = self.rng.sample(unrouted, k=stop_count)
        driver = self.rng.choice(self.drivers.all())
        vehicle = self.rng.choice(self.vehicles)
        route_id = self.id_gen.next("rte")
        stops: list[dict[str, Any]] = []
        for idx, shipment in enumerate(selected):
            stop_id = self.id_gen.next("stp")
            eta = clock.now + timedelta(minutes=15 * (idx + 1))
            stop = {
                "stop_id": stop_id,
                "shipment_id": shipment["shipment_id"],
                "sequence": idx + 1,
                "eta": eta,
                "status": "planned",
            }
            self.stops.append(stop)
            stops.append({"stop_id": stop_id, "shipment_id": shipment["shipment_id"], "sequence": idx + 1, "eta": eta.isoformat()})
        route = {
            "route_id": route_id,
            "driver_id": driver.actor_id,
            "vehicle_id": vehicle["vehicle_id"],
            "stops": [s["stop_id"] for s in self.stops[-stop_count:]],
            "status": "planned",
            "planned_miles": round(self.rng.uniform(20.0, 80.0), 1),
        }
        self.routes.append(route)
        return {
            "event_type": "route_planned",
            "payload": {
                "route_id": route_id,
                "driver_id": driver.actor_id,
                "vehicle_id": vehicle["vehicle_id"],
                "hub_id": self.hub_id,
                "stops": stops,
                "planned_miles": route["planned_miles"],
                "planned_at": clock.now.isoformat(),
            },
        }

    def _emit_driver_assigned(self, clock: Clock) -> dict[str, Any]:
        planned = [r for r in self.routes if r["status"] == "planned"]
        if not planned:
            return self._emit_route_planned(clock)
        route = self.rng.choice(planned)
        return {
            "event_type": "driver_assigned",
            "payload": {
                "route_id": route["route_id"],
                "driver_id": route["driver_id"],
                "vehicle_id": route["vehicle_id"],
                "assigned_at": clock.now.isoformat(),
                "driver_app_version": "3.2.1",
            },
        }

    def _emit_vehicle_departed(self, clock: Clock) -> dict[str, Any]:
        planned = [r for r in self.routes if r["status"] == "planned"]
        if not planned:
            return self._emit_route_planned(clock)
        route = self.rng.choice(planned)
        route["status"] = "started"
        route["started_at"] = clock.now
        vehicle = next(
            v for v in self.vehicles if v["vehicle_id"] == route["vehicle_id"]
        )
        return {
            "event_type": "vehicle_departed",
            "payload": {
                "route_id": route["route_id"],
                "vehicle_id": route["vehicle_id"],
                "departed_at": clock.now.isoformat(),
                "initial_odometer_km": vehicle["odometer_km"],
            },
        }

    def _emit_stop_arrived(self, clock: Clock) -> dict[str, Any]:
        active = [
            r
            for r in self.routes
            if r["status"] == "started" and any(
                s["status"] in ("planned", "arrived")
                for s in self.stops
                if s["stop_id"] in r["stops"]
            )
        ]
        if not active:
            return self._emit_vehicle_departed(clock)
        route = self.rng.choice(active)
        candidate_stops = [
            s
            for s in self.stops
            if s["stop_id"] in route["stops"] and s["status"] in ("planned", "arrived")
        ]
        if not candidate_stops:
            return self._emit_vehicle_departed(clock)
        stop = self.rng.choice(candidate_stops)
        stop["status"] = "arrived"
        stop["arrived_at"] = clock.now
        shipment = next(
            s for s in self.shipments if s["shipment_id"] == stop["shipment_id"]
        )
        return {
            "event_type": "stop_arrived",
            "payload": {
                "stop_id": stop["stop_id"],
                "route_id": route["route_id"],
                "shipment_id": shipment["shipment_id"],
                "arrived_at": clock.now.isoformat(),
                "location": shipment["destination"],
            },
        }

    def _emit_delivery_attempted(self, clock: Clock) -> dict[str, Any]:
        arrived = [s for s in self.stops if s["status"] == "arrived"]
        if not arrived:
            return self._emit_stop_arrived(clock)
        stop = self.rng.choice(arrived)
        shipment = next(
            s for s in self.shipments if s["shipment_id"] == stop["shipment_id"]
        )
        outcome = self.rng.choices(
            ["delivered", "delivered", "delivered", "failed"],
            weights=[70, 0, 0, 30],
            k=1,
        )[0]
        if outcome == "failed":
            outcome = self.rng.choice(
                ["customer_not_home", "address_not_found", "refused_by_customer"]
            )
        attempt_id = self.id_gen.next("att")
        attempt = {
            "attempt_id": attempt_id,
            "stop_id": stop["stop_id"],
            "shipment_id": shipment["shipment_id"],
            "outcome": outcome,
            "attempted_at": clock.now,
        }
        self.attempts.append(attempt)
        if outcome == "delivered":
            shipment["status"] = "delivered"
            stop["status"] = "delivered"
        else:
            shipment["status"] = "exception"
            stop["status"] = "exception"
        return {
            "event_type": "delivery_attempted",
            "payload": {
                "attempt_id": attempt_id,
                "stop_id": stop["stop_id"],
                "shipment_id": shipment["shipment_id"],
                "outcome": outcome,
                "attempted_at": clock.now.isoformat(),
            },
        }

    def _emit_delivery_delivered(self, clock: Clock) -> dict[str, Any]:
        delivered = [a for a in self.attempts if a["outcome"] == "delivered"]
        if not delivered:
            return self._emit_delivery_attempted(clock)
        attempt = self.rng.choice(delivered)
        return {
            "event_type": "delivery_delivered",
            "payload": {
                "delivery_id": self.id_gen.next("dlv"),
                "shipment_id": attempt["shipment_id"],
                "attempt_id": attempt["attempt_id"],
                "stop_id": attempt["stop_id"],
                "delivered_at": clock.now.isoformat(),
                "recipient_name": self.faker.name(),
            },
        }

    def _emit_proof_of_delivery_captured(self, clock: Clock) -> dict[str, Any]:
        delivered = [a for a in self.attempts if a["outcome"] == "delivered"]
        if not delivered:
            return self._emit_delivery_attempted(clock)
        attempt = self.rng.choice(delivered)
        return {
            "event_type": "proof_of_delivery_captured",
            "payload": {
                "pod_id": self.id_gen.next("pod"),
                "shipment_id": attempt["shipment_id"],
                "attempt_id": attempt["attempt_id"],
                "recipient_name": self.faker.name(),
                "signature_type": self.rng.choice(["signature", "photo", "pin"]),
                "captured_at": clock.now.isoformat(),
            },
        }

    def _emit_delivery_exception_recorded(self, clock: Clock) -> dict[str, Any]:
        failed = [a for a in self.attempts if a["outcome"] != "delivered"]
        if not failed:
            return self._emit_delivery_attempted(clock)
        attempt = self.rng.choice(failed)
        resolution = self.rng.choice(
            ["reschedule_next_day", "leave_with_neighbor", "return_to_hub", "contact_customer"]
        )
        return {
            "event_type": "delivery_exception_recorded",
            "payload": {
                "exception_id": self.id_gen.next("exc"),
                "shipment_id": attempt["shipment_id"],
                "attempt_id": attempt["attempt_id"],
                "reason": attempt["outcome"],
                "resolution": resolution,
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_customer_feedback_received(self, clock: Clock) -> dict[str, Any]:
        delivered = [a for a in self.attempts if a["outcome"] == "delivered"]
        if not delivered:
            return self._emit_delivery_attempted(clock)
        attempt = self.rng.choice(delivered)
        return {
            "event_type": "customer_feedback_received",
            "payload": {
                "feedback_id": self.id_gen.next("fb"),
                "shipment_id": attempt["shipment_id"],
                "rating": self.rng.randint(1, 5),
                "comment": self.rng.choice(
                    ["Fast delivery", "Driver friendly", "Package damaged", "Late"]
                ),
                "received_at": clock.now.isoformat(),
            },
        }

    def _emit_return_initiated(self, clock: Clock) -> dict[str, Any]:
        delivered = [a for a in self.attempts if a["outcome"] == "delivered"]
        if not delivered:
            return self._emit_delivery_attempted(clock)
        attempt = self.rng.choice(delivered)
        return_id = self.id_gen.next("ret")
        ret = {
            "return_id": return_id,
            "shipment_id": attempt["shipment_id"],
            "reason": self.rng.choice(["wrong_item", "damaged", "not_needed"]),
        }
        self.returns.append(ret)
        return {
            "event_type": "return_initiated",
            "payload": {
                "return_id": return_id,
                "shipment_id": attempt["shipment_id"],
                "reason": ret["reason"],
                "initiated_at": clock.now.isoformat(),
            },
        }

    def _emit_vehicle_location_update(self, clock: Clock) -> dict[str, Any]:
        active = [r for r in self.routes if r["status"] == "started"]
        if not active:
            return self._emit_vehicle_departed(clock)
        route = self.rng.choice(active)
        lat = round(self.rng.uniform(-38.0, -33.0), 4)
        lon = round(self.rng.uniform(140.0, 153.0), 4)
        return {
            "event_type": "vehicle_location_update",
            "payload": {
                "vehicle_id": route["vehicle_id"],
                "driver_id": route["driver_id"],
                "route_id": route["route_id"],
                "location": {"lat": lat, "lon": lon},
                "speed_kmh": self.rng.randint(15, 60),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_route_completed(self, clock: Clock) -> dict[str, Any]:
        active = [r for r in self.routes if r["status"] == "started"]
        if not active:
            return self._emit_vehicle_departed(clock)
        route = self.rng.choice(active)
        route["status"] = "completed"
        route["completed_at"] = clock.now
        vehicle = next(
            v for v in self.vehicles if v["vehicle_id"] == route["vehicle_id"]
        )
        miles = round(self.rng.uniform(route["planned_miles"], route["planned_miles"] + 8.0), 1)
        vehicle["odometer_km"] += int(miles * 1.60934)
        return {
            "event_type": "route_completed",
            "payload": {
                "route_id": route["route_id"],
                "vehicle_id": route["vehicle_id"],
                "completed_at": clock.now.isoformat(),
                "stops_completed": len(route["stops"]),
                "stops_failed": 0,
                "actual_miles": miles,
                "final_odometer_km": vehicle["odometer_km"],
            },
        }

    def _emit_fuel_stop_logged(self, clock: Clock) -> dict[str, Any]:
        active = [r for r in self.routes if r["status"] == "started"]
        if not active:
            return self._emit_vehicle_departed(clock)
        route = self.rng.choice(active)
        liters = round(self.rng.uniform(20.0, 60.0), 1)
        cost = round(liters * self.rng.uniform(1.6, 2.0), 2)
        tax_amount = compute_tax(cost)
        return {
            "event_type": "fuel_stop_logged",
            "payload": {
                "fuel_stop_id": self.id_gen.next("fs"),
                "vehicle_id": route["vehicle_id"],
                "liters": liters,
                "cost": cost,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "location": {
                    "lat": round(self.rng.uniform(-38.0, -33.0), 4),
                    "lon": round(self.rng.uniform(140.0, 153.0), 4),
                },
                "logged_at": clock.now.isoformat(),
            },
        }
