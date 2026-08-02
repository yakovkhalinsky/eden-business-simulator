# Cafe simulator

Slug: `cafe`  
Source module: [`src/eden_business_simulator/businesses/cafe.py`](../../src/eden_business_simulator/businesses/cafe.py)

## Domain overview

The cafe simulator models a small hospitality venue across a single shift. It covers opening the register, clocking in staff, loading the menu, receiving supplier deliveries, taking orders, firing tickets to a kitchen display system, preparing items, accepting payments, awarding loyalty points, logging wastage, counting stock, and closing the shift.

## Systems represented

- Point of sale (POS) register and shift floats
- Staff rostering and clock-in/clock-out
- Menu catalog and recipes / BOM
- Supplier deliveries and purchase orders
- Table management
- Kitchen display system (KDS) tickets
- Inventory ledger with auto-deduction on prep
- Loyalty program
- Wastage and stock counts

## Operational workflow

1. **Shift open** — open the register and clock in the first staff member.
2. **Setup** — clock in remaining staff, add menu items, receive supplier deliveries.
3. **Service** — occupy tables, take orders, fire tickets to the kitchen, prepare items, pay orders, and award loyalty points.
4. **Close** — log wastage, record stock counts, close the register, and reconcile cash.

## Event catalog

### `shift_opened`

```json
{
  "register_id": "reg_0001",
  "opened_at": "2026-08-02T13:21:13.729580+00:00",
  "opening_float": 150.0,
  "staff_id": "staff_0001"
}
```

### `staff_clocked_in`

```json
{
  "staff_id": "staff_0002",
  "role": "barista",
  "clocked_in_at": "2026-08-02T13:21:13.829580+00:00"
}
```

### `menu_item_added`

```json
{
  "item_id": "item_0001",
  "name": "oat flat white",
  "category": "coffee",
  "price": 5.5,
  "recipe": [
    {
      "ingredient_id": "ing_001",
      "name": "espresso",
      "qty": 0.036,
      "unit": "kg"
    },
    {
      "ingredient_id": "ing_002",
      "name": "oat_milk",
      "qty": 0.24,
      "unit": "l"
    }
  ],
  "added_at": "2026-08-02T13:21:14.229580+00:00"
}
```

### `supplier_delivery_received`

```json
{
  "delivery_id": "del_0001",
  "supplier_id": "sup_01",
  "items": [
    {
      "ingredient_id": "ing_001",
      "name": "espresso",
      "qty": 25.0,
      "unit": "kg",
      "unit_cost": 18.0
    }
  ],
  "received_at": "2026-08-02T13:21:14.729580+00:00",
  "po_reference": "po_0001"
}
```

### `table_occupied`

```json
{
  "table_id": "tbl_04",
  "party_size": 4,
  "occupied_at": "2026-08-02T13:21:16.229580+00:00",
  "channel": "dine_in"
}
```

### `order_taken`

```json
{
  "order_id": "ord_0001",
  "channel": "takeaway",
  "table_id": null,
  "items": [
    {
      "item_id": "item_0002",
      "name": "espresso",
      "qty": 1,
      "modifiers": [],
      "line_total": 3.0
    },
    {
      "item_id": "item_0001",
      "name": "oat flat white",
      "qty": 1,
      "modifiers": [
        {
          "name": "extra_shot",
          "price": 0.8
        }
      ],
      "line_total": 6.3
    },
    {
      "item_id": "item_0005",
      "name": "fresh orange juice",
      "qty": 1,
      "modifiers": [],
      "line_total": 6.0
    }
  ],
  "taken_at": "2026-08-02T13:21:15.629580+00:00",
  "subtotal": 15.3
}
```

### `item_fired_to_kitchen`

```json
{
  "ticket_id": "tkt_0007",
  "order_id": "ord_0003",
  "item_id": "item_0001",
  "station": "bar",
  "fired_at": "2026-08-02T13:21:16.029580+00:00",
  "due_at": "2026-08-02T13:21:16.029580+00:00"
}
```

### `item_prepared`

```json
{
  "ticket_id": "tkt_0009",
  "item_id": "item_0003",
  "station": "pastry",
  "prepared_at": "2026-08-02T13:21:16.829580+00:00",
  "status": "ready"
}
```

### `order_paid`

```json
{
  "payment_id": "pay_0001",
  "order_id": "ord_0010",
  "amount": 6.0,
  "tip": 1.2,
  "method": "card",
  "paid_at": "2026-08-02T13:21:22.729580+00:00"
}
```

### `loyalty_points_earned`

```json
{
  "member_id": "member_0004",
  "order_id": "ord_0010",
  "points": 7,
  "tier": "gold",
  "earned_at": "2026-08-02T13:21:23.229580+00:00"
}
```

### `wastage_logged`

```json
{
  "waste_id": "wst_0001",
  "ingredient_id": "ing_008",
  "item_id": null,
  "reason": "expired",
  "qty": 2.0,
  "unit": "each",
  "estimated_cost": 1.6,
  "logged_at": "2026-08-02T13:21:17.929580+00:00"
}
```

### `stock_count_recorded`

```json
{
  "ingredient_id": "ing_005",
  "expected_qty": 50.0,
  "actual_qty": 50.0,
  "variance": 0.0,
  "unit": "each",
  "counted_at": "2026-08-02T13:21:15.929580+00:00"
}
```

### `shift_closed`

```json
{
  "register_id": "reg_0001",
  "closed_at": "2026-08-02T13:24:28.739745+00:00",
  "opening_float": 150.0,
  "closing_float": 206.88,
  "cash_sales": 56.88,
  "card_sales": 243.42,
  "total_sales": 300.3,
  "discrepancy": 0.0,
  "waste_total": 90.16
}
```

## Configuration notes

- Override the opening float with `initial_state_overrides.opening_float` (default `150.0`).
- Override the initial loyalty member count with `initial_state_overrides.initial_members` (default `5`).
- The simulator emits setup events (`shift_opened`, all `staff_clocked_in`, all `menu_item_added`, all `supplier_delivery_received`) before any service events.
- `InventoryLedger` auto-deducts recipe ingredients when an item is prepared.
- The cafe uses `DaypartScheduler.default_cafe_schedule()` so order rates vary by simulated hour.
- `TransitionModel` tracks order and ticket states (`new` -> `fired` -> `ready` -> `paid`).
- `shift_closed` fires near the end of the configured duration.

## CLI quick start

```bash
uv run eden-business-simulator run cafe --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon cafe \
  --stream-id cafe_seed42 \
  --storage sqlite --storage-uri cafe.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay cafe_seed42 \
  --storage sqlite --storage-uri cafe.db --speed 1.0
```
