# Plumber simulator

Slug: `plumber`  
Source module: [`src/eden_business_simulator/businesses/plumber.py`](../../src/eden_business_simulator/businesses/plumber.py)

## Domain overview

The plumber simulator models dispatched plumbers who visit customer sites to repair leaks, clear drains, install pipes, and handle emergencies. Tickets are created, plumbers are assigned and dispatched, they arrive, diagnose issues, use parts, complete work, capture customer sign-off, generate invoices, receive payments, schedule follow-ups, and reorder truck stock.

## Systems represented

- Service ticket / work order creation
- Dispatch board and plumber mobile app
- GPS vehicle tracking
- Plumbing parts inventory and reordering
- CRM and contract linking
- Quoting, invoicing, and payment
- Follow-up scheduling

## Operational workflow

1. Seed plumbers, vehicles, customers, and a plumbing parts catalog.
2. Create service tickets linked to customers and optional contracts.
3. Assign a plumber and vehicle to each ticket.
4. Dispatch and track the plumber to the site.
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
  "priority": "urgent",
  "category": "leak_repair",
  "description": "Why rise month shake voice.",
  "created_at": "2026-08-02T13:21:16.650428+00:00"
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
  "fault_code": "F_LEAK_REPAIR",
  "notes": "Two beyond picture rich fast sea time wind may whose.",
  "labor_minutes_estimated": 83,
  "recorded_at": "2026-08-02T13:21:17.250428+00:00"
}
```

### `parts_used`

```json
{
  "ticket_id": "tkt_0003",
  "part_id": "prt_p001",
  "part_name": "pvc_pipe_10ft",
  "qty": 1,
  "unit_cost": 8.0,
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
  "invoice_id": "inv_0002",
  "amount": 160.67,
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
      "part_id": "prt_p001",
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
- Categories: `leak_repair`, `drain_cleaning`, `pipe_install`, `emergency`.
- Parts catalog includes `prt_p001` through `prt_p004` with deterministic costs.
- Note: `payment_received.invoice_id` is generated independently; it is not guaranteed to reference a prior `invoice_generated` event in the same stream.

## CLI quick start

```bash
uv run eden-business-simulator run plumber --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon plumber \
  --stream-id plumber_seed42 \
  --storage sqlite --storage-uri plumber.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay plumber_seed42 \
  --storage sqlite --storage-uri plumber.db --speed 1.0
```
