"""Healthcare clinic / general practice simulator."""

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
from eden_business_simulator.framework.actors import ActorPool
from eden_business_simulator.framework.catalog import WeightedEventCatalog
from eden_business_simulator.framework.ids import IdGenerator
from eden_business_simulator.models import Clock


class ClinicSimulator(BusinessSimulator):
    """Simulates a general-practice healthcare clinic."""

    business_type = "clinic"

    _APPOINTMENT_TYPES = (
        "new_patient_visit",
        "annual_physical",
        "follow_up",
        "sick_visit",
    )
    _ICD10 = (
        ("Z00.00", "routine_health_exam"),
        ("E11.9", "type_2_diabetes"),
        ("I10", "essential_hypertension"),
        ("J06.9", "acute_upper_respiratory_infection"),
    )
    _CPT = (
        ("99213", "office_visit_established"),
        ("99214", "office_visit_established_extended"),
        ("99395", "annual_physical"),
    )

    def __init__(self) -> None:
        self.config = None
        self.rng = random.Random()
        self.faker = Faker()
        self.id_gen = IdGenerator(self.rng)
        self.patients = ActorPool(
            self.id_gen,
            "patient",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.providers = ActorPool(
            self.id_gen,
            "provider",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: f"Dr. {faker.last_name()}",
        )
        self.appointments: list[dict[str, Any]] = []
        self.encounters: list[dict[str, Any]] = []
        self.lab_orders: list[dict[str, Any]] = []
        self.claims: list[dict[str, Any]] = []
        self.provider_shifts: list[dict[str, Any]] = []
        self.catalog = WeightedEventCatalog(self.rng)

    def initialize(self, seed: int) -> None:
        self.rng.seed(seed)
        self.faker.seed_instance(seed)
        self.id_gen = IdGenerator(self.rng)
        self.patients = ActorPool(
            self.id_gen,
            "patient",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: faker.name(),
        )
        self.providers = ActorPool(
            self.id_gen,
            "provider",
            self.faker,
            self.rng,
            name_factory=lambda faker, rng: f"Dr. {faker.last_name()}",
        )
        self.appointments = []
        self.encounters = []
        self.lab_orders = []
        self.claims = []
        self.provider_shifts = []
        self.catalog = WeightedEventCatalog(self.rng)
        definition = getattr(self, "definition", None)
        if definition is not None:
            domain = definition.domain
            if "appointment_types" in domain:
                self._APPOINTMENT_TYPES = tuple(domain["appointment_types"])
            if "icd10" in domain:
                self._ICD10 = tuple(tuple(row) for row in domain["icd10"])
            if "cpt" in domain:
                self._CPT = tuple(tuple(row) for row in domain["cpt"])

        for _ in range(self.config.initial_state_overrides.get("initial_providers", 3)):
            self.providers.create(specialty="family_medicine")
        for _ in range(self.config.initial_state_overrides.get("initial_patients", 6)):
            self._create_patient()

        self._build_catalog()

    def available_event_types(self) -> list[str]:
        return [
            "appointment_scheduled",
            "appointment_cancelled",
            "appointment_rescheduled",
            "patient_checked_in",
            "vitals_recorded",
            "encounter_started",
            "diagnosis_recorded",
            "procedure_performed",
            "lab_order_placed",
            "lab_result_received",
            "medication_prescribed",
            "claim_submitted",
            "claim_adjudicated",
            "payment_posted",
            "referral_sent",
            "no_show_recorded",
        ]

    def next_event(self, clock: Clock) -> dict[str, Any]:
        event_type = self.catalog.choose(hour=clock.now.hour, context=self)
        if event_type is None:
            event_type = "appointment_scheduled"
        return self._emit(event_type, clock)

    def state_snapshot(self) -> dict[str, Any]:
        return {
            "patient_count": len(self.patients.all()),
            "provider_count": len(self.providers.all()),
            "appointment_count": len(self.appointments),
            "encounter_count": len(self.encounters),
            "lab_order_count": len(self.lab_orders),
            "claim_count": len(self.claims),
            "appointments_by_status": self._appointments_by_status(),
        }

    def position_snapshot(self) -> dict[str, Any]:
        encounters_by_status: dict[str, int] = {}
        for encounter in self.encounters:
            encounters_by_status[encounter["status"]] = (
                encounters_by_status.get(encounter["status"], 0) + 1
            )
        open_claims = [c for c in self.claims if c["status"] == "submitted"]
        return {
            "schema_version": 1,
            "business_type": self.business_type,
            "position": {
                "appointments_by_status": self._appointments_by_status(),
                "encounters_by_status": encounters_by_status,
                "open_claims_count": len(open_claims),
                "open_claims_amount": round(
                    sum(c["amount"] for c in open_claims), 2
                ),
                "lab_orders_pending": sum(
                    1 for lo in self.lab_orders if lo["status"] == "pending"
                ),
                "patient_count": len(self.patients.all()),
                "provider_count": len(self.providers.all()),
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _build_catalog(self) -> None:
        def has_patients(ctx: "ClinicSimulator") -> bool:
            return len(ctx.patients.all()) > 0

        def has_scheduled(ctx: "ClinicSimulator") -> bool:
            return any(a["status"] == "scheduled" for a in ctx.appointments)

        def has_checked_in(ctx: "ClinicSimulator") -> bool:
            return any(a["status"] == "checked_in" for a in ctx.appointments)

        def has_active_encounters(ctx: "ClinicSimulator") -> bool:
            return any(e["status"] == "started" for e in ctx.encounters)

        def has_diagnosed(ctx: "ClinicSimulator") -> bool:
            return any(e["status"] == "diagnosed" for e in ctx.encounters)

        def has_claims(ctx: "ClinicSimulator") -> bool:
            return len(ctx.claims) > 0

        guards = {
            "has_patients": has_patients,
            "has_scheduled": has_scheduled,
            "has_checked_in": has_checked_in,
            "has_active_encounters": has_active_encounters,
            "has_diagnosed": has_diagnosed,
            "has_claims": has_claims,
        }
        if getattr(self, "definition", None) is not None:
            from eden_business_simulator.framework.definition import (
                build_weighted_catalog,
            )

            self.catalog = build_weighted_catalog(self.definition, self.rng, guards)
            return
        for event_type, weight, guard_name in [
            ("appointment_scheduled", 8.0, "has_patients"),
            ("appointment_cancelled", 2.0, "has_scheduled"),
            ("appointment_rescheduled", 2.0, "has_scheduled"),
            ("patient_checked_in", 6.0, "has_scheduled"),
            ("vitals_recorded", 5.0, "has_checked_in"),
            ("encounter_started", 6.0, "has_checked_in"),
            ("diagnosis_recorded", 5.0, "has_active_encounters"),
            ("procedure_performed", 5.0, "has_active_encounters"),
            ("lab_order_placed", 4.0, "has_active_encounters"),
            ("lab_result_received", 3.0, "has_diagnosed"),
            ("medication_prescribed", 3.0, "has_diagnosed"),
            ("claim_submitted", 3.0, "has_diagnosed"),
            ("claim_adjudicated", 2.0, "has_claims"),
            ("payment_posted", 2.0, "has_claims"),
            ("referral_sent", 2.0, "has_diagnosed"),
            ("no_show_recorded", 2.0, "has_scheduled"),
        ]:
            self.catalog.register(
                event_type, base_weight=weight, guard=guards[guard_name]
            )

    # ------------------------------------------------------------------
    # Action handlers (Phase 4a). Each validates before mutating and
    # consumes no RNG, so a rejection leaves state and stream untouched.
    # New action-only effect types: provider_shift_added, claim_chased.
    # Note: provider shifts are a roster-availability record only —
    # the clinic simulation has no shift state that gates events.
    # ------------------------------------------------------------------

    def _appointment_or_reject(
        self, appointment_id: str
    ) -> dict[str, Any]:
        appt = next(
            (a for a in self.appointments
             if a["appointment_id"] == appointment_id),
            None,
        )
        if appt is None:
            raise ActionRejected(f"unknown_appointment:{appointment_id}")
        return appt

    def _scheduled_appointment_or_reject(
        self, appointment_id: str
    ) -> dict[str, Any]:
        appt = self._appointment_or_reject(appointment_id)
        if appt["status"] != "scheduled":
            raise ActionRejected(
                f"appointment_not_scheduled:{appointment_id}"
            )
        return appt

    def _apply_schedule_appointment(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        if self.patients.get(params["patient_id"]) is None:
            raise ActionRejected(f"unknown_patient:{params['patient_id']}")
        if self.providers.get(params["provider_id"]) is None:
            raise ActionRejected(f"unknown_provider:{params['provider_id']}")
        appt_time = clock.now + timedelta(hours=float(params["starts_in_hours"]))
        appt = {
            "appointment_id": self.id_gen.next("appt"),
            "patient_id": params["patient_id"],
            "provider_id": params["provider_id"],
            "appointment_type": params["appointment_type"],
            "scheduled_time": appt_time,
            "status": "scheduled",
        }
        self.appointments.append(appt)
        return {
            "event_type": "appointment_scheduled",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "appointment_type": appt["appointment_type"],
                "scheduled_at": clock.now.isoformat(),
                "appointment_time": appt_time.isoformat(),
                "reason": "manager_action",
                "channel": "manager",
            },
        }

    def _apply_cancel_appointment(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        appt = self._scheduled_appointment_or_reject(params["appointment_id"])
        appt["status"] = "cancelled"
        return {
            "event_type": "appointment_cancelled",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "reason": params.get("reason", "patient_request"),
                "cancelled_at": clock.now.isoformat(),
            },
        }

    def _apply_reschedule_appointment(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        appt = self._scheduled_appointment_or_reject(params["appointment_id"])
        new_time = clock.now + timedelta(hours=float(params["starts_in_hours"]))
        appt["scheduled_time"] = new_time
        return {
            "event_type": "appointment_rescheduled",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "new_appointment_time": new_time.isoformat(),
                "reason": params.get("reason", "patient_request"),
                "rescheduled_at": clock.now.isoformat(),
            },
        }

    def _apply_add_provider_shift(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        if self.providers.get(params["provider_id"]) is None:
            raise ActionRejected(f"unknown_provider:{params['provider_id']}")
        shift_hours = params.get("shift_hours", 8)
        self.provider_shifts.append(
            {
                "provider_id": params["provider_id"],
                "date": clock.now.date(),
                "hours": shift_hours,
            }
        )
        return {
            "event_type": "provider_shift_added",
            "payload": {
                "provider_id": params["provider_id"],
                "shift_date": clock.now.date().isoformat(),
                "shift_hours": shift_hours,
                "added_at": clock.now.isoformat(),
            },
        }

    def _apply_chase_claim(
        self, params: dict[str, Any], clock: Clock
    ) -> dict[str, Any]:
        claim = next(
            (c for c in self.claims if c["claim_id"] == params["claim_id"]),
            None,
        )
        if claim is None:
            raise ActionRejected(f"unknown_claim:{params['claim_id']}")
        if claim["status"] not in ("submitted", "adjudicated"):
            raise ActionRejected(f"claim_not_chaseable:{params['claim_id']}")
        claim["chase_count"] = claim.get("chase_count", 0) + 1
        claim["last_chased_at"] = clock.now.isoformat()
        return {
            "event_type": "claim_chased",
            "payload": {
                "claim_id": claim["claim_id"],
                "patient_id": claim["patient_id"],
                "channel": params.get("channel", "email"),
                "chase_count": claim["chase_count"],
                "chased_at": clock.now.isoformat(),
            },
        }

    def _create_patient(self) -> dict[str, Any]:
        actor = self.patients.create()
        return {
            "patient_id": actor.actor_id,
            "name": actor.name,
            "dob": self.faker.date_of_birth(minimum_age=18, maximum_age=85).isoformat(),
        }

    def _appointments_by_status(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for appt in self.appointments:
            counts[appt["status"]] = counts.get(appt["status"], 0) + 1
        return counts

    def _emit(self, event_type: str, clock: Clock) -> dict[str, Any]:
        method = getattr(self, f"_emit_{event_type}", None)
        if method is None:
            raise RuntimeError(f"Unhandled event type: {event_type}")
        return method(clock)

    def _emit_appointment_scheduled(self, clock: Clock) -> dict[str, Any]:
        if not self.patients.all():
            self._create_patient()
        patient = self.rng.choice(self.patients.all())
        provider = self.rng.choice(self.providers.all())
        appt_id = self.id_gen.next("appt")
        appt_time = clock.now + timedelta(hours=self.rng.randint(1, 72))
        appt = {
            "appointment_id": appt_id,
            "patient_id": patient.actor_id,
            "provider_id": provider.actor_id,
            "appointment_type": self.rng.choice(self._APPOINTMENT_TYPES),
            "scheduled_time": appt_time,
            "status": "scheduled",
        }
        self.appointments.append(appt)
        return {
            "event_type": "appointment_scheduled",
            "payload": {
                "appointment_id": appt_id,
                "patient_id": patient.actor_id,
                "provider_id": provider.actor_id,
                "appointment_type": appt["appointment_type"],
                "scheduled_at": clock.now.isoformat(),
                "appointment_time": appt_time.isoformat(),
                "reason": self.rng.choice(["annual_physical", "follow_up", "sick_visit"]),
                "channel": self.rng.choice(["phone", "patient_portal", "referral"]),
            },
        }

    def _emit_appointment_cancelled(self, clock: Clock) -> dict[str, Any]:
        scheduled = [a for a in self.appointments if a["status"] == "scheduled"]
        if not scheduled:
            return self._emit_appointment_scheduled(clock)
        appt = self.rng.choice(scheduled)
        appt["status"] = "cancelled"
        return {
            "event_type": "appointment_cancelled",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "reason": self.rng.choice(["patient_request", "provider_unavailable", "no_longer_needed"]),
                "cancelled_at": clock.now.isoformat(),
            },
        }

    def _emit_appointment_rescheduled(self, clock: Clock) -> dict[str, Any]:
        scheduled = [a for a in self.appointments if a["status"] == "scheduled"]
        if not scheduled:
            return self._emit_appointment_scheduled(clock)
        appt = self.rng.choice(scheduled)
        new_time = appt["scheduled_time"] + timedelta(hours=self.rng.randint(1, 72))
        appt["scheduled_time"] = new_time
        return {
            "event_type": "appointment_rescheduled",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "new_appointment_time": new_time.isoformat(),
                "reason": self.rng.choice(["patient_request", "provider_unavailable"]),
                "rescheduled_at": clock.now.isoformat(),
            },
        }

    def _emit_patient_checked_in(self, clock: Clock) -> dict[str, Any]:
        scheduled = [a for a in self.appointments if a["status"] == "scheduled"]
        if not scheduled:
            return self._emit_appointment_scheduled(clock)
        appt = self.rng.choice(scheduled)
        appt["status"] = "checked_in"
        return {
            "event_type": "patient_checked_in",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "checked_in_at": clock.now.isoformat(),
                "waiting_minutes": self.rng.randint(0, 15),
                "self_reported_symptoms": ["fatigue"] if self.rng.random() < 0.3 else [],
            },
        }

    def _emit_vitals_recorded(self, clock: Clock) -> dict[str, Any]:
        checked_in = [a for a in self.appointments if a["status"] == "checked_in"]
        if not checked_in:
            return self._emit_patient_checked_in(clock)
        appt = self.rng.choice(checked_in)
        return {
            "event_type": "vitals_recorded",
            "payload": {
                "encounter_id": self.id_gen.next("enc"),
                "patient_id": appt["patient_id"],
                "height_cm": self.rng.randint(150, 190),
                "weight_kg": round(self.rng.uniform(50.0, 110.0), 1),
                "bp_systolic": self.rng.randint(110, 150),
                "bp_diastolic": self.rng.randint(70, 95),
                "heart_rate": self.rng.randint(55, 100),
                "temperature_c": round(self.rng.uniform(36.1, 37.5), 1),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_encounter_started(self, clock: Clock) -> dict[str, Any]:
        checked_in = [a for a in self.appointments if a["status"] == "checked_in"]
        if not checked_in:
            return self._emit_patient_checked_in(clock)
        appt = self.rng.choice(checked_in)
        appt["status"] = "in_encounter"
        encounter_id = self.id_gen.next("enc")
        encounter = {
            "encounter_id": encounter_id,
            "appointment_id": appt["appointment_id"],
            "patient_id": appt["patient_id"],
            "provider_id": appt["provider_id"],
            "status": "started",
        }
        self.encounters.append(encounter)
        return {
            "event_type": "encounter_started",
            "payload": {
                "encounter_id": encounter_id,
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "provider_id": appt["provider_id"],
                "started_at": clock.now.isoformat(),
                "chief_complaint": self.rng.choice(["annual_physical", "cough", "follow_up"]),
            },
        }

    def _emit_diagnosis_recorded(self, clock: Clock) -> dict[str, Any]:
        started = [e for e in self.encounters if e["status"] == "started"]
        if not started:
            return self._emit_encounter_started(clock)
        encounter = self.rng.choice(started)
        encounter["status"] = "diagnosed"
        code, description = self.rng.choice(self._ICD10)
        return {
            "event_type": "diagnosis_recorded",
            "payload": {
                "encounter_id": encounter["encounter_id"],
                "patient_id": encounter["patient_id"],
                "icd10_code": code,
                "diagnosis": description,
                "severity": self.rng.choice(["acute", "chronic"]),
                "recorded_at": clock.now.isoformat(),
            },
        }

    def _emit_procedure_performed(self, clock: Clock) -> dict[str, Any]:
        started = [e for e in self.encounters if e["status"] == "started"]
        if not started:
            return self._emit_encounter_started(clock)
        encounter = self.rng.choice(started)
        code, description = self.rng.choice(self._CPT)
        return {
            "event_type": "procedure_performed",
            "payload": {
                "encounter_id": encounter["encounter_id"],
                "procedure_code": code,
                "description": description,
                "provider_id": encounter["provider_id"],
                "performed_at": clock.now.isoformat(),
            },
        }

    def _emit_lab_order_placed(self, clock: Clock) -> dict[str, Any]:
        started = [e for e in self.encounters if e["status"] in ("started", "diagnosed")]
        if not started:
            return self._emit_encounter_started(clock)
        encounter = self.rng.choice(started)
        tests = self.rng.sample(["CBC", "CMP", "lipid_panel", "A1C"], k=self.rng.randint(1, 3))
        lab_order_id = self.id_gen.next("lo")
        lab_order = {
            "lab_order_id": lab_order_id,
            "encounter_id": encounter["encounter_id"],
            "patient_id": encounter["patient_id"],
            "tests": tests,
            "status": "pending",
        }
        self.lab_orders.append(lab_order)
        return {
            "event_type": "lab_order_placed",
            "payload": {
                "lab_order_id": lab_order_id,
                "encounter_id": encounter["encounter_id"],
                "patient_id": encounter["patient_id"],
                "tests": [{"code": t, "name": t.replace("_", " ")} for t in tests],
                "lab_id": "lab_quest",
                "ordered_at": clock.now.isoformat(),
            },
        }

    def _emit_lab_result_received(self, clock: Clock) -> dict[str, Any]:
        pending = [lo for lo in self.lab_orders if lo["status"] == "pending"]
        if not pending:
            diagnosed = [e for e in self.encounters if e["status"] == "diagnosed"]
            if not diagnosed:
                return self._emit_diagnosis_recorded(clock)
            return self._emit_lab_order_placed(clock)
        lab_order = self.rng.choice(pending)
        lab_order["status"] = "resulted"
        return {
            "event_type": "lab_result_received",
            "payload": {
                "lab_result_id": self.id_gen.next("lr"),
                "lab_order_id": lab_order["lab_order_id"],
                "patient_id": lab_order["patient_id"],
                "results": [
                    {
                        "code": "WBC",
                        "value": round(self.rng.uniform(4.0, 11.0), 1),
                        "unit": "10^9/L",
                        "flag": "normal",
                    }
                ],
                "received_at": clock.now.isoformat(),
            },
        }

    def _emit_medication_prescribed(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [e for e in self.encounters if e["status"] == "diagnosed"]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        encounter = self.rng.choice(diagnosed)
        return {
            "event_type": "medication_prescribed",
            "payload": {
                "prescription_id": self.id_gen.next("rx"),
                "encounter_id": encounter["encounter_id"],
                "patient_id": encounter["patient_id"],
                "medication": self.rng.choice(["metformin_500mg", "lisinopril_10mg", "atorvastatin_20mg"]),
                "quantity": self.rng.randint(30, 90),
                "refills": self.rng.randint(0, 3),
                "pharmacy_id": "ph_01",
                "prescribed_at": clock.now.isoformat(),
            },
        }

    def _emit_claim_submitted(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [e for e in self.encounters if e["status"] == "diagnosed" and not e.get("claim_submitted")]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        encounter = self.rng.choice(diagnosed)
        encounter["claim_submitted"] = True
        claim_id = self.id_gen.next("clm")
        billed_amount = round(self.rng.uniform(100.0, 350.0), 2)
        tax_amount = compute_tax(billed_amount)
        claim = {
            "claim_id": claim_id,
            "encounter_id": encounter["encounter_id"],
            "patient_id": encounter["patient_id"],
            "status": "submitted",
            "amount": billed_amount,
            "tax_amount": tax_amount,
            "tax_type": DEFAULT_TAX_TYPE,
        }
        self.claims.append(claim)
        return {
            "event_type": "claim_submitted",
            "payload": {
                "claim_id": claim_id,
                "encounter_id": encounter["encounter_id"],
                "patient_id": encounter["patient_id"],
                "payer_id": self.id_gen.next("ins"),
                "diagnosis_codes": [self.rng.choice(self._ICD10)[0]],
                "procedure_codes": [self.rng.choice(self._CPT)[0]],
                "billed_amount": billed_amount,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "submitted_at": clock.now.isoformat(),
            },
        }

    def _emit_claim_adjudicated(self, clock: Clock) -> dict[str, Any]:
        submitted = [c for c in self.claims if c["status"] == "submitted"]
        if not submitted:
            return self._emit_claim_submitted(clock)
        claim = self.rng.choice(submitted)
        claim["status"] = "adjudicated"
        status = self.rng.choice(["paid", "denied", "partial"])
        paid = round(claim["amount"] * self.rng.uniform(0.7, 1.0), 2) if status != "denied" else 0.0
        tax_amount = compute_tax(claim["amount"])
        return {
            "event_type": "claim_adjudicated",
            "payload": {
                "claim_id": claim["claim_id"],
                "status": status,
                "paid_amount": paid,
                "patient_responsibility": round(claim["amount"] - paid, 2),
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "adjudicated_at": clock.now.isoformat(),
            },
        }

    def _emit_payment_posted(self, clock: Clock) -> dict[str, Any]:
        adjudicated = [c for c in self.claims if c["status"] == "adjudicated"]
        if not adjudicated:
            return self._emit_claim_adjudicated(clock)
        claim = self.rng.choice(adjudicated)
        amount = round(self.rng.uniform(20.0, 100.0), 2)
        tax_amount = compute_tax(amount)
        return {
            "event_type": "payment_posted",
            "payload": {
                "payment_id": self.id_gen.next("pmt"),
                "claim_id": claim["claim_id"],
                "patient_id": claim["patient_id"],
                "amount": amount,
                "tax_amount": tax_amount,
                "tax_type": DEFAULT_TAX_TYPE,
                "method": self.rng.choice(["card", "cash", "check"]),
                "posted_at": clock.now.isoformat(),
            },
        }

    def _emit_referral_sent(self, clock: Clock) -> dict[str, Any]:
        diagnosed = [e for e in self.encounters if e["status"] == "diagnosed"]
        if not diagnosed:
            return self._emit_diagnosis_recorded(clock)
        encounter = self.rng.choice(diagnosed)
        return {
            "event_type": "referral_sent",
            "payload": {
                "referral_id": self.id_gen.next("ref"),
                "encounter_id": encounter["encounter_id"],
                "patient_id": encounter["patient_id"],
                "to_provider_id": self.id_gen.next("spec"),
                "reason": self.rng.choice(["endocrinology_consult", "cardiology", "orthopedics"]),
                "sent_at": clock.now.isoformat(),
            },
        }

    def _emit_no_show_recorded(self, clock: Clock) -> dict[str, Any]:
        scheduled = [a for a in self.appointments if a["status"] == "scheduled"]
        if not scheduled:
            return self._emit_appointment_scheduled(clock)
        appt = self.rng.choice(scheduled)
        appt["status"] = "no_show"
        return {
            "event_type": "no_show_recorded",
            "payload": {
                "appointment_id": appt["appointment_id"],
                "patient_id": appt["patient_id"],
                "scheduled_time": appt["scheduled_time"].isoformat(),
                "recorded_at": clock.now.isoformat(),
            },
        }
