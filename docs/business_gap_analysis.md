# Business Type Gap Analysis

Goal: b13c5e81-8889-4859-94d5-35dbfc2e9c15  
Date: 2026-08-16  
Status: findings captured; prioritized fixes implemented in branch `feat/b13c5e81-business-gap-fixes`

## Scope

This document records the comprehensive review of all business types in `eden-business-simulator`. It identifies gaps in event/data coverage, KPI support, and entity-linking correctness, and it documents the fixes that were applied.

Sources consulted during the review are recorded in Eden-memory:

- Context summary record: `6e7a414e-f9f2-4442-932f-526c47b4beaf`
- Researcher-to-Builder hand-off: `004da535-74c3-4fcd-ac0f-b039f1f4689b`

## Summary of findings

| Area | Finding | Severity |
|------|---------|----------|
| Insights KPI registry | Only `ecommerce`, `gym`, `cafe`, and `saas` have KPI calculators. `clinic`, `field_service`, `electrician`, `plumber`, and `logistics` return empty metrics. | P1 |
| Entity linking | `payment_received.invoice_id` in trades simulators and `lab_result_received.lab_order_id` in `clinic` are generated independently of prior events, breaking referential integrity. | P1 |
| SaaS payloads | Every SaaS event except `account_signed_up` reuses a generic `feature_used`-style payload. | P1 |
| Missing lifecycle events | Several domains lack core lifecycle events: ecommerce checkout funnel; clinic appointment cancel/reschedule; logistics explicit `delivery_delivered`; gym membership renewal/upgrade/downgrade; trades estimate/quote events. | P1 |

## Findings by business type

### Clinic

- **KPIs**: no calculator registered in `insights/kpi.py`.
- **Entity-linking bug**: `_emit_lab_result_received` generates a fresh `lab_order_id` instead of referencing a previously emitted `lab_order_placed` event.
- **Missing lifecycle events**: no `appointment_cancelled` or `appointment_rescheduled` events.

### Field service / electrician / plumber

- **KPIs**: no calculators registered for `field_service`, `electrician`, or `plumber`.
- **Entity-linking bug**: `_emit_payment_received` generates a fresh `invoice_id` instead of referencing a previously emitted `invoice_generated` event.
- **Missing lifecycle events**: no estimate/quote events before work begins.

### Logistics

- **KPIs**: no calculator registered.
- **Missing lifecycle events**: there is no explicit `delivery_delivered` event; delivery success is only encoded as the `outcome` field of `delivery_attempted`.

### SaaS

- **Payloads**: all events except `account_signed_up` share the same generic payload (`account_id`, `event_id`, `feature_key`, `quantity`, `recorded_at`). Per-event schemas for `plan_subscribed`, `feature_used`, `invoice_generated`, `payment_succeeded`, `payment_failed`, `support_ticket_opened`, `ticket_resolved`, and `churned` are missing.

### Ecommerce

- **Missing lifecycle events**: there is no explicit checkout funnel between `cart_updated` and `order_placed`.

### Gym

- **Missing lifecycle events**: there is no `membership_renewed`, `membership_upgraded`, or `membership_downgraded` event.

## Prioritized recommendations

### P1: Register KPI calculators for every business type

Add read-only calculators in `src/eden_business_simulator/insights/kpi.py` for:

- `clinic` — appointments, no-shows, encounters, claims, payments, referrals.
- `field_service`, `electrician`, `plumber` — tickets, completed jobs, invoice/payment totals, parts usage.
- `logistics` — shipments, delivered/failed attempts, returns, route/fuel costs.

### P1: Fix entity-linking bugs

- In `clinic.py`, persist `lab_order_id` on the encounter and reuse it in `lab_result_received`.
- In `field_service.py` (and its subclasses), persist `invoice_id` on the ticket and reuse it in `payment_received`.

### P1: Implement proper SaaS per-event payloads

Replace the generic `feature_used`-style fallback with dedicated emitters and payloads for every SaaS event type while keeping determinism and event-type coverage intact.

### P1: Add missing lifecycle events where feasible

- **Ecommerce**: add `checkout_started` and `payment_info_entered` events to represent the checkout funnel.
- **Clinic**: add `appointment_cancelled` and `appointment_rescheduled` events.
- **Logistics**: add a `delivery_delivered` event emitted after a successful `delivery_attempted`.
- **Gym**: add `membership_renewed`, `membership_upgraded`, and `membership_downgraded` events.
- **Trades**: add `estimate_requested` and `estimate_approved` events before work begins.

## Implementation notes

The prioritized fixes above were implemented on branch `feat/b13c5e81-business-gap-fixes`. See the action record for this goal in Eden-memory for the full change list, test results, and rollback options.

Key implementation choices:

- New events were gated by the existing `WeightedEventCatalog` patterns and added to each simulator's `available_event_types()` so that documentation tests and insights coverage remain consistent.
- KPIs were added without mutating events; they aggregate the existing event stream.
- Entity-linking fixes reuse IDs generated by `IdGenerator` so that determinism and replay fidelity are preserved.
- SaaS per-event payloads keep the same event types but now include domain-specific fields (plan details, invoice line items, ticket severity, churn reason, etc.).
