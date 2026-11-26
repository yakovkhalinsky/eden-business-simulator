"""Gym / fitness studio simulator."""

from __future__ import annotations

import random
from datetime import datetime, timedelta, timezone
from typing import Any

from faker import Faker

from eden_business_simulator.businesses.base import (
    ActionRejected,
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
        definition = getattr(self, "definition", None)
        if definition is not None:
            domain = definition.domain
            if "tiers" in domain:
                self._TIERS = tuple(domain["tiers"])
            if "plans" in domain:
                self._PLANS = {
                    name: dict(plan) for name, plan in domain["plans"].items()
                }
            if "class_types" in domain:
                self._CLASS_TYPES = tuple(domain["class_types"])
            if "retail_skus" in domain:
                self._RETAIL_SKUS = tuple(
                    tuple(row) for row in domain["retail_skus"]
                )

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
            "invoices_counter": self.invoices_counter,
        }

    def position_snapshot(self) -> dict[str, Any]:
        members_by_status: dict[str, int] = {}
        for membership in self.memberships.values():
            members_by_status[membership["status"]] = (
                members_by_status.get(membership["status"], 0) + 1
            )
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "position": {
                "members_by_status": members_by_status,
                "active_count": members_by_status.get("active", 0),
                "frozen_count": members_by_status.get("frozen", 0),
                "cancelled_count": members_by_status.get("cancelled", 0),
                "at_risk_count": len(self.at_risk_members),
                "class_count": len(self.classes),
                "booking_count": len(self.bookings),
                "failed_payments_count": len(self.payments_failed),
                "failed_payments_amount": round(
                    sum(f["amount"] for f in self.payments_failed), 2
                ),
            },
        }

    def restore(self, snapshot: dict[str, Any]) -> None:
        """Restore the restorable fields of the summary snapshot.

        ``state_snapshot()`` is a summary: it does not capture the member,
        membership, class or booking collections, so those restart from
        ``initialize()`` on resume (full-state restore is the restorable-
        state project's S3 slice). What the snapshot *does* carry as state
        in its own right is restored here, so snapshot and restore stay
        consistent: the invoice counter and the cancelled-member list
        (the catalogue guards consult the latter, so losing it on resume
        changed which events could fire).
        """
        self.invoices_counter = snapshot.get("invoices_counter", 0)
        self._cancelled_members = list(snapshot.get("cancelled_members", []))

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

        guards = {
            "has_active_members": has_active_members,
            "has_staff": lambda ctx: len(ctx.staff.all()) > 0,
            "has_active_members_and_classes": lambda ctx: has_active_members(ctx)
            and has_classes(ctx),
            "has_bookings": has_bookings,
            "has_active_members_and_trainer": lambda ctx: has_active_members(ctx)
            and any(s["role"] == "trainer" for s in ctx.staff.all()),
            "has_failed_payments": has_failed_payments,
            "has_at_risk_active_members": has_at_risk_active_members,
        }
        if getattr(self, "definition", None) is not None:
            from eden_business_simulator.framework.definition import (
                build_weighted_catalog,
            )

            scheduler = self.definition.scheduler() or self._build_schedule()
            self.catalog = build_weighted_catalog(
                self.definition, self.rng, guards, scheduler
            )
            return
        for event_type, weight, guard in [
            ("membership_enrolled", 3.0, None),
            ("check_in_recorded", 15.0, guards["has_active_members"]),
            ("class_scheduled", 4.0, guards["has_staff"]),
            ("class_booked", 8.0, guards["has_active_members_and_classes"]),
            ("class_cancelled", 3.0, guards["has_bookings"]),
            ("class_attended", 10.0, guards["has_bookings"]),
            ("pt_session_scheduled", 4.0, guards["has_active_members_and_trainer"]),
            ("workout_logged", 7.0, guards["has_active_members"]),
            ("progress_recorded", 3.0, guards["has_active_members"]),
            ("retail_purchase_made", 4.0, guards["has_active_members"]),
            ("payment_failed", 2.0, guards["has_active_members"]),
            ("retention_outreach_sent", 2.0, guards["has_failed_payments"]),
            ("membership_renewed", 4.0, guards["has_active_members"]),
            ("membership_upgraded", 3.0, guards["has_active_members"]),
            ("membership_downgraded", 2.5, guards["has_active_members"]),
            ("membership_frozen", 4.0, guards["has_at_risk_active_members"]),
            ("membership_cancelled", 1.0, guards["has_active_members"]),
        ]:
            self.catalog.register(event_type, base_weight=weight, guard=guard)

    def _active_member_ids(self) -> list[str]:
        """Return member IDs that are neither cancelled nor frozen."""
        return [
            mid
            for mid, membership in self.memberships.items()
            if mid not in self._cancelled_members
            and membership["status"] == "active"
        ]

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

    # ------------------------------------------------------------------
    # Action handlers (Phase 4a). Each validates before mutating and
    # consumes no RNG, so a rejection leaves state and stream untouched.
    # New action-only effect types: membership_unfrozen,
    # class_session_cancelled, plan_price_adjusted.
    # ------------------------------------------------------------------

    def _membership_or_reject(self, member_id: str) -> dict[str, Any]:
        membership = self.memberships.get(member_id)
        if membership is None:
            raise ActionRejected(f"unknown_member:{member_id}")
        return membership

    def _apply_freeze_membership(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        membership = self._membership_or_reject(params["member_id"])
        if membership["status"] != "active":
            raise ActionRejected(
                f"membership_not_active:{params['member_id']}"
            )
        resume_days = params.get("resume_days", 30)
        reason = params.get("reason", "manager_action")
        membership["status"] = "frozen"
        if params["member_id"] not in self._frozen_members:
            self._frozen_members.append(params["member_id"])
        self.at_risk_members.discard(params["member_id"])
        resume_date = clock.now + timedelta(days=resume_days)
        return {
            "event_type": "membership_frozen",
            "payload": {
                "member_id": params["member_id"],
                "membership_id": membership["membership_id"],
                "reason": reason,
                "frozen_at": clock.now.isoformat(),
                "resume_date": resume_date.date().isoformat(),
            },
        }

    def _apply_unfreeze_membership(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        membership = self._membership_or_reject(params["member_id"])
        if membership["status"] != "frozen":
            raise ActionRejected(
                f"membership_not_frozen:{params['member_id']}"
            )
        membership["status"] = "active"
        if params["member_id"] in self._frozen_members:
            self._frozen_members.remove(params["member_id"])
        return {
            "event_type": "membership_unfrozen",
            "payload": {
                "member_id": params["member_id"],
                "membership_id": membership["membership_id"],
                "unfrozen_at": clock.now.isoformat(),
            },
        }

    def _apply_schedule_class(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        instructor_id = params.get("instructor_id")
        if instructor_id is not None:
            if self.staff.get(instructor_id) is None:
                raise ActionRejected(f"unknown_instructor:{instructor_id}")
        else:
            instructors = [
                s for s in self.staff.all() if s["role"] == "instructor"
            ]
            pool = instructors or self.staff.all()
            if not pool:
                raise ActionRejected("no_instructor_available")
            instructor_id = pool[0]["staff_id"]
        class_type = params["class_type"]
        cls = {
            "class_id": self.id_gen.next("cls"),
            "class_name": f"{class_type.title()} class",
            "class_type": class_type,
            "instructor_id": instructor_id,
            "capacity": params["capacity"],
            "duration_minutes": params["duration_minutes"],
            "status": "scheduled",
        }
        self.classes.append(cls)
        return {
            "event_type": "class_scheduled",
            "payload": {
                "class_id": cls["class_id"],
                "class_name": cls["class_name"],
                "class_type": class_type,
                "instructor_id": instructor_id,
                "capacity": cls["capacity"],
                "duration_minutes": cls["duration_minutes"],
                "scheduled_at": clock.now.isoformat(),
                "created_at": clock.now.isoformat(),
            },
        }

    def _apply_cancel_class(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        cls = next(
            (c for c in self.classes if c["class_id"] == params["class_id"]),
            None,
        )
        if cls is None:
            raise ActionRejected(f"unknown_class:{params['class_id']}")
        if cls.get("status", "scheduled") == "cancelled":
            raise ActionRejected(f"class_already_cancelled:{params['class_id']}")
        cls["status"] = "cancelled"
        return {
            "event_type": "class_session_cancelled",
            "payload": {
                "class_id": cls["class_id"],
                "class_name": cls["class_name"],
                "cancelled_at": clock.now.isoformat(),
                "reason": params.get("reason", "manager_action"),
            },
        }

    def _apply_send_retention_outreach(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        membership = self._membership_or_reject(params["member_id"])
        if membership["status"] == "cancelled" or (
            params["member_id"] in self._cancelled_members
        ):
            raise ActionRejected(f"member_cancelled:{params['member_id']}")
        return {
            "event_type": "retention_outreach_sent",
            "payload": {
                "outreach_id": self.id_gen.next("out"),
                "member_id": params["member_id"],
                "channel": params.get("channel", "email"),
                "message_type": params.get("message_type", "payment_retry"),
                "sent_at": clock.now.isoformat(),
            },
        }

    def _apply_adjust_plan_price(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        tier = params["tier"]
        plan = self._PLANS.get(tier)
        if plan is None:
            raise ActionRejected(f"unknown_tier:{tier}")
        old_price = plan["monthly_fee"]
        new_price = round(float(params["new_monthly_fee"]), 2)
        plan["monthly_fee"] = new_price
        updated = 0
        for membership in self.memberships.values():
            if membership["tier"] == tier:
                membership["monthly_fee"] = new_price
                updated += 1
        return {
            "event_type": "plan_price_adjusted",
            "payload": {
                "tier": tier,
                "old_price": old_price,
                "new_price": new_price,
                "memberships_updated": updated,
                "adjusted_at": clock.now.isoformat(),
            },
        }

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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
        # Classes cancelled via the cancel_class action are not bookable.
        bookable = [
            c for c in self.classes if c.get("status", "scheduled") != "cancelled"
        ]
        if not bookable or not active_members:
            return self._emit_check_in_recorded(clock)
        member_id = self.rng.choice(active_members)
        cls = self.rng.choice(bookable)
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
        active_members = self._active_member_ids()
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
