"""Field service / trades simulator (HVAC, plumbing, electrical)."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import BusinessSimulator
from eden_business_simulator.framework.actors import ActorPool
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.models import Clock


class FieldServiceSimulator(BusinessSimulator):
    """Simulates trades field-service operations."""

    business_type = "field_service"

    _CATEGORIES = ("hvac_repair", "plumbing", "electrical", "appliance")
    _PARTS = (
        ("prt_001", "dual_run_capacitor", 24.5),
        ("prt_002", "contact_relay", 18.0),
        ("prt_003", "pvc_fitting", 3.5),
        ("prt_004", "breaker_20a", 12.0),
    )

    def __init__(self) -> None:
        self.config = None
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.techs = ActorPool(
            self.id_gen,
            "tech",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.customers: list[dict[str, Any]] = []
        self.tickets: list[dict[str, Any]] = []
        self.vehicles: list[dict[str, Any]] = []
        self.catalog = WeightedEventCatalog(self.rng)

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.id_gen = IdGenerator(self.rng)
        self.techs = ActorPool(
            self.id_gen,
            "tech",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.customers = []
        self.tickets = []
        self.vehicles = []
        self.catalog = WeightedEventCatalog(self.rng)

        for _ in range(self.config.initial_state_overrides.get("initial_techs", 3)):
            self.techs.create(skill=self.rng.choice(self._CATEGORIES))
        for _ in range(self.config.initial_state_overrides.get("initial_vehicles", 2)):
            self.vehicles.append({"vehicle_id": self.id_gen.next("vh")})
        for _ in range(self.config.initial_state_overrides.get("initial_customers", 5)):
            self._create_customer()

        self._build_catalog()

    def available_event_types(self) -> list[str]:
        return [
            "service_ticket_created",
            "technician_assigned",
            "technician_dispatched",
            "technician_arrived",
            "diagnosis_recorded",
            "parts_used",
            "work_completed",
            "customer_sign_off",
            "invoice_generated",
            "payment_received",
            "follow_up_scheduled",
            "vehicle_location_update",
            "parts_reorder_placed",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "service_ticket_created"
        return self._emit(event_type, clock)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "customer_count": len(self.customers),
            "ticket_count": len(self.tickets),
            "tech_count": len(self.techs.all()),
            "vehicle_count": len(self.vehicles),
            "tickets_by_status": self._tickets_by_status(),
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_catalog(self) -> None:
        def has_customers(ctx: "FieldServiceSimulator") -> bool:
            return len(ctx.customers) > 0

        def has_open_tickets(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "open" for t in ctx.tickets)

        def has_assigned(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "assigned" for t in ctx.tickets)

        def has_dispatched(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "dispatched" for t in ctx.tickets)

        def has_arrived(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "arrived" for t in ctx.tickets)

        def has_diagnosed(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "diagnosed" for t in ctx.tickets)

        def has_work_completed(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "completed" for t in ctx.tickets)

        def has_invoiced(ctx: "FieldServiceSimulator") -> bool:
            return any(t["status"] == "invoiced" for t in ctx.tickets)

        self.catalog.register(
            "service_ticket_created", base_weight=8.0, guard=has_customers
        )
        self.catalog.register(
            "technician_assigned", base_weight=6.0, guard=has_open_tickets
        )
        self.catalog.register(
            "technician_dispatched", base_weight=5.0, guard=has_assigned
        )
        self.catalog.register(
            "technician_arrived", base_weight=5.0, guard=has_dispatched
        )
        self.catalog.register(
            "diagnosis_recorded", base_weight=5.0, guard=has_arrived
        )
        self.catalog.register(
            "parts_used", base_weight=4.0, guard=has_diagnosed
        )
        self.catalog.register(
            "work_completed", base_weight=5.0, guard=has_diagnosed
        )
        self.catalog.register(
            "customer_sign_off", base_weight=4.0, guard=has_work_completed
        )
        self.catalog.register(
            "invoice_generated", base_weight=4.0, guard=has_work_completed
        )
        self.catalog.register(
            "payment_received", base_weight=3.0, guard=has_invoiced
        )
        self.catalog.register(
            "follow_up_scheduled", base_weight=2.0, guard=has_work_completed
        )
        self.catalog.register(
            "vehicle_location_update", base_weight=3.0, guard=has_dispatched
        )
        self.catalog.register(
            "parts_reorder_placed", base_weight=2.0, guard=has_diagnosed
        )

    def _create_customer(self) -> dict[str, Any]:
        customer_id = self.id_gen.next("cust")
        customer = {
            "customer_id": customer_id,
            "name": self.faker.name(),
            "location": {
                "lat": round(self.rng.uniform(-38.0, -33.0), 4),
                "lon": round(self.rng.uniform(140.0, 153.0), 4),
            },
        }
        self.customers.append(customer)
        return customer

    def _tickets_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for ticket in self.tickets:
            counts[ticket["status"]] = counts.get(ticket["status"], 0) + 1
        return counts

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _emit_service_ticket_created(self, clock: Clock) -> dict[str, Any]:
        if not self.customers:
            self._create_customer()
        customer = self.rng.choice(self.customers)
        ticket_id = self.id_gen.next("tkt")
        ticket = {
            "ticket_id": ticket_id,
            "customer_id": customer["customer_id"],
            "status": "open",
            "category": self.rng.choice(self._CATEGORIES),
            "priority": self.rng.choice(["low", "medium", "high", "urgent"]),
        }
        self.tickets.append(ticket)
        return {
            "event_type": "service_ticket_created",
            "payload": {
                "ticket_id": ticket_id,
                "customer_id": customer["customer_id"],
                "contract_id": None,
                "priority": ticket["priority"],
                "category": ticket["category"],
                "description": self.faker.sentence(nb_words=6),
                "created_at": clock.now.isoformat(),
            },
        }

    def _emit_technician_assigned(self, clock: Clock) -> dict[str, Any]:
        open_tickets = [t for t in self.tickets if t["status"] == "open"]
        if not open_tickets or not self.techs.all():
            return self._emit_service_ticket_created(clock)
        ticket = self.rng.choice(open_tickets)
        tech = self.rng.choice(self.techs.all())
        ticket["status"] = "assigned"
        ticket["technician_id"] = tech.actor_id
        eta = clock.now + timedelta(minutes=self.rng.randint(20, 60))
        return {
            "event_type": "technician_assigned",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "technician_id": tech.actor_id,
                "vehicle_id": self.rng.choice(self.vehicles)["vehicle_id"],
                "assigned_at": clock.now.isoformat(),
                "estimated_arrival": eta.isoformat(),
            },
        }

    def _emit_technician_dispatched(self, clock: Clock) -> dict[str, Any]:
        assigned = [t for t in self.tickets if t["status"] == "assigned"]
        if not assigned:
            return self._emit_technician_assigned(clock)
        ticket = self.rng.choice(assigned)
        ticket["status"] = "dispatched"
        return {
            "event_type": "technician_dispatched",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "technician_id": ticket["technician_id"],
                "dispatched_at": clock.now.isoformat(),
                "location": {"lat": -33.8688, "lon": 151.2093},
            },
        }

    def _emit_technician_arrived(self, clock: Clock) -> dict[str, Any]:
        dispatched = [t for t in self.tickets if t["status"] == "dispatched"]
        if not dispatched:
            return self._emit_technician_dispatched(clock)
        ticket = self.rng.choice(dispatched)
        ticket["status"] = "arrived"
        return {
            "event_type": "technician_arrived",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "technician_id": ticket["technician_id"],
                "arrived_at": clock.now.isoformat(),
                "travel_minutes": self.rng.randint(15, 45),
            },
        }

    def _emit_diagnosis_recorded(self, clock: Clock) -> dict[str, Any]:
        arrived = [t for t in self.tickets if t["status"] == "arrived"]
        if not arrived:
            return self._emit_technician_arrived(clock)
        ticket = self.rng.choice(arrived)
        ticket["status"] = "diagnosed"
        return {
            "event_type": "diagnosis_recorded",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "technician_id": ticket["technician_id"],
                "fault_code": f"F_{ticket['category'].upper()}",
                "notes": self.faker.sentence(nb_words=8),
                "labor_minutes_estimated": self.rng.randint(30, 120),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_parts_used(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [t for t in self.tickets if t["status"] == "diagnosed"]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        ticket = self.rng.choice(diagnosed)
        part = self.rng.choice(self._PARTS)
        return {
            "event_type": "parts_used",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "part_id": part[0],
                "part_name": part[1],
                "qty": 1,
                "unit_cost": part[2],
                "truck_stock": True,
                "used_at": clock.now.isoformat(),
            },
        }

    def _emit_work_completed(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [t for t in self.tickets if t["status"] == "diagnosed"]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        ticket = self.rng.choice(diagnosed)
        ticket["status"] = "completed"
        labor_minutes = self.rng.randint(30, 120)
        return {
            "event_type": "work_completed",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "technician_id": ticket["technician_id"],
                "resolution": self.faker.sentence(nb_words=5),
                "labor_minutes": labor_minutes,
                "completed_at": clock.now.isoformat(),
            },
        }

    def _emit_customer_sign_off(self, clock: Clock) -> dict[str, Any]:
        completed = [t for t in self.tickets if t["status"] == "completed"]
        if not completed:
            return self._emit_work_completed(clock)
        ticket = self.rng.choice(completed)
        return {
            "event_type": "customer_sign_off",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "customer_id": ticket["customer_id"],
                "signed_at": clock.now.isoformat(),
                "signature_method": self.rng.choice(["mobile_pin", "signature", "sms"]),
                "satisfaction_score": self.rng.randint(1, 5),
            },
        }

    def _emit_invoice_generated(self, clock: Clock) -> dict[str, Any]:
        completed = [t for t in self.tickets if t["status"] == "completed"]
        if not completed:
            return self._emit_work_completed(clock)
        ticket = self.rng.choice(completed)
        ticket["status"] = "invoiced"
        labor = round(self.rng.uniform(80.0, 180.0), 2)
        parts = round(self.rng.uniform(15.0, 90.0), 2)
        tax = round((labor + parts) * 0.1, 2)
        total = round(labor + parts + tax, 2)
        return {
            "event_type": "invoice_generated",
            "payload": {
                "invoice_id": self.id_gen.next("inv"),
                "ticket_id": ticket["ticket_id"],
                "customer_id": ticket["customer_id"],
                "labor_amount": labor,
                "parts_amount": parts,
                "tax_amount": tax,
                "total": total,
                "generated_at": clock.now.isoformat(),
            },
        }

    def _emit_payment_received(self, clock: Clock) -> dict[str, Any]:
        invoiced = [t for t in self.tickets if t["status"] == "invoiced"]
        if not invoiced:
            return self._emit_invoice_generated(clock)
        ticket = self.rng.choice(invoiced)
        return {
            "event_type": "payment_received",
            "payload": {
                "payment_id": self.id_gen.next("pay"),
                "invoice_id": self.id_gen.next("inv"),
                "amount": round(self.rng.uniform(100.0, 250.0), 2),
                "method": self.rng.choice(["card", "cash", "check"]),
                "status": "settled",
                "received_at": clock.now.isoformat(),
            },
        }

    def _emit_follow_up_scheduled(self, clock: Clock) -> dict[str, Any]:
        completed = [t for t in self.tickets if t["status"] in ("completed", "invoiced")]
        if not completed:
            return self._emit_work_completed(clock)
        ticket = self.rng.choice(completed)
        due = clock.now + timedelta(days=self.rng.randint(14, 90))
        return {
            "event_type": "follow_up_scheduled",
            "payload": {
                "ticket_id": ticket["ticket_id"],
                "follow_up_id": self.id_gen.next("fu"),
                "follow_up_type": "maintenance_check",
                "scheduled_date": due.date().isoformat(),
                "scheduled_at": clock.now.isoformat(),
            },
        }

    def _emit_vehicle_location_update(self, clock: Clock) -> dict[str, Any]:
        dispatched = [t for t in self.tickets if t["status"] in ("dispatched", "arrived", "diagnosed")]
        if not dispatched:
            return self._emit_technician_dispatched(clock)
        ticket = self.rng.choice(dispatched)
        return {
            "event_type": "vehicle_location_update",
            "payload": {
                "vehicle_id": self.rng.choice(self.vehicles)["vehicle_id"],
                "technician_id": ticket["technician_id"],
                "location": {
                    "lat": round(self.rng.uniform(-34.0, -33.0), 4),
                    "lon": round(self.rng.uniform(150.0, 152.0), 4),
                },
                "speed_kmh": self.rng.randint(20, 60),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_parts_reorder_placed(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [t for t in self.tickets if t["status"] == "diagnosed"]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        part = self.rng.choice(self._PARTS)
        return {
            "event_type": "parts_reorder_placed",
            "payload": {
                "reorder_id": self.id_gen.next("ro"),
                "supplier_id": self.id_gen.next("sup"),
                "parts": [{"part_id": part[0], "qty": self.rng.randint(5, 20)}],
                "placed_at": clock.now.isoformat(),
            },
        }
