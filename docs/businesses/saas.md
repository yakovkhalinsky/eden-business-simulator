# SaaS simulator

Slug: `saas`  
Source module: [`src/eden_business_simulator/businesses/saas.py`](../../src/eden_business_simulator/businesses/saas.py)

## Domain overview

The SaaS simulator models a subscription software business. Accounts sign up, subscribe to plans, use features, receive invoices, make or fail payments, open support tickets, and eventually churn.

## Important behavioral note

The current implementation is a minimal stub. Only `account_signed_up` has a dedicated payload schema. Every other event type in the catalog (`plan_subscribed`, `feature_used`, `invoice_generated`, `payment_succeeded`, `payment_failed`, `support_ticket_opened`, `ticket_resolved`, `churned`) reuses the same generic `feature_used`-style payload shown below. The `event_type` field is the only difference for those events. Full per-event payload schemas are planned for a future release.

## Systems represented

- Account signup and attribution
- Subscription plans
- Feature usage metering
- Invoicing
- Payment success/failure
- Support tickets
- Churn

## Operational workflow

1. Seed an initial account base.
2. Accounts sign up with an attribution channel.
3. Accounts subscribe to plans and consume features.
4. Invoices are generated and payments succeed or fail.
5. Support tickets are opened and resolved.
6. Some accounts churn.

## Event catalog

### `account_signed_up`

```json
{
  "account_id": "acc_0004",
  "email": "barrypeter@example.net",
  "signup_at": "2026-08-02T13:21:12.768564+00:00",
  "attribution": "paid"
}
```

### `plan_subscribed`

Generic stub payload.

```json
{
  "account_id": "acc_0002",
  "event_id": "evt_000002",
  "feature_key": "workflow_run",
  "quantity": 61,
  "recorded_at": "2026-08-02T13:21:12.867472+00:00"
}
```

### `feature_used`

```json
{
  "account_id": "acc_0003",
  "event_id": "evt_000001",
  "feature_key": "api_call",
  "quantity": 33,
  "recorded_at": "2026-08-02T13:21:12.767472+00:00"
}
```

### `invoice_generated`

Generic stub payload.

```json
{
  "account_id": "acc_0001",
  "event_id": "evt_000005",
  "feature_key": "user_invited",
  "quantity": 4,
  "recorded_at": "2026-08-02T13:21:13.267472+00:00"
}
```

### `payment_succeeded`

Generic stub payload.

```json
{
  "account_id": "acc_0001",
  "event_id": "evt_000012",
  "feature_key": "user_invited",
  "quantity": 93,
  "recorded_at": "2026-08-02T13:21:14.067472+00:00"
}
```

### `payment_failed`

Generic stub payload.

```json
{
  "account_id": "acc_0005",
  "event_id": "evt_000027",
  "feature_key": "report_export",
  "quantity": 65,
  "recorded_at": "2026-08-02T13:21:15.667472+00:00"
}
```

### `support_ticket_opened`

Generic stub payload.

```json
{
  "account_id": "acc_0001",
  "event_id": "evt_000003",
  "feature_key": "api_call",
  "quantity": 63,
  "recorded_at": "2026-08-02T13:21:12.967472+00:00"
}
```

### `ticket_resolved`

Generic stub payload.

```json
{
  "account_id": "acc_0005",
  "event_id": "evt_000018",
  "feature_key": "api_call",
  "quantity": 100,
  "recorded_at": "2026-08-02T13:21:14.667472+00:00"
}
```

### `churned`

Generic stub payload.

```json
{
  "account_id": "acc_0001",
  "event_id": "evt_000006",
  "feature_key": "workflow_run",
  "quantity": 88,
  "recorded_at": "2026-08-02T13:21:13.467472+00:00"
}
```

## Configuration notes

- Override the initial account count with `initial_state_overrides.initial_accounts` (default `3`).
- Feature keys are chosen randomly from a small catalog.
- Attribution values: `organic`, `paid`, `referral`.

## CLI quick start

```bash
uv run eden-business-simulator run saas --duration 60 --rate 2 --seed 42 --no-realtime

uv run eden-business-simulator daemon saas \
  --stream-id saas_seed42 \
  --storage sqlite --storage-uri saas.db \
  --duration 120 --rate 2 --no-realtime
```
