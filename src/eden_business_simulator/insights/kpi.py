"""Business-specific KPI calculators for the insights workflow.

Each calculator receives the full list of event envelopes (in sequence order) and
returns a JSON-serializable dict of metrics. Calculators are read-only and must
not mutate the events.
"""

from __future__ import annotations

from typing import Any, Callable

from eden_business_simulator.models import EventEnvelope

KpiCalculator = Callable[[list[EventEnvelope]], dict[str, Any]]


def _sum_numeric(events: list[EventEnvelope], event_type: str, *keys: str) -> float:
    total = 0.0
    for envelope in events:
        if envelope.event_type != event_type:
            continue
        value: Any = envelope.payload
        for key in keys:
            if isinstance(value, dict):
                value = value.get(key, 0.0)
            else:
                value = 0.0
                break
        try:
            total += float(value)  # type: ignore[arg-type]
        except (TypeError, ValueError):
            pass
    return round(total, 2)


def _count(events: list[EventEnvelope], event_type: str) -> int:
    return sum(1 for e in events if e.event_type == event_type)


def _unique_ids(events: list[EventEnvelope], event_type: str | None, key: str) -> int:
    ids: set[str] = set()
    for envelope in events:
        if event_type is not None and envelope.event_type != event_type:
            continue
        value = envelope.payload.get(key)
        if isinstance(value, str):
            ids.add(value)
    return len(ids)


def _safe_div(numerator: float, denominator: float) -> float | None:
    if denominator == 0:
        return None
    return round(numerator / denominator, 4)


def _ecommerce_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    orders = _count(events, "order_placed")
    revenue = sum(
        float(e.payload.get("amount", 0.0))
        for e in events
        if e.event_type == "payment_processed" and e.payload.get("status") == "approved"
    )
    approved_payments = sum(
        1
        for e in events
        if e.event_type == "payment_processed" and e.payload.get("status") == "approved"
    )
    declined_payments = sum(
        1
        for e in events
        if e.event_type == "payment_processed" and e.payload.get("status") == "declined"
    )
    refunds = _count(events, "refund_issued")
    product_views = _count(events, "product_viewed")
    total_order_value = _sum_numeric(events, "order_placed", "total")

    return {
        "revenue": round(revenue, 2),
        "total_order_value": total_order_value,
        "orders": orders,
        "average_order_value": _safe_div(total_order_value, orders),
        "approved_payments": approved_payments,
        "declined_payments": declined_payments,
        "payment_decline_rate": _safe_div(declined_payments, approved_payments + declined_payments),
        "refunds": refunds,
        "refund_rate": _safe_div(refunds, orders),
        "product_views": product_views,
        "conversion_rate": _safe_div(orders, product_views),
    }


def _gym_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    enrolled = _unique_ids(events, "membership_enrolled", "member_id")
    cancelled = _unique_ids(events, "membership_cancelled", "member_id")
    active_members = max(0, enrolled - cancelled)
    check_ins = _count(events, "check_in_recorded")
    bookings = _count(events, "class_booked")
    attended = _count(events, "class_attended")
    cancelled_bookings = _count(events, "class_cancelled")
    retail_revenue = _sum_numeric(events, "retail_purchase_made", "total")
    failed_payments = _count(events, "payment_failed")
    monthly_fees = [
        float(e.payload.get("monthly_fee", 0.0))
        for e in events
        if e.event_type == "membership_enrolled" and isinstance(e.payload.get("monthly_fee"), (int, float))
    ]
    avg_monthly_fee = round(sum(monthly_fees) / len(monthly_fees), 2) if monthly_fees else None

    return {
        "enrolled_members": enrolled,
        "cancelled_members": cancelled,
        "active_members": active_members,
        "check_ins": check_ins,
        "check_in_rate_per_active_member": _safe_div(check_ins, active_members),
        "class_bookings": bookings,
        "class_attended": attended,
        "class_cancelled": cancelled_bookings,
        "class_attendance_rate": _safe_div(attended, bookings),
        "class_cancellation_rate": _safe_div(cancelled_bookings, bookings),
        "retail_revenue": retail_revenue,
        "payment_failures": failed_payments,
        "average_monthly_fee": avg_monthly_fee,
    }


def _cafe_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    orders_taken = _count(events, "order_taken")
    orders_paid = _count(events, "order_paid")
    sales = _sum_numeric(events, "order_paid", "amount")
    tips = _sum_numeric(events, "order_paid", "tip")
    waste_cost = _sum_numeric(events, "wastage_logged", "estimated_cost")
    table_occupancies = _count(events, "table_occupied")
    cash_sales = sum(
        float(e.payload.get("amount", 0.0))
        for e in events
        if e.event_type == "order_paid" and e.payload.get("method") == "cash"
    )
    card_sales = sum(
        float(e.payload.get("amount", 0.0))
        for e in events
        if e.event_type == "order_paid" and e.payload.get("method") in ("card", "mobile")
    )

    return {
        "orders_taken": orders_taken,
        "orders_paid": orders_paid,
        "total_sales": round(sales, 2),
        "total_tips": round(tips, 2),
        "average_order_value": _safe_div(sales, orders_paid),
        "table_occupancies": table_occupancies,
        "waste_cost": round(waste_cost, 2),
        "cash_sales": round(cash_sales, 2),
        "card_sales": round(card_sales, 2),
    }


def _saas_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    signups = _count(events, "account_signed_up")
    active_accounts = _unique_ids(events, None, "account_id")
    feature_usage = _count(events, "feature_used")
    quantities = [
        int(e.payload.get("quantity", 0))
        for e in events
        if e.event_type == "feature_used" and isinstance(e.payload.get("quantity"), int)
    ]
    avg_quantity = round(sum(quantities) / len(quantities), 2) if quantities else None

    top_feature: str | None = None
    feature_counts: dict[str, int] = {}
    for e in events:
        if e.event_type != "feature_used":
            continue
        key = e.payload.get("feature_key")
        if isinstance(key, str):
            feature_counts[key] = feature_counts.get(key, 0) + 1
    if feature_counts:
        top_feature = max(feature_counts, key=feature_counts.get)

    invoices = _sum_numeric(events, "invoice_generated", "total")
    revenue = _sum_numeric(events, "payment_succeeded", "amount")
    failed_payments = _count(events, "payment_failed")
    churned = _count(events, "churned")
    tickets_opened = _count(events, "support_ticket_opened")
    tickets_resolved = _count(events, "ticket_resolved")
    mrr = _sum_numeric(events, "plan_subscribed", "monthly_fee")

    return {
        "signups": signups,
        "active_accounts": active_accounts,
        "feature_usage_events": feature_usage,
        "average_feature_quantity": avg_quantity,
        "top_feature": top_feature,
        "invoiced_amount": invoices,
        "revenue": revenue,
        "failed_payments": failed_payments,
        "churned_accounts": churned,
        "support_tickets_opened": tickets_opened,
        "support_tickets_resolved": tickets_resolved,
        "mrr_from_subscriptions": mrr,
    }


def _clinic_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    appointments = _count(events, "appointment_scheduled")
    checked_in = _count(events, "patient_checked_in")
    no_shows = _count(events, "no_show_recorded")
    encounters = _count(events, "encounter_started")
    diagnoses = _count(events, "diagnosis_recorded")
    procedures = _count(events, "procedure_performed")
    lab_orders = _count(events, "lab_order_placed")
    prescriptions = _count(events, "medication_prescribed")
    claims_submitted = _count(events, "claim_submitted")
    claims_adjudicated = _count(events, "claim_adjudicated")
    payments = _count(events, "payment_posted")
    referrals = _count(events, "referral_sent")
    billed = _sum_numeric(events, "claim_submitted", "billed_amount")
    paid = _sum_numeric(events, "claim_adjudicated", "paid_amount")
    collected = _sum_numeric(events, "payment_posted", "amount")

    return {
        "appointments_scheduled": appointments,
        "patients_checked_in": checked_in,
        "no_shows": no_shows,
        "no_show_rate": _safe_div(no_shows, appointments),
        "encounters_started": encounters,
        "diagnoses_recorded": diagnoses,
        "procedures_performed": procedures,
        "lab_orders_placed": lab_orders,
        "medications_prescribed": prescriptions,
        "claims_submitted": claims_submitted,
        "claims_adjudicated": claims_adjudicated,
        "payments_posted": payments,
        "referrals_sent": referrals,
        "total_billed_amount": billed,
        "total_paid_by_payer": paid,
        "total_patient_payments": collected,
        "check_in_rate": _safe_div(checked_in, appointments),
    }


def _trades_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    tickets_created = _count(events, "service_ticket_created")
    tickets_completed = _count(events, "work_completed")
    invoices_generated = _count(events, "invoice_generated")
    payments_received = _count(events, "payment_received")
    parts_used = _count(events, "parts_used")
    follow_ups = _count(events, "follow_up_scheduled")
    sign_offs = _count(events, "customer_sign_off")
    estimates = _count(events, "estimate_requested")
    estimates_approved = _count(events, "estimate_approved")
    invoices_total = _sum_numeric(events, "invoice_generated", "total")
    payments_total = _sum_numeric(events, "payment_received", "amount")
    parts_cost = _sum_numeric(events, "parts_used", "qty")

    return {
        "tickets_created": tickets_created,
        "tickets_completed": tickets_completed,
        "completion_rate": _safe_div(tickets_completed, tickets_created),
        "invoices_generated": invoices_generated,
        "payments_received": payments_received,
        "invoiced_total": invoices_total,
        "payments_total": payments_total,
        "collection_rate": _safe_div(payments_total, invoices_total),
        "parts_used_events": parts_used,
        "parts_quantity": parts_cost,
        "follow_ups_scheduled": follow_ups,
        "customer_sign_offs": sign_offs,
        "estimates_requested": estimates,
        "estimates_approved": estimates_approved,
        "estimate_approval_rate": _safe_div(estimates_approved, estimates),
    }


def _logistics_kpis(events: list[EventEnvelope]) -> dict[str, Any]:
    shipments = _count(events, "shipment_created")
    routes = _count(events, "route_planned")
    attempts = _count(events, "delivery_attempted")
    delivered = _count(events, "delivery_delivered")
    failed = sum(
        1
        for e in events
        if e.event_type == "delivery_attempted" and e.payload.get("outcome") != "delivered"
    )
    exceptions = _count(events, "delivery_exception_recorded")
    returns = _count(events, "return_initiated")
    feedback = _count(events, "customer_feedback_received")
    pods = _count(events, "proof_of_delivery_captured")
    routes_completed = _count(events, "route_completed")
    fuel_cost = _sum_numeric(events, "fuel_stop_logged", "cost")
    fuel_liters = _sum_numeric(events, "fuel_stop_logged", "liters")
    ratings = [
        int(e.payload.get("rating", 0))
        for e in events
        if e.event_type == "customer_feedback_received" and isinstance(e.payload.get("rating"), int)
    ]
    avg_rating = round(sum(ratings) / len(ratings), 2) if ratings else None

    return {
        "shipments_created": shipments,
        "routes_planned": routes,
        "delivery_attempts": attempts,
        "deliveries_completed": delivered,
        "delivery_success_rate": _safe_div(delivered, attempts),
        "failed_attempts": failed,
        "delivery_exceptions": exceptions,
        "returns_initiated": returns,
        "customer_feedback_events": feedback,
        "average_feedback_rating": avg_rating,
        "proof_of_delivery_captures": pods,
        "routes_completed": routes_completed,
        "fuel_cost": fuel_cost,
        "fuel_liters": fuel_liters,
    }


KPI_REGISTRY: dict[str, KpiCalculator] = {
    "ecommerce": _ecommerce_kpis,
    "gym": _gym_kpis,
    "cafe": _cafe_kpis,
    "saas": _saas_kpis,
    "clinic": _clinic_kpis,
    "field_service": _trades_kpis,
    "electrician": _trades_kpis,
    "plumber": _trades_kpis,
    "logistics": _logistics_kpis,
}


def calculate_kpis(business_type: str, events: list[EventEnvelope]) -> dict[str, Any]:
    """Return KPI metrics for a business type, or an empty dict if unregistered."""
    calculator = KPI_REGISTRY.get(business_type)
    if calculator is None:
        return {}
    return calculator(events)
