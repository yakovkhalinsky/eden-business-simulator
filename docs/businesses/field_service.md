# Field service simulator

Slug: `field_service`  
Source module: [`src/eden_business_simulator/businesses/field_service.py`](../../src/eden_business_simulator/businesses/field_service.py)

## Domain overview

The field service simulator models dispatched technicians who visit customer sites. Tickets are created, technicians are assigned and dispatched, they arrive, diagnose issues, use parts, complete work, capture customer sign-off, generate invoices, receive payments, schedule follow-ups, and reorder truck stock.

## Systems represented

- Service ticket / work order creation
- Dispatch board and technician mobile app
- GPS vehicle tracking
- Parts inventory and reordering
- CRM and contract linking
- Quoting, invoicing, and payment
- Follow-up scheduling

## Operational workflow

1. Seed technicians, vehicles, customers, and a parts catalog.
2. Create service tickets linked to customers and optional contracts.
3. Assign a technician and vehicle to each ticket.
4. Dispatch and track the technician to the site.
5. Record diagnosis, use parts, and complete the work.
6. Capture customer sign-off and satisfaction.
7. Generate an invoice and receive payment.
8. Schedule follow-ups and reorder parts as needed.

## Event catalog

### `service_ticket_created`

```json
{
  "ticket_id": "tkt_0001",
  "customer_id": "cust_0003",
  "contract_id": null,
  "priority": "low",
  "category": "plumbing",
  "description": "Why rise month shake voice.",
  "created_at": "2026-08-02T13:21:16.650428+00:00"
}
```

### `estimate_requested`

A customer requests a work estimate before a ticket is assigned.

```json
{
  "estimate_id": "est_0001",
  "ticket_id": "tkt_0001",
  "customer_id": "cust_0003",
  "requested_at": "2026-08-02T13:21:16.750428+00:00",
  "notes": "Customer wants a quote for the repair."
}
```

### `estimate_approved`

The customer approves the estimate and work can proceed.

```json
{
  "estimate_id": "est_0002",
  "ticket_id": "tkt_0001",
  "customer_id": "cust_0003",
  "labor_estimate": 120.0,
  "parts_estimate": 45.0,
  "total_estimate": 165.0,
  "approved_at": "2026-08-02T13:21:16.850428+00:00"
}
```

### `technician_assigned`

```json
{
  "ticket_id": "tkt_0001",
  "technician_id": "tech_0001",
  "vehicle_id": "vh_0001",
  "assigned_at": "2026-08-02T13:21:16.750428+00:00",
  "estimated_arrival": "2026-08-02T13:42:16.750428+00:00"
}
```

### `technician_dispatched`

```json
{
  "ticket_id": "tkt_0001",
  "technician_id": "tech_0001",
  "dispatched_at": "2026-08-02T13:21:16.850428+00:00",
  "location": {
    "lat": -33.8688,
    "lon": 151.2093
  }
}
```

### `technician_arrived`

```json
{
  "ticket_id": "tkt_0001",
  "technician_id": "tech_0001",
  "arrived_at": "2026-08-02T13:21:16.950428+00:00",
  "travel_minutes": 38
}
```

### `diagnosis_recorded`

```json
{
  "ticket_id": "tkt_0001",
  "technician_id": "tech_0001",
  "fault_code": "F_PLUMBING",
  "notes": "Two beyond picture rich fast sea time wind may whose.",
  "labor_minutes_estimated": 83,
  "recorded_at": "2026-08-02T13:21:17.250428+00:00"
}
```

### `parts_used`

```json
{
  "ticket_id": "tkt_0003",
  "part_id": "prt_004",
  "part_name": "breaker_20a",
  "qty": 1,
  "unit_cost": 12.0,
  "truck_stock": true,
  "used_at": "2026-08-02T13:21:20.050428+00:00"
}
```

### `work_completed`

```json
{
  "ticket_id": "tkt_0001",
  "technician_id": "tech_0001",
  "resolution": "Stop prove field onto think suffer.",
  "labor_minutes": 53,
  "completed_at": "2026-08-02T13:21:17.350428+00:00"
}
```

### `customer_sign_off`

```json
{
  "ticket_id": "tkt_0001",
  "customer_id": "cust_0003",
  "signed_at": "2026-08-02T13:21:17.450428+00:00",
  "signature_method": "mobile_pin",
  "satisfaction_score": 3
}
```

### `invoice_generated`

```json
{
  "invoice_id": "inv_0001",
  "ticket_id": "tkt_0001",
  "customer_id": "cust_0003",
  "labor_amount": 130.77,
  "parts_amount": 83.26,
  "tax_amount": 21.4,
  "total": 235.43,
  "generated_at": "2026-08-02T13:21:17.550428+00:00"
}
```

### `payment_received`

```json
{
  "payment_id": "pay_0001",
  "invoice_id": "inv_0001",
  "ticket_id": "tkt_0001",
  "amount": 235.43,
  "method": "cash",
  "status": "settled",
  "received_at": "2026-08-02T13:21:18.450428+00:00"
}
```

### `follow_up_scheduled`

```json
{
  "ticket_id": "tkt_0001",
  "follow_up_id": "fu_0001",
  "follow_up_type": "maintenance_check",
  "scheduled_date": "2026-10-13",
  "scheduled_at": "2026-08-02T13:21:23.650428+00:00"
}
```

### `vehicle_location_update`

```json
{
  "vehicle_id": "vh_0002",
  "technician_id": "tech_0002",
  "location": {
    "lat": -33.0902,
    "lon": 151.3184
  },
  "speed_kmh": 58,
  "recorded_at": "2026-08-02T13:21:18.550428+00:00"
}
```

### `parts_reorder_placed`

```json
{
  "reorder_id": "ro_0001",
  "supplier_id": "sup_0001",
  "parts": [
    {
      "part_id": "prt_004",
      "qty": 5
    }
  ],
  "placed_at": "2026-08-02T13:21:20.550428+00:00"
}
```

## Configuration notes

- Override initial counts with `initial_state_overrides`:
  - `initial_techs` (default `3`)
  - `initial_vehicles` (default `2`)
  - `initial_customers` (default `5`)
- Categories: `hvac_repair`, `plumbing`, `electrical`, `appliance`.
- Parts catalog includes `prt_001` through `prt_004` with deterministic costs.
- `payment_received.invoice_id` now references a prior `invoice_generated` event from the same stream.

## CLI quick start

```bash
uv run eden-business-simulator run field_service --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon field_service \
  --stream-id field_service_seed42 \
  --storage sqlite --storage-uri field_service.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay field_service_seed42 \
  --storage sqlite --storage-uri field_service.db --speed 1.0
```
