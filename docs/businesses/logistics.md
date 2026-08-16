# Logistics simulator

Slug: `logistics`  
Source module: [`src/eden_business_simulator/businesses/logistics.py`](../../src/eden_business_simulator/businesses/logistics.py)

## Domain overview

The logistics simulator models last-mile delivery operations. Shipments are created, routes are planned, drivers and vehicles are assigned, routes depart, drivers arrive at stops, deliveries are attempted, proof of delivery or exceptions are captured, customers provide feedback, returns are initiated, routes complete, and vehicles log fuel stops.

## Systems represented

- Shipment creation and order linking
- Route planning and optimization
- Driver and vehicle assignment
- GPS vehicle tracking
- Stop arrival and delivery attempts
- Proof of delivery (POD)
- Delivery exceptions
- Customer feedback
- Returns processing
- Fuel stops

## Operational workflow

1. Create initial drivers, vehicles, and shipments.
2. Plan routes and assign drivers/vehicles.
3. Vehicles depart.
4. Drivers arrive at stops and attempt deliveries.
5. Successful deliveries capture POD; failed deliveries record exceptions.
6. Customers submit feedback and returns are initiated.
7. Routes complete and vehicles log fuel stops.

## Event catalog

### `shipment_created`

```json
{
  "shipment_id": "shp_0009",
  "order_id": "ord_0009",
  "destination": {
    "lat": -33.4187,
    "lon": 151.9885
  },
  "service_level": "same_day",
  "weight_kg": 3.2,
  "hub_id": "hub_east",
  "created_at": "2026-08-02T13:21:15.684405+00:00"
}
```

### `route_planned`

```json
{
  "route_id": "rte_0001",
  "driver_id": "driver_0002",
  "vehicle_id": "vh_0002",
  "hub_id": "hub_east",
  "stops": [
    {
      "stop_id": "stp_0001",
      "shipment_id": "shp_0002",
      "sequence": 1,
      "eta": "2026-08-02T13:36:15.784405+00:00"
    },
    {
      "stop_id": "stp_0002",
      "shipment_id": "shp_0006",
      "sequence": 2,
      "eta": "2026-08-02T13:51:15.784405+00:00"
    }
  ],
  "planned_miles": 37.0,
  "planned_at": "2026-08-02T13:21:15.784405+00:00"
}
```

### `driver_assigned`

```json
{
  "route_id": "rte_0003",
  "driver_id": "driver_0003",
  "vehicle_id": "vh_0002",
  "assigned_at": "2026-08-02T13:21:20.484405+00:00",
  "driver_app_version": "3.2.1"
}
```

### `vehicle_departed`

```json
{
  "route_id": "rte_0001",
  "vehicle_id": "vh_0002",
  "departed_at": "2026-08-02T13:21:15.884405+00:00",
  "initial_odometer_km": 119740
}
```

### `stop_arrived`

```json
{
  "stop_id": "stp_0005",
  "route_id": "rte_0001",
  "shipment_id": "shp_0004",
  "arrived_at": "2026-08-02T13:21:16.484405+00:00",
  "location": {
    "lat": -33.4929,
    "lon": 140.3977
  }
}
```

### `delivery_attempted`

```json
{
  "attempt_id": "att_0001",
  "stop_id": "stp_0002",
  "shipment_id": "shp_0006",
  "outcome": "delivered",
  "attempted_at": "2026-08-02T13:21:16.684405+00:00"
}
```

### `delivery_delivered`

A delivery is confirmed as successfully completed.

```json
{
  "delivery_id": "dlv_0001",
  "shipment_id": "shp_0006",
  "attempt_id": "att_0001",
  "stop_id": "stp_0002",
  "delivered_at": "2026-08-02T13:21:17.084405+00:00",
  "recipient_name": "April Griffin"
}
```

### `proof_of_delivery_captured`

```json
{
  "pod_id": "pod_0001",
  "shipment_id": "shp_0006",
  "attempt_id": "att_0001",
  "recipient_name": "April Griffin",
  "signature_type": "photo",
  "captured_at": "2026-08-02T13:21:17.884405+00:00"
}
```

### `delivery_exception_recorded`

```json
{
  "exception_id": "exc_0001",
  "shipment_id": "shp_0002",
  "attempt_id": "att_0004",
  "reason": "customer_not_home",
  "resolution": "contact_customer",
  "recorded_at": "2026-08-02T13:21:19.784405+00:00"
}
```

### `customer_feedback_received`

```json
{
  "feedback_id": "fb_0001",
  "shipment_id": "shp_0006",
  "rating": 4,
  "comment": "Fast delivery",
  "received_at": "2026-08-02T13:21:16.984405+00:00"
}
```

### `return_initiated`

```json
{
  "return_id": "ret_0001",
  "shipment_id": "shp_0003",
  "reason": "wrong_item",
  "initiated_at": "2026-08-02T13:21:21.184405+00:00"
}
```

### `vehicle_location_update`

```json
{
  "vehicle_id": "vh_0002",
  "driver_id": "driver_0002",
  "route_id": "rte_0001",
  "location": {
    "lat": -35.055,
    "lon": 140.4488
  },
  "speed_kmh": 30,
  "recorded_at": "2026-08-02T13:21:15.984405+00:00"
}
```

### `route_completed`

```json
{
  "route_id": "rte_0001",
  "vehicle_id": "vh_0002",
  "completed_at": "2026-08-02T13:21:17.684405+00:00",
  "stops_completed": 5,
  "stops_failed": 0,
  "actual_miles": 37.7,
  "final_odometer_km": 119800
}
```

### `fuel_stop_logged`

```json
{
  "fuel_stop_id": "fs_0001",
  "vehicle_id": "vh_0001",
  "liters": 25.0,
  "cost": 41.16,
  "tax_amount": 4.12,
  "tax_type": "GST",
  "location": {
    "lat": -35.5113,
    "lon": 140.4917
  },
  "logged_at": "2026-08-02T13:22:39.197320+00:00"
}
```

## Configuration notes

- Override initial counts with `initial_state_overrides`:
  - `initial_drivers` (default `4`)
  - `initial_vehicles` (default `3`)
  - `initial_shipments` (default `8`)
- Service levels: `same_day`, `next_day`, `standard`.
- Delivery outcomes: `delivered`, `customer_not_home`, `address_not_found`, `refused_by_customer`.
- The simulator uses `ActorPool` for drivers and `WeightedEventCatalog` with a `DaypartScheduler` that varies delivery and fuel-stop likelihoods by simulated hour.

## CLI quick start

```bash
uv run eden-business-simulator run logistics --duration 300 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon logistics \
  --stream-id logistics_seed42 \
  --storage sqlite --storage-uri logistics.db \
  --duration 600 --rate 2 --no-realtime

uv run eden-business-simulator replay logistics_seed42 \
  --storage sqlite --storage-uri logistics.db --speed 1.0
```
