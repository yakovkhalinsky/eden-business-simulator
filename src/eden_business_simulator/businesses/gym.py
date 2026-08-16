"""Gym / fitness studio simulator."""

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
from eden_business_simulator.framework.actors import ActorPool, StaffRoster
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.framework.scheduler import Daypart, DaypartScheduler
from eden_business_simulator.models import Clock


class GymSimulator(BusinessSimulator):
    """Simulates a fitness studio with memberships, classes, and retail."""

    business_type = "gym"

    _TIERS = ("basic", "standard", "premium")
    _PLANS: dict[str, dict[str, Any]] = {
        "basic": {"monthly_fee": 29.0, "classes_per_week": 2},
        "standard": {"monthly_fee": 49.0, "classes_per_week": 5},
        "premium": {"monthly_fee": 79.0, "classes_per_week": 999},
    }
    _CLASS_TYPES = (
        "yoga",
        "spin",
        "hiit",
        "pilates",
        "strength",
        "zumba",
    )
    _RETAIL_SKUS = (
        ("PROT_SHAKE_VAN", "protein_shake_vanilla", 6.5),
        ("PROT_BAR_CHOC", "protein_bar_chocolate", 3.5),
        ("GYM_TOWEL", "gym_towel", 12.0),
        ("WATER_BOTTLE", "water_bottle", 18.0),
    )

    def __init__(self) -> None:
        self.config = None
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.members = ActorPool(
            self.id_gen,
            "member",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.staff = StaffRoster(self.id_gen, self.rng, self.faker)
        self.catalog = WeightedEventCatalog(self.rng)

        self.memberships: dict[str, dict[str, Any]] = {}
        self.classes: list[dict[str, Any]] = []
        self.bookings: list[dict[str, Any]] = []
        self.sessions: list[dict[str, Any]] = []
        self.workouts: list[dict[str, Any]] = []
        self.payments_failed: list[dict[str, Any]] = []
        self.invoices_counter = 0

        self._cancelled_members: list[str] = []
        self._frozen_members: list[str] = []
        self.at_risk_members: set[str] = set()

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)

        self.id_gen = IdGenerator(self.rng)
        self.members = ActorPool(
            self.id_gen,
            "member",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.staff = StaffRoster(self.id_gen, self.rng, self.faker)
        self.catalog = WeightedEventCatalog(self.rng, self._build_schedule())

        self.memberships = {}
        self.classes = []
        self.bookings = []
        self.sessions = []
        self.workouts = []
        self.payments_failed = []
        self.invoices_counter = 0
        self._cancelled_members = []
        self._frozen_members = []
        self.at_risk_members = set()

        # Seed a small roster of instructors and trainers.
        for role in ("instructor", "instructor", "trainer", "front_desk"):
            self.staff.hire(role=role)

        initial_members = self.config.initial_state_overrides.get("initial_members", 6)
        for _ in range(initial_members):
            self._create_member()

        initial_classes = self.config.initial_state_overrides.get("initial_classes", 4)
        for _ in range(initial_classes):
            self._create_class()

        # Seed one pre-existing at-risk member so freezes can occur before any
        # payment failure in short deterministic runs.
        if self.memberships:
            seeded_at_risk = self.rng.choice(list(self.memberships.keys()))
            self.at_risk_members.add(seeded_at_risk)

        self._build_catalog()

    def available_event_types(self) -> list[str]:
        return [
            "membership_enrolled",
            "check_in_recorded",
            "class_scheduled",
            "class_booked",
            "class_cancelled",
            "class_attended",
            "pt_session_scheduled",
            "workout_logged",
            "progress_recorded",
            "retail_purchase_made",
            "payment_failed",
            "retention_outreach_sent",
            "membership_renewed",
            "membership_upgraded",
            "membership_downgraded",
            "membership_frozen",
            "membership_cancelled",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "check_in_recorded"
        return self._emit(event_type, clock)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "member_count": len(self.members.all()),
            "active_membership_count": len(self.memberships),
            "cancelled_members": list(self._cancelled_members),
            "class_count": len(self.classes),
            "booking_count": len(self.bookings),
            "session_count": len(self.sessions),
            "workout_count": len(self.workouts),
            "payments_failed_count": len(self.payments_failed),
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore from a checkpoint snapshot.

        For the gym simulator we only restore lightweight counters; member/class
        identity is rebuilt from deterministic generation on resume.
        """
        self.invoices_counter = snapshot.get("invoices_counter", 0)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_schedule(self) -> DaypartScheduler:
        return DaypartScheduler(
            dayparts=[
                Daypart(
                    name="early",
                    start_hour=5,
                    end_hour=8,
                    weight_modifiers={"check_in_recorded": 1.5, "class_scheduled": 0.5},
                    default_modifier=0.6,
                ),
                Daypart(
                    name="morning_peak",
                    start_hour=8,
                    end_hour=12,
                    weight_modifiers={
                        "check_in_recorded": 2.5,
                        "class_booked": 2.0,
                        "class_attended": 2.5,
                        "workout_logged": 2.0,
                    },
                    default_modifier=1.2,
                ),
                Daypart(
                    name="afternoon",
                    start_hour=12,
                    end_hour=17,
                    weight_modifiers={
                        "check_in_recorded": 1.5,
                        "pt_session_scheduled": 1.5,
                        "workout_logged": 1.5,
                    },
                    default_modifier=0.9,
                ),
                Daypart(
                    name="evening_peak",
                    start_hour=17,
                    end_hour=21,
                    weight_modifiers={
                        "check_in_recorded": 3.0,
                        "class_booked": 2.5,
                        "class_attended": 3.0,
                        "retail_purchase_made": 2.0,
                        "progress_recorded": 1.5,
                    },
                    default_modifier=1.5,
                ),
                Daypart(
                    name="late",
                    start_hour=21,
                    end_hour=5,
                    weight_modifiers={"payment_failed": 1.5, "membership_cancelled": 1.5},
                    default_modifier=0.3,
                ),
            ]
        )

    def _build_catalog(self) -> None:
        def has_members(ctx: "GymSimulator") -> bool:
            return len(ctx.members.all()) > 0

        def has_classes(ctx: "GymSimulator") -> bool:
            return len(ctx.classes) > 0

        def has_bookings(ctx: "GymSimulator") -> bool:
            return any(b["status"] == "confirmed" for b in ctx.bookings)

        def has_active_members(ctx: "GymSimulator") -> bool:
            return any(
                m not in ctx._cancelled_members
                and ctx.memberships[m]["status"] != "frozen"
                for m in ctx.memberships
            )

        def has_at_risk_active_members(ctx: "GymSimulator") -> bool:
            return any(
                mid in ctx.memberships
                and mid not in ctx._cancelled_members
                and ctx.memberships[mid]["status"] not in ("frozen", "cancelled")
                for mid in ctx.at_risk_members
            )

        def has_failed_payments(ctx: "GymSimulator") -> bool:
            return len(ctx.payments_failed) > 0

        self.catalog.register("membership_enrolled", base_weight=3.0)
        self.catalog.register(
            "check_in_recorded",
            base_weight=15.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "class_scheduled",
            base_weight=4.0,
            guard=lambda ctx: len(ctx.staff.all()) > 0,
        )
        self.catalog.register(
            "class_booked",
            base_weight=8.0,
            guard=lambda ctx: has_members(ctx) and has_classes(ctx),
        )
        self.catalog.register(
            "class_cancelled",
            base_weight=3.0,
            guard=has_bookings,
        )
        self.catalog.register(
            "class_attended",
            base_weight=10.0,
            guard=has_bookings,
        )
        self.catalog.register(
            "pt_session_scheduled",
            base_weight=4.0,
            guard=lambda ctx: has_members(ctx)
            and any(s["role"] == "trainer" for s in ctx.staff.all()),
        )
        self.catalog.register(
            "workout_logged",
            base_weight=7.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "progress_recorded",
            base_weight=3.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "retail_purchase_made",
            base_weight=4.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "payment_failed",
            base_weight=2.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "retention_outreach_sent",
            base_weight=2.0,
            guard=has_failed_payments,
        )
        self.catalog.register(
            "membership_renewed",
            base_weight=4.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "membership_upgraded",
            base_weight=3.0,
            guard=has_active_members,
        )
        self.catalog.register(
            "membership_downgraded",
            base_weight=2.5,
            guard=has_active_members,
        )
        self.catalog.register(
            "membership_frozen",
            base_weight=4.0,
            guard=has_at_risk_active_members,
        )
        self.catalog.register(
            "membership_cancelled",
            base_weight=1.0,
            guard=has_active_members,
        )

    def _create_member(self) -> dict[str, Any]:
        actor = self.members.create()
        tier = self.rng.choice(self._TIERS)
        plan = self._PLANS[tier]
        membership_id = self.id_gen.next("mbr")
        self.memberships[actor.actor_id] = {
            "membership_id": membership_id,
            "member_id": actor.actor_id,
            "tier": tier,
            "monthly_fee": plan["monthly_fee"],
            "status": "active",
        }
        return self.memberships[actor.actor_id]

    def _create_class(self) -> dict[str, Any]:
        class_type = self.rng.choice(self._CLASS_TYPES)
        instructors = [s for s in self.staff.all() if s["role"] == "instructor"]
        if not instructors:
            instructors = self.staff.all()
        instructor = self.rng.choice(instructors)
        class_id = self.id_gen.next("cls")
        cls = {
            "class_id": class_id,
            "class_name": f"{class_type.title()} class",
            "class_type": class_type,
            "instructor_id": instructor["staff_id"],
            "capacity": self.rng.choice([10, 15, 20, 25]),
            "duration_minutes": self.rng.choice([30, 45, 60, 90]),
        }
        self.classes.append(cls)
        return cls

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _emit_membership_enrolled(self, clock: Clock) -> dict[str, Any]:
        membership = self._create_member()
        monthly_fee = membership["monthly_fee"]
        return {
            "event_type": "membership_enrolled",
            "payload": {
                "member_id": membership["member_id"],
                "membership_id": membership["membership_id"],
                "tier": membership["tier"],
                "monthly_fee": monthly_fee,
                "tax_amount": compute_tax(monthly_fee),
                "tax_type": DEFAULT_TAX_TYPE,
                "start_date": clock.now.date().isoformat(),
                "enrolled_at": clock.now.isoformat(),
            },
        }

    def _emit_check_in_recorded(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        check_in_id = self.id_gen.next("ci")
        return {
            "event_type": "check_in_recorded",
            "payload": {
                "check_in_id": check_in_id,
                "member_id": member_id,
                "location_id": "main_gym",
                "access_method": self.rng.choice(["rfid", "app", "front_desk"]),
                "checked_in_at": clock.now.isoformat(),
            },
        }

    def _emit_class_scheduled(self, clock: Clock) -> dict[str, Any]:
        cls = self._create_class()
        scheduled_at = clock.now + timedelta(hours=self.rng.randint(1, 48))
        return {
            "event_type": "class_scheduled",
            "payload": {
                "class_id": cls["class_id"],
                "class_name": cls["class_name"],
                "class_type": cls["class_type"],
                "instructor_id": cls["instructor_id"],
                "capacity": cls["capacity"],
                "duration_minutes": cls["duration_minutes"],
                "scheduled_at": scheduled_at.isoformat(),
                "created_at": clock.now.isoformat(),
            },
        }

    def _emit_class_booked(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not self.classes or not active_members:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        cls = self.rng.choice(self.classes)
        booking_id = self.id_gen.next("bk")
        booking = {
            "booking_id": booking_id,
            "member_id": member_id,
            "class_id": cls["class_id"],
            "status": "confirmed",
            "booked_at": clock.now,
        }
        self.bookings.append(booking)
        return {
            "event_type": "class_booked",
            "payload": {
                "booking_id": booking_id,
                "member_id": member_id,
                "class_id": cls["class_id"],
                "class_name": cls["class_name"],
                "status": "confirmed",
                "waitlist_position": 0,
                "booked_at": clock.now.isoformat(),
            },
        }

    def _emit_class_cancelled(self, clock: Clock) -> dict[str, Any]:
        confirmed = [b for b in self.bookings if b["status"] == "confirmed"]
        if not confirmed:
            return self._emit_class_booked(clock)
        booking = self.rng.choice(confirmed)
        booking["status"] = "cancelled"
        late_cancel = self.rng.random() < 0.3
        return {
            "event_type": "class_cancelled",
            "payload": {
                "booking_id": booking["booking_id"],
                "member_id": booking["member_id"],
                "class_id": booking["class_id"],
                "cancelled_at": clock.now.isoformat(),
                "late_cancel": late_cancel,
                "penalty_applied": late_cancel and self.rng.random() < 0.5,
            },
        }

    def _emit_class_attended(self, clock: Clock) -> dict[str, Any]:
        confirmed = [b for b in self.bookings if b["status"] == "confirmed"]
        if not confirmed:
            return self._emit_class_booked(clock)
        booking = self.rng.choice(confirmed)
        booking["status"] = "attended"
        attendance_id = self.id_gen.next("att")
        return {
            "event_type": "class_attended",
            "payload": {
                "attendance_id": attendance_id,
                "booking_id": booking["booking_id"],
                "member_id": booking["member_id"],
                "class_id": booking["class_id"],
                "checked_in_at": clock.now.isoformat(),
            },
        }

    def _emit_pt_session_scheduled(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        trainers = [s for s in self.staff.all() if s["role"] == "trainer"]
        if not active_members or not trainers:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        trainer = self.rng.choice(trainers)
        session_id = self.id_gen.next("ses")
        session_type = self.rng.choice(
            ["strength", "cardio", "mobility", "nutrition"]
        )
        scheduled_at = clock.now + timedelta(hours=self.rng.randint(1, 72))
        session = {
            "session_id": session_id,
            "member_id": member_id,
            "trainer_id": trainer["staff_id"],
            "session_type": session_type,
            "scheduled_at": scheduled_at,
        }
        self.sessions.append(session)
        return {
            "event_type": "pt_session_scheduled",
            "payload": {
                "session_id": session_id,
                "member_id": member_id,
                "trainer_id": trainer["staff_id"],
                "session_type": session_type,
                "duration_minutes": self.rng.choice([30, 45, 60]),
                "scheduled_at": scheduled_at.isoformat(),
                "booked_at": clock.now.isoformat(),
            },
        }

    def _emit_workout_logged(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        workout_id = self.id_gen.next("wo")
        exercises = []
        for name in self.rng.sample(
            ["bench_press", "squat", "deadlift", "treadmill", "rower"],
            k=self.rng.randint(1, 3),
        ):
            exercises.append(
                {
                    "name": name,
                    "sets": self.rng.randint(2, 5),
                    "reps": self.rng.randint(5, 15),
                    "weight_kg": round(self.rng.uniform(20.0, 120.0), 1)
                    if name in ("bench_press", "squat", "deadlift")
                    else None,
                    "duration_minutes": self.rng.randint(10, 30)
                    if name in ("treadmill", "rower")
                    else None,
                }
            )
        workout = {
            "workout_id": workout_id,
            "member_id": member_id,
            "exercises": exercises,
            "logged_at": clock.now,
        }
        self.workouts.append(workout)
        return {
            "event_type": "workout_logged",
            "payload": {
                "workout_id": workout_id,
                "member_id": member_id,
                "exercises": exercises,
                "logged_at": clock.now.isoformat(),
            },
        }

    def _emit_progress_recorded(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        metric_type = self.rng.choice(
            ["body_weight_kg", "body_fat_pct", "bench_press_kg", "squat_kg"]
        )
        value = round(self.rng.uniform(50.0, 110.0), 1)
        if metric_type == "body_fat_pct":
            value = round(self.rng.uniform(8.0, 35.0), 1)
        return {
            "event_type": "progress_recorded",
            "payload": {
                "log_id": self.id_gen.next("log"),
                "member_id": member_id,
                "measurement_type": metric_type,
                "value": value,
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_retail_purchase_made(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        item = self.rng.choice(self._RETAIL_SKUS)
        qty = self.rng.randint(1, 3)
        subtotal = round(item[2] * qty, 2)
        tax_amount = compute_tax(subtotal)
        total = round(subtotal + tax_amount, 2)
        return {
            "event_type": "retail_purchase_made",
            "payload": {
                "purchase_id": self.id_gen.next("pur"),
                "member_id": member_id,
                "items": [
                    {"sku": item[0], "name": item[1], "qty": qty, "unit_price": item[2]}
                ],
                "subtotal": subtotal,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "total": total,
                "payment_method": self.rng.choice(["card", "cash", "app"]),
                "purchased_at": clock.now.isoformat(),
            },
        }

    def _emit_payment_failed(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        membership = self.memberships[member_id]
        amount = membership["monthly_fee"]
        tax_amount = compute_tax(amount)
        failure = {
            "member_id": member_id,
            "membership_id": membership["membership_id"],
            "amount": amount,
            "tax_amount": tax_amount,
            "tax_type": DEFAULT_TAX_TYPE,
            "reason": self.rng.choice(
                ["insufficient_funds", "expired_card", "bank_declined"]
            ),
            "attempted_at": clock.now,
        }
        self.payments_failed.append(failure)
        self.at_risk_members.add(member_id)
        return {
            "event_type": "payment_failed",
            "payload": {
                "payment_id": self.id_gen.next("pay"),
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "amount": amount,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "failure_reason": failure["reason"],
                "retry_attempt": len(
                    [p for p in self.payments_failed if p["member_id"] == member_id]
                ),
                "failed_at": clock.now.isoformat(),
            },
        }

    def _emit_retention_outreach_sent(self, clock: Clock) -> dict[str, Any]:
        if not self.payments_failed:
            return self._emit_payment_failed(clock)
        failure = self.rng.choice(self.payments_failed)
        return {
            "event_type": "retention_outreach_sent",
            "payload": {
                "outreach_id": self.id_gen.next("out"),
                "member_id": failure["member_id"],
                "channel": self.rng.choice(["email", "sms", "phone"]),
                "message_type": self.rng.choice(["payment_retry", "win_back"]),
                "sent_at": clock.now.isoformat(),
            },
        }

    def _emit_membership_renewed(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        membership = self.memberships[member_id]
        monthly_fee = membership["monthly_fee"]
        return {
            "event_type": "membership_renewed",
            "payload": {
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "tier": membership["tier"],
                "monthly_fee": monthly_fee,
                "tax_amount": compute_tax(monthly_fee),
                "tax_type": DEFAULT_TAX_TYPE,
                "renewal_term_months": self.rng.choice([1, 3, 6, 12]),
                "renewed_at": clock.now.isoformat(),
            },
        }

    def _emit_membership_upgraded(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        membership = self.memberships[member_id]
        current_tier = membership["tier"]
        higher_tiers = [t for t in self._TIERS if t != current_tier]
        new_tier = self.rng.choice(higher_tiers)
        plan = self._PLANS[new_tier]
        membership["tier"] = new_tier
        new_monthly_fee = plan["monthly_fee"]
        membership["monthly_fee"] = new_monthly_fee
        return {
            "event_type": "membership_upgraded",
            "payload": {
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "previous_tier": current_tier,
                "new_tier": new_tier,
                "new_monthly_fee": new_monthly_fee,
                "tax_amount": compute_tax(new_monthly_fee),
                "tax_type": DEFAULT_TAX_TYPE,
                "upgraded_at": clock.now.isoformat(),
            },
        }

    def _emit_membership_downgraded(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        membership = self.memberships[member_id]
        current_tier = membership["tier"]
        lower_tiers = [t for t in self._TIERS if t != current_tier]
        new_tier = self.rng.choice(lower_tiers)
        plan = self._PLANS[new_tier]
        membership["tier"] = new_tier
        new_monthly_fee = plan["monthly_fee"]
        membership["monthly_fee"] = new_monthly_fee
        return {
            "event_type": "membership_downgraded",
            "payload": {
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "previous_tier": current_tier,
                "new_tier": new_tier,
                "new_monthly_fee": new_monthly_fee,
                "tax_amount": compute_tax(new_monthly_fee),
                "tax_type": DEFAULT_TAX_TYPE,
                "reason": self.rng.choice(["cost", "usage", "seasonal"]),
                "downgraded_at": clock.now.isoformat(),
            },
        }

    _FROZEN_REASONS = (
        "payment_issue",
        "medical_hold",
        "travel",
        "injury",
        "seasonal_pause",
    )

    def _emit_membership_frozen(self, clock: Clock) -> dict[str, Any]:
        at_risk = [
            mid
            for mid in self.at_risk_members
            if mid in self.memberships
            and mid not in self._cancelled_members
            and self.memberships[mid]["status"] == "active"
        ]
        if not at_risk:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(at_risk)
        self.at_risk_members.discard(member_id)
        self._frozen_members.append(member_id)
        membership = self.memberships[member_id]
        membership["status"] = "frozen"
        resume_date = clock.now + timedelta(days=self.rng.randint(14, 90))
        return {
            "event_type": "membership_frozen",
            "payload": {
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "reason": self.rng.choice(self._FROZEN_REASONS),
                "frozen_at": clock.now.isoformat(),
                "resume_date": resume_date.date().isoformat(),
            },
        }

    def _emit_membership_cancelled(self, clock: Clock) -> dict[str, Any]:
        active_members = [
            m.actor_id
            for m in self.members.all()
            if m.actor_id not in self._cancelled_members
        ]
        if not active_members:
            return self._emit_membership_enrolled(clock)
        member_id = self.rng.choice(active_members)
        self._cancelled_members.append(member_id)
        membership = self.memberships[member_id]
        membership["status"] = "cancelled"
        return {
            "event_type": "membership_cancelled",
            "payload": {
                "member_id": member_id,
                "membership_id": membership["membership_id"],
                "cancelled_at": clock.now.isoformat(),
                "reason": self.rng.choice(
                    ["relocating", "cost", "injury", "not_using"]
                ),
                "churn_risk_score": round(self.rng.uniform(0.7, 1.0), 2),
            },
        }
