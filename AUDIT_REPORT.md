# Business Simulator Event Type Audit

Goal: `b22833c3-889f-4617-b8d6-af715270234b`  
Date: 2026-08-16  
Branch: `feat/b22833c3-gym-membership-frozen`

## Scope

Review all nine business simulators in `src/eden_business_simulator/businesses/*.py`:

- `cafe.py`
- `clinic.py`
- `ecommerce.py`
- `electrician.py`
- `field_service.py`
- `gym.py`
- `logistics.py`
- `plumber.py`
- `saas.py`

For each simulator we checked:

1. `available_event_types()`
2. A matching `_emit_<event_type>` method (or equivalent handler path)
3. Registration in the `WeightedEventCatalog` where applicable

## Summary

| Simulator | Event types | Missing `_emit_*` | Not in catalog | Notes |
|-----------|------------:|------------------:|---------------:|-------|
| Cafe      | 13 | 0 | 1 (`shift_opened`) | `shift_opened` is emitted unconditionally via the `_pending_setup` queue. |
| Clinic    | 16 | 0 | 0 | All event types have emitters and catalog entries. |
| Ecommerce | 10 | 10* | 10 | No `_emit_*` naming convention; events are dispatched inline in `next_event`. Catalog is managed by `_choose_event_type()`. |
| Electrician | 15 | 0 | 0 | Inherits `FieldServiceSimulator` emitters/catalog. |
| Field service | 15 | 0 | 0 | All event types have emitters and catalog entries. |
| Gym       | 17 | 0 | 0 | `membership_frozen` now guarded by at-risk active members and weighted 4.0. |
| Logistics | 14 | 0 | 0 | All event types have emitters and catalog entries. |
| Plumber   | 15 | 0 | 0 | Inherits `FieldServiceSimulator` emitters/catalog. |
| SaaS      | 9  | 9* | 9  | No `_emit_*` naming convention for the public event types; events are dispatched inline in `next_event`. Helper methods `_emit_invoice_generated_for_account` and `_emit_support_ticket_opened_for_account` are internal fallbacks. |

*For Ecommerce and SaaS, every event type is still emitted; the dispatch is just not split into per-type `_emit_<event_type>` methods.

## Detailed findings

### Cafe

- **Catalog gap**: `shift_opened` is not registered in the catalog because it is enqueued in `_pending_setup` and emitted unconditionally before normal catalog-driven events.
- **Status**: intentional; no action needed.

### Clinic, Field service, Gym, Logistics

- **Status**: every advertised event type has a matching `_emit_*` method and a catalog entry.

### Ecommerce

- `_choose_event_type()` builds a dynamic weight map based on state (customers, carts, orders, shipments).
- All event types are handled directly inside `next_event()`.
- **Observation**: not a functional gap, but the codebase is inconsistent with the catalog + `_emit_*` pattern used by the other simulators.

### SaaS

- `next_event()` picks uniformly from `available_event_types()` and dispatches inline.
- If no accounts exist it falls back to `account_signed_up`.
- Helper methods `_emit_invoice_generated_for_account` and `_emit_support_ticket_opened_for_account` are used as fallbacks, not as the primary dispatch path.
- **Observation**: like Ecommerce, this is a structural style difference rather than a missing event type.

### Electrician / Plumber

- Both subclass `FieldServiceSimulator` and reuse its full catalog and emitters.
- Their only overrides are `_CATEGORIES` and `_PARTS`.

## Conclusion

No event type is unemitted or unregistered in a way that breaks the advertised `available_event_types()` contract. The only deviations are intentional (`cafe.shift_opened` via setup queue) or stylistic (`ecommerce` and `saas` dispatch inline rather than through `_emit_*` helpers). The gym `membership_frozen` gap identified by this goal has been closed.
